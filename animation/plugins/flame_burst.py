#!/usr/bin/env python3
"""
Flame Burst Animation Plugin

A vibrant radial burst that ignites from the center of the grid and
pushes outward in hot gradients.
"""

import math
import numpy as np
from typing import Dict, Any
from animation import AnimationBase


class FlameBurstAnimation(AnimationBase):
    """Energetic flame burst expanding from the grid center"""

    ANIMATION_NAME = "Frame Burst"
    ANIMATION_DESCRIPTION = "Radial flame burst from the strip center with hot gradients and flicker"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.1"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.default_params.update({
            'speed': 1.0,
            'burst_rate': 0.9,
            'shell_thickness': 0.22,
            'flicker': 0.35,
            'afterglow': 0.35,
            'serpentine': False,
            'visible_leds': 20,
            'center_offset_x': 0.0,
            'center_offset_y': 0.0
        })

        self.params = {**self.default_params, **self.config}

        self._cached_strip_count = None
        self._cached_leds_per_strip = None
        self._strip_coords = None
        self._led_coords = None

    def _build_coord_arrays(self, strip_count, leds_per_strip, visible_leds, serpentine):
        """Pre-compute flat pixel coordinate arrays."""
        total = strip_count * leds_per_strip
        strips = np.repeat(np.arange(strip_count, dtype=np.float32), leds_per_strip)
        leds = np.tile(np.arange(leds_per_strip, dtype=np.int32), strip_count)

        if serpentine:
            odd_strip = (strips.astype(np.int32) % 2 == 1)
            y_pos = np.where(odd_strip, leds_per_strip - 1 - leds, leds).astype(np.float32)
        else:
            y_pos = leds.astype(np.float32)

        np.clip(y_pos, 0, visible_leds - 1, out=y_pos)

        self._strip_coords = strips
        self._led_coords = y_pos
        self._flicker_strip = strips * 0.8
        self._flicker_led = leds.astype(np.float32) * 0.35
        self._cached_strip_count = strip_count
        self._cached_leds_per_strip = leds_per_strip

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'burst_rate': {
                'type': 'float',
                'min': 0.2,
                'max': 3.0,
                'default': 0.9,
                'description': 'How often a burst ignites (bursts per second)'
            },
            'shell_thickness': {
                'type': 'float',
                'min': 0.05,
                'max': 0.6,
                'default': 0.22,
                'description': 'Width of the expanding flame shell (normalized)'
            },
            'flicker': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 0.35,
                'description': 'Amount of energetic flicker in the burst'
            },
            'afterglow': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 0.35,
                'description': 'Lingering heat behind the wave front'
            },
            'serpentine': {
                'type': 'bool',
                'default': False,
                'description': 'Flip every other strip to match serpentine wiring'
            },
            'visible_leds': {
                'type': 'int',
                'min': 1,
                'max': 1000,
                'default': 20,
                'description': 'Physical LEDs per strip for geometry math'
            },
            'center_offset_x': {
                'type': 'float',
                'min': -3.0,
                'max': 3.0,
                'default': 0.0,
                'description': 'Horizontal center adjustment (strips)'
            },
            'center_offset_y': {
                'type': 'float',
                'min': -10.0,
                'max': 10.0,
                'default': 0.0,
                'description': 'Vertical center adjustment (LEDs)'
            }
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        strip_count, leds_per_strip = self.get_strip_info()
        visible_leds = max(1, min(int(self.params.get('visible_leds', leds_per_strip)), leds_per_strip))
        serpentine = bool(self.params.get('serpentine', False))

        if self._cached_strip_count != strip_count or self._cached_leds_per_strip != leds_per_strip:
            self._build_coord_arrays(strip_count, leds_per_strip, visible_leds, serpentine)

        center_x = (strip_count - 1) / 2.0 + self.params.get('center_offset_x', 0.0)
        center_y = (visible_leds - 1) / 2.0 + self.params.get('center_offset_y', 0.0)
        corners = np.array([
            [0.0, 0.0],
            [strip_count - 1.0, 0.0],
            [0.0, visible_leds - 1.0],
            [strip_count - 1.0, visible_leds - 1.0]
        ])
        max_distance = float(np.max(np.hypot(center_x - corners[:, 0], center_y - corners[:, 1]))) or 1.0

        speed = self.params.get('speed', 1.0)
        burst_rate = self.params.get('burst_rate', 0.9)
        shell_thickness = max(self.params.get('shell_thickness', 0.22), 0.01)
        flicker_amount = self.params.get('flicker', 0.35)
        afterglow = self.params.get('afterglow', 0.35)
        saturation_boost = self.params.get('color_saturation', 1.0)
        value_boost = self.params.get('color_value', 1.0)

        cycle = time_elapsed * burst_rate * speed
        phase = cycle % 1.0
        radius = phase
        envelope = math.sin(phase * math.pi)

        dx = self._strip_coords - center_x
        dy = self._led_coords - center_y
        distance_norm = np.hypot(dx, dy) / max_distance

        shell = np.exp(-((distance_norm - radius) / shell_thickness) ** 2 * 2.5)
        core = np.exp(-distance_norm * 3.2) * (0.6 + 0.4 * phase)
        glow = np.maximum(0.0, 1.0 - distance_norm) * afterglow * phase

        intensity = (shell * 1.2 + core + glow) * envelope

        flicker_phase = time_elapsed * 18.0 + self._flicker_strip + self._flicker_led
        flicker = (np.sin(flicker_phase) + np.sin(flicker_phase * 0.7 + 3.1)) * 0.5
        intensity += np.maximum(flicker, 0.0) * flicker_amount * (shell + 0.5 * core)
        np.clip(intensity, 0.0, 1.0, out=intensity)

        hue = (0.02 + 0.1 * intensity) % 1.0
        saturation = np.minimum(1.0, (0.75 + 0.25 * intensity) * saturation_boost)
        value = np.minimum(1.0, (0.45 + 0.55 * intensity) * value_boost)

        result = self.hsv_to_rgb_array(hue, saturation, value)
        return self.apply_brightness_array(result)
