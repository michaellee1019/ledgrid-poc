#!/usr/bin/env python3
"""
File-backed control and status channel for decoupling controller and web UI.
"""

import json
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
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        tmp_path.replace(path)

    def read_control(self) -> Optional[Dict[str, Any]]:
        if not self.control_path.exists():
            return None
        try:
            with self.control_path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:  # pragma: no cover - best effort read
            print(f"⚠️ Failed to read control file {self.control_path}: {exc}")
            return None

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
        if not self.status_path.exists():
            return None
        try:
            with self.status_path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:  # pragma: no cover - best effort read
            print(f"⚠️ Failed to read status file {self.status_path}: {exc}")
            return None

    def write_status(self, payload: Dict[str, Any]):
        payload = dict(payload)
        payload.setdefault("written_at", time.time())
        self._atomic_write(self.status_path, payload)
