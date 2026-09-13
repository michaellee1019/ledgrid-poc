#!/usr/bin/env python3
"""
File-backed control and status channel for decoupling controller and web UI.
"""

from copy import deepcopy
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional
import uuid


CONTROL_COMMAND_SCHEMA = "ledgrid.control-command"
CONTROL_STATUS_SCHEMA = "ledgrid.controller-status"
CONTROL_CHANNEL_VERSION = 1
ACTIVATION_COMMAND_SCHEMA = "ledgrid.scene-activation-command"
ACTIVATION_STATUS_SCHEMA = "ledgrid.scene-activation-status"
ACTIVATION_CANCEL_SCHEMA = "ledgrid.scene-activation-cancel"
ACTIVATION_ROLLBACK_SCHEMA = "ledgrid.scene-activation-rollback-request"
ACTIVATION_CANCEL_RESULT_SCHEMA = "ledgrid.scene-activation-cancel-result"
ACTIVATION_ROLLBACK_RESULT_SCHEMA = "ledgrid.scene-activation-rollback-result"
ACTIVATION_CHANNEL_VERSION = 1
ACTIVATION_REQUEST_OUTCOMES = frozenset({"succeeded", "rejected", "failed"})
MAINTENANCE_COMMAND_SCHEMA = "ledgrid.maintenance-frame-request"
MAINTENANCE_STATUS_SCHEMA = "ledgrid.maintenance-frame-status"
MAINTENANCE_RESULT_SCHEMA = "ledgrid.maintenance-frame-result"
MAINTENANCE_CHANNEL_VERSION = 1
MAINTENANCE_PHASES = frozenset(
    {"queued", "running", "restored", "safe_idle", "rejected", "failed"}
)
MAINTENANCE_TERMINAL_PHASES = frozenset({"restored", "safe_idle", "rejected", "failed"})
PLAYLIST_COMMAND_SCHEMA = "ledgrid.playlist-command"
PLAYLIST_STATUS_SCHEMA = "ledgrid.playlist-status"
PLAYLIST_CHANNEL_VERSION = 1


