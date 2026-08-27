"""Small no-I/O ``animation`` package surface for browser preview plugins.

This file is installed as ``animation/__init__.py`` inside the generated
Pyodide source bundle.  Plugin implementation files are copied byte-for-byte
from ``animation/plugins``; this shim supplies the narrow host contract used by
the explicitly supported browser subset without importing drivers, Flask,
threads, calibration files, or receiver orchestration.
"""

from __future__ import annotations

import colorsys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, Union

import numpy as np

from animation.core.plant_awareness import PlantMaskCache


PLANT_MODIFIER_IDS = (
    "illuminate", "shadow", "refract", "hue_shift", "liquid_glass",
    "attractor", "repulsor", "slow_zone", "obstacle", "portal", "bumper",
    "hazard", "habitat", "emitter",
)
FRAMEWORK_VISUAL_MODIFIERS = frozenset(("hue_shift", "liquid_glass"))


@dataclass(frozen=True)
class RenderedFrame:
    """Frame pixels plus the host's changed-frame presentation hint."""

    pixels: Any
    changed: bool = True
    dirty_ranges: Optional[Tuple[Tuple[int, int], ...]] = None


FrameOutput = Union[np.ndarray, RenderedFrame]


@dataclass(frozen=True)
class PlantModifierState:
    """Validated subset of the host state used for inactive browser previews."""

    version: int = 1
    active: Tuple[str, ...] = ()
    strengths: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "PlantModifierState":
        return cls()

    @classmethod
    def from_payload(cls, payload: Any) -> "PlantModifierState":
        if isinstance(payload, cls):
            return payload
        if payload is None:
            return cls.empty()
        if not isinstance(payload, Mapping):
            raise ValueError("plant_modifiers must be an object")
        if payload.get("version", 1) != 1:
            raise ValueError("plant_modifiers.version must be 1")
        raw_active = payload.get("active", ())
        if not isinstance(raw_active, (list, tuple)) or any(
            not isinstance(item, str) for item in raw_active
        ):
            raise ValueError("plant_modifiers.active must be an array of IDs")
        if len(set(raw_active)) != len(raw_active):
            raise ValueError("plant_modifiers.active contains duplicate IDs")
        unknown = set(raw_active) - set(PLANT_MODIFIER_IDS)
        if unknown:
            raise ValueError(f"unknown plant modifier: {sorted(unknown)[0]}")
        raw_strengths = payload.get("strengths", {})
        if not isinstance(raw_strengths, Mapping):
            raise ValueError("plant_modifiers.strengths must be an object")
        strengths = {}
        for modifier in raw_active:
            value = float(raw_strengths.get(modifier, 1.0 if modifier == "obstacle" else 0.5))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"strength for {modifier} must be between 0 and 1")
            strengths[modifier] = value
        active = tuple(item for item in PLANT_MODIFIER_IDS if item in raw_active)
        return cls(active=active, strengths=strengths)

    def enabled(self, modifier: str, supported: Iterable[str] = PLANT_MODIFIER_IDS) -> bool:
        return modifier in self.active and modifier in supported

    def strength(self, modifier: str, supported: Iterable[str] = PLANT_MODIFIER_IDS) -> float:
        return self.strengths.get(modifier, 0.0) if self.enabled(modifier, supported) else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "active": list(self.active),
            "strengths": {key: self.strengths[key] for key in self.active},
        }


