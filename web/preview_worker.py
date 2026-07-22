"""Low-priority renderer for presets created on the deployed controller host."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from typing import Any, Dict, Optional, Tuple

from animation.core.preview_assets import load_catalog, referenced_asset_names, write_catalog


Job = Tuple[str, str, Path]


class RuntimePreviewWorker:
    def __init__(
        self,
        project_root: Path,
        *,
        strips: int,
        leds_per_strip: int,
    ):
        self.project_root = project_root
        self.output_dir = project_root / "run_state" / "animation_previews"
        self.catalog_path = self.output_dir / "catalog.json"
        self.status_path = project_root / "run_state" / "status.json"
        self.strips = strips
        self.leds_per_strip = leds_per_strip
        self._jobs: "queue.Queue[Job]" = queue.Queue()
        self._queued: set[tuple[str, str]] = set()
        self._rerun: set[tuple[str, str]] = set()
        self._cancelled: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def queue(self, animation_name: str, preset_id: str, preset_path: Path, fallback: Dict[str, Any]) -> None:
        key = (animation_name, preset_id)
        with self._lock:
            self._cancelled.discard(key)
            if key in self._queued:
                self._rerun.add(key)
                return
            self._queued.add(key)
            catalog = load_catalog(self.catalog_path)
            pending = {
                "status": "pending",
                "poster_url": fallback.get("poster_url"),
                "loop_url": fallback.get("loop_url"),
            }
            catalog.setdefault("presets", {}).setdefault(animation_name, {})[preset_id] = pending
            write_catalog(self.catalog_path, catalog)
            self._jobs.put((animation_name, preset_id, preset_path))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="runtime-preview-worker", daemon=True
                )
                self._thread.start()

    def delete(self, animation_name: str, preset_id: str) -> None:
        key = (animation_name, preset_id)
        with self._lock:
            self._cancelled.add(key)
            self._rerun.discard(key)
            self._remove_entry_locked(animation_name, preset_id)

    def _remove_entry_locked(self, animation_name: str, preset_id: str) -> None:
        catalog = load_catalog(self.catalog_path)
        presets = catalog.setdefault("presets", {}).setdefault(animation_name, {})
        removed = presets.pop(preset_id, None)
        if not presets:
            catalog["presets"].pop(animation_name, None)
        write_catalog(self.catalog_path, catalog)
        if isinstance(removed, dict):
            keep = referenced_asset_names(catalog)
            for key in ("poster_url", "loop_url"):
                value = removed.get(key)
                if not isinstance(value, str):
                    continue
                name = value.rsplit("/", 1)[-1]
                path = self.output_dir / name
                if name not in keep and path.is_file():
                    path.unlink()

    @staticmethod
    def _lower_priority() -> None:
        try:
            os.nice(19)
        except OSError:
            pass

    def _run(self) -> None:
        while True:
            try:
                animation_name, preset_id, preset_path = self._jobs.get_nowait()
            except queue.Empty:
                return
            key = (animation_name, preset_id)
            with self._lock:
                cancelled = key in self._cancelled
            if not cancelled and preset_path.is_file():
                command = [
                    sys.executable,
                    str(self.project_root / "tools" / "generate_animation_previews.py"),
                    "--single-preset", str(preset_path),
                    "--animation", animation_name,
                    "--output", str(self.output_dir),
                    "--public-prefix", "/preview-assets/runtime",
                    "--status-path", str(self.status_path),
                    "--strips", str(self.strips),
                    "--leds-per-strip", str(self.leds_per_strip),
                    "--throttle-seconds", "0.02",
                ]
                subprocess.run(
                    command,
                    cwd=self.project_root,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=self._lower_priority if os.name == "posix" else None,
                )
            with self._lock:
                if key in self._cancelled:
                    self._queued.discard(key)
                    self._remove_entry_locked(animation_name, preset_id)
                    self._cancelled.discard(key)
                elif key in self._rerun and preset_path.is_file():
                    self._rerun.discard(key)
                    self._jobs.put((animation_name, preset_id, preset_path))
                else:
                    self._queued.discard(key)
            self._jobs.task_done()
