"""Atomic hidden crash recovery for the current Composer Scene v2."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


class WorkingDraftError(ValueError):
    """A persisted recovery record cannot safely be restored."""


class WorkingDraftStore:
    """Keep the newest accepted scene, never an unsound editor draft."""

    _SCHEMA = "ledgrid.composer.recovery.v2"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def get(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkingDraftError("Crash recovery is unreadable; discard it.") from exc
        if not isinstance(value, dict) or set(value) != {"schema", "scene", "basis", "opened_look_id", "saved_at"}:
            raise WorkingDraftError("Crash recovery is malformed; discard it.")
        if value["schema"] != self._SCHEMA:
            raise WorkingDraftError("Crash recovery is not current Scene v2; discard it.")
        if value["opened_look_id"] is not None and (not isinstance(value["opened_look_id"], str) or not value["opened_look_id"]):
            raise WorkingDraftError("Crash recovery look reference is invalid; discard it.")
        saved_at = value["saved_at"]
        if not isinstance(saved_at, (int, float)) or isinstance(saved_at, bool) or not math.isfinite(saved_at):
            raise WorkingDraftError("Crash recovery timestamp is invalid; discard it.")
        return value

    def save(self, scene: Any, basis: Any, opened_look_id: str | None, saved_at: float) -> dict[str, Any]:
        value = {"schema": self._SCHEMA, "scene": scene, "basis": basis,
                 "opened_look_id": opened_look_id, "saved_at": saved_at}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(self.path)
        return value

    def discard(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
