"""Runtime adapter for unchanged LED-grid Python plugins in Pyodide."""

from __future__ import annotations

import importlib
import gc
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType, MethodType
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np


ENGINE = "python-pyodide-wasm"
DEFAULT_INSTANCE_ID = "primary"
_INSTANCE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MISSING = object()


@dataclass(frozen=True)
class PluginSpec:
    class_name: str
    role: str
    frame_format: str
    timing_adapter: str
    required_packages: Tuple[str, ...]


def _catalog_payload() -> Mapping[str, Any]:
    bundled = Path(__file__).with_name("ledgrid_browser_manifest.json")
    if bundled.is_file():
        return json.loads(bundled.read_text(encoding="utf-8"))

    # Development imports run from animation/browser_preview/python/runtime.py.
    repo_root = Path(__file__).resolve().parents[3]
    entries = []
    for manifest_path in sorted((repo_root / "animation/plugins").glob("*/manifest.json")):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("provider", "python") != "python":
            continue
        plugin_id = manifest_path.parent.name
        role = payload.get("role", "full_scene" if plugin_id == "clock" else "background")
        vibe = payload.get("vibe") if isinstance(payload.get("vibe"), dict) else {}
        entries.append({
            "pluginId": plugin_id,
            "className": payload["class"],
            "role": role,
            "frameFormat": "premultiplied-rgba" if role == "overlay" else "rgb",
            "timingAdapter": vibe.get("timing_adapter", "legacy_speed_param"),
            "requiredPackages": ["pillow"] if plugin_id == "gif_animation" else [],
        })
    return {"plugins": entries}


def _load_specs() -> Mapping[str, PluginSpec]:
    specs = {}
    for item in _catalog_payload().get("plugins", ()):
        plugin_id = item["pluginId"]
        specs[plugin_id] = PluginSpec(
            class_name=item["className"],
            role=item["role"],
            frame_format=item["frameFormat"],
            timing_adapter=item.get("timingAdapter", "legacy_speed_param"),
            required_packages=tuple(item.get("requiredPackages", ())),
        )
    if not specs:
        raise RuntimeError("Python browser runtime catalog is empty")
    return MappingProxyType(specs)


PLUGIN_SPECS: Mapping[str, PluginSpec] = _load_specs()
SUPPORTED_PLUGINS: Mapping[str, str] = MappingProxyType({
    plugin_id: spec.class_name for plugin_id, spec in PLUGIN_SPECS.items()
})


@dataclass(frozen=True)
class PreviewController:
    """Geometry-only controller surface; intentionally exposes no device I/O."""

    strip_count: int
    leds_per_strip: int
    debug: bool = False

    @property
    def total_leds(self) -> int:
        return self.strip_count * self.leds_per_strip


@dataclass
class _RuntimeInstance:
    identity: Tuple[str, str, int, int]
    animation: Any
    controller: PreviewController
    spec: PluginSpec
    frame_bytes: bytes = b""


