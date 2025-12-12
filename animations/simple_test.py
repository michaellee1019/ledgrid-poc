#!/usr/bin/env python3
"""
Simple Test Animation Plugin

Very basic animation to test LED strip connectivity.
Lights up all LEDs in solid colors to verify which strips work.
"""

import time
from typing import List, Tuple, Dict, Any
from animation_system import AnimationBase
from led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


class SimpleTestAnimation(AnimationBase):
    """Simple test animation - solid colors"""
    
    ANIMATION_NAME = "Simple Test"
    ANIMATION_DESCRIPTION = "Solid color test for all LEDs"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    
    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        
        # Animation state
        self.color_index = 0
        self.last_change = 0
        self.change_interval = 2.0  # Change color every 2 seconds
        
        # Test colors
        self.colors = [
            (255, 0, 0),    # Red
            (0, 255, 0),    # Green  
            (0, 0, 255),    # Blue
            (255, 255, 0),  # Yellow
            (255, 0, 255),  # Magenta
            (0, 255, 255),  # Cyan
            (255, 255, 255) # White
        ]
        
        # Get controller dimensions
        self.num_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.total_leds = self.num_strips * self.leds_per_strip
        
        print(f"🔍 Simple Test Animation initialized:")
        print(f"   Strips: {self.num_strips}")
        print(f"   LEDs per strip: {self.leds_per_strip}")
        print(f"   Total LEDs: {self.total_leds}")
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        """Generate test frame"""
        current_time = time.time()
        
        # Change color every few seconds
        if current_time - self.last_change >= self.change_interval:
            self.color_index = (self.color_index + 1) % len(self.colors)
            self.last_change = current_time
            current_color = self.colors[self.color_index]
            color_name = ["Red", "Green", "Blue", "Yellow", "Magenta", "Cyan", "White"][self.color_index]
            print(f"🎨 Switching to {color_name}: RGB{current_color}")
        
        # Get current color
        current_color = self.colors[self.color_index]
        
        # Create frame with all LEDs the same color
        frame = [current_color] * self.total_leds
        
        return frame
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        """Return configurable parameters"""
        return {
            'change_interval': {
                'type': 'float',
                'min': 0.5,
                'max': 10.0,
                'default': 2.0,
                'description': 'Color change interval (seconds)'
            }
        }
    
    def update_parameters(self, params: Dict[str, Any]):
        """Update animation parameters"""
        if 'change_interval' in params:
            self.change_interval = max(0.5, float(params['change_interval']))
