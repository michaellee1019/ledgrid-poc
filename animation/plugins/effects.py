#!/usr/bin/env python3
"""
Special Effects Animation Plugins

Various special effects like sparkle, fire, and matrix rain.
"""

import math
import random
from typing import List, Tuple, Dict, Any
from animation import AnimationBase


class SparkleAnimation(AnimationBase):
    """Random sparkle effect"""
    
    ANIMATION_NAME = "Sparkle"
    ANIMATION_DESCRIPTION = "Random sparkling lights effect"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    
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
            'fade_speed': 0.9
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
            'fade_speed': {'type': 'float', 'min': 0.5, 'max': 0.99, 'default': 0.9, 'description': 'Fade speed'}
        })
        return schema
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        """Generate sparkle frame"""
        total_pixels = self.get_pixel_count()
        
        base_color = (
            self.params.get('base_red', 0),
            self.params.get('base_green', 0),
            self.params.get('base_blue', 20)
        )
        
        sparkle_color = (
            self.params.get('sparkle_red', 255),
            self.params.get('sparkle_green', 255),
            self.params.get('sparkle_blue', 255)
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


class WaveAnimation(AnimationBase):
    """Sine wave animation"""
    
    ANIMATION_NAME = "Wave"
    ANIMATION_DESCRIPTION = "Sine wave pattern moving across the strips"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    
    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        
        self.default_params.update({
            'wave_color_red': 0,
            'wave_color_green': 255,
            'wave_color_blue': 255,
            'background_red': 0,
            'background_green': 0,
            'background_blue': 20,
            'frequency': 2.0,
            'speed': 1.0,
            'amplitude': 1.0
        })
        
        self.params = {**self.default_params, **self.config}
    
    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'wave_color_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 0, 'description': 'Wave color red'},
            'wave_color_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Wave color green'},
            'wave_color_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Wave color blue'},
            'background_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 0, 'description': 'Background red'},
            'background_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 0, 'description': 'Background green'},
            'background_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 20, 'description': 'Background blue'},
            'frequency': {'type': 'float', 'min': 0.1, 'max': 10.0, 'default': 2.0, 'description': 'Wave frequency'},
            'amplitude': {'type': 'float', 'min': 0.1, 'max': 2.0, 'default': 1.0, 'description': 'Wave amplitude'}
        })
        return schema
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        """Generate wave frame"""
        strip_count, leds_per_strip = self.get_strip_info()
        
        wave_color = (
            self.params.get('wave_color_red', 0),
            self.params.get('wave_color_green', 255),
            self.params.get('wave_color_blue', 255)
        )
        
        bg_color = (
            self.params.get('background_red', 0),
            self.params.get('background_green', 0),
            self.params.get('background_blue', 20)
        )
        
        frequency = self.params.get('frequency', 2.0)
        speed = self.params.get('speed', 1.0)
        amplitude = self.params.get('amplitude', 1.0)
        
        pixel_colors = []
        
        for strip in range(strip_count):
            for led in range(leds_per_strip):
                # Calculate wave position
                x = led / leds_per_strip
                wave_phase = x * frequency * 2 * math.pi + time_elapsed * speed * 2 * math.pi
                wave_value = (math.sin(wave_phase) * amplitude + 1) / 2  # Normalize to 0-1
                
                # Interpolate between background and wave color
                r = int(bg_color[0] * (1 - wave_value) + wave_color[0] * wave_value)
                g = int(bg_color[1] * (1 - wave_value) + wave_color[1] * wave_value)
                b = int(bg_color[2] * (1 - wave_value) + wave_color[2] * wave_value)
                
                color = self.apply_brightness((r, g, b))
                pixel_colors.append(color)
        
        return pixel_colors
