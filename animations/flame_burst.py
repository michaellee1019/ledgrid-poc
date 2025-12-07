#!/usr/bin/env python3
"""
Flame Burst Animation Plugin

A vibrant radial burst that ignites from the center of the grid and
pushes outward in hot gradients.
"""

import math
from typing import List, Tuple, Dict, Any
from animation_system import AnimationBase


class FlameBurstAnimation(AnimationBase):
    """Energetic flame burst expanding from the grid center"""

    ANIMATION_NAME = "Flame Burst"
    ANIMATION_DESCRIPTION = "Radial flame burst from the strip center with hot gradients and flicker"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.default_params.update({
            'speed': 1.0,            # Multiplier for burst speed
            'burst_rate': 0.9,       # Bursts per second
            'shell_thickness': 0.22, # Width of the expanding wave (normalized 0-1)
            'flicker': 0.35,         # Extra flicker energy
            'afterglow': 0.35        # How much heat lingers behind the front
        })

        self.params = {**self.default_params, **self.config}

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
            }
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        strip_count, leds_per_strip = self.get_strip_info()

        # Grid geometry
        center_x = (strip_count - 1) / 2.0
        center_y = (leds_per_strip - 1) / 2.0
        max_distance = math.hypot(center_x, center_y) or 1.0

        # Animation parameters
        speed = self.params.get('speed', 1.0)
        burst_rate = self.params.get('burst_rate', 0.9)
        shell_thickness = max(self.params.get('shell_thickness', 0.22), 0.01)
        flicker_amount = self.params.get('flicker', 0.35)
        afterglow = self.params.get('afterglow', 0.35)

        saturation_boost = self.params.get('color_saturation', 1.0)
        value_boost = self.params.get('color_value', 1.0)

        # Burst timing
        cycle = time_elapsed * burst_rate * speed
        phase = cycle % 1.0                # 0 → 1 over a burst
        radius = phase                     # Normalized radius across the grid
        envelope = math.sin(phase * math.pi)  # Ease in/out for each burst

        pixel_colors = []

        for strip in range(strip_count):
            for led in range(leds_per_strip):
                dx = strip - center_x
                dy = led - center_y
                distance_norm = math.hypot(dx, dy) / max_distance

                # Strong shell at the moving front plus a molten core and trailing heat
                shell = math.exp(-((distance_norm - radius) / shell_thickness) ** 2 * 2.5)
                core = math.exp(-distance_norm * 3.2) * (0.6 + 0.4 * phase)
                glow = max(0.0, 1.0 - distance_norm) * afterglow * phase

                intensity = (shell * 1.2 + core + glow) * envelope

                # Add lively flicker tied to position and time
                flicker_phase = time_elapsed * 18.0 + strip * 0.8 + led * 0.35
                flicker = (math.sin(flicker_phase) + math.sin(flicker_phase * 0.7 + 3.1)) * 0.5
                intensity += max(flicker, 0.0) * flicker_amount * (shell + 0.5 * core)
                intensity = max(0.0, min(intensity, 1.0))

                # Map intensity to a hot palette: deep red → amber → white-hot
                hue = (0.02 + 0.1 * intensity) % 1.0
                saturation = min(1.0, 0.75 + 0.25 * intensity) * saturation_boost
                value = min(1.0, 0.45 + 0.55 * intensity) * value_boost

                color = self.hsv_to_rgb(hue, min(saturation, 1.0), min(value, 1.0))
                color = self.apply_brightness(color)
                pixel_colors.append(color)

        return pixel_colors
