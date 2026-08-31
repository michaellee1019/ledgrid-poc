"""Tiny local persistence for named, current Scene-v1 Composer looks."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
from uuid import UUID, uuid4

from ipc.scene_contract import CanonicalScene


class SceneLookStoreError(ValueError):
    """A local look is missing, malformed, or cannot use the current schema."""


class SceneLookStore:
    """Persist only named canonical Scene-v1 records, newest first."""

    _SCHEMA = "ledgrid.composer.looks.v1"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def list(self) -> list[dict[str, Any]]:
        return [self._summary(record) for record in self._records()]

    def get(self, look_id: str) -> dict[str, Any]:
        return dict(self._find(look_id, self._records()))

    def save(self, name: Any, canonical: CanonicalScene) -> dict[str, Any]:
        clean_name = self._name(name)
        records = self._records()
        self._require_unique_name(clean_name, records)
        record = {"id": str(uuid4()), "name": clean_name, "basis": canonical.identity.to_dict(), "scene": canonical.scene}
        self._write([record, *records])
        return dict(record)

    def duplicate(self, look_id: str, name: Any) -> dict[str, Any]:
        records = self._records()
        source = self._find(look_id, records)
        clean_name = self._name(name)
        self._require_unique_name(clean_name, records)
        record = {**source, "id": str(uuid4()), "name": clean_name}
        self._write([record, *records])
        return dict(record)

    def rename(self, look_id: str, name: Any) -> dict[str, Any]:
        records = self._records()
        source = self._find(look_id, records)
        clean_name = self._name(name)
        self._require_unique_name(clean_name, records, excluding=look_id)
        renamed = {**source, "name": clean_name}
        self._write([renamed if record["id"] == look_id else record for record in records])
        return dict(renamed)

    def delete(self, look_id: str) -> None:
        records = self._records()
        self._find(look_id, records)
        self._write([record for record in records if record["id"] != look_id])

    def _records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SceneLookStoreError("Saved looks are unreadable; recreate them.") from exc
        if not isinstance(payload, dict) or set(payload) != {"schema", "looks"} or payload["schema"] != self._SCHEMA:
            raise SceneLookStoreError("Saved looks use an unsupported format; recreate them.")
        if not isinstance(payload["looks"], list):
            raise SceneLookStoreError("Saved looks are malformed; recreate them.")
        records = [self._record(record) for record in payload["looks"]]
        if len({record["id"] for record in records}) != len(records) or len({record["name"].casefold() for record in records}) != len(records):
            raise SceneLookStoreError("Saved looks contain duplicate identities or names; recreate them.")
        return records

    def _record(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {"id", "name", "basis", "scene"}:
            raise SceneLookStoreError("A saved look is malformed; recreate it.")
        try:
            UUID(value["id"])
        except (AttributeError, ValueError, TypeError) as exc:
            raise SceneLookStoreError("A saved look identity is malformed; recreate it.") from exc
        return {"id": value["id"], "name": self._name(value["name"]), "basis": value["basis"], "scene": value["scene"]}

    @staticmethod
    def _name(value: Any) -> str:
        if not isinstance(value, str):
            raise SceneLookStoreError("A look needs a name.")
        name = value.strip()
        if not name or len(name) > 80:
            raise SceneLookStoreError("A look name must be from 1 to 80 characters.")
        return name

    @staticmethod
    def _summary(record: Mapping[str, Any]) -> dict[str, Any]:
        return {"id": record["id"], "name": record["name"], "basis": record["basis"]}

    @staticmethod
    def _find(look_id: Any, records: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(look_id, str):
            raise SceneLookStoreError("Choose a saved look.")
        for record in records:
            if record["id"] == look_id:
                return record
        raise SceneLookStoreError("That saved look no longer exists.")

    @staticmethod
    def _require_unique_name(name: str, records: list[dict[str, Any]], excluding: str | None = None) -> None:
        if any(record["id"] != excluding and record["name"].casefold() == name.casefold() for record in records):
            raise SceneLookStoreError("A saved look already has that name.")

    def _write(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            json.dump({"schema": self._SCHEMA, "looks": records}, handle, sort_keys=True, separators=(",", ":"))
            temporary = Path(handle.name)
        temporary.replace(self.path)
