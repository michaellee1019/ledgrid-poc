#!/usr/bin/env python3
"""
Test Animation Plugin
"""

import math
from typing import List, Tuple, Dict, Any
from animation import AnimationBase


class TestAnimation(AnimationBase):
    """Test animation for validation"""
    
    ANIMATION_NAME = "Test Animation"
    ANIMATION_DESCRIPTION = "Simple test animation"
    ANIMATION_AUTHOR = "Test System"
    ANIMATION_VERSION = "1.0"
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        """Generate test frame"""
        strip_count, leds_per_strip = self.get_strip_info()
        
        # Simple red color
        pixel_colors = []
        for strip in range(strip_count):
            for led in range(leds_per_strip):
                pixel_colors.append((255, 0, 0))  # Red
        
        return pixel_colors
