#!/usr/bin/env python3
"""
Rainbow Animation Plugin

Classic rainbow cycle animation that flows across all LED strips.
Based on the original rainbow_animation from led_controller_spi.py
"""

import numpy as np
from typing import Dict, Any
from animation import AnimationBase


class RainbowAnimation(AnimationBase):
    """Rainbow cycle animation flowing across all strips"""

    _COLOR_LUT_SIZE = 1536
    
    ANIMATION_NAME = "Rainbow Cycle"
    ANIMATION_DESCRIPTION = "Classic rainbow animation that cycles through all colors across the LED strips"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    
    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        
        self.hue_offset = 0.0
        
        self.default_params.update({
            'speed': 0.3,
            'span_ratio': 1.0,
            'direction': 1,
            'brightness': 1.0,
            'color_saturation': 1.0,
            'color_value': 1.0
        })
        
        self.params = {**self.default_params, **self.config}
        self._cached_geometry = None
        self._base_indices = None
        self._indices = None
        self._color_lut = None
        self._color_lut_key = None

    def _ensure_arrays(self, strip_count: int, leds_per_strip: int, span_pixels: int):
        key = (strip_count, leds_per_strip, span_pixels)
        if key == self._cached_geometry:
            return
        strip_hues = np.arange(leds_per_strip, dtype=np.float32) / span_pixels
        strip_indices = np.floor(
            np.remainder(strip_hues, 1.0) * self._COLOR_LUT_SIZE
        ).astype(np.int32)
        self._base_indices = np.tile(strip_indices, strip_count)
        self._indices = np.empty_like(self._base_indices)
        self._cached_geometry = key

    def _ensure_color_lut(self, saturation: float, value: float, brightness: float):
        key = (float(saturation), float(value), float(brightness))
        if key == self._color_lut_key:
            return

        hues = np.arange(self._COLOR_LUT_SIZE, dtype=np.float32)
        hues /= self._COLOR_LUT_SIZE
        sats = np.full_like(hues, saturation)
        vals = np.full_like(hues, value)
        self._color_lut = np.empty((self._COLOR_LUT_SIZE, 3), dtype=np.uint8)
        self.hsv_to_rgb_array(hues, sats, vals, out=self._color_lut)
        self.apply_brightness_array(self._color_lut, out=self._color_lut)
        self._color_lut_key = key
    
    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'span_ratio': {
                'type': 'float',
                'min': 0.1,
                'max': 3.0,
                'default': 1.0,
                'description': 'Rainbow span ratio (1.0 = one rainbow per strip)'
            },
            'direction': {
                'type': 'int',
                'min': -1,
                'max': 1,
                'default': 1,
                'description': 'Animation direction (1=forward, -1=reverse)'
            }
        })
        return schema
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        """Generate rainbow frame. Returns (N,3) uint8 ndarray."""
        strip_count, leds_per_strip = self.get_strip_info()
        speed = self.params.get('speed', 0.3)
        span_ratio = self.params.get('span_ratio', 1.0)
        direction = self.params.get('direction', 1)
        saturation = self.params.get('color_saturation', 1.0)
        value = self.params.get('color_value', 1.0)
        brightness = self.params.get('brightness', 1.0)
        
        span_pixels = max(int(leds_per_strip * span_ratio), 1)
        
        hue_step = 0.01 * speed * direction
        self.hue_offset += hue_step
        if self.hue_offset >= 1.0:
            self.hue_offset -= 1.0
        elif self.hue_offset < 0.0:
            self.hue_offset += 1.0
        
        self._ensure_arrays(strip_count, leds_per_strip, span_pixels)
        self._ensure_color_lut(saturation, value, brightness)
        offset = int(self.hue_offset * self._COLOR_LUT_SIZE)
        np.add(self._base_indices, offset, out=self._indices)
        np.remainder(self._indices, self._COLOR_LUT_SIZE, out=self._indices)
        result = self.next_frame_buffer(clear=False)
        np.take(self._color_lut, self._indices, axis=0, out=result)
        return result
