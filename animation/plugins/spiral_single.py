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
        })
        self.params = {**self.default_params, **self.config}

        self.num_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.total_pixels = self.num_strips * self.leds_per_strip

        self.spiral_indices = self._build_spiral_indices(self.num_strips, self.leds_per_strip)
        self.step_index = 0
        # Alternate buffers so the manager can retain the current frame for the
        # web preview while the animation prepares the next frame in place.
        self._frame_buffers = [
            np.zeros((self.total_pixels, 3), dtype=np.uint8),
            np.zeros((self.total_pixels, 3), dtype=np.uint8),
        ]
        self._buffer_pixel = [None, None]
        self._buffer_index = 0

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'red': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Red component (0-255)'},
            'green': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Green component (0-255)'},
            'blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Blue component (0-255)'},
        })
        schema['speed']['description'] = 'Ignored; animation always advances one pixel per frame'
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        if self.total_pixels <= 0:
            return np.empty((0, 3), dtype=np.uint8)

        buffer_index = self._buffer_index
        frame = self._frame_buffers[buffer_index]
        previous_pixel = self._buffer_pixel[buffer_index]
        if previous_pixel is not None:
            frame[previous_pixel] = 0

        idx = self.spiral_indices[self.step_index]
        color = (
            int(self.params.get('red', 255)),
            int(self.params.get('green', 255)),
            int(self.params.get('blue', 255)),
        )
        frame[idx] = self.apply_brightness(color)
        self._buffer_pixel[buffer_index] = idx
        self._buffer_index = 1 - buffer_index

        self.step_index = (self.step_index + 1) % len(self.spiral_indices)

        return frame

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
