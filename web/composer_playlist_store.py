"""Atomic persistence for small ordered Composer playlist definitions."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
from uuid import UUID, uuid4


class ComposerPlaylistStoreError(ValueError):
    pass


_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: dict[str, threading.RLock] = {}


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


class ComposerPlaylistStore:
    _SCHEMA = "ledgrid.composer.playlists.v1"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._thread_lock = _thread_lock(self.lock_path)

    @contextmanager
    def _transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            with self.lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def list(self) -> list[dict[str, Any]]:
        return [self._summary(item) for item in self._records()]

    def get(self, playlist_id: str) -> dict[str, Any]:
        return deepcopy(self._find(playlist_id, self._records()))

    def save(self, value: Any, *, playlist_id: str | None = None) -> dict[str, Any]:
        record = self._definition(value, playlist_id=playlist_id)
        with self._transaction():
            records = self._records()
            if playlist_id is None:
                if any(item["name"].casefold() == record["name"].casefold() for item in records):
                    raise ComposerPlaylistStoreError("A playlist already has that name.")
                records.insert(0, record)
            else:
                prior = self._find(playlist_id, records)
                if any(item["id"] != playlist_id and item["name"].casefold() == record["name"].casefold() for item in records):
                    raise ComposerPlaylistStoreError("A playlist already has that name.")
                record["id"] = prior["id"]
                records = [record if item["id"] == playlist_id else item for item in records]
            self._write(records)
        return deepcopy(record)

    def delete(self, playlist_id: str) -> None:
        with self._transaction():
            records = self._records()
            self._find(playlist_id, records)
            self._write([item for item in records if item["id"] != playlist_id])

    def _records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ComposerPlaylistStoreError("Saved playlists are unreadable; recreate them.") from exc
        if not isinstance(payload, dict) or set(payload) != {"schema", "playlists"} or payload["schema"] != self._SCHEMA or not isinstance(payload["playlists"], list):
            raise ComposerPlaylistStoreError("Saved playlists use an unsupported format; recreate them.")
        records = [self._definition(item, playlist_id=item.get("id") if isinstance(item, dict) else None) for item in payload["playlists"]]
        if len({item["id"] for item in records}) != len(records):
            raise ComposerPlaylistStoreError("Saved playlists contain duplicate identities.")
        return records

    def _definition(self, value: Any, *, playlist_id: str | None) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ComposerPlaylistStoreError("A playlist must be an object.")
        allowed = {"id", "name", "entries"}
        if set(value) - allowed:
            raise ComposerPlaylistStoreError("A playlist contains unknown fields.")
        name = value.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ComposerPlaylistStoreError("A playlist name must be from 1 to 80 characters.")
        entries = value.get("entries")
        if not isinstance(entries, list) or not entries or len(entries) > 100:
            raise ComposerPlaylistStoreError("A playlist needs from 1 to 100 entries.")
        clean = []
        ids = set()
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping) or set(entry) != {"entry_id", "label", "duration_seconds", "scene"}:
                raise ComposerPlaylistStoreError(f"Playlist entry {index + 1} is malformed.")
            entry_id = entry["entry_id"]
            try:
                UUID(entry_id)
            except (TypeError, ValueError, AttributeError) as exc:
                raise ComposerPlaylistStoreError(f"Playlist entry {index + 1} identity is malformed.") from exc
            if entry_id in ids:
                raise ComposerPlaylistStoreError("Playlist entries contain duplicate identities.")
            ids.add(entry_id)
            label = entry["label"]
            duration = entry["duration_seconds"]
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
                raise ComposerPlaylistStoreError(f"Playlist entry {index + 1} label is invalid.")
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0.05 <= float(duration) <= 86400:
                raise ComposerPlaylistStoreError(f"Playlist entry {index + 1} duration must be from 0.05 to 86400 seconds.")
            if not isinstance(entry["scene"], Mapping):
                raise ComposerPlaylistStoreError(f"Playlist entry {index + 1} scene is malformed.")
            clean.append({"entry_id": entry_id, "label": label.strip(),
                          "duration_seconds": float(duration), "scene": deepcopy(dict(entry["scene"]))})
        identity = playlist_id or str(uuid4())
        try:
            UUID(identity)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ComposerPlaylistStoreError("Playlist identity is malformed.") from exc
        return {"id": identity, "name": name.strip(), "entries": clean}

    @staticmethod
    def _summary(record: Mapping[str, Any]) -> dict[str, Any]:
        return {"id": record["id"], "name": record["name"],
                "entry_count": len(record["entries"]),
                "total_duration_seconds": sum(item["duration_seconds"] for item in record["entries"])}

    @staticmethod
    def _find(playlist_id: Any, records: list[dict[str, Any]]) -> dict[str, Any]:
        for record in records:
            if record["id"] == playlist_id:
                return record
        raise ComposerPlaylistStoreError("That saved playlist no longer exists.")

    def _write(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            json.dump({"schema": self._SCHEMA, "playlists": records}, handle,
                      sort_keys=True, separators=(",", ":"), allow_nan=False)
            handle.flush(); os.fsync(handle.fileno()); temporary = Path(handle.name)
        temporary.replace(self.path)
