#!/usr/bin/env python3
"""
Rainbow Animation Plugin

Classic rainbow cycle animation that flows across all LED strips.
Based on the original rainbow_animation from led_controller_spi.py
"""

import math
import numpy as np
from typing import Dict, Any
from animation import AnimationBase


class RainbowAnimation(AnimationBase):
    """Rainbow cycle animation flowing across all strips"""
    
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
        total_pixels = self.get_pixel_count()
        
        speed = self.params.get('speed', 0.3)
        span_ratio = self.params.get('span_ratio', 1.0)
        direction = self.params.get('direction', 1)
        saturation = self.params.get('color_saturation', 1.0)
        value = self.params.get('color_value', 1.0)
        
        span_pixels = max(int(leds_per_strip * span_ratio), 1)
        
        hue_step = 0.01 * speed * direction
        self.hue_offset += hue_step
        if self.hue_offset >= 1.0:
            self.hue_offset -= 1.0
        elif self.hue_offset < 0.0:
            self.hue_offset += 1.0
        
        led_indices = np.arange(leds_per_strip, dtype=np.float32)
        strip_hues = (self.hue_offset + led_indices / span_pixels) % 1.0

        hues = np.tile(strip_hues, strip_count)
        sats = np.full(total_pixels, saturation, dtype=np.float32)
        vals = np.full(total_pixels, value, dtype=np.float32)
        
        result = self.hsv_to_rgb_array(hues, sats, vals)
        return self.apply_brightness_array(result)


class RainbowWaveAnimation(AnimationBase):
    """Rainbow wave that moves along the strips"""
    
    ANIMATION_NAME = "Rainbow Wave"
    ANIMATION_DESCRIPTION = "Rainbow wave that travels along each strip independently"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    
    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        
        self.default_params.update({
            'speed': 1.0,
            'wavelength': 0.5,
            'direction': 1,
            'brightness': 1.0,
            'color_saturation': 1.0,
            'color_value': 1.0
        })
        
        self.params = {**self.default_params, **self.config}
    
    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'wavelength': {
                'type': 'float',
                'min': 0.1,
                'max': 2.0,
                'default': 0.5,
                'description': 'Wave length as fraction of strip (0.5 = half strip)'
            },
            'direction': {
                'type': 'int',
                'min': -1,
                'max': 1,
                'default': 1,
                'description': 'Wave direction (1=forward, -1=reverse)'
            }
        })
        return schema
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        """Generate rainbow wave frame. Returns (N,3) uint8 ndarray."""
        strip_count, leds_per_strip = self.get_strip_info()
        total_pixels = strip_count * leds_per_strip
        
        speed = self.params.get('speed', 1.0)
        wavelength = self.params.get('wavelength', 0.5)
        direction = self.params.get('direction', 1)
        saturation = self.params.get('color_saturation', 1.0)
        value = self.params.get('color_value', 1.0)
        
        wave_pixels = max(int(leds_per_strip * wavelength), 1)
        phase_offset = time_elapsed * speed * direction * 2.0 * math.pi
        
        led_indices = np.arange(leds_per_strip, dtype=np.float32)
        wave_pos = (led_indices / wave_pixels * 2.0 * math.pi + phase_offset) % (2.0 * math.pi)
        strip_hues = (np.sin(wave_pos) + 1.0) * 0.5

        hues = np.tile(strip_hues, strip_count)
        sats = np.full(total_pixels, saturation, dtype=np.float32)
        vals = np.full(total_pixels, value, dtype=np.float32)
        
        result = self.hsv_to_rgb_array(hues, sats, vals)
        return self.apply_brightness_array(result)
