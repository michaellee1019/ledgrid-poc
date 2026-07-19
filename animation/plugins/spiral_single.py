#!/usr/bin/env python3
"""
Single pixel spiral animation.
"""

from typing import List, Tuple, Dict, Any

import numpy as np

from animation import AnimationBase
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


class SpiralSingleAnimation(AnimationBase):
    """Illuminates one pixel at a time following a spiral path."""

    ANIMATION_NAME = "Spiral Single"
    ANIMATION_DESCRIPTION = "Single pixel spirals across the grid at maximum framerate"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.default_params.update({
            'red': 255,
            'green': 255,
            'blue': 255,
            'pixels_per_second': 200.0,
        })
        self.params = {**self.default_params, **self.config}

        self.num_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.total_pixels = self.num_strips * self.leds_per_strip

        self.spiral_indices = self._build_spiral_indices(self.num_strips, self.leds_per_strip)
        self.step_index = 0
        self._buffer_pixel = [None, None]
        self._last_output_pixel = None
        self._last_step_number = None
        self._last_frame = None

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'red': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Red component (0-255)'},
            'green': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Green component (0-255)'},
            'blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Blue component (0-255)'},
            'pixels_per_second': {
                'type': 'float', 'min': 1.0, 'max': 1000.0, 'default': 200.0,
                'description': 'Travel speed independent of render FPS',
            },
        })
        schema.pop('speed', None)
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        if self.total_pixels <= 0:
            return np.empty((0, 3), dtype=np.uint8)

        pixels_per_second = max(1.0, float(self.params.get('pixels_per_second', 200.0)))
        step_number = int(time_elapsed * pixels_per_second)
        if step_number == self._last_step_number and self._last_frame is not None:
            return self.rendered_frame(self._last_frame, changed=False)

        buffer_index = self._frame_buffer_index
        frame = self.next_frame_buffer(clear=False)
        previous_pixel = self._buffer_pixel[buffer_index]
        if previous_pixel is not None:
            frame[previous_pixel] = 0

        self.step_index = step_number % len(self.spiral_indices)
        idx = self.spiral_indices[self.step_index]
        color = (
            int(self.params.get('red', 255)),
            int(self.params.get('green', 255)),
            int(self.params.get('blue', 255)),
        )
        frame[idx] = self.apply_brightness(color)
        self._buffer_pixel[buffer_index] = idx
        self._last_step_number = step_number
        self._last_frame = frame
        dirty_pixels = {idx}
        if self._last_output_pixel is not None:
            dirty_pixels.add(self._last_output_pixel)
        self._last_output_pixel = idx
        dirty_ranges = tuple((pixel, pixel + 1) for pixel in sorted(dirty_pixels))
        return self.rendered_frame(frame, dirty_ranges=dirty_ranges)

    def _build_spiral_indices(self, width: int, height: int) -> List[int]:
        coords: List[Tuple[int, int]] = []
        left = 0
        right = width - 1
        top = 0
        bottom = height - 1

        while left <= right and top <= bottom:
            for x in range(left, right + 1):
                coords.append((x, top))
            for y in range(top + 1, bottom + 1):
                coords.append((right, y))
            if top < bottom:
                for x in range(right - 1, left - 1, -1):
                    coords.append((x, bottom))
            if left < right:
                for y in range(bottom - 1, top, -1):
                    coords.append((left, y))

            left += 1
            right -= 1
            top += 1
            bottom -= 1

        indices = []
        for x, y in coords:
            phys_led = (height - 1) - y
            idx = x * height + phys_led
            if 0 <= idx < self.total_pixels:
                indices.append(idx)

        if not indices:
            indices = [0]
        return indices
