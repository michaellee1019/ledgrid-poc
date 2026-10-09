"""Atomic file queues for controller commands and playlists."""
from copy import deepcopy
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional
import uuid

CONTROL_COMMAND_SCHEMA='ledgrid.control-command'
CONTROL_STATUS_SCHEMA='ledgrid.controller-status'
CONTROL_CHANNEL_VERSION=1
PLAYLIST_COMMAND_SCHEMA='ledgrid.playlist-command'
PLAYLIST_STATUS_SCHEMA='ledgrid.playlist-status'
PLAYLIST_CHANNEL_VERSION=1

class FileControlChannel:
    def __init__(self, control_path='run_state/control.json',status_path='run_state/status.json',**_unused):
        self.control_path=Path(control_path)
        self.status_path=Path(status_path)
        self.command_queue_path=self.control_path.parent/'commands'
        self.command_result_path=self.control_path.parent/'command-results'
        self.playlist_root=self.control_path.parent/'playlists'
        self.playlist_queue_path=self.playlist_root/'queue'
        self.playlist_status_path=self.playlist_root/'status'
        self.playlist_current_path=self.playlist_root/'current.json'
        self._playlist_poll_session=None
        self._playlist_poll_directory=None
        self._playlist_poll_files={}
        self._playlist_poll_pending=set()
        self.control_path.parent.mkdir(parents=True,exist_ok=True)
        self.status_path.parent.mkdir(parents=True,exist_ok=True)

    def read_control(self):
        return self._read_json_file(self.control_path,'control')

    def write_control(self,payload):
        self._atomic_write(self.control_path,payload)

    def send_command(self, action, **data):
        command_id = time.time_ns()
        payload={'schema':CONTROL_COMMAND_SCHEMA,'schema_version':CONTROL_CHANNEL_VERSION,
            'command_id':command_id,'request_id':str(uuid.uuid4()),'action':action,
            'data':data,'written_at':time.time()}
        self._atomic_create(self.command_queue_path/f'{command_id:020d}-{payload["request_id"]}.json',payload)
        self.write_control(payload)
        return payload

    def poll_commands(self):
        return [payload for path in sorted(self.command_queue_path.glob('*.json'))
                if (payload:=self._read_json_file(path,'command')) is not None]

    def acknowledge_command(self, command, result):
        request_id=str(uuid.UUID(command['request_id']))
        self._atomic_write(self.command_result_path/f'{request_id}.json',result)
        path=self.command_queue_path/f'{command["command_id"]:020d}-{request_id}.json'
        path.unlink(missing_ok=True)

    def read_command_result(self, request_id):
        return self._read_json_file(self.command_result_path/f'{uuid.UUID(request_id)}.json','command result')

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


    @staticmethod
    def _path_fingerprint(path: Path):
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_ino, stat.st_size, stat.st_mtime_ns

    @staticmethod
    def _strict_json(path: Path, label: str):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        if not isinstance(value, dict):
            raise ValueError(f'{label} must be a JSON object')
        return value

    def poll_playlist_commands(self, session_id: str) -> list[Dict[str, Any]]:
        """Discover new immutable requests without rescanning history while idle."""
        if session_id != self._playlist_poll_session:
            self._playlist_poll_session = session_id
            self._playlist_poll_directory = None
            self._playlist_poll_files = {}
            self._playlist_poll_pending = set()
        signature = self._path_fingerprint(self.playlist_queue_path)
        if signature != self._playlist_poll_directory:
            current = {}
            if signature is not None:
                for path in self.playlist_queue_path.glob("*.json"):
                    fingerprint = self._path_fingerprint(path)
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
