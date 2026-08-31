"""Serialized local control plane for Composer's live-first Scene v2 state.

The object in this module deliberately has no wall, receiver, deployment, or
preview responsibilities.  It is the narrow place where a canonical scene is
accepted, made current, and (when armed) atomically acknowledged by the local
adapter.  Keeping that state separate from Flask makes retries and concurrent
clients deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
import copy
import threading
from typing import Any, Mapping

from animation.core.component_catalog import ComponentCatalog
from ipc.scene_contract import (
    CanonicalScene,
    LocalSceneAdapter,
    SceneContractError,
    build_scene_activation_command,
    normalize_composer_scene,
)


class LiveSceneStateError(RuntimeError):
    """A valid request could not change the live-state machine."""


class LiveSceneStale(LiveSceneStateError):
    """A delayed mutation from one client must not replace a newer one."""


class LiveSceneBlocked(LiveSceneStateError):
    """Go Live needs an explicit recovery action before it can proceed."""

    def __init__(self, blockers: list[dict[str, str]]) -> None:
        super().__init__("Go Live is not ready")
        self.blockers = blockers


@dataclass(frozen=True)
class _RequestResult:
    sequence: int | None
    digest: str
    identity: dict[str, Any]


class LiveSceneState:
    """Current-only, newest-valid-scene-wins publication state.

    Scene identity remains the canonical Scene v2 digest.  ``revision`` in the
    status payload is a separate, server-serialized publication revision used
    by clients to invalidate local undo after another editor wins.
    """

    def __init__(self, catalog: ComponentCatalog, adapter: LocalSceneAdapter, control_channel: Any) -> None:
        if not isinstance(catalog, ComponentCatalog):
            raise TypeError("LiveSceneState requires a ComponentCatalog")
        if not isinstance(adapter, LocalSceneAdapter):
            raise TypeError("LiveSceneState requires a LocalSceneAdapter")
        if not callable(getattr(control_channel, "send_command", None)):
            raise TypeError("LiveSceneState requires a control channel")
        self._catalog = catalog
        self._adapter = adapter
        self._control = control_channel
        self._lock = threading.RLock()
        self._connected = True
        self._running = True
        self._armed = True
        self._current: CanonicalScene | None = None
        self._desired: CanonicalScene | None = None
        self._observed: CanonicalScene | None = None
        self._revision = 0
        self._desired_revision = 0
        self._observed_revision = 0
        self._last_author: str | None = None
        self._last_error: str | None = None
        self._client_sequences: dict[str, int] = {}
        self._client_owned_revisions: dict[str, int] = {}
        self._client_acknowledged_remote_revisions: dict[str, int] = {}
        self._requests: dict[tuple[str, str], _RequestResult] = {}

    def submit(self, request: Mapping[str, Any], *, client_id: str = "composer", mutation_id: str | None = None,
               client_sequence: int | None = None) -> dict[str, Any]:
        """Validate a complete scene then publish it immediately when armed.

        Canonicalization happens before taking the publication lock, so an
        invalid scene cannot alter desired, observed, revisions, or another
        client's current output.  The lock serializes the valid commit and the
        acknowledgement as one local transaction.
        """
        canonical = normalize_composer_scene(request, self._catalog)
        client_id = self._client_id(client_id)
        mutation_id = self._mutation_id(mutation_id)
        sequence = self._sequence(client_sequence)
        with self._lock:
            prior = self._requests.get((client_id, mutation_id)) if mutation_id else None
            if prior is not None:
                if prior.digest != canonical.identity.digest:
                    raise LiveSceneStale("a retry must name its original scene")
                # Never replay the old response body: another client may have
                # published a newer valid scene since this request first won.
                # The retry identifies its own acknowledged basis but carries
                # the current authoritative desired/observed/revision state.
                return {
                    **self.snapshot(client_id=client_id),
                    "mutation_basis": dict(prior.identity),
                    "published": False,
                    "coalesced": True,
                    "exact_retry": True,
                }
            previous_sequence = self._client_sequences.get(client_id)
            if sequence is not None and previous_sequence is not None and sequence <= previous_sequence:
                raise LiveSceneStale("a delayed edit cannot replace a newer edit from this client")
            if sequence is not None:
                self._client_sequences[client_id] = sequence

            changed = self._current is None or self._current.identity != canonical.identity
            if changed:
                self._revision += 1
                self._current = canonical
                self._desired = canonical
                self._desired_revision = self._revision
                self._last_author = client_id
                self._client_owned_revisions[client_id] = self._revision
                self._client_acknowledged_remote_revisions[client_id] = self._revision
            published = False
            if self._connected and self._running and self._armed and changed:
                self._publish_current_locked()
                published = True
            result = self.snapshot(client_id=client_id)
            result.update({"canonical_scene": canonical.scene, "published": published, "coalesced": not changed,
                           "exact_retry": False})
            if mutation_id:
                self._requests[(client_id, mutation_id)] = _RequestResult(
                    sequence, canonical.identity.digest, canonical.identity.to_dict(),
                )
                # Request IDs are retry hints, not a durable mutation log.
                # Bound them so a long-running Composer cannot grow without
                # limit while preserving recent rapid-control retries.
                if len(self._requests) > 256:
                    self._requests.pop(next(iter(self._requests)))
            return result

    def stop(self, *, client_id: str = "composer") -> dict[str, Any]:
        """Turn output off without discarding the locally editable current scene."""
        with self._lock:
            if not self._running:
                return self.snapshot(client_id=client_id)
            if not self._connected:
                self._running = False
                self._armed = False
                return self.snapshot(client_id=client_id)
            if self._observed is not None:
                try:
                    self._control.send_command("stop_scene", basis=self._observed.identity.to_dict())
                    self._adapter.accept_stop(self._observed.identity.to_dict())
                except TimeoutError as exc:
                    # Stop has uncertain output semantics after a timeout.  A
                    # later edit therefore remains local; only an explicit Go
                    # Live may re-arm and retry publication.
                    self._running = False
                    self._armed = False
                    self._last_error = str(exc) or "Stop acknowledgement timed out."
                    raise
            self._running = False
            self._armed = False
            self._last_error = None
            return self.snapshot(client_id=client_id)

    def go_live(self, *, client_id: str = "composer") -> dict[str, Any]:
        """Explicitly arm and publish the current scene after Stop/reconnect."""
        client_id = self._client_id(client_id)
        with self._lock:
            blockers = self._readiness_locked()
            if blockers:
                raise LiveSceneBlocked(blockers)
            self._running = True
            self._armed = True
            self._last_error = None
            self._publish_current_locked()
            return self.snapshot(client_id=client_id)

    def set_connected(self, connected: bool) -> dict[str, Any]:
        """Suspend synchronization on disconnect; reconnection never replays."""
        if not isinstance(connected, bool):
            raise ValueError("connection state must be boolean")
        with self._lock:
            if not connected:
                self._connected = False
                self._armed = False
            elif not self._connected:
                self._connected = True
                self._armed = False
                self._running = False
            return self.snapshot()

    def check(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Return advisory diagnostics without issuing a token or changing state."""
        canonical = normalize_composer_scene(request, self._catalog)
        with self._lock:
            return {"valid": True, "canonical_scene": canonical.scene, "basis": canonical.identity.to_dict(),
                    "status": self.snapshot()}

    def acknowledge_undo_invalidation(self, *, client_id: str, revision: int) -> dict[str, Any]:
        """Acknowledge one observed remote revision after clearing local undo.

        The marker is edge-triggered: it remains visible until the client
        acknowledges this exact-or-newer server revision, then appears again
        only when another client successfully publishes a newer scene.
        """
        client_id = self._client_id(client_id)
        if type(revision) is not int or revision < 0:
            raise ValueError("revision must be a non-negative integer")
        with self._lock:
            if revision > self._revision:
                raise ValueError("revision has not been published")
            self._client_acknowledged_remote_revisions[client_id] = max(
                revision, self._client_acknowledged_remote_revisions.get(client_id, 0),
            )
            return self.snapshot(client_id=client_id)

    def snapshot(self, *, client_id: str | None = None, include_current_scene: bool = False) -> dict[str, Any]:
        with self._lock:
            observed = self._observed.identity.to_dict() if self._running and self._observed else None
            current = self._current.identity.to_dict() if self._current else None
            desired = self._desired.identity.to_dict() if self._desired else None
            if not self._connected:
                state = "disconnected"
            elif self._last_error:
                state = "recovery"
            elif not self._running:
                state = "stopped"
            elif self._current is None:
                state = "ready"
            elif observed == desired:
                state = "live"
            else:
                state = "pending"
            client = client_id if isinstance(client_id, str) and client_id else None
            owned_revision = self._client_owned_revisions.get(client or "", 0)
            acknowledged_remote_revision = self._client_acknowledged_remote_revisions.get(client or "", 0)
            undo_invalidated = bool(
                client and self._last_author and self._last_author != client
                and owned_revision < self._revision
                and acknowledged_remote_revision < self._revision
            )
            payload = {
                "state": state,
                "connected": self._connected,
                "running": self._running,
                "armed": self._armed,
                "current": current,
                "desired": desired,
                "observed": observed,
                "revision": self._revision,
                "desired_revision": self._desired_revision,
                "observed_revision": self._observed_revision if observed else 0,
                "remote_revision": self._revision,
                "undo_invalidated": undo_invalidated,
                "undo_invalidation_revision": self._revision if undo_invalidated else None,
                "last_error": self._last_error,
                "readiness": self._readiness_locked(),
                "wall_mutations": 0,
            }
            if include_current_scene:
                # Recovery must receive scene bytes and identity from this same
                # lock acquisition, never from a later durable-store read.
                payload["current_scene"] = copy.deepcopy(self._current.scene) if self._current else None
            return payload

    def _publish_current_locked(self) -> None:
        if self._current is None:
            return
        command = build_scene_activation_command(self._current)
        # Validate exactly the command that will be acknowledged before the
        # control sink observes it.  No scene state changes if this fails.
        self._adapter.validate_control(command)
        try:
            self._control.send_command("activate_scene", basis=dict(command["basis"]), scene=dict(command["scene"]))
            observed = self._adapter.accept_control(command)
        except TimeoutError as exc:
            self._armed = False
            self._last_error = str(exc) or "Scene acknowledgement timed out."
            raise
        if observed != self._current.identity:  # pragma: no cover - adapter invariant
            raise SceneContractError("local adapter acknowledged a different scene")
        self._observed = self._current
        self._observed_revision = self._desired_revision
        self._last_error = None

    def _readiness_locked(self) -> list[dict[str, str]]:
        blockers: list[dict[str, str]] = []
        if not self._connected:
            blockers.append({"code": "not_connected", "message": "Composer is disconnected.", "recovery": "Reconnect, then choose Go Live."})
        if self._current is None:
            blockers.append({"code": "no_scene", "message": "Choose a valid scene first.", "recovery": "Select a look or make a valid edit."})
        return blockers

    @staticmethod
    def _client_id(value: Any) -> str:
        if not isinstance(value, str) or not value or len(value) > 160:
            raise ValueError("client_id must be a non-empty short string")
        return value

    @staticmethod
    def _mutation_id(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value or len(value) > 160:
            raise ValueError("mutation_id must be a non-empty short string")
        return value

    @staticmethod
    def _sequence(value: Any) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value < 0:
            raise ValueError("client_sequence must be a non-negative integer")
        return value
