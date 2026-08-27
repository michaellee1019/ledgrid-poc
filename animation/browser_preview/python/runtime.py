"""Runtime adapter for unchanged LED-grid Python plugins in Pyodide."""

from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np


SUPPORTED_PLUGINS: Mapping[str, str] = MappingProxyType({
    "gradient": "GradientAnimation",
    "rainbow": "RainbowAnimation",
    "sparkle": "SparkleAnimation",
    "wave": "WaveAnimation",
})
ENGINE = "python-pyodide-wasm"


@dataclass(frozen=True)
class PreviewController:
    """Geometry-only controller surface; intentionally exposes no device I/O."""

    strip_count: int
    leds_per_strip: int

    @property
    def total_leds(self) -> int:
        return self.strip_count * self.leds_per_strip


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


class BrowserPreviewRuntime:
    """Own one plugin instance and preserve its state across live parameter edits."""

    def __init__(self) -> None:
        self._identity: Optional[Tuple[str, str, int, int]] = None
        self.animation: Any = None
        self.controller: Optional[PreviewController] = None
        self.frame_bytes = b""

    def initialize(
        self,
        plugin_id: str,
        class_name: str,
        geometry: Mapping[str, Any],
        params: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        expected_class = SUPPORTED_PLUGINS.get(plugin_id)
        if expected_class is None:
            raise ValueError(
                f"Python browser preview does not support plugin {plugin_id!r}; "
                f"supported plugins: {', '.join(SUPPORTED_PLUGINS)}"
            )
        if class_name != expected_class:
            raise ValueError(
                f"plugin {plugin_id!r} requires class {expected_class!r}, "
                f"not {class_name!r}"
            )
        strip_count, leds_per_strip = normalize_geometry(geometry)
        resolved_params = dict(params or {})
        _validate_browser_parameters(resolved_params)
        identity = (plugin_id, class_name, strip_count, leds_per_strip)
        reset = identity != self._identity
        if reset:
            module = importlib.import_module(f"animation.plugins.{plugin_id}")
            animation_class = getattr(module, class_name)
            self.controller = PreviewController(strip_count, leds_per_strip)
            self.animation = animation_class(self.controller, resolved_params)
            self._identity = identity
        elif resolved_params:
            self.animation.update_parameters(resolved_params)
        return {
            "engine": ENGINE,
            "pluginId": plugin_id,
            "className": class_name,
            "width": strip_count,
            "height": leds_per_strip,
            "reset": reset,
            "supportedPlugins": list(SUPPORTED_PLUGINS),
            "supportsPlantModifiers": False,
        }

    def render(
        self,
        elapsed: Any,
        frame_index: Any,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self.animation is None or self.controller is None:
            raise RuntimeError("Python browser preview has not been initialized")
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
            raise ValueError("elapsed must be numeric")
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

        started = time.perf_counter()
        if live_params:
            self.animation.update_parameters(live_params)
        output = self.animation.generate_frame(float(elapsed), resolved_frame_index)
        changed = bool(getattr(output, "changed", True))
        pixels = getattr(output, "pixels", output)
        canonical = np.asarray(pixels)
        expected_shape = (self.controller.total_leds, 3)
        if canonical.shape != expected_shape:
            raise ValueError(
                f"plugin returned frame shape {canonical.shape}; expected {expected_shape}"
            )
        if canonical.dtype != np.uint8:
            raise ValueError(
                f"plugin returned frame dtype {canonical.dtype}; expected uint8"
            )
        if not canonical.flags.c_contiguous:
            canonical = np.ascontiguousarray(canonical)
        # No transpose or row flip: bytes stay canonical strip-major RGB.
        self.frame_bytes = canonical.tobytes(order="C")
        render_ms = (time.perf_counter() - started) * 1000.0
        return {
            "engine": ENGINE,
            "width": self.controller.strip_count,
            "height": self.controller.leds_per_strip,
            "changed": changed,
            "renderMs": render_ms,
        }

    def initialize_json(self, payload_json: str) -> str:
        payload = json.loads(payload_json)
        result = self.initialize(
            payload.get("pluginId"),
            payload.get("className"),
            payload.get("geometry"),
            payload.get("params"),
        )
        return json.dumps(result, separators=(",", ":"), sort_keys=True)

    def render_json(self, payload_json: str) -> str:
        payload = json.loads(payload_json)
        result = self.render(
            payload.get("elapsed"),
            payload.get("frameIndex"),
            payload.get("params"),
        )
        return json.dumps(result, separators=(",", ":"), sort_keys=True)


__all__ = [
    "BrowserPreviewRuntime", "ENGINE", "PreviewController", "SUPPORTED_PLUGINS",
    "normalize_geometry",
]
