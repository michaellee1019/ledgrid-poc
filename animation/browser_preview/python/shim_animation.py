"""Small no-I/O ``animation`` package surface for browser preview plugins.

This file is installed as ``animation/__init__.py`` inside the generated
Pyodide source bundle. Plugin and library implementation files are copied
byte-for-byte from the repository; this shim supplies their rendering contract
without importing device drivers, the scene manager, or receiver orchestration.
"""

from __future__ import annotations

import colorsys
import math
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

import numpy as np

from animation.core.plant_awareness import (
    FRAMEWORK_VISUAL_MODIFIERS,
    PlantMaskCache,
    PlantModifierState,
    plant_parameter_schema,
)
from animation.core.presentation_contracts import TimingAdapter
from animation.core.receiver_optics import HUE_STRENGTH_MAX, apply_hue_shift_u8


@dataclass(frozen=True)
class RenderedFrame:
    """Frame pixels plus the host's changed-frame presentation hint."""

    pixels: Any
    changed: bool = True
    dirty_ranges: Optional[Tuple[Tuple[int, int], ...]] = None


FrameOutput = Union[np.ndarray, RenderedFrame]


class AnimationBase(ABC):
    """No-I/O implementation of the stable plugin frame-buffer contract."""

    PLANT_MODIFIER_SUPPORT = frozenset()
    INTERACTION_TYPES = frozenset()
    TIMING_ADAPTER = TimingAdapter.LEGACY_SPEED_PARAM
    VIBE_CAPABILITIES = frozenset()
    VIBE_COLOR_POLICY = "preserve"
    VIBE_PARAMETER_MAPPINGS: Mapping[str, Any] = MappingProxyType({})

    def __init__(self, controller: Any, config: Optional[Dict[str, Any]] = None):
        self.controller = controller
        self.config = config or {}
        self.start_time = time.time()
        self.frame_count = 0
        self.is_running = False
        self._frame_buffers: List[np.ndarray] = []
        self._frame_buffer_index = 0
        self._frame_buffer_geometry = None
        self._hsv_scratch: Dict[str, np.ndarray] = {}
        self._presentation_context = None
        self._presentation_lock = threading.RLock()
        self._plant_mask_cache = PlantMaskCache(self)
        self._framework_modifier_buffers: List[np.ndarray] = []
        self._framework_modifier_buffer_index = 0
        self._framework_modifier_geometry = None
        self._framework_modifier_cached_frame = None
        self.name = getattr(self, "ANIMATION_NAME", self.__class__.__name__)
        self.description = getattr(self, "ANIMATION_DESCRIPTION", "No description")
        self.author = getattr(self, "ANIMATION_AUTHOR", "Unknown")
        self.version = getattr(self, "ANIMATION_VERSION", "1.0")
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
        self._authored_params = {**self.default_params, **self.config}
        self.params = self._authored_params
        self._plant_modifier_state = self._resolve_plant_modifier_state()

    @property
    def presentation_context(self) -> None:
        return self._presentation_context

    @property
    def authored_params(self) -> Mapping[str, Any]:
        self._sync_authored_params()
        return MappingProxyType(self._authored_params)

    def get_authored_parameter(self, name: str, default: Any = None) -> Any:
        self._sync_authored_params()
        return self._authored_params.get(name, default)

    def authored_params_snapshot(self) -> Dict[str, Any]:
        self._sync_authored_params()
        return dict(self._authored_params)

    @property
    def effective_params(self) -> Mapping[str, Any]:
        self._sync_authored_params()
        return MappingProxyType(dict(self.params))

    def _sync_authored_params(self) -> None:
        # Many legacy plugins replace ``self.params`` after ``super().__init__``.
        if self._presentation_context is None and self.params is not self._authored_params:
            self._authored_params = dict(self.params)
            self.params = self._authored_params

    def on_presentation_context_changed(self, old: Any, new: Any) -> None:
        del old, new

    @abstractmethod
    def generate_frame(self, time_elapsed: float, frame_count: int) -> FrameOutput:
        raise NotImplementedError

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = {
            "speed": {"type": "float", "min": 0.1, "max": 5.0, "default": 1.0},
            "brightness": {"type": "float", "min": 0.0, "max": 1.0, "default": 1.0},
            "color_saturation": {"type": "float", "min": 0.0, "max": 1.0, "default": 1.0},
            "color_value": {"type": "float", "min": 0.0, "max": 1.0, "default": 1.0},
        }
        schema.update(plant_parameter_schema())
        return schema

    def update_parameters(self, new_params: Dict[str, Any]) -> None:
        self._sync_authored_params()
        if "plant_modifiers" in new_params:
            new_params = dict(new_params)
            new_params["plant_modifiers"] = PlantModifierState.from_payload(
                new_params["plant_modifiers"]
            ).to_dict()
        self._authored_params.update(new_params)
        if self.params is not self._authored_params:
            self.params.update(new_params)
        if {"plant_aware", "plant_modifiers"} & new_params.keys():
            self._plant_modifier_state = self._resolve_plant_modifier_state()
            self._framework_modifier_cached_frame = None
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

    def _resolve_plant_modifier_state(self) -> PlantModifierState:
        state = PlantModifierState.from_payload(self.params.get("plant_modifiers"))
        if state.active or not self.params.get("plant_aware", False):
            return state
        return PlantModifierState.from_legacy(True)

    def plant_modifier_enabled(self, modifier: str) -> bool:
        supported = self.PLANT_MODIFIER_SUPPORT | FRAMEWORK_VISUAL_MODIFIERS
        return self._plant_modifier_state.enabled(modifier, supported)

    def plant_modifier_strength(self, modifier: str) -> float:
        supported = self.PLANT_MODIFIER_SUPPORT | FRAMEWORK_VISUAL_MODIFIERS
        return self._plant_modifier_state.strength(modifier, supported)

    @staticmethod
    def _quantize_q8_8(value: float, maximum: int) -> int:
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0.0:
            raise ValueError("plant modifier strength must be finite and non-negative")
        quantized = math.floor(numeric * 256 + 0.5)
        if quantized > maximum:
            raise ValueError("plant modifier strength overflows Q8.8")
        return quantized

    def framework_plant_modifiers_active(self) -> bool:
        return (
            self._quantize_q8_8(
                self.plant_modifier_strength("hue_shift"), HUE_STRENGTH_MAX
            ) > 0
            or self.plant_modifier_strength("liquid_glass") > 0.0
        )

    def framework_plant_modifier_refresh_pending(self) -> bool:
        return (
            self.framework_plant_modifiers_active()
            and self._framework_modifier_cached_frame is None
        )

    def apply_framework_plant_modifiers(
        self, pixels: np.ndarray, *, changed: bool = True
    ) -> np.ndarray:
        """Apply the host's universal calibrated hue/glass optics exactly once."""
        hue_strength_q8_8 = self._quantize_q8_8(
            self.plant_modifier_strength("hue_shift"), HUE_STRENGTH_MAX
        )
        liquid_glass = self.plant_modifier_strength("liquid_glass")
        if hue_strength_q8_8 == 0 and liquid_glass <= 0.0:
            return pixels
        if not changed and self._framework_modifier_cached_frame is not None:
            return self._framework_modifier_cached_frame

        array = np.asarray(pixels)
        expected = self.get_pixel_count()
        if array.shape != (expected, 3):
            raise ValueError(
                f"framework plant modifiers require shape ({expected}, 3), got {array.shape}"
            )
        if self._framework_modifier_geometry != expected:
            self._framework_modifier_buffers = [
                np.empty((expected, 3), dtype=np.uint8) for _ in range(2)
            ]
            self._framework_modifier_buffer_index = 0
            self._framework_modifier_geometry = expected

        output = self._framework_modifier_buffers[self._framework_modifier_buffer_index]
        self._framework_modifier_buffer_index = (
            self._framework_modifier_buffer_index + 1
        ) % len(self._framework_modifier_buffers)
        np.copyto(output, array, casting="unsafe")

        masks = self.get_plant_masks()
        apply_hue_shift_u8(output, hue_strength_q8_8, masks.obstacle_flat)

        if liquid_glass > 0.0:
            width, height = self.get_strip_info()
            source = output.reshape(width, height, 3).copy()
            distance = masks.distance
            radius = 1.5 + 4.5 * liquid_glass
            glass = distance <= radius
            if np.any(glass):
                x, y = np.indices((width, height))
                displacement = 1 + int(round(2.0 * liquid_glass))
                sample_x = np.clip(
                    x + np.rint(masks.normal_x * displacement).astype(np.int16),
                    0, width - 1,
                )
                sample_y = np.clip(
                    y + np.rint(masks.normal_y * displacement).astype(np.int16),
                    0, height - 1,
                )
                sampled = source[sample_x, sample_y]
                weight = (
                    np.exp(-distance / max(0.5, radius * 0.7))
                    * (0.2 + 0.55 * liquid_glass)
                )[..., None]
                logical = output.reshape(width, height, 3)
                blended = source * (1.0 - weight) + sampled * weight
                logical[glass] = np.clip(
                    blended[glass], 0.0, 255.0
                ).astype(np.uint8)
                highlight = masks.obstacle_edge
                if np.any(highlight):
                    lifted = logical[highlight].astype(np.float32)
                    lifted += 18.0 + 34.0 * liquid_glass
                    logical[highlight] = np.clip(
                        lifted, 0.0, 255.0
                    ).astype(np.uint8)

        self._framework_modifier_cached_frame = output
        return output

    def get_plant_masks(self, clearance: Optional[int] = None) -> Any:
        return self._plant_mask_cache.get(clearance)

    def get_info(self) -> Dict[str, Any]:
        state = self.plant_modifier_state()
        supported_set = self.PLANT_MODIFIER_SUPPORT | FRAMEWORK_VISUAL_MODIFIERS
        return {
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "version": self.version,
            "parameters": self.get_parameter_schema(),
            "current_params": self.authored_params_snapshot(),
            "plant_modifier_support": sorted(supported_set),
            "interaction_types": sorted(self.INTERACTION_TYPES),
            "unsupported_plant_modifiers": [
                modifier for modifier in state.active if modifier not in supported_set
            ],
        }

    def handle_interaction(
        self, kind: str, x: float, y: float, strength: float = 1.0
    ) -> bool:
        del kind, x, y, strength
        return False

    def get_runtime_stats(self) -> Dict[str, Any]:
        return {}

    def start(self) -> None:
        self.start_time = time.time()
        self.frame_count = 0
        self.is_running = True

    def stop(self) -> None:
        self.is_running = False

    def cleanup(self) -> None:
        self.stop()

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
    """Host-compatible stateful base; previews never start its device thread."""

    def __init__(self, controller: Any, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.animation_thread = None
        self.stop_event = threading.Event()

    @abstractmethod
    def run_animation(self) -> None:
        raise NotImplementedError

    def generate_frame(self, time_elapsed: float, frame_count: int) -> FrameOutput:
        del time_elapsed, frame_count
        return np.zeros((self.controller.total_leds, 3), dtype=np.uint8)

    def start(self) -> None:
        # Browser previews are pull-rendered and deliberately never spawn a
        # stateful hardware loop. Preserve lifecycle state for compatibility.
        AnimationBase.start(self)

    def stop(self) -> None:
        AnimationBase.stop(self)
        self.stop_event.set()


__all__ = [
    "AnimationBase", "FrameOutput", "PlantModifierState", "RenderedFrame",
    "StatefulAnimationBase",
]
