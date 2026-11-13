"""Atomic storage for the wall Composer's numbered Scene presets."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from ipc.scene_contract import CanonicalScene, normalize_composer_scene


PRESET_COUNT = 20


class NumberedPresetStore:
    def __init__(self, directory, catalog):
        self.directory = Path(directory)
        self.catalog = catalog

    def _path(self, slot):
        if type(slot) is not int or not 1 <= slot <= PRESET_COUNT:
            raise ValueError(f"Choose preset 1–{PRESET_COUNT}")
        return self.directory / f"{slot:02d}.json"

    def occupied(self):
        return tuple(self._path(slot).is_file() for slot in range(1, PRESET_COUNT + 1))

    def load(self, slot):
        try:
            scene = json.loads(self._path(slot).read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ValueError(f"Preset {slot:02d} is empty") from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Preset {slot:02d} could not be read") from error
        return normalize_composer_scene({"origin": "composer", "scene": scene}, self.catalog)

    def save(self, slot, canonical):
        path = self._path(slot)
        if not isinstance(canonical, CanonicalScene):
            raise ValueError("A preset needs a current Scene")
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile("wb", dir=self.directory, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(canonical.canonical_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
