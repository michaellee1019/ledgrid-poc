#!/usr/bin/env python3
"""
Solid Color Animation Plugin

Simple solid color animation with optional breathing effect.
"""

import math
from typing import List, Tuple, Dict, Any
from animation import AnimationBase


class SolidColorAnimation(AnimationBase):
    """Solid color animation with optional breathing effect"""
    
    ANIMATION_NAME = "Solid Color"
    ANIMATION_DESCRIPTION = "Display a solid color across all LEDs with optional breathing effect"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    
    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        
        self.default_params.update({
            'red': 255,
            'green': 0,
            'blue': 0,
            'breathing': False,
            'breathing_speed': 1.0,
            'min_brightness': 0.1,
            'max_brightness': 1.0
        })
        
        self.params = {**self.default_params, **self.config}
    
    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'red': {
                'type': 'int',
                'min': 0,
                'max': 255,
                'default': 255,
                'description': 'Red component (0-255)'
            },
            'green': {
                'type': 'int',
                'min': 0,
                'max': 255,
                'default': 0,
                'description': 'Green component (0-255)'
            },
            'blue': {
                'type': 'int',
                'min': 0,
                'max': 255,
                'default': 0,
                'description': 'Blue component (0-255)'
            },
            'breathing': {
                'type': 'bool',
                'default': False,
                'description': 'Enable breathing effect'
            },
            'breathing_speed': {
                'type': 'float',
                'min': 0.1,
                'max': 5.0,
                'default': 1.0,
                'description': 'Breathing effect speed'
            },
            'min_brightness': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 0.1,
                'description': 'Minimum brightness for breathing'
            },
            'max_brightness': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 1.0,
                'description': 'Maximum brightness for breathing'
            }
        })
        return schema
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        """Generate solid color frame"""
        total_pixels = self.get_pixel_count()
        
        # Get base color
        r = self.params.get('red', 255)
        g = self.params.get('green', 0)
        b = self.params.get('blue', 0)
        
        # Apply breathing effect if enabled
        if self.params.get('breathing', False):
            breathing_speed = self.params.get('breathing_speed', 1.0)
            min_brightness = self.params.get('min_brightness', 0.1)
            max_brightness = self.params.get('max_brightness', 1.0)
            
            # Calculate breathing brightness using sine wave
            breathing_phase = time_elapsed * breathing_speed * 2 * math.pi
            breathing_factor = (math.sin(breathing_phase) + 1) / 2  # 0-1
            brightness = min_brightness + (max_brightness - min_brightness) * breathing_factor
            
            r = int(r * brightness)
            g = int(g * brightness)
            b = int(b * brightness)
        
        # Apply global brightness
        color = self.apply_brightness((r, g, b))
        
        # Return same color for all pixels
        return [color] * total_pixels


class GradientAnimation(AnimationBase):
    """Gradient animation between two colors"""
    
    ANIMATION_NAME = "Color Gradient"
    ANIMATION_DESCRIPTION = "Smooth gradient between two colors across the strips"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    
    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        
        self.default_params.update({
            'color1_red': 255,
            'color1_green': 0,
            'color1_blue': 0,
            'color2_red': 0,
            'color2_green': 0,
            'color2_blue': 255,
            'direction': 'horizontal',  # 'horizontal' or 'vertical'
            'animated': False,
            'animation_speed': 1.0
        })
        
        self.params = {**self.default_params, **self.config}
    
    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'color1_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'First color red'},
            'color1_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 0, 'description': 'First color green'},
            'color1_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 0, 'description': 'First color blue'},
            'color2_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 0, 'description': 'Second color red'},
            'color2_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 0, 'description': 'Second color green'},
            'color2_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Second color blue'},
            'direction': {'type': 'str', 'default': 'horizontal', 'description': 'Gradient direction'},
            'animated': {'type': 'bool', 'default': False, 'description': 'Animate gradient'},
            'animation_speed': {'type': 'float', 'min': 0.1, 'max': 5.0, 'default': 1.0, 'description': 'Animation speed'}
        })
        return schema
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        """Generate gradient frame"""
        strip_count, leds_per_strip = self.get_strip_info()
        
        # Get colors
        color1 = (
            self.params.get('color1_red', 255),
            self.params.get('color1_green', 0),
            self.params.get('color1_blue', 0)
        )
        color2 = (
            self.params.get('color2_red', 0),
            self.params.get('color2_green', 0),
            self.params.get('color2_blue', 255)
        )
        
        direction = self.params.get('direction', 'horizontal')
        animated = self.params.get('animated', False)
        animation_speed = self.params.get('animation_speed', 1.0)
        
        pixel_colors = []
        
        for strip in range(strip_count):
            for led in range(leds_per_strip):
                if direction == 'horizontal':
                    # Gradient along strip length
                    t = led / max(leds_per_strip - 1, 1)
                else:
                    # Gradient across strips
                    t = strip / max(strip_count - 1, 1)
                
                # Apply animation offset if enabled
                if animated:
                    offset = time_elapsed * animation_speed
                    t = (t + offset) % 1.0
                
                # Interpolate between colors
                r = int(color1[0] * (1 - t) + color2[0] * t)
                g = int(color1[1] * (1 - t) + color2[1] * t)
                b = int(color1[2] * (1 - t) + color2[2] * t)
                
                color = self.apply_brightness((r, g, b))
                pixel_colors.append(color)
        
        return pixel_colors
