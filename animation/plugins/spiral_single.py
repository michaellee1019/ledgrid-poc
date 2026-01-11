#!/usr/bin/env python3
"""
Single pixel spiral animation.
"""

from typing import List, Tuple, Dict, Any

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
            'serpentine': False,
        })
        self.params = {**self.default_params, **self.config}

        self.num_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.total_pixels = self.num_strips * self.leds_per_strip

        self.serpentine = bool(self.params.get('serpentine', False))
        self.spiral_indices = self._build_spiral_indices(self.num_strips, self.leds_per_strip, self.serpentine)
        self.step_index = 0

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'red': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Red component (0-255)'},
            'green': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Green component (0-255)'},
            'blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Blue component (0-255)'},
            'serpentine': {
                'type': 'bool',
                'default': False,
                'description': 'Flip every other strip to match serpentine wiring',
            },
        })
        schema['speed']['description'] = 'Ignored; animation always advances one pixel per frame'
        return schema

    def update_parameters(self, params: Dict[str, Any]):
        """Update animation parameters."""
        super().update_parameters(params)
        new_serpentine = bool(self.params.get('serpentine', False))
        if new_serpentine != self.serpentine:
            self.serpentine = new_serpentine
            self.spiral_indices = self._build_spiral_indices(
                self.num_strips, self.leds_per_strip, self.serpentine
            )
            self.step_index %= len(self.spiral_indices)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        if self.total_pixels <= 0:
            return []

        frame = [(0, 0, 0)] * self.total_pixels

        idx = self.spiral_indices[self.step_index]
        color = (
            int(self.params.get('red', 255)),
            int(self.params.get('green', 255)),
            int(self.params.get('blue', 255)),
        )
        frame[idx] = self.apply_brightness(color)

        self.step_index = (self.step_index + 1) % len(self.spiral_indices)

        return frame

    def _build_spiral_indices(self, width: int, height: int, serpentine: bool) -> List[int]:
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
            mapped_y = y if not (serpentine and (x % 2 == 1)) else (height - 1 - y)
            phys_led = (height - 1) - mapped_y
            idx = x * height + phys_led
            if 0 <= idx < self.total_pixels:
                indices.append(idx)

        if not indices:
            indices = [0]
        return indices
