#!/usr/bin/env python3
"""
Solid Color Animation Plugin

Simple solid color animation with optional breathing effect.
"""

import math
from typing import Dict, Any
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
        self._last_frame_key = None
        self._last_frame = None
    
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
    
    def generate_frame(self, time_elapsed: float, frame_count: int):
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
        
        static_key = None
        if not self.params.get('breathing', False):
            static_key = (color, total_pixels)
            if static_key == self._last_frame_key and self._last_frame is not None:
                return self.rendered_frame(self._last_frame, changed=False)

        frame = self.next_frame_buffer(clear=False)
        frame[:] = color
        self._last_frame_key = static_key
        self._last_frame = frame
        return self.rendered_frame(frame)
