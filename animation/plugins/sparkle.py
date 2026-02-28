#!/usr/bin/env python3
"""
Sparkle Animation Plugin

Keeps only the sparkle effect from the previous effects collection so it can
stand alone as a single plugin.
"""

import numpy as np
from typing import Dict, Any
from animation import AnimationBase


class SparkleAnimation(AnimationBase):
    """Random sparkle effect over a dim base color"""

    ANIMATION_NAME = "Sparkle"
    ANIMATION_DESCRIPTION = "Random sparkling lights effect"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.1"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.default_params.update({
            'base_red': 34,
            'base_green': 22,
            'base_blue': 8,
            'sparkle_red': 255,
            'sparkle_green': 200,
            'sparkle_blue': 80,
            'sparkle_probability': 0.02,
            'fade_speed': 0.9,
        })

        self.params = {**self.default_params, **self.config}

        total_pixels = self.get_pixel_count()
        self.sparkle_brightness = np.zeros(total_pixels, dtype=np.float32)

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'base_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 30, 'description': 'Base color red'},
            'base_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 18, 'description': 'Base color green'},
            'base_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 4, 'description': 'Base color blue'},
            'sparkle_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Sparkle color red'},
            'sparkle_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 200, 'description': 'Sparkle color green'},
            'sparkle_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 80, 'description': 'Sparkle color blue'},
            'sparkle_probability': {'type': 'float', 'min': 0.001, 'max': 0.1, 'default': 0.02, 'description': 'Sparkle probability'},
            'fade_speed': {'type': 'float', 'min': 0.5, 'max': 0.99, 'default': 0.9, 'description': 'Fade speed'},
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        """Generate sparkle frame. Returns (N,3) uint8 ndarray."""
        total_pixels = self.get_pixel_count()

        base = np.array([
            self.params.get('base_red', 0),
            self.params.get('base_green', 0),
            self.params.get('base_blue', 20),
        ], dtype=np.float32)

        sparkle = np.array([
            self.params.get('sparkle_red', 255),
            self.params.get('sparkle_green', 255),
            self.params.get('sparkle_blue', 255),
        ], dtype=np.float32)

        sparkle_prob = self.params.get('sparkle_probability', 0.02)
        fade_speed = self.params.get('fade_speed', 0.9)

        if self.sparkle_brightness.shape[0] != total_pixels:
            self.sparkle_brightness = np.zeros(total_pixels, dtype=np.float32)

        self.sparkle_brightness *= fade_speed
        mask = np.random.random(total_pixels) < sparkle_prob
        self.sparkle_brightness[mask] = 1.0

        b = self.sparkle_brightness[:, np.newaxis]  # (N, 1)
        colors = base * (1.0 - b) + sparkle * b

        result = np.clip(colors, 0, 255).astype(np.uint8)
        return self.apply_brightness_array(result)