class AnimationBase(ABC):
    """No-I/O implementation of the stable plugin frame-buffer contract."""

    PLANT_MODIFIER_SUPPORT = frozenset()
    INTERACTION_TYPES = frozenset()

    def __init__(self, controller: Any, config: Optional[Dict[str, Any]] = None):
        self.controller = controller
        self.config = config or {}
        self._frame_buffers = []
        self._frame_buffer_index = 0
        self._frame_buffer_geometry = None
        self._hsv_scratch: Dict[str, np.ndarray] = {}
        self._presentation_context = None
        self._plant_mask_cache = PlantMaskCache(self)
        self.default_params = {
            "speed": 1.0,
            "brightness": 1.0,
            "color_saturation": 1.0,
            "color_value": 1.0,
            "plant_aware": False,
            "plant_modifiers": PlantModifierState.empty().to_dict(),
            "plant_clearance": 1,
            "plant_mask_path": "config/plant_pixel_map_32x138.json",
            "plant_globe_mask_path": "config/plant_globe_map_32x138.json",
        }
        self.params = {**self.default_params, **self.config}
        self._plant_modifier_state = PlantModifierState.from_payload(
            self.params.get("plant_modifiers")
        )

    @property
    def presentation_context(self) -> None:
        return self._presentation_context

    @abstractmethod
    def generate_frame(self, time_elapsed: float, frame_count: int) -> FrameOutput:
        raise NotImplementedError

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        return {
            "speed": {"type": "float", "min": 0.1, "max": 5.0, "default": 1.0},
            "brightness": {"type": "float", "min": 0.0, "max": 1.0, "default": 1.0},
            "color_saturation": {"type": "float", "min": 0.0, "max": 1.0, "default": 1.0},
            "color_value": {"type": "float", "min": 0.0, "max": 1.0, "default": 1.0},
            "plant_aware": {"type": "bool", "default": False},
            "plant_modifiers": {"type": "object", "default": PlantModifierState.empty().to_dict()},
        }

    def update_parameters(self, new_params: Dict[str, Any]) -> None:
        self.params.update(new_params)
        if "plant_modifiers" in new_params:
            self._plant_modifier_state = PlantModifierState.from_payload(
                new_params["plant_modifiers"]
            )
        if {
            "plant_clearance", "plant_mask_path", "plant_globe_mask_path"
        } & new_params.keys():
            self._plant_mask_cache.invalidate()

    def plant_aware_enabled(self) -> bool:
        return bool(self.params.get("plant_aware", False)) or bool(
            self._plant_modifier_state.active
        )

    def plant_modifier_state(self) -> PlantModifierState:
        return self._plant_modifier_state

    def plant_modifier_enabled(self, modifier: str) -> bool:
        supported = self.PLANT_MODIFIER_SUPPORT | FRAMEWORK_VISUAL_MODIFIERS
        return self._plant_modifier_state.enabled(modifier, supported)

    def plant_modifier_strength(self, modifier: str) -> float:
        supported = self.PLANT_MODIFIER_SUPPORT | FRAMEWORK_VISUAL_MODIFIERS
        return self._plant_modifier_state.strength(modifier, supported)

    def get_plant_masks(self, clearance: Optional[int] = None) -> Any:
        return self._plant_mask_cache.get(clearance)

    def hsv_to_rgb(self, h: float, s: float, v: float) -> Tuple[int, int, int]:
        red, green, blue = colorsys.hsv_to_rgb(h, s, v)
        return int(red * 255), int(green * 255), int(blue * 255)

    def apply_brightness(self, color: Tuple[int, int, int]) -> Tuple[int, int, int]:
        brightness = self.params.get("brightness", 1.0)
        return tuple(int(channel * brightness) for channel in color)

    def hsv_to_rgb_array(
        self,
        h: np.ndarray,
        s: np.ndarray,
        v: np.ndarray,
        out: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Exact vectorized implementation used by the host AnimationBase."""
        h = np.asarray(h, dtype=np.float32).ravel()
        s = np.asarray(s, dtype=np.float32).ravel()
        v = np.asarray(v, dtype=np.float32).ravel()
        size = h.size
        if s.size != size or v.size != size:
            raise ValueError("HSV component arrays must have equal lengths")
        if self._hsv_scratch.get("h6", np.empty(0)).size != size:
            self._hsv_scratch = {
                "h6": np.empty(size, dtype=np.float32),
                "f": np.empty(size, dtype=np.float32),
                "p": np.empty(size, dtype=np.float32),
                "q": np.empty(size, dtype=np.float32),
                "t": np.empty(size, dtype=np.float32),
                "sector": np.empty(size, dtype=np.int32),
                "rgb": np.empty((size, 3), dtype=np.float32),
            }
        h6 = self._hsv_scratch["h6"]
        fraction = self._hsv_scratch["f"]
        p = self._hsv_scratch["p"]
        q = self._hsv_scratch["q"]
        t = self._hsv_scratch["t"]
        sector = self._hsv_scratch["sector"]
        rgb = self._hsv_scratch["rgb"]
        np.remainder(h, 1.0, out=h6)
        h6 *= 6.0
        np.floor(h6, out=fraction)
        np.copyto(sector, fraction, casting="unsafe")
        np.remainder(sector, 6, out=sector)
        np.subtract(h6, fraction, out=fraction)
        np.subtract(1.0, s, out=p)
        p *= v
        np.multiply(s, fraction, out=q)
        np.subtract(1.0, q, out=q)
        q *= v
        np.subtract(1.0, fraction, out=t)
        t *= s
        np.subtract(1.0, t, out=t)
        t *= v
        components = (
            (v, t, p), (q, v, p), (p, v, t),
            (p, q, v), (t, p, v), (v, p, q),
        )
        for index, (red, green, blue) in enumerate(components):
            mask = sector == index
            rgb[mask, 0] = red[mask]
            rgb[mask, 1] = green[mask]
            rgb[mask, 2] = blue[mask]
        if out is None:
            out = np.empty((size, 3), dtype=np.uint8)
        elif out.shape != (size, 3) or out.dtype != np.uint8:
            raise ValueError("HSV output buffer must be uint8 with shape (N, 3)")
        rgb *= 255.0
        np.clip(rgb, 0.0, 255.0, out=rgb)
        np.copyto(out, rgb, casting="unsafe")
        return out

    def apply_brightness_array(
        self, colors: np.ndarray, out: Optional[np.ndarray] = None
    ) -> np.ndarray:
        brightness = self.params.get("brightness", 1.0)
        if brightness >= 1.0:
            if out is not None and out is not colors:
                np.copyto(out, colors, casting="unsafe")
                return out
            return colors
        if out is None:
            scaled = colors.astype(np.float32) * max(0.0, brightness)
            return np.clip(scaled, 0, 255).astype(np.uint8)
        np.multiply(colors, max(0.0, brightness), out=out, casting="unsafe")
        return out

    def next_frame_buffer(self, *, clear: bool = True, count: int = 2) -> np.ndarray:
        total_pixels = self.get_pixel_count()
        geometry = (total_pixels, max(2, int(count)))
        if geometry != self._frame_buffer_geometry:
            self._frame_buffers = [
                np.zeros((total_pixels, 3), dtype=np.uint8)
                for _ in range(geometry[1])
            ]
            self._frame_buffer_index = 0
            self._frame_buffer_geometry = geometry
        frame = self._frame_buffers[self._frame_buffer_index]
        self._frame_buffer_index = (self._frame_buffer_index + 1) % len(self._frame_buffers)
        if clear:
            frame.fill(0)
        return frame

    @staticmethod
    def rendered_frame(
        pixels: Any,
        *,
        changed: bool = True,
        dirty_ranges: Optional[Tuple[Tuple[int, int], ...]] = None,
    ) -> RenderedFrame:
        return RenderedFrame(pixels, changed=changed, dirty_ranges=dirty_ranges)

    def get_pixel_count(self) -> int:
        return self.controller.total_leds

    def get_strip_info(self) -> Tuple[int, int]:
        return self.controller.strip_count, self.controller.leds_per_strip


class StatefulAnimationBase(AnimationBase):
    """Marker retained for import compatibility; browser previews are frame based."""


__all__ = [
    "AnimationBase", "FrameOutput", "PlantModifierState", "RenderedFrame",
    "StatefulAnimationBase",
]
