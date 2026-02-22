#!/usr/bin/env python3
"""
File-backed control and status channel for decoupling controller and web UI.
"""

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


class FileControlChannel:
    """
    Simple JSON file channel used to pass commands to the controller process and
    read back status/frame data. Writes are atomic (temp file + rename) so the
    other process never sees partial data.
    """

    def __init__(self, control_path: str = "run_state/control.json",
                 status_path: str = "run_state/status.json"):
        self.control_path = Path(control_path)
        self.status_path = Path(status_path)
        self.control_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.parent.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, path: Path, payload: Dict[str, Any]):
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
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
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
        payload.setdefault("written_at", time.time())
        self._atomic_write(self.status_path, payload)
