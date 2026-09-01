"""Atomic local Composer library preferences for current starter/look references."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable


class ComposerLibraryStateError(ValueError):
    """Library state is not safe to use without replacing it explicitly."""


class ComposerLibraryState:
    """Persist a bounded set of favorites and an ordered, bounded recent list."""

    _SCHEMA = "ledgrid.composer.library.v1"
    _FAVORITES_LIMIT = 32
    _RECENTS_LIMIT = 8

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def get(self) -> dict[str, list[dict[str, str]]]:
        if not self.path.exists():
            return {"favorites": [], "recents": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ComposerLibraryStateError("Library state is unreadable; reset it before using the library.") from exc
        if not isinstance(value, dict) or set(value) != {"schema", "favorites", "recents"}:
            raise ComposerLibraryStateError("Library state is malformed; reset it before using the library.")
        if value["schema"] != self._SCHEMA:
            raise ComposerLibraryStateError("Library state is not current; reset it before using the library.")
        favorites = self._references(value["favorites"], "favorites", self._FAVORITES_LIMIT)
        recents = self._references(value["recents"], "recents", self._RECENTS_LIMIT)
        return {"favorites": favorites, "recents": recents}

    def favorite(self, value: Any) -> dict[str, list[dict[str, str]]]:
        reference = self._reference(value)
        state = self.get()
        if reference not in state["favorites"]:
            if len(state["favorites"]) >= self._FAVORITES_LIMIT:
                raise ComposerLibraryStateError("Favorites are full; remove one before adding another.")
            state["favorites"].append(reference)
            self._write(state)
        return state

    def unfavorite(self, value: Any) -> dict[str, list[dict[str, str]]]:
        reference = self._reference(value)
        state = self.get()
        favorites = [item for item in state["favorites"] if item != reference]
        if favorites != state["favorites"]:
            state["favorites"] = favorites
            self._write(state)
        return state

    def revisit(self, value: Any) -> dict[str, list[dict[str, str]]]:
        reference = self._reference(value)
        state = self.get()
        state["recents"] = [reference, *(item for item in state["recents"] if item != reference)][:self._RECENTS_LIMIT]
        self._write(state)
        return state

    def prune_look(self, look_id: str) -> dict[str, list[dict[str, str]]]:
        """Remove a deleted saved look without touching any remaining entry."""
        state = self.get()
        next_state = {
            key: [item for item in state[key] if not (item["kind"] == "look" and item["id"] == look_id)]
            for key in ("favorites", "recents")
        }
        if next_state != state:
            self._write(next_state)
        return next_state

    @classmethod
    def project(cls, state: dict[str, list[dict[str, str]]], current: Iterable[dict[str, str]]) -> dict[str, Any]:
        """Resolve current names without resurrecting deleted saved-look references."""
        items = [dict(item) for item in current]
        indexed = {(item["kind"], item["id"]): item for item in items}
        favorites = [dict(indexed[(item["kind"], item["id"])]) for item in state["favorites"] if (item["kind"], item["id"]) in indexed]
        recents = [dict(indexed[(item["kind"], item["id"])]) for item in state["recents"] if (item["kind"], item["id"]) in indexed]
        favorite_ids = {(item["kind"], item["id"]) for item in favorites}
        recent_ids = {(item["kind"], item["id"]) for item in recents}
        return {
            "items": [{**item, "favorite": (item["kind"], item["id"]) in favorite_ids, "recent": (item["kind"], item["id"]) in recent_ids} for item in items],
            "favorites": favorites,
            "recents": recents,
        }

    @classmethod
    def _references(cls, value: Any, label: str, limit: int) -> list[dict[str, str]]:
        if not isinstance(value, list) or len(value) > limit:
            raise ComposerLibraryStateError(f"Library {label} are malformed; reset the library state.")
        references = [cls._reference(item) for item in value]
        if len({(item["kind"], item["id"]) for item in references}) != len(references):
            raise ComposerLibraryStateError(f"Library {label} contain duplicate references; reset the library state.")
        return references

    @staticmethod
    def _reference(value: Any) -> dict[str, str]:
        if not isinstance(value, dict) or set(value) != {"kind", "id"} or value.get("kind") not in {"starter", "look"} or not isinstance(value.get("id"), str) or not value["id"]:
            raise ComposerLibraryStateError("Library reference is malformed; reset the library state.")
        return {"kind": value["kind"], "id": value["id"]}

    def _write(self, state: dict[str, list[dict[str, str]]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            json.dump({"schema": self._SCHEMA, **state}, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(self.path)
