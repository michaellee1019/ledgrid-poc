"""Smooth two-color gradients for the LED wall."""

from typing import Any, Dict, Optional

import numpy as np

from animation import AnimationBase
from animation.libraries.mask_effects import dilate_8
from animation.libraries.spatial import normalized_axis_positions


class GradientAnimation(AnimationBase):
    ANIMATION_NAME = "Color Gradient"
    ANIMATION_DESCRIPTION = "Two-color horizontal, vertical, or diagonal gradient"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "color1_red": 255, "color1_green": 0, "color1_blue": 80,
            "color2_red": 0, "color2_green": 80, "color2_blue": 255,
            "direction": "vertical", "animated": False, "speed": 0.15,
            "plant_contour_strength": 0.85,
        })
        self.params = {**self.default_params, **self.config}
        self.params.pop("color_saturation", None)
        self.params.pop("color_value", None)
        self._plant_position_cache: Dict[tuple, np.ndarray] = {}
        self._scratch = np.empty((self.get_pixel_count(), 3), dtype=np.float32)
        self._last_static_key = None
        self._last_frame = None

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.pop("color_saturation", None)
        schema.pop("color_value", None)
        schema["speed"].update({
            "min": -2.0, "max": 2.0, "default": 0.15,
            "description": "Animated cycles per second; negative reverses",
        })
        schema.update({
            "color1_red": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "First color red"},
            "color1_green": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "First color green"},
            "color1_blue": {"type": "int", "min": 0, "max": 255, "default": 80, "description": "First color blue"},
            "color2_red": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "Second color red"},
            "color2_green": {"type": "int", "min": 0, "max": 255, "default": 80, "description": "Second color green"},
            "color2_blue": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Second color blue"},
            "direction": {"type": "str", "default": "vertical", "description": "horizontal, vertical, or diagonal"},
            "animated": {"type": "bool", "default": False, "description": "Continuously cycle between both colors"},
            "plant_contour_strength": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 0.85,
                "description": "How strongly gradient bands contour around foliage and globe silhouettes in plant-aware mode",
            },
        })
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        super().update_parameters(new_params)
        self._last_static_key = None
        self._plant_position_cache.clear()

    def generate_frame(self, time_elapsed: float, frame_count: int):
        direction = str(self.params.get("direction", "vertical")).lower()
        if direction not in {"horizontal", "vertical", "diagonal"}:
            direction = "vertical"
        animated = bool(self.params.get("animated", False))
        plant_aware = self.plant_aware_enabled()
        first, second = self._color("color1"), self._color("color2")
        brightness = min(1.0, max(0.0, float(self.params.get("brightness", 1.0))))
        plant_key = ()
        if plant_aware:
            plant_key = (
                float(self.params.get("plant_contour_strength", 0.85)),
                int(self.params.get("plant_clearance", 1)),
                str(self.params.get("plant_mask_path", "")),
                str(self.params.get("plant_globe_mask_path", "")),
            )
        static_key = None if animated else (
            direction, tuple(first), tuple(second), brightness, plant_aware, plant_key,
        )
        if not animated and static_key == self._last_static_key and self._last_frame is not None:
            return self.rendered_frame(self._last_frame, changed=False)

        position = (
            self._plant_positions(direction)
            if plant_aware
            else self._positions(direction)
        )
        if animated:
            phase = position + float(time_elapsed) * float(self.params.get("speed", 0.15))
            blend = 0.5 - 0.5 * np.cos(phase * (2.0 * np.pi))
        else:
            blend = position
        np.multiply(second - first, blend[:, None], out=self._scratch)
        self._scratch += first
        self._scratch *= brightness
        np.clip(self._scratch, 0, 255, out=self._scratch)
        frame = self.next_frame_buffer(clear=False)
        frame[:] = self._scratch
        self._last_static_key, self._last_frame = static_key, frame
        return self.rendered_frame(frame)

    def _positions(self, direction: str) -> np.ndarray:
        width, height = self.get_strip_info()
        return normalized_axis_positions(width, height, direction)

    def _plant_positions(self, direction: str) -> np.ndarray:
        """Bend the phase field around the two calibrated semantic layers.

        Foliage holds the early part of the gradient while rooting globes hold
        the late part.  Soft, clearance-aware rings pull neighboring bands
        toward those anchors, making both silhouettes appear to shape the
        gradient rather than merely having color painted over them.
        """
        masks = self.get_plant_masks()
        strength = min(
            1.0, max(0.0, float(self.params.get("plant_contour_strength", 0.85)))
        )
        radius = min(10, max(1, int(self.params.get("plant_clearance", 1)) + 2))
        cache_key = (direction, id(masks), strength, radius)
        cached = self._plant_position_cache.get(cache_key)
        if cached is not None:
            return cached

        result = self._positions(direction).reshape(masks.obstacle.shape).copy()
        # Keep the semantic cores distinct even at strength zero: opting into
        # plant awareness should still reveal the calibrated subjects.
        # Half-cycle separation also keeps the layers visually distinct when
        # the gradient is animated through its cosine color cycle.
        layers = (
            (masks.foliage, np.float32(0.12)),
            (masks.globes, np.float32(0.62)),
        )
        for core, anchor in layers:
            if not np.any(core):
                continue
            reached = core.copy()
            frontier = core.copy()
            result[core] = anchor
            for distance in range(1, radius + 1):
                expanded = dilate_8(frontier)
                ring = expanded & ~reached
                weight = strength * (1.0 - distance / (radius + 1.0)) ** 1.5
                result[ring] += (anchor - result[ring]) * np.float32(weight)
                reached |= expanded
                frontier = expanded

        flattened = result.ravel()
        self._plant_position_cache[cache_key] = flattened
        return flattened

    def _color(self, prefix: str) -> np.ndarray:
        defaults = (255, 0, 80) if prefix == "color1" else (0, 80, 255)
        return np.asarray([
            max(0, min(255, int(self.params.get(f"{prefix}_{channel}", default))))
            for channel, default in zip(("red", "green", "blue"), defaults)
        ], dtype=np.float32)