def _positive_dimension(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"geometry.{name} must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"geometry.{name} must be a positive integer") from exc
    if number <= 0 or number != value:
        raise ValueError(f"geometry.{name} must be a positive integer")
    return number


def normalize_geometry(geometry: Mapping[str, Any]) -> Tuple[int, int]:
    """Accept API, canvas, and protocol spellings without changing orientation."""
    if not isinstance(geometry, Mapping):
        raise ValueError("geometry must be an object")
    width = geometry.get("stripCount", geometry.get("strip_count", geometry.get("width")))
    height = geometry.get(
        "ledsPerStrip", geometry.get("leds_per_strip", geometry.get("height"))
    )
    return _positive_dimension(width, "stripCount"), _positive_dimension(
        height, "ledsPerStrip"
    )


def _validate_browser_parameters(params: Mapping[str, Any]) -> None:
    if not isinstance(params, Mapping):
        raise ValueError("params must be an object")


def _validated_instance_id(value: Any) -> str:
    resolved = DEFAULT_INSTANCE_ID if value is None else value
    if not isinstance(resolved, str) or _INSTANCE_ID.fullmatch(resolved) is None:
        raise ValueError(
            "instanceId must be 1-64 letters, digits, dots, underscores, or hyphens"
        )
    return resolved


def _validated_wall_time(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("wallTime must be a non-negative finite Unix timestamp")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved < 0.0:
        raise ValueError("wallTime must be a non-negative finite Unix timestamp")
    return resolved


class BrowserPreviewRuntime:
    """Keep multiple stateful animations inside one memory-conscious interpreter."""

    def __init__(self) -> None:
        self._instances: Dict[str, _RuntimeInstance] = {}
        self._last_instance_id = DEFAULT_INSTANCE_ID

    @property
    def animation(self) -> Any:
        instance = self._instances.get(DEFAULT_INSTANCE_ID)
        return None if instance is None else instance.animation

    @property
    def controller(self) -> Optional[PreviewController]:
        instance = self._instances.get(DEFAULT_INSTANCE_ID)
        return None if instance is None else instance.controller

    @property
    def frame_bytes(self) -> bytes:
        instance = self._instances.get(DEFAULT_INSTANCE_ID)
        return b"" if instance is None else instance.frame_bytes

    def frame_bytes_for(self, instance_id: Optional[str] = None) -> bytes:
        resolved_id = _validated_instance_id(instance_id)
        instance = self._instances.get(resolved_id)
        if instance is None:
            raise RuntimeError(f"Python browser preview instance {resolved_id!r} is not initialized")
        return instance.frame_bytes

    def dispose_instance(self, instance_id: Optional[str] = None) -> Dict[str, Any]:
        """Release one animation's buffers without tearing down Pyodide."""
        resolved_instance_id = _validated_instance_id(instance_id)
        instance = self._instances.pop(resolved_instance_id, None)
        disposed = instance is not None
        if disposed:
            cleanup = getattr(instance.animation, "cleanup", None)
            if callable(cleanup):
                cleanup()
            instance.frame_bytes = b""
            del instance
            # Explicit disposal is infrequent and exists specifically to make
            # large NumPy simulation planes reusable on memory-bound phones.
            gc.collect()
        return {
            "engine": ENGINE,
            "instanceId": resolved_instance_id,
            "disposed": disposed,
            "remainingInstances": len(self._instances),
        }

    def initialize(
        self,
        plugin_id: str,
        class_name: str,
        geometry: Mapping[str, Any],
        params: Optional[Mapping[str, Any]] = None,
        instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_instance_id = _validated_instance_id(instance_id)
        spec = PLUGIN_SPECS.get(plugin_id)
        if spec is None:
            raise ValueError(
                f"Python browser preview does not support plugin {plugin_id!r}; "
                f"catalog plugins: {', '.join(SUPPORTED_PLUGINS)}"
            )
        if class_name != spec.class_name:
            raise ValueError(
                f"plugin {plugin_id!r} requires class {spec.class_name!r}, "
                f"not {class_name!r}"
            )
        strip_count, leds_per_strip = normalize_geometry(geometry)
        resolved_params = dict(params or {})
        _validate_browser_parameters(resolved_params)
        identity = (plugin_id, class_name, strip_count, leds_per_strip)
        instance = self._instances.get(resolved_instance_id)
        reset = instance is None or identity != instance.identity
        if reset:
            module = importlib.import_module(f"animation.plugins.{plugin_id}")
            animation_class = getattr(module, class_name)
            controller = PreviewController(strip_count, leds_per_strip)
            animation = animation_class(controller, resolved_params)
            instance = _RuntimeInstance(identity, animation, controller, spec)
            self._instances[resolved_instance_id] = instance
        elif resolved_params:
            instance.animation.update_parameters(resolved_params)
        self._last_instance_id = resolved_instance_id
        return {
            "engine": ENGINE,
            "instanceId": resolved_instance_id,
            "pluginId": plugin_id,
            "className": class_name,
            "role": spec.role,
            "frameFormat": spec.frame_format,
            "width": strip_count,
            "height": leds_per_strip,
            "reset": reset,
            "supportedPlugins": list(SUPPORTED_PLUGINS),
            "supportsPlantModifiers": True,
            "supportsMultipleInstances": True,
            "supportsFixedWallTime": True,
            "requiredPackages": list(spec.required_packages),
        }

    @staticmethod
    def _generate(
        instance: _RuntimeInstance,
        elapsed: float,
        frame_index: int,
        wall_time: Optional[float],
    ) -> Any:
        animation = instance.animation
        if wall_time is None or not hasattr(animation, "_clock_now"):
            return animation.generate_frame(elapsed, frame_index)

        previous = animation.__dict__.get("_clock_now", _MISSING)

        def fixed_clock_now(owner: Any) -> datetime:
            local = datetime.fromtimestamp(wall_time).astimezone()
            apply_offset = getattr(owner, "_apply_clock_offset", None)
            return apply_offset(local) if apply_offset is not None else local

        animation._clock_now = MethodType(fixed_clock_now, animation)
        try:
            return animation.generate_frame(elapsed, frame_index)
        finally:
            if previous is _MISSING:
                del animation.__dict__["_clock_now"]
            else:
                animation.__dict__["_clock_now"] = previous

    def render(
        self,
        elapsed: Any,
        frame_index: Any,
        params: Optional[Mapping[str, Any]] = None,
        instance_id: Optional[str] = None,
        wall_time: Any = None,
    ) -> Dict[str, Any]:
        resolved_instance_id = _validated_instance_id(instance_id)
        instance = self._instances.get(resolved_instance_id)
        if instance is None:
            raise RuntimeError(
                f"Python browser preview instance {resolved_instance_id!r} is not initialized"
            )
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
            raise ValueError("elapsed must be numeric")
        resolved_elapsed = float(elapsed)
        if not math.isfinite(resolved_elapsed) or resolved_elapsed < 0.0:
            raise ValueError("elapsed must be a non-negative finite number")
        if isinstance(frame_index, bool):
            raise ValueError("frameIndex must be a non-negative integer")
        try:
            resolved_frame_index = int(frame_index)
        except (TypeError, ValueError) as exc:
            raise ValueError("frameIndex must be a non-negative integer") from exc
        if resolved_frame_index < 0 or resolved_frame_index != frame_index:
            raise ValueError("frameIndex must be a non-negative integer")
        live_params = dict(params or {})
        _validate_browser_parameters(live_params)
        resolved_wall_time = _validated_wall_time(wall_time)

        started = time.perf_counter()
        if live_params:
            instance.animation.update_parameters(live_params)
        output = self._generate(
            instance, resolved_elapsed, resolved_frame_index, resolved_wall_time
        )
        changed = bool(getattr(output, "changed", True))
        pixels = getattr(output, "pixels", output)
        if instance.spec.frame_format == "rgb":
            refresh_pending = instance.animation.framework_plant_modifier_refresh_pending()
            pixels = instance.animation.apply_framework_plant_modifiers(
                pixels, changed=changed
            )
            if instance.animation.framework_plant_modifiers_active():
                changed = changed or refresh_pending
        canonical = np.asarray(pixels)
        channels = 4 if instance.spec.frame_format == "premultiplied-rgba" else 3
        expected_shape = (instance.controller.total_leds, channels)
        if canonical.shape != expected_shape:
            raise ValueError(
                f"plugin returned frame shape {canonical.shape}; expected {expected_shape} "
                f"for role {instance.spec.role!r}"
            )
        if canonical.dtype != np.uint8:
            raise ValueError(
                f"plugin returned frame dtype {canonical.dtype}; expected uint8"
            )
        if channels == 4 and np.any(canonical[:, :3] > canonical[:, 3:4]):
            raise ValueError("overlay plugin returned non-premultiplied RGBA pixels")
        if not canonical.flags.c_contiguous:
            canonical = np.ascontiguousarray(canonical)
        # No transpose or row flip: bytes stay canonical strip-major RGB/RGBA.
        instance.frame_bytes = canonical.tobytes(order="C")
        self._last_instance_id = resolved_instance_id
        render_ms = (time.perf_counter() - started) * 1000.0
        return {
            "engine": ENGINE,
            "instanceId": resolved_instance_id,
            "role": instance.spec.role,
            "frameFormat": instance.spec.frame_format,
            "width": instance.controller.strip_count,
            "height": instance.controller.leds_per_strip,
            "changed": changed,
            "renderMs": render_ms,
            "wallClockFixed": resolved_wall_time is not None,
        }

    def initialize_json(self, payload_json: str) -> str:
        payload = json.loads(payload_json)
        result = self.initialize(
            payload.get("pluginId"),
            payload.get("className"),
            payload.get("geometry"),
            payload.get("params"),
            payload.get("instanceId"),
        )
        return json.dumps(result, separators=(",", ":"), sort_keys=True)

    def render_json(self, payload_json: str) -> str:
        payload = json.loads(payload_json)
        result = self.render(
            payload.get("elapsed"),
            payload.get("frameIndex"),
            payload.get("params"),
            payload.get("instanceId"),
            payload.get("wallTime"),
        )
        return json.dumps(result, separators=(",", ":"), sort_keys=True)

    def dispose_json(self, payload_json: str) -> str:
        payload = json.loads(payload_json)
        result = self.dispose_instance(payload.get("instanceId"))
        return json.dumps(result, separators=(",", ":"), sort_keys=True)


__all__ = [
    "BrowserPreviewRuntime", "DEFAULT_INSTANCE_ID", "ENGINE", "PLUGIN_SPECS",
    "PreviewController", "SUPPORTED_PLUGINS", "normalize_geometry",
]
