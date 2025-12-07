#!/usr/bin/env python3
"""
Sparkle Animation Plugin

Keeps only the sparkle effect from the previous effects collection so it can
stand alone as a single plugin.
"""

import random
from typing import List, Tuple, Dict, Any
from animation_system import AnimationBase


class SparkleAnimation(AnimationBase):
    """Random sparkle effect over a dim base color"""

    ANIMATION_NAME = "Sparkle"
    ANIMATION_DESCRIPTION = "Random sparkling lights effect"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.1"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.default_params.update({
            'base_red': 0,
            'base_green': 0,
            'base_blue': 20,
            'sparkle_red': 255,
            'sparkle_green': 255,
            'sparkle_blue': 255,
            'sparkle_probability': 0.02,
            'fade_speed': 0.9,
        })

        self.params = {**self.default_params, **self.config}

        # Initialize sparkle state
        total_pixels = self.get_pixel_count()
        self.sparkle_brightness = [0.0] * total_pixels

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'base_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 0, 'description': 'Base color red'},
            'base_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 0, 'description': 'Base color green'},
            'base_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 20, 'description': 'Base color blue'},
            'sparkle_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Sparkle color red'},
            'sparkle_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Sparkle color green'},
            'sparkle_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Sparkle color blue'},
            'sparkle_probability': {'type': 'float', 'min': 0.001, 'max': 0.1, 'default': 0.02, 'description': 'Sparkle probability'},
            'fade_speed': {'type': 'float', 'min': 0.5, 'max': 0.99, 'default': 0.9, 'description': 'Fade speed'},
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        """Generate sparkle frame"""
        total_pixels = self.get_pixel_count()

        base_color = (
            self.params.get('base_red', 0),
            self.params.get('base_green', 0),
            self.params.get('base_blue', 20),
        )

        sparkle_color = (
            self.params.get('sparkle_red', 255),
            self.params.get('sparkle_green', 255),
            self.params.get('sparkle_blue', 255),
        )

        sparkle_prob = self.params.get('sparkle_probability', 0.02)
        fade_speed = self.params.get('fade_speed', 0.9)

        # Update sparkle state
        for i in range(total_pixels):
            # Fade existing sparkles
            self.sparkle_brightness[i] *= fade_speed

            # Add new sparkles randomly
            if random.random() < sparkle_prob:
                self.sparkle_brightness[i] = 1.0

        # Generate colors
        pixel_colors = []
        for i in range(total_pixels):
            brightness = self.sparkle_brightness[i]

            # Interpolate between base and sparkle color
            r = int(base_color[0] * (1 - brightness) + sparkle_color[0] * brightness)
            g = int(base_color[1] * (1 - brightness) + sparkle_color[1] * brightness)
            b = int(base_color[2] * (1 - brightness) + sparkle_color[2] * brightness)

            color = self.apply_brightness((r, g, b))
            pixel_colors.append(color)

        return pixel_colors
