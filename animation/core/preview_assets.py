"""Deterministic, headless WebP previews for animation dashboard cards."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import random
import time
from types import MethodType
from typing import Any, Callable, Dict, Iterable, Optional

import numpy as np
from PIL import Image

from animation.core.base import RenderedFrame
from animation.core.manager import PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


CATALOG_VERSION = 1
DEFAULT_CAPTURE_SECONDS = (0.0, 0.5, 1.0, 2.0, 3.5, 5.5, 8.0, 12.0)
DEFAULT_SIMULATION_FPS = 30
FRAME_DURATION_MS = 500
FIXED_CLOCK = datetime(2026, 1, 15, 10, 19, 0, tzinfo=timezone.utc)


def empty_catalog(strips: int, leds_per_strip: int) -> Dict[str, Any]:
    return {
        "version": CATALOG_VERSION,
        "layout": {
            "strip_count": strips,
            "leds_per_strip": leds_per_strip,
            "total_leds": strips * leds_per_strip,
        },
        "animations": {},
        "presets": {},
    }


def load_catalog(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return empty_catalog(DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP)
    if not isinstance(payload, dict) or payload.get("version") != CATALOG_VERSION:
        return empty_catalog(DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP)
    payload.setdefault("animations", {})
    payload.setdefault("presets", {})
    return payload


def write_catalog(path: Path, catalog: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def merge_catalogs(*catalogs: Dict[str, Any]) -> Dict[str, Any]:
    merged = empty_catalog(DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP)
    for catalog in catalogs:
        if not isinstance(catalog, dict) or catalog.get("version") != CATALOG_VERSION:
            continue
        if isinstance(catalog.get("layout"), dict):
            merged["layout"] = dict(catalog["layout"])
        if isinstance(catalog.get("animations"), dict):
            merged["animations"].update(catalog["animations"])
        if isinstance(catalog.get("presets"), dict):
            for animation_name, entries in catalog["presets"].items():
                if isinstance(entries, dict):
                    merged["presets"].setdefault(animation_name, {}).update(entries)
    return merged


def preview_profile(manifest: Dict[str, Any]) -> tuple[tuple[float, ...], int]:
    configured = manifest.get("preview") if isinstance(manifest, dict) else None
    configured = configured if isinstance(configured, dict) else {}
    captures = tuple(
        float(value)
        for value in configured.get("capture_seconds", DEFAULT_CAPTURE_SECONDS)
    )
    fps = int(configured.get("simulation_fps", DEFAULT_SIMULATION_FPS))
    return captures, fps


def _safe_component(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in value)


def _file_digest(paths: Iterable[Path], extra: Dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(extra, sort_keys=True, separators=(",", ":")).encode())
    for path in sorted(set(paths), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        digest.update(path.as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _source_paths(root: Path, plugin_dir: Path, preset_path: Optional[Path]) -> list[Path]:
    paths: list[Path] = []
    for directory in (root / "animation" / "core", root / "animation" / "libraries", plugin_dir):
        if directory.is_dir():
            paths.extend(
                path for path in directory.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix in {".py", ".json", ".gif", ".png", ".webp"}
            )
    for relative in (
        "config/plant_pixel_map_32x138.json",
        "config/plant_globe_map_32x138.json",
    ):
        paths.append(root / relative)
    if preset_path is not None:
        paths.append(preset_path)
    return paths


class PreviewRenderer:
    """Render one animation or preset into a poster and a compact WebP loop."""

    def __init__(
        self,
        root: Path,
        output_dir: Path,
        public_prefix: str,
        *,
        strips: int = DEFAULT_STRIP_COUNT,
        leds_per_strip: int = DEFAULT_LEDS_PER_STRIP,
        throttle_seconds: float = 0.0,
        pause_guard: Optional[Callable[[], bool]] = None,
    ):
        self.root = root.resolve()
        self.output_dir = output_dir.resolve()
        self.public_prefix = public_prefix.rstrip("/")
        self.strips = strips
        self.leds_per_strip = leds_per_strip
        self.throttle_seconds = max(0.0, float(throttle_seconds))
        self.pause_guard = pause_guard
        self.loader = AnimationPluginLoader()
        self.plugins = self.loader.load_all_plugins()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _pause_if_needed(self) -> None:
        while self.pause_guard is not None and self.pause_guard():
            time.sleep(1.0)

    def _normalize_frame(self, rendered: Any) -> np.ndarray:
        pixels = rendered.pixels if isinstance(rendered, RenderedFrame) else rendered
        array = np.asarray(pixels, dtype=np.uint8)
        expected = self.strips * self.leds_per_strip
        if array.shape != (expected, 3):
            array = np.asarray(list(pixels), dtype=np.uint8).reshape((expected, 3))
        # Physical LED zero is at the bottom. Dashboard images use top-down rows.
        return array.reshape(self.strips, self.leds_per_strip, 3).transpose(1, 0, 2)[::-1].copy()

    @staticmethod
    def _make_deterministic(animation: Any, config: Dict[str, Any], key: str) -> None:
        stable_seed = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
        random.seed(stable_seed)
        np.random.seed(stable_seed & 0xFFFFFFFF)
        schema = animation.get_parameter_schema()
        seed_updates: Dict[str, Any] = {}
        for name in ("seed", "random_seed"):
            if name in schema and not config.get(name):
                definition = schema[name]
                maximum = int(definition.get("max", 999999))
                minimum = int(definition.get("min", 1))
                seed_updates[name] = minimum + stable_seed % max(1, maximum - minimum + 1)
        if seed_updates:
            animation.update_parameters(seed_updates)
        if hasattr(animation, "_clock_now"):
            animation._clock_now = MethodType(lambda _self: FIXED_CLOCK, animation)

    def render(
        self,
        animation_name: str,
        *,
        preset_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        preset_path: Optional[Path] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        animation_class = self.plugins.get(animation_name)
        if animation_class is None:
            raise ValueError(f"unknown animation: {animation_name}")
        plugin_dir = self.loader.get_plugin_dir(animation_name)
        if plugin_dir is None:
            raise ValueError(f"plugin directory is unavailable: {animation_name}")
        manifest = self.loader.plugin_manifests.get(animation_name, {})
        captures, simulation_fps = preview_profile(manifest)
        item_key = f"{animation_name}/{preset_id or '_default'}"
        effective_config = dict(config or {})
        digest = _file_digest(
            _source_paths(self.root, plugin_dir, preset_path),
            {
                "catalog_version": CATALOG_VERSION,
                "item": item_key,
                "config": effective_config,
                "captures": captures,
                "simulation_fps": simulation_fps,
                "layout": [self.strips, self.leds_per_strip],
                "fixed_clock": FIXED_CLOCK.isoformat(),
            },
        )
        stem = _safe_component(f"{animation_name}--{preset_id or 'default'}--{digest[:16]}")
        poster_path = self.output_dir / f"{stem}--poster.webp"
        loop_path = self.output_dir / f"{stem}--loop.webp"
        if not force and poster_path.is_file() and loop_path.is_file():
            with Image.open(loop_path) as cached_loop:
                cached_frames = int(getattr(cached_loop, "n_frames", 1))
            return self._entry(
                digest, poster_path, loop_path, cached_frames, cached_frames == 1
            )

        controller = PreviewLEDController(self.strips, self.leds_per_strip)
        constructor_seed = int(hashlib.sha256(item_key.encode()).hexdigest()[:8], 16)
        random.seed(constructor_seed)
        np.random.seed(constructor_seed & 0xFFFFFFFF)
        animation = animation_class(controller, effective_config)
        self._make_deterministic(animation, effective_config, item_key)
        frames: list[np.ndarray] = []
        capture_index = 0
        final_step = int(math.ceil(captures[-1] * simulation_fps))
        for frame_count in range(final_step + 1):
            self._pause_if_needed()
            elapsed = frame_count / simulation_fps
            rendered = animation.generate_frame(elapsed, frame_count)
            while capture_index < len(captures) and elapsed + 1e-9 >= captures[capture_index]:
                frames.append(self._normalize_frame(rendered))
                capture_index += 1
            if self.throttle_seconds:
                time.sleep(self.throttle_seconds)
        if not frames:
            raise RuntimeError(f"no frames captured for {item_key}")

        first_fingerprint = hashlib.sha256(frames[0].tobytes()).digest()
        is_static = all(
            hashlib.sha256(frame.tobytes()).digest() == first_fingerprint
            for frame in frames[1:]
        )
        authored_frames = [frames[0]] if is_static else frames

        images = [Image.fromarray(frame, mode="RGB") for frame in authored_frames]
        images[0].save(poster_path, format="WEBP", lossless=True, method=6)
        images[0].save(
            loop_path,
            format="WEBP",
            save_all=len(images) > 1,
            append_images=images[1:],
            duration=FRAME_DURATION_MS,
            loop=0,
            lossless=True,
            method=6,
        )
        return self._entry(digest, poster_path, loop_path, len(images), len(images) == 1)

    def _entry(
        self,
        digest: str,
        poster_path: Path,
        loop_path: Path,
        frame_count: int,
        static: bool,
    ) -> Dict[str, Any]:
        return {
            "status": "ready",
            "digest": digest,
            "poster_url": f"{self.public_prefix}/{poster_path.name}",
            "loop_url": f"{self.public_prefix}/{loop_path.name}",
            "frame_count": frame_count,
            "duration_ms": FRAME_DURATION_MS,
            "static": bool(static),
        }


def preset_payload(path: Path, animation_name: str) -> tuple[str, Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("params"), dict):
        raise ValueError(f"invalid animation preset: {path}")
    if payload.get("animation", animation_name) != animation_name:
        raise ValueError(f"preset animation does not match {animation_name}: {path}")
    preset_id = str(payload.get("preset_id") or path.stem)
    return preset_id, dict(payload["params"])


def referenced_asset_names(catalog: Dict[str, Any]) -> set[str]:
    names: set[str] = set()
    entries: list[Any] = list(catalog.get("animations", {}).values())
    for presets in catalog.get("presets", {}).values():
        if isinstance(presets, dict):
            entries.extend(presets.values())
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key in ("poster_url", "loop_url"):
            value = entry.get(key)
            if isinstance(value, str):
                names.add(value.rsplit("/", 1)[-1])
    return names


def clean_stale_assets(output_dir: Path, catalog: Dict[str, Any]) -> None:
    keep = referenced_asset_names(catalog) | {"catalog.json"}
    for path in output_dir.glob("*.webp"):
        if path.name not in keep:
            path.unlink()
