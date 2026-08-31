"""Atomic persistence for the one recoverable Composer working draft."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


class WorkingDraftError(ValueError):
    """A persisted working draft cannot safely be offered for recovery."""


class WorkingDraftStore:
    """Keep one current-schema record and replace it atomically after validation."""

    _SCHEMA = "ledgrid.composer.draft.v1"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def get(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkingDraftError("Working draft is unreadable; discard it.") from exc
        if not isinstance(value, dict) or set(value) != {"schema", "draft", "basis", "reference", "saved_at"}:
            raise WorkingDraftError("Working draft is malformed; discard it.")
        if value["schema"] != self._SCHEMA:
            raise WorkingDraftError("Working draft is not current; discard it.")
        saved_at = value["saved_at"]
        if not isinstance(saved_at, (int, float)) or isinstance(saved_at, bool) or not math.isfinite(saved_at):
            raise WorkingDraftError("Working draft timestamp is invalid; discard it.")
        return value

    def save(self, draft: Any, basis: Any, reference: Any, saved_at: float) -> dict[str, Any]:
        value = {"schema": self._SCHEMA, "draft": draft, "basis": basis, "reference": reference, "saved_at": saved_at}
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
