"""Individual-pixel diagnostic chase for validating the complete wall."""

from typing import Any, Dict, Optional

import numpy as np

from animation import AnimationBase


class PixelChaseAnimation(AnimationBase):
    ANIMATION_NAME = "Pixel Chase"
    ANIMATION_DESCRIPTION = "Walks every physical LED to identify dead or misordered pixels"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "pixels_per_second": 120.0,
            "red": 255, "green": 255, "blue": 255,
        })
        self.params = {**self.default_params, **self.config}
        for unused in ("speed", "color_saturation", "color_value"):
            self.params.pop(unused, None)
        width, height = self.get_strip_info()
        self._path = np.asarray([
            strip * height + physical_led
            for strip in range(width)
            for physical_led in range(height - 1, -1, -1)
        ], dtype=np.int32)
        self._buffer_pixel = [None, None]
        self._last_output_pixel = None
        self._last_step = None
        self._last_frame = None

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        for name in ("speed", "color_saturation", "color_value"):
            schema.pop(name, None)
        schema.update({
            "pixels_per_second": {
                "type": "float", "min": 0.5, "max": 1000.0, "default": 120.0,
                "description": "Number of physical LEDs visited per second",
            },
            "red": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Pixel red"},
            "green": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Pixel green"},
            "blue": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Pixel blue"},
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int):
        if self._path.size == 0:
            return np.empty((0, 3), dtype=np.uint8)
        rate = max(0.5, float(self.params.get("pixels_per_second", 120.0)))
        step = int(max(0.0, float(time_elapsed)) * rate)
        if step == self._last_step and self._last_frame is not None:
            return self.rendered_frame(self._last_frame, changed=False)

        buffer_index = self._frame_buffer_index
        frame = self.next_frame_buffer(clear=False)
        previous = self._buffer_pixel[buffer_index]
        if previous is not None:
            frame[previous] = 0
        pixel = int(self._path[step % self._path.size])
        brightness = min(1.0, max(0.0, float(self.params.get("brightness", 1.0))))
        frame[pixel] = tuple(
            int(max(0, min(255, int(self.params.get(channel, 255)))) * brightness)
            for channel in ("red", "green", "blue")
        )
        self._buffer_pixel[buffer_index] = pixel
        self._last_step, self._last_frame = step, frame

        dirty = {pixel}
        if self._last_output_pixel is not None:
            dirty.add(self._last_output_pixel)
        self._last_output_pixel = pixel
        return self.rendered_frame(
            frame,
            dirty_ranges=tuple((index, index + 1) for index in sorted(dirty)),
        )

    def get_runtime_stats(self) -> Dict[str, Any]:
        if self._last_output_pixel is None:
            return {"pixel_index": None}
        _, height = self.get_strip_info()
        physical_led = self._last_output_pixel % height
        return {
            "pixel_index": self._last_output_pixel,
            "strip": self._last_output_pixel // height,
            "led": physical_led,
            "display_row": height - 1 - physical_led,
        }
