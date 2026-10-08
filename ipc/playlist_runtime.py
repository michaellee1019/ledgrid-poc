"""Controller-owned finite playlist execution with CAS ownership."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import threading
import time
from typing import Any, Callable, Mapping

from ipc.scene_contract import canonical_json_sha256
from ipc.runtime_control import (
    ControllerCommandConflictError,
    controller_activation_coordinator,
    normalize_managed_scene,
    start_scene,
)

PLAYLIST_COMMAND_SCHEMA = "ledgrid.playlist-command"
PLAYLIST_STATUS_SCHEMA = "ledgrid.playlist-status"
PLAYLIST_VERSION = 1
RETRYABLE_RECEIVER_STATUS_ERROR = "receiver 3 lacks intact protected-v8 display status"
PLAYLIST_RETRY_WINDOW_SECONDS = 30.0


class PlaylistError(RuntimeError):
    """A playlist request cannot execute safely."""


def normalize_playlist_command(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("playlist command must be an object")
    if value.get("schema") != PLAYLIST_COMMAND_SCHEMA or value.get("schema_version") != PLAYLIST_VERSION:
        raise ValueError("playlist command schema is invalid")
    request_id = value.get("request_id")
    action = value.get("action")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("playlist request_id is required")
    if action not in {"start", "stop"}:
        raise ValueError("playlist action must be start or stop")
    normalized = {
        "schema": PLAYLIST_COMMAND_SCHEMA,
        "schema_version": PLAYLIST_VERSION,
        "request_id": request_id,
        "action": action,
        "requested_at": float(value.get("requested_at", 0)),
    }
    if action == "stop":
        run_id = value.get("run_id")
        if run_id is not None and (not isinstance(run_id, str) or not run_id):
            raise ValueError("playlist stop run_id is invalid")
        normalized["run_id"] = run_id
        return normalized
    run_id = value.get("run_id")
    session_id = value.get("expected_controller_session_id")
    revision = value.get("expected_controller_state_revision")
    entries = value.get("entries")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("playlist run_id is required")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("playlist controller session is required")
    if type(revision) is not int or revision < 0:
        raise ValueError("playlist controller revision is invalid")
    if not isinstance(entries, list) or not entries or len(entries) > 100:
        raise ValueError("playlist needs from 1 to 100 entries")
    clean_entries = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping) or set(entry) != {"entry_id", "label", "duration_seconds", "scene"}:
            raise ValueError(f"playlist entry {index + 1} is malformed")
        duration = entry["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0.05 <= float(duration) <= 86400:
            raise ValueError(f"playlist entry {index + 1} duration must be from 0.05 to 86400 seconds")
        if not isinstance(entry["entry_id"], str) or not entry["entry_id"]:
            raise ValueError(f"playlist entry {index + 1} identity is invalid")
        if not isinstance(entry["label"], str) or not entry["label"].strip() or len(entry["label"].strip()) > 120:
            raise ValueError(f"playlist entry {index + 1} label is invalid")
        if not isinstance(entry["scene"], Mapping):
            raise ValueError(f"playlist entry {index + 1} scene is malformed")
        clean_entries.append({
            "entry_id": entry["entry_id"], "label": entry["label"].strip(),
            "duration_seconds": float(duration), "scene": deepcopy(dict(entry["scene"])),
        })
    normalized.update({
        "run_id": run_id,
        "playlist_id": value.get("playlist_id"),
        "playlist_name": str(value.get("playlist_name") or "Playlist")[:80],
        "expected_controller_session_id": session_id,
        "expected_controller_state_revision": revision,
        "entries": clean_entries,
    })
    return normalized


class PlaylistRunner:
    """Advance one finite playlist while its exact controller revision is owned."""

    def __init__(self, manager: Any, coordinator=None, *, clock: Callable[[], float] | None = None,
                 wall_clock: Callable[[], float] | None = None,
                 status_sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.manager = manager
        self.coordinator = coordinator or controller_activation_coordinator(manager)
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._status_sink = status_sink
        self._lock = threading.RLock()
        self._active: dict[str, Any] | None = None
        self._status = self._inactive_status()
        self._pending_publication: dict[str, Any] | None = None
        self._request_results: OrderedDict[str, tuple[dict[str, Any], dict[str, Any]]] = OrderedDict()

    def _inactive_status(self) -> dict[str, Any]:
        return {"schema": PLAYLIST_STATUS_SCHEMA, "schema_version": PLAYLIST_VERSION,
                "phase": "idle", "controller_session_id": self.coordinator.session_id,
                "updated_at": self._wall_clock(), "run_id": None, "request_id": None,
                "playlist_id": None, "playlist_name": None, "current_index": None,
                "entry_count": 0, "current_entry": None, "next_entry": None, "entry_started_at": None,
                "entry_deadline_at": None, "remaining_seconds": None, "error": None,
                "retry_count": 0, "last_recovery_error": None}

    def _flush_pending_publication(self) -> bool:
        pending = self._pending_publication
        if pending is None:
            return True
        if self._status_sink is None:
            self._pending_publication = None
            return True
        try:
            self._status_sink(deepcopy(pending))
        except Exception:
            # The controller state already changed. Keep the latest snapshot so
            # its durable projection can catch up without replaying a mutation.
            return False
        self._pending_publication = None
        return True

    def _publish(self, **updates: Any) -> dict[str, Any]:
        self._status = {**self._status, **updates, "updated_at": self._wall_clock()}
        # Project the immutable active run, never the editable saved definition.
        next_entry = None
        if self._status.get("phase") == "running" and self._active is not None:
            next_index = self._active["index"] + 1
            if next_index < len(self._active["entries"]):
                next_entry = self._entry_summary(self._active["entries"][next_index])
        self._status["next_entry"] = next_entry
        payload = deepcopy(self._status)
        self._pending_publication = deepcopy(payload)
        self._flush_pending_publication()
        return payload

    def status(self) -> dict[str, Any]:
        with self._lock:
            result = deepcopy(self._status)
            if result.get("phase") == "running" and self._active is not None:
                result["remaining_seconds"] = max(0.0, self._active["deadline"] - self._clock())
            return result

    def _guard(self, revision: int) -> dict[str, Any]:
        return {"expected_controller_session_id": self.coordinator.session_id,
                "expected_controller_state_revision": revision,
                "expires_at": time.time() + 10.0}

    def _validate_scene(self, scene: Mapping[str, Any]) -> dict[str, Any]:
        return normalize_managed_scene(self.manager, scene)

    def _start_entry_with_verified_recovery(
        self, scene: Mapping[str, Any], *, entry_index: int,
        expected_revision: int, session_id: str, operation_id: str,
    ) -> dict[str, Any]:
        """Try a complete entry at most twice, with exact restoration first.

        Each attempt owns a separate CAS lease. Releasing the lease after
        compensation lets a newer manual command win before the second try.
        """
        first_snapshot = None
        retry_count = 0
        last_recovery_error = None
        retry_deadline = time.monotonic() + PLAYLIST_RETRY_WINDOW_SECONDS
        for attempt in range(2):
            if self.coordinator.session_id != session_id:
                raise ControllerCommandConflictError("controller session changed during playlist entry")
            if attempt and time.monotonic() >= retry_deadline:
                return {"started": False, "snapshot": first_snapshot,
                        "owned_revision": expected_revision,
                        "retry_count": retry_count,
                        "last_recovery_error": last_recovery_error,
                        "error": "playlist recovery window expired before its single retry"}
            failure = None
            restore_error = None
            with self.coordinator.legacy_mutation_guard(self._guard(expected_revision)) as mutation:
                prior = self.coordinator.capture_display_snapshot()
                if first_snapshot is None:
                    first_snapshot = prior
                prior_failure = deepcopy(getattr(self.manager, "_receiver_last_failure", None))
                attempt_started_at = time.time()
                try:
                    if not start_scene(self.manager, dict(scene)):
                        raise PlaylistError(f"controller rejected playlist entry {entry_index + 1}")
                except Exception as exc:
                    failure = exc
                    preparation = getattr(self.manager, "_receiver_last_failure", None)
                    prepared_without_takeover = (
                        isinstance(exc, PlaylistError)
                        and scene.get("schema") == "ledgrid.scene.v2"
                        and isinstance(preparation, Mapping)
                        and preparation != prior_failure
                        and preparation.get("operation") == "receiver_hybrid_preparation"
                        and preparation.get("phase") == "before_presentation_takeover"
                        and preparation.get("scene_digest") == canonical_json_sha256(scene)
                        and isinstance(preparation.get("observed_at"), (int, float))
                        and preparation["observed_at"] >= attempt_started_at
                    )
                    try:
                        if prepared_without_takeover:
                            try:
                                self.coordinator.verify_display_snapshot_unchanged(prior)
                            except Exception:
                                # A preparation label never substitutes for
                                # exact current receiver/display evidence.
                                self.coordinator.restore_display_snapshot_verified(
                                    prior, operation_id=operation_id
                                )
                        else:
                            self.coordinator.restore_display_snapshot_verified(
                                prior, operation_id=operation_id
                            )
                    except Exception as exc:
                        restore_error = exc
            owned_revision = mutation.resulting_state_revision
            if owned_revision is None:
                raise PlaylistError("controller did not report playlist ownership")
            if failure is None:
                return {"started": True, "snapshot": first_snapshot,
                        "owned_revision": owned_revision,
                        "retry_count": retry_count,
                        "last_recovery_error": last_recovery_error,
                        "error": None}
            last_recovery_error = str(failure)
            if restore_error is not None:
                return {"started": False, "snapshot": first_snapshot,
                        "owned_revision": owned_revision,
                        "retry_count": retry_count,
                        "last_recovery_error": last_recovery_error,
                        "error": f"{failure}; exact restoration failed: {restore_error}"}
            if (attempt or str(failure) != RETRYABLE_RECEIVER_STATUS_ERROR
                    or time.monotonic() >= retry_deadline):
                return {"started": False, "snapshot": first_snapshot,
                        "owned_revision": owned_revision,
                        "retry_count": retry_count,
                        "last_recovery_error": last_recovery_error,
                        "error": str(failure)}
            retry_count = 1
            expected_revision = owned_revision
        raise AssertionError("playlist entry retry budget was exceeded")

    def dispatch(self, raw_command: Any) -> dict[str, Any]:
        """Execute one immutable request once and replay its exact result."""
        command = normalize_playlist_command(raw_command)
        request_id = command["request_id"]
        with self._lock:
            cached = self._request_results.get(request_id)
            if cached is not None:
                cached_command, cached_result = cached
                if cached_command != command:
                    return self._request_result(
                        command, "rejected",
                        "playlist request identity already names another command",
                    )
                self._request_results.move_to_end(request_id)
                return deepcopy(cached_result)
            result = self.start(command) if command["action"] == "start" else self.stop(command)
            self._request_results[request_id] = (deepcopy(command), deepcopy(result))
            while len(self._request_results) > 512:
                self._request_results.popitem(last=False)
            return deepcopy(result)

    def start(self, raw_command: Any) -> dict[str, Any]:
        command = normalize_playlist_command(raw_command)
        if command["action"] != "start":
            raise ValueError("start requires a playlist start command")
        with self._lock:
            if self._active is not None:
                return self._request_result(command, "rejected", "another playlist is already running")
            if command["expected_controller_session_id"] != self.coordinator.session_id:
                return self._request_result(command, "rejected", "controller session is stale")
            if command["expected_controller_state_revision"] != self.coordinator.state_revision:
                return self._request_result(command, "rejected", "controller state revision is stale")
            try:
                entries = [{**entry, "scene": self._validate_scene(entry["scene"])} for entry in command["entries"]]
                transition = self._start_entry_with_verified_recovery(
                    entries[0]["scene"], entry_index=0,
                    expected_revision=command["expected_controller_state_revision"],
                    session_id=command["expected_controller_session_id"],
                    operation_id=command["run_id"],
                )
                if not transition["started"]:
                    return self._request_result(
                        command, "rejected", transition["error"],
                        retry_count=transition["retry_count"],
                        last_recovery_error=transition["last_recovery_error"],
                    )
            except (ControllerCommandConflictError, PlaylistError, TypeError, ValueError, RuntimeError) as exc:
                return self._request_result(command, "rejected", str(exc))
            now = self._clock()
            now_wall = self._wall_clock()
            self._active = {"command": command, "entries": entries,
                            "snapshot": transition["snapshot"],
                            "index": 0, "owned_revision": transition["owned_revision"],
                            "deadline": now + entries[0]["duration_seconds"]}
            return self._publish(
                phase="running", run_id=command["run_id"], request_id=command["request_id"],
                playlist_id=command.get("playlist_id"), playlist_name=command["playlist_name"],
                current_index=0, entry_count=len(entries), current_entry=self._entry_summary(entries[0]),
                entry_started_at=now_wall,
                entry_deadline_at=now_wall + entries[0]["duration_seconds"],
                remaining_seconds=entries[0]["duration_seconds"], error=None,
                retry_count=transition["retry_count"],
                last_recovery_error=transition["last_recovery_error"],
            )

    def stop(self, raw_command: Any) -> dict[str, Any]:
        command = normalize_playlist_command(raw_command)
        if command["action"] != "stop":
            raise ValueError("stop requires a playlist stop command")
        with self._lock:
            active = self._active
            if active is None:
                return self._request_result(command, "stopped", None)
            requested_run = command.get("run_id")
            if requested_run is not None and requested_run != active["command"]["run_id"]:
                return self._request_result(command, "rejected", "playlist run is no longer current")
            self._active = None
            return self._publish(phase="stopped", request_id=command["request_id"],
                                 remaining_seconds=None, entry_deadline_at=None, error=None)

    def advance(self) -> dict[str, Any]:
        with self._lock:
            self._flush_pending_publication()
            active = self._active
            if active is None:
                return self.status()
            if self.coordinator.state_revision != active["owned_revision"]:
                self._active = None
                return self._publish(phase="overridden", remaining_seconds=None,
                                     entry_deadline_at=None,
                                     error="Playlist stopped because another controller change took priority.")
            now = self._clock()
            if now < active["deadline"]:
                return self.status()
            next_index = active["index"] + 1
            try:
                if next_index >= len(active["entries"]):
                    snapshot = active["snapshot"]
                    with self.coordinator.legacy_mutation_guard(self._guard(active["owned_revision"])):
                        self.coordinator.restore_display_snapshot_verified(
                            snapshot, operation_id=active["command"]["run_id"]
                        )
                    self._active = None
                    return self._publish(phase="completed", current_index=None,
                                         current_entry=None, remaining_seconds=0.0,
                                         entry_deadline_at=None, error=None)
                entry = active["entries"][next_index]
                transition = self._start_entry_with_verified_recovery(
                    entry["scene"], entry_index=next_index,
                    expected_revision=active["owned_revision"],
                    session_id=active["command"]["expected_controller_session_id"],
                    operation_id=active["command"]["run_id"],
                )
                if not transition["started"]:
                    self._active = None
                    return self._publish(
                        phase="failed", remaining_seconds=None,
                        entry_deadline_at=None, error=transition["error"],
                        retry_count=self._status["retry_count"] + transition["retry_count"],
                        last_recovery_error=transition["last_recovery_error"],
                    )
                active["index"] = next_index
                active["owned_revision"] = transition["owned_revision"]
                activated_at = self._clock()
                activated_wall = self._wall_clock()
                # Compensate polling overshoot, but do not spend the new
                # entry's display duration preparing its presentation.
                active["deadline"] = (
                    max(active["deadline"] + entry["duration_seconds"], now)
                    + activated_at - now
                )
                remaining = max(0.0, active["deadline"] - activated_at)
                return self._publish(
                    phase="running", current_index=next_index,
                    current_entry=self._entry_summary(entry),
                    entry_started_at=activated_wall,
                    entry_deadline_at=activated_wall + remaining,
                    remaining_seconds=remaining, error=None,
                    retry_count=self._status["retry_count"] + transition["retry_count"],
                    last_recovery_error=(transition["last_recovery_error"]
                                         or self._status["last_recovery_error"]),
                )
            except ControllerCommandConflictError:
                self._active = None
                return self._publish(phase="overridden", remaining_seconds=None,
                                     entry_deadline_at=None,
                                     error="Playlist stopped because another controller change took priority.")
            except (PlaylistError, TypeError, ValueError, RuntimeError) as exc:
                self._active = None
                return self._publish(phase="failed", remaining_seconds=None,
                                     entry_deadline_at=None, error=str(exc))

    def _request_result(
        self, command: Mapping[str, Any], phase: str, error: str | None,
        *, retry_count: int = 0, last_recovery_error: str | None = None,
    ) -> dict[str, Any]:
        return {"schema": PLAYLIST_STATUS_SCHEMA, "schema_version": PLAYLIST_VERSION,
                "phase": phase, "controller_session_id": self.coordinator.session_id,
                "updated_at": self._wall_clock(), "run_id": command.get("run_id"),
                "request_id": command["request_id"], "playlist_id": command.get("playlist_id"),
                "playlist_name": command.get("playlist_name"), "current_index": None,
                "entry_count": len(command.get("entries", [])), "current_entry": None,
                "entry_started_at": None, "entry_deadline_at": None,
                "remaining_seconds": None, "error": error,
                "retry_count": retry_count,
                "last_recovery_error": last_recovery_error}

    @staticmethod
    def _entry_summary(entry: Mapping[str, Any]) -> dict[str, Any]:
        return {"entry_id": entry["entry_id"], "label": entry["label"],
                "duration_seconds": entry["duration_seconds"]}
