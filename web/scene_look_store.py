"""Atomic, current-only persistence for named Scene v2 Composer looks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable, Mapping
from uuid import UUID, uuid4

from ipc.scene_contract import CanonicalScene


class SceneLookStoreError(ValueError):
    """A local look is missing, malformed, or cannot use the current schema."""


class SceneLookStore:
    """Persist whole canonical Scene v2 looks, newest first.

    This store deliberately understands no historical scene format. Legacy data
    can enter only through ``import_legacy_once``, whose caller validates and
    translates every selected candidate before a single atomic write.
    """

    _SCHEMA = "ledgrid.composer.looks.v2"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def list(self) -> list[dict[str, Any]]:
        return [self._summary(record) for record in self._records()[0]]

    def get(self, look_id: str) -> dict[str, Any]:
        return dict(self._find(look_id, self._records()[0]))

    def save_as(self, name: Any, canonical: CanonicalScene) -> dict[str, Any]:
        clean_name = self._name(name)
        records, imported = self._records()
        self._require_unique_name(clean_name, records)
        record = self._new_record(clean_name, canonical)
        self._write([record, *records], imported=imported)
        return dict(record)

    # Existing callers that mean Save As can retain this spelling. It does not
    # provide a compatibility reader for historical on-disk data.
    save = save_as

    def update(self, look_id: str, canonical: CanonicalScene) -> dict[str, Any]:
        records, imported = self._records()
        source = self._find(look_id, records)
        updated = {"id": source["id"], "name": source["name"],
                   "basis": canonical.identity.to_dict(), "scene": canonical.scene}
        self._write([updated if record["id"] == look_id else record for record in records], imported=imported)
        return dict(updated)

    def duplicate(self, look_id: str, name: Any) -> dict[str, Any]:
        records, imported = self._records()
        source = self._find(look_id, records)
        clean_name = self._name(name)
        self._require_unique_name(clean_name, records)
        record = {**source, "id": str(uuid4()), "name": clean_name}
        self._write([record, *records], imported=imported)
        return dict(record)

    def rename(self, look_id: str, name: Any) -> dict[str, Any]:
        records, imported = self._records()
        source = self._find(look_id, records)
        clean_name = self._name(name)
        self._require_unique_name(clean_name, records, excluding=look_id)
        renamed = {**source, "name": clean_name}
        self._write([renamed if record["id"] == look_id else record for record in records], imported=imported)
        return dict(renamed)

    def delete(self, look_id: str) -> None:
        records, imported = self._records()
        self._find(look_id, records)
        self._write([record for record in records if record["id"] != look_id], imported=imported)

    def import_legacy_once(
        self, values: Any, translate: Callable[[Mapping[str, Any]], tuple[str, CanonicalScene] | None],
    ) -> list[dict[str, Any]]:
        """Explicitly import a selected legacy collection once.

        The application owns old-format knowledge. It must return a validated,
        useful current scene or ``None`` to skip an obsolete candidate. All
        translations and duplicate checks finish before the store is changed.
        """
        records, imported = self._records()
        if imported:
            raise SceneLookStoreError("Legacy looks have already been reviewed.")
        if not isinstance(values, list):
            raise SceneLookStoreError("Legacy import must contain a list of looks.")
        imported_records: list[dict[str, Any]] = []
        names = {record["name"].casefold() for record in records}
        for value in values:
            if not isinstance(value, Mapping):
                raise SceneLookStoreError("A legacy look is malformed.")
            result = translate(value)
            if result is None:
                continue
            name, canonical = result
            clean_name = self._name(name)
            if clean_name.casefold() in names:
                raise SceneLookStoreError("A legacy look duplicates an existing name.")
            names.add(clean_name.casefold())
            imported_records.append(self._new_record(clean_name, canonical))
        self._write([*imported_records, *records], imported=True)
        return [dict(record) for record in imported_records]

    def _records(self) -> tuple[list[dict[str, Any]], bool]:
        if not self.path.exists():
            return [], False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SceneLookStoreError("Saved looks are unreadable; recreate them.") from exc
        if (not isinstance(payload, dict) or set(payload) != {"schema", "looks", "legacy_imported"}
                or payload["schema"] != self._SCHEMA or not isinstance(payload["legacy_imported"], bool)):
            raise SceneLookStoreError("Saved looks use an unsupported format; recreate them.")
        if not isinstance(payload["looks"], list):
            raise SceneLookStoreError("Saved looks are malformed; recreate them.")
        records = [self._record(record) for record in payload["looks"]]
        if len({record["id"] for record in records}) != len(records) or len({record["name"].casefold() for record in records}) != len(records):
            raise SceneLookStoreError("Saved looks contain duplicate identities or names; recreate them.")
        return records, payload["legacy_imported"]

    def _record(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {"id", "name", "basis", "scene"}:
            raise SceneLookStoreError("A saved look is malformed; recreate it.")
        try:
            UUID(value["id"])
        except (AttributeError, ValueError, TypeError) as exc:
            raise SceneLookStoreError("A saved look identity is malformed; recreate it.") from exc
        return {"id": value["id"], "name": self._name(value["name"]), "basis": value["basis"], "scene": value["scene"]}

    @staticmethod
    def _new_record(name: str, canonical: CanonicalScene) -> dict[str, Any]:
        if not isinstance(canonical, CanonicalScene):
            raise SceneLookStoreError("A saved look needs a current canonical Scene v2.")
        return {"id": str(uuid4()), "name": name, "basis": canonical.identity.to_dict(), "scene": canonical.scene}

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

    def _write(self, records: list[dict[str, Any]], *, imported: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            json.dump({"schema": self._SCHEMA, "looks": records, "legacy_imported": imported}, handle,
                      sort_keys=True, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(self.path)