class FileControlChannel:
    """
    Simple JSON file channel used to pass commands to the controller process and
    read back status/frame data. Writes are atomic (temp file + rename) so the
    other process never sees partial data.
    """

    def __init__(self, control_path: str = "run_state/control.json",
                 status_path: str = "run_state/status.json",
                 activation_root: str | None = None):
        self.control_path = Path(control_path)
        self.status_path = Path(status_path)
        self.activation_root = (
            Path(activation_root)
            if activation_root is not None
            else self.control_path.parent / "activations"
        )
        self.activation_queue_path = self.activation_root / "queue"
        self.activation_status_path = self.activation_root / "status"
        self.activation_cancel_path = self.activation_root / "cancel"
        self.activation_rollback_path = self.activation_root / "rollback"
        self.activation_cancel_result_path = self.activation_root / "cancel-result"
        self.activation_rollback_result_path = self.activation_root / "rollback-result"
        self._activation_poll_session = None
        self._activation_poll_directories = {}
        self._activation_poll_files = {}
        self._activation_poll_commands = {}
        self._activation_poll_pending = set()
        self.playlist_root = self.control_path.parent / "playlists"
        self.playlist_queue_path = self.playlist_root / "queue"
        self.playlist_status_path = self.playlist_root / "status"
        self.playlist_current_path = self.playlist_root / "current.json"
        self._playlist_poll_session = None
        self._playlist_poll_directory = None
        self._playlist_poll_files = {}
        self._playlist_poll_pending = set()
        self.maintenance_root = self.control_path.parent / "maintenance"
        self.maintenance_queue_path = self.maintenance_root / "queue"
        self.maintenance_status_path = self.maintenance_root / "status"
        self.maintenance_result_path = self.maintenance_root / "result"
        self.control_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.parent.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, path: Path, payload: Dict[str, Any]):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            tmp_path.replace(path)
            self._fsync_directory(path.parent)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _atomic_create(self, path: Path, payload: Dict[str, Any]) -> bool:
        """Create one fully-written immutable queue record without overwrite."""

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp_path, path)
            except FileExistsError:
                return False
            self._fsync_directory(path.parent)
            return True
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _recover_last_json_object(raw_payload: str) -> Optional[Dict[str, Any]]:
        """
        Best-effort recovery for files that accidentally contain concatenated JSON
        objects (e.g. {"a":1}{"b":2}). Returns the last object if parseable.
        """
        decoder = json.JSONDecoder()
        index = 0
        last_obj: Optional[Dict[str, Any]] = None
        length = len(raw_payload)

        while index < length:
            while index < length and raw_payload[index].isspace():
                index += 1
            if index >= length:
                break

            parsed, end = decoder.raw_decode(raw_payload, index)
            if isinstance(parsed, dict):
                last_obj = parsed
            index = end

        return last_obj

    def _read_json_file(self, path: Path, label: str) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            raw_payload = path.read_text(encoding="utf-8")
        except Exception as exc:  # pragma: no cover - best effort read
            print(f"⚠️ Failed to read {label} file {path}: {exc}")
            return None

        if not raw_payload.strip():
            return None

        try:
            parsed = json.loads(raw_payload)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError as exc:
            recovered = self._recover_last_json_object(raw_payload)
            if recovered is not None:
                print(f"⚠️ {label} file {path} contained concatenated JSON; recovered latest command")
                self._atomic_write(path, recovered)
                return recovered
            print(f"⚠️ Failed to read {label} file {path}: {exc}")
            return None

    def read_control(self) -> Optional[Dict[str, Any]]:
        return self._read_json_file(self.control_path, "control")

    def write_control(self, payload: Dict[str, Any]):
        payload = dict(payload)
        payload.setdefault("written_at", time.time())
        self._atomic_write(self.control_path, payload)

    def send_command(self, action: str, **data) -> Dict[str, Any]:
        """
        Convenience helper for writing a single command payload with a unique id.
        """
        command_id = time.time()
        payload = {
            "schema": CONTROL_COMMAND_SCHEMA,
            "schema_version": CONTROL_CHANNEL_VERSION,
            "command_id": command_id,
            "action": action,
            "data": data or {},
            "written_at": command_id,
        }
        self.write_control(payload)
        return payload

    def read_status(self) -> Optional[Dict[str, Any]]:
        return self._read_json_file(self.status_path, "status")

    def write_status(self, payload: Dict[str, Any]):
        payload = dict(payload)
        payload.setdefault("schema", CONTROL_STATUS_SCHEMA)
        payload.setdefault("schema_version", CONTROL_CHANNEL_VERSION)
        payload.setdefault("written_at", time.time())
        self._atomic_write(self.status_path, payload)

    @staticmethod
    def _playlist_request_id(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("playlist request_id must be a lowercase UUID")
        try:
            canonical = str(uuid.UUID(value))
        except (ValueError, AttributeError) as exc:
            raise ValueError("playlist request_id must be a lowercase UUID") from exc
        if canonical != value:
            raise ValueError("playlist request_id must be a lowercase UUID")
        return value

    def playlist_command_path(self, request_id: str) -> Path:
        return self.playlist_queue_path / f"{self._playlist_request_id(request_id)}.json"

    def playlist_status_file(self, request_id: str) -> Path:
        return self.playlist_status_path / f"{self._playlist_request_id(request_id)}.json"

    def enqueue_playlist_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        from ipc.playlist_runtime import normalize_playlist_command
        payload = normalize_playlist_command(command)
        path = self.playlist_command_path(payload["request_id"])
        if self._atomic_create(path, payload):
            return payload
        existing = self._strict_json(path, "playlist command")
        if existing != payload:
            raise FileExistsError("playlist request ID already names a different command")
        return existing

    def read_playlist_command(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self._strict_json(self.playlist_command_path(request_id), "playlist command")

    def read_playlist_request_status(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self._strict_json(self.playlist_status_file(request_id), "playlist status")

    def write_playlist_request_status(self, status: Dict[str, Any]) -> Dict[str, Any]:
        request_id = self._playlist_request_id(status.get("request_id"))
        payload = dict(status)
        payload.setdefault("schema", PLAYLIST_STATUS_SCHEMA)
        payload.setdefault("schema_version", PLAYLIST_CHANNEL_VERSION)
        self._atomic_write(self.playlist_status_file(request_id), payload)
        return payload

    def write_playlist_current_status(self, status: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(status)
        payload.setdefault("schema", PLAYLIST_STATUS_SCHEMA)
        payload.setdefault("schema_version", PLAYLIST_CHANNEL_VERSION)
        self._atomic_write(self.playlist_current_path, payload)
        return payload

    def read_playlist_current_status(self) -> Optional[Dict[str, Any]]:
        payload = self._strict_json(self.playlist_current_path, "playlist current status")
        if payload is not None and payload.get("phase") == "running":
            deadline = payload.get("entry_deadline_at")
            if isinstance(deadline, (int, float)):
                payload["remaining_seconds"] = max(0.0, float(deadline) - time.time())
        return payload

    def poll_playlist_commands(self, session_id: str) -> list[Dict[str, Any]]:
        """Discover new immutable requests without rescanning history while idle."""
        if session_id != self._playlist_poll_session:
            self._playlist_poll_session = session_id
            self._playlist_poll_directory = None
            self._playlist_poll_files = {}
            self._playlist_poll_pending = set()
        signature = self._activation_poll_fingerprint(self.playlist_queue_path)
        if signature != self._playlist_poll_directory:
            current = {}
            if signature is not None:
                for path in self.playlist_queue_path.glob("*.json"):
                    fingerprint = self._activation_poll_fingerprint(path)
                    if fingerprint is not None:
                        current[path.stem] = fingerprint
            for request_id, fingerprint in current.items():
                if self._playlist_poll_files.get(request_id) != fingerprint:
                    self._playlist_poll_pending.add(request_id)
            self._playlist_poll_files = current
            self._playlist_poll_directory = signature
        commands = []
        for request_id in sorted(self._playlist_poll_pending):
            if request_id not in self._playlist_poll_files:
                self._playlist_poll_pending.discard(request_id)
                continue
            if self.read_playlist_request_status(request_id) is not None:
                self._playlist_poll_pending.discard(request_id)
                continue
            command = self.read_playlist_command(request_id)
            if command is not None:
                commands.append(command)
        commands.sort(key=lambda item: (item.get("requested_at", 0), item.get("request_id", "")))
        return commands

    def acknowledge_playlist_poll(self, request_id: str) -> None:
        self._playlist_poll_pending.discard(request_id)

    @staticmethod
    def _activation_id(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("activation_id must be a lowercase UUID")
        try:
            parsed = uuid.UUID(value)
        except (AttributeError, ValueError) as exc:
            raise ValueError("activation_id must be a lowercase UUID") from exc
        canonical = str(parsed)
        if value != canonical:
            raise ValueError("activation_id must be a lowercase UUID")
        return canonical

    @staticmethod
    def _strict_json(path: Path, label: str) -> Optional[Dict[str, Any]]:
        """Read activation state fail-closed; never recover trailing objects."""

        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{label} is malformed: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{label} must be a JSON object")
        return payload

    def activation_command_path(self, activation_id: str) -> Path:
        return self.activation_queue_path / f"{self._activation_id(activation_id)}.json"

    def activation_status_file(self, activation_id: str) -> Path:
        return self.activation_status_path / f"{self._activation_id(activation_id)}.json"

    def activation_cancel_file(self, activation_id: str) -> Path:
        return self.activation_cancel_path / f"{self._activation_id(activation_id)}.json"

    def activation_rollback_file(self, activation_id: str) -> Path:
        return self.activation_rollback_path / f"{self._activation_id(activation_id)}.json"

    def activation_cancel_result_file(self, activation_id: str) -> Path:
        return self.activation_cancel_result_path / f"{self._activation_id(activation_id)}.json"

    def activation_rollback_result_file(self, activation_id: str) -> Path:
        return self.activation_rollback_result_path / f"{self._activation_id(activation_id)}.json"

    def enqueue_activation(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """Durably enqueue one activation; exact retries never duplicate it."""

        if not isinstance(command, dict):
            raise TypeError("activation command must be an object")
        if command.get("schema") != ACTIVATION_COMMAND_SCHEMA:
            raise ValueError("activation command schema is invalid")
        if command.get("schema_version") != ACTIVATION_CHANNEL_VERSION:
            raise ValueError("activation command schema_version is invalid")
        activation_id = self._activation_id(command.get("activation_id"))
        payload = dict(command)
        path = self.activation_command_path(activation_id)
        if self._atomic_create(path, payload):
            return payload
        existing = self._strict_json(path, "activation command")
        if existing != payload:
            raise FileExistsError(
                "activation ID already names a different durable command"
            )
        return existing

    def read_activation_command(
        self, activation_id: str
    ) -> Optional[Dict[str, Any]]:
        return self._strict_json(
            self.activation_command_path(activation_id), "activation command"
        )

    def list_activation_commands(self) -> list[Dict[str, Any]]:
        if not self.activation_queue_path.is_dir():
            return []
        commands = []
        for path in sorted(self.activation_queue_path.glob("*.json")):
            payload = self._strict_json(path, "activation command")
            if payload is not None:
                commands.append(payload)
        return commands

    @staticmethod
    def _activation_poll_fingerprint(path: Path):
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return (stat.st_dev, stat.st_ino, stat.st_mtime_ns,
                stat.st_ctime_ns, stat.st_size)

    def poll_activation_commands(
        self, session_id: str, *, retry_ids=()
    ) -> list[Dict[str, Any]]:
        """Discover work without reparsing unchanged durable terminal history.

        All channel writers publish by atomic create/replace. Directory metadata
        therefore discovers their changes, including other processes, without an
        idle per-record scan. In-place edits outside that protocol require a new
        session/full scan. History remains durable and the public list API still
        returns every record. This polling view has one controller consumer.
        """
        if session_id != self._activation_poll_session:
            self._activation_poll_session = session_id
            self._activation_poll_directories.clear()
            self._activation_poll_files.clear()
            self._activation_poll_commands.clear()
            self._activation_poll_pending.clear()
        self._activation_poll_pending.update(retry_ids)
        directories = (
            self.activation_queue_path, self.activation_status_path,
            self.activation_cancel_path, self.activation_rollback_path,
            self.activation_cancel_result_path, self.activation_rollback_result_path,
        )
        for directory in directories:
            # Save the signature from BEFORE enumeration. A publication during
            # the scan must make the next poll revisit this directory.
            signature = self._activation_poll_fingerprint(directory)
            if (directory in self._activation_poll_directories
                    and signature == self._activation_poll_directories[directory]):
                continue
            previous = self._activation_poll_files.get(directory, {})
            current = {}
            if signature is not None:
                for path in directory.glob('*.json'):
                    fingerprint = self._activation_poll_fingerprint(path)
                    if fingerprint is not None:
                        current[path.stem] = fingerprint
            for activation_id in previous.keys() | current.keys():
                if previous.get(activation_id) != current.get(activation_id):
                    self._activation_poll_pending.add(activation_id)
                    if directory == self.activation_queue_path:
                        self._activation_poll_commands.pop(activation_id, None)
            self._activation_poll_files[directory] = current
            self._activation_poll_directories[directory] = signature
        commands = []
        queued = self._activation_poll_files.get(self.activation_queue_path, {})
        for activation_id in sorted(self._activation_poll_pending):
            if activation_id not in queued:
                # A web receipt may precede its command; atomic enqueue will
                # rediscover it. A removed command must never replay from cache.
                self._activation_poll_commands.pop(activation_id, None)
                self._activation_poll_pending.discard(activation_id)
                continue
            command = self._activation_poll_commands.get(activation_id)
            if command is None:
                command = self.read_activation_command(activation_id)
                if command is None:
                    continue
                if command.get('activation_id') != activation_id:
                    raise ValueError('activation command identity does not match its file')
                self._activation_poll_commands[activation_id] = command
            commands.append(deepcopy(command))
        return commands

    def acknowledge_activation_poll(self, activation_id: str) -> None:
        """Retire successful terminal work; later atomic writes rediscover it."""
        self._activation_poll_pending.discard(activation_id)

    def write_activation_status(self, status: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(status, dict):
            raise TypeError("activation status must be an object")
        if status.get("schema") != ACTIVATION_STATUS_SCHEMA:
            raise ValueError("activation status schema is invalid")
        if status.get("schema_version") != ACTIVATION_CHANNEL_VERSION:
            raise ValueError("activation status schema_version is invalid")
        activation_id = self._activation_id(status.get("activation_id"))
        from ipc.scene_contract import (
            normalize_scene_activation_status,
            validate_scene_activation_status_transition,
        )

        payload = normalize_scene_activation_status(status)
        existing = self.read_activation_status(activation_id)
        if existing is not None:
            payload = validate_scene_activation_status_transition(existing, payload)
        self._atomic_write(self.activation_status_file(activation_id), payload)
        return payload

    def read_activation_status(
        self, activation_id: str
    ) -> Optional[Dict[str, Any]]:
        return self._strict_json(
            self.activation_status_file(activation_id), "activation status"
        )

    def request_activation_cancel(self, activation_id: str) -> Dict[str, Any]:
        activation_id = self._activation_id(activation_id)
        payload = {
            "schema": ACTIVATION_CANCEL_SCHEMA,
            "schema_version": ACTIVATION_CHANNEL_VERSION,
            "request_id": str(uuid.uuid4()),
            "activation_id": activation_id,
            "requested_at": time.time(),
        }
        path = self.activation_cancel_file(activation_id)
        if self._atomic_create(path, payload):
            return payload
        existing = self._strict_json(path, "activation cancellation")
        if existing is None:
            raise RuntimeError("activation cancellation disappeared")
        return existing

    def read_activation_cancel(
        self, activation_id: str
    ) -> Optional[Dict[str, Any]]:
        return self._strict_json(
            self.activation_cancel_file(activation_id), "activation cancellation"
        )

    @staticmethod
    def _request_id(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("request_id must be a lowercase UUID")
        try:
            canonical = str(uuid.UUID(value))
        except (AttributeError, ValueError) as exc:
            raise ValueError("request_id must be a lowercase UUID") from exc
        if value != canonical:
            raise ValueError("request_id must be a lowercase UUID")
        return canonical

    def _write_activation_request_result(
        self,
        *,
        schema: str,
        path: Path,
        activation_id: str,
        request_id: str,
        outcome: str,
        status_phase: str,
        error: str | None,
    ) -> Dict[str, Any]:
        activation_id = self._activation_id(activation_id)
        request_id = self._request_id(request_id)
        if outcome not in ACTIVATION_REQUEST_OUTCOMES:
            raise ValueError("activation request outcome is invalid")
        if not isinstance(status_phase, str) or not status_phase:
            raise ValueError("activation request status phase is required")
        if error is not None and (not isinstance(error, str) or not error):
            raise ValueError("activation request error must be null or non-empty")
        if outcome != "succeeded" and error is None:
            raise ValueError("rejected or failed activation requests require an error")
        payload = {
            "schema": schema,
            "schema_version": ACTIVATION_CHANNEL_VERSION,
            "request_id": request_id,
            "activation_id": activation_id,
            "outcome": outcome,
            "status_phase": status_phase,
            "error": error,
            "completed_at": time.time(),
        }
        if self._atomic_create(path, payload):
            return payload
        existing = self._strict_json(path, "activation request result")
        if existing is None:
            raise RuntimeError("activation request result disappeared")
        comparable = dict(existing)
        comparable.pop("completed_at", None)
        requested = dict(payload)
        requested.pop("completed_at", None)
        if comparable != requested:
            raise FileExistsError(
                "activation request already has a different terminal result"
            )
        return existing

    def write_activation_cancel_result(
        self,
        activation_id: str,
        *,
        request_id: str,
        outcome: str,
        status_phase: str,
        error: str | None = None,
    ) -> Dict[str, Any]:
        return self._write_activation_request_result(
            schema=ACTIVATION_CANCEL_RESULT_SCHEMA,
            path=self.activation_cancel_result_file(activation_id),
            activation_id=activation_id,
            request_id=request_id,
            outcome=outcome,
            status_phase=status_phase,
            error=error,
        )

    def read_activation_cancel_result(
        self, activation_id: str
    ) -> Optional[Dict[str, Any]]:
        return self._strict_json(
            self.activation_cancel_result_file(activation_id),
            "activation cancellation result",
        )

    def request_activation_rollback(
        self,
        activation_id: str,
        *,
        snapshot_id: str,
        expected_controller_session_id: str,
        expected_controller_state_revision: int,
    ) -> Dict[str, Any]:
        activation_id = self._activation_id(activation_id)
        if not all(isinstance(value, str) and value for value in (
            snapshot_id, expected_controller_session_id
        )):
            raise ValueError("rollback snapshot and controller session are required")
        if (
            isinstance(expected_controller_state_revision, bool)
            or not isinstance(expected_controller_state_revision, int)
            or expected_controller_state_revision < 0
        ):
            raise ValueError("rollback controller revision must be non-negative")
        payload = {
            "schema": ACTIVATION_ROLLBACK_SCHEMA,
            "schema_version": ACTIVATION_CHANNEL_VERSION,
            "request_id": str(uuid.uuid4()),
            "activation_id": activation_id,
            "snapshot_id": snapshot_id,
            "expected_controller_session_id": expected_controller_session_id,
            "expected_controller_state_revision": expected_controller_state_revision,
            "requested_at": time.time(),
        }
        path = self.activation_rollback_file(activation_id)
        if self._atomic_create(path, payload):
            return payload
        existing = self._strict_json(path, "activation rollback request")
        if existing is None:
            raise RuntimeError("activation rollback request disappeared")
        comparable = dict(existing)
        comparable.pop("requested_at", None)
        comparable.pop("request_id", None)
        requested = dict(payload)
        requested.pop("requested_at", None)
        requested.pop("request_id", None)
        if comparable != requested:
            raise FileExistsError(
                "activation already has a different rollback request"
            )
        return existing

    def read_activation_rollback(
        self, activation_id: str
    ) -> Optional[Dict[str, Any]]:
        return self._strict_json(
            self.activation_rollback_file(activation_id),
            "activation rollback request",
        )

    def write_activation_rollback_result(
        self,
        activation_id: str,
        *,
        request_id: str,
        outcome: str,
        status_phase: str,
        error: str | None = None,
    ) -> Dict[str, Any]:
        return self._write_activation_request_result(
            schema=ACTIVATION_ROLLBACK_RESULT_SCHEMA,
            path=self.activation_rollback_result_file(activation_id),
            activation_id=activation_id,
            request_id=request_id,
            outcome=outcome,
            status_phase=status_phase,
            error=error,
        )

    def read_activation_rollback_result(
        self, activation_id: str
    ) -> Optional[Dict[str, Any]]:
        return self._strict_json(
            self.activation_rollback_result_file(activation_id),
            "activation rollback result",
        )

    # Maintenance is intentionally separate from the legacy control file.  The
    # latter is a last-write-wins convenience channel and is not a suitable
    # authority boundary for a full-wall diagnostic transaction.
    def maintenance_request_path(self, request_id: str) -> Path:
        return self.maintenance_queue_path / f"{self._request_id(request_id)}.json"

    def maintenance_status_file(self, request_id: str) -> Path:
        return self.maintenance_status_path / f"{self._request_id(request_id)}.json"

    def maintenance_result_file(self, request_id: str) -> Path:
        return self.maintenance_result_path / f"{self._request_id(request_id)}.json"

    @staticmethod
    def _maintenance_digest(value: Any) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ValueError("maintenance authority_digest must be a SHA-256")
        return value

    def enqueue_maintenance_request(
        self, command: Dict[str, Any], *, authority_digest: str
    ) -> Dict[str, Any]:
        """Create one immutable named diagnostic request and queued receipt."""
        if not isinstance(command, dict) or command.get("schema") != MAINTENANCE_COMMAND_SCHEMA:
            raise ValueError("maintenance command schema is invalid")
        if command.get("schema_version") != MAINTENANCE_CHANNEL_VERSION:
            raise ValueError("maintenance command schema_version is invalid")
        request_id = self._request_id(command.get("request_id"))
        payload = {
            "command": dict(command),
            "authority_digest": self._maintenance_digest(authority_digest),
        }
        path = self.maintenance_request_path(request_id)
        if self._atomic_create(path, payload):
            self.write_maintenance_status(request_id, phase="queued", authority_digest=authority_digest)
            return payload
        existing = self._strict_json(path, "maintenance command")
        if existing != payload:
            raise FileExistsError("maintenance request ID already names a different durable command")
        return existing

    def read_maintenance_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self._strict_json(self.maintenance_request_path(request_id), "maintenance command")

    def list_maintenance_requests(self) -> list[Dict[str, Any]]:
        if not self.maintenance_queue_path.is_dir():
            return []
        requests = []
        for path in sorted(self.maintenance_queue_path.glob("*.json")):
            request = self._strict_json(path, "maintenance command")
            if request is not None:
                requests.append(request)
        return requests

    def read_maintenance_status(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self._strict_json(self.maintenance_status_file(request_id), "maintenance status")

    def write_maintenance_status(
        self,
        request_id: str,
        *,
        phase: str,
        authority_digest: str,
        result: Dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Dict[str, Any]:
        request_id = self._request_id(request_id)
        if phase not in MAINTENANCE_PHASES:
            raise ValueError("maintenance phase is invalid")
        if error is not None and (not isinstance(error, str) or not error):
            raise ValueError("maintenance error must be null or non-empty")
        existing = self.read_maintenance_status(request_id)
        order = {
            "queued": 0,
            "running": 1,
            "restored": 2,
            "safe_idle": 2,
            "rejected": 2,
            "failed": 2,
        }
        if existing is not None:
            if existing.get("authority_digest") != authority_digest:
                raise ValueError("maintenance status authority digest changed")
            old = existing.get("phase")
            if old in MAINTENANCE_TERMINAL_PHASES:
                if old != phase:
                    raise FileExistsError("maintenance request already has a terminal status")
                return existing
            if order.get(phase, -1) <= order.get(old, -1):
                raise ValueError("maintenance status must advance monotonically")
        payload = {
            "schema": MAINTENANCE_STATUS_SCHEMA,
            "schema_version": MAINTENANCE_CHANNEL_VERSION,
            "request_id": request_id,
            "phase": phase,
            "authority_digest": self._maintenance_digest(authority_digest),
            "result": result,
            "error": error,
            "updated_at": time.time(),
        }
        self._atomic_write(self.maintenance_status_file(request_id), payload)
        if phase in MAINTENANCE_TERMINAL_PHASES:
            self._write_maintenance_result(payload)
        return payload

    def _write_maintenance_result(self, status: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(status)
        payload["schema"] = MAINTENANCE_RESULT_SCHEMA
        path = self.maintenance_result_file(payload["request_id"])
        if self._atomic_create(path, payload):
            return payload
        existing = self._strict_json(path, "maintenance result")
        comparable = dict(existing or {})
        requested = dict(payload)
        comparable.pop("updated_at", None)
        requested.pop("updated_at", None)
        if comparable != requested:
            raise FileExistsError("maintenance request already has a different terminal result")
        return existing

    def read_maintenance_result(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self._strict_json(self.maintenance_result_file(request_id), "maintenance result")
