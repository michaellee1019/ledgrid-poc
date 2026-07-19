"""Smooth two-color gradients for the LED wall."""

from typing import Any, Dict, Optional

import numpy as np

from animation import AnimationBase


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
        })
        self.params = {**self.default_params, **self.config}
        self.params.pop("color_saturation", None)
        self.params.pop("color_value", None)
        self._position_cache: Dict[str, np.ndarray] = {}
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
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int):
        direction = str(self.params.get("direction", "vertical")).lower()
        if direction not in {"horizontal", "vertical", "diagonal"}:
            direction = "vertical"
        animated = bool(self.params.get("animated", False))
        first, second = self._color("color1"), self._color("color2")
        brightness = min(1.0, max(0.0, float(self.params.get("brightness", 1.0))))
        static_key = None if animated else (direction, tuple(first), tuple(second), brightness)
        if not animated and static_key == self._last_static_key and self._last_frame is not None:
            return self.rendered_frame(self._last_frame, changed=False)

        position = self._positions(direction)
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
        if direction in self._position_cache:
            return self._position_cache[direction]
        width, height = self.get_strip_info()
        x = np.repeat(np.linspace(0.0, 1.0, width, dtype=np.float32), height)
        y = np.tile(np.linspace(1.0, 0.0, height, dtype=np.float32), width)
        result = x if direction == "horizontal" else ((x + y) * 0.5 if direction == "diagonal" else y)
        self._position_cache[direction] = result
        return result

    def _color(self, prefix: str) -> np.ndarray:
        defaults = (255, 0, 80) if prefix == "color1" else (0, 80, 255)
        return np.asarray([
            max(0, min(255, int(self.params.get(f"{prefix}_{channel}", default))))
            for channel, default in zip(("red", "green", "blue"), defaults)
        ], dtype=np.float32)
