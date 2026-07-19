#!/usr/bin/env python3
"""
Test Animation Plugin
"""

import math
from typing import List, Tuple, Dict, Any
import numpy as np
from animation import AnimationBase


class TestAnimation(AnimationBase):
    """Test animation for validation"""
    
    ANIMATION_NAME = "Test Animation"
    ANIMATION_DESCRIPTION = "Simple test animation"
    ANIMATION_AUTHOR = "Test System"
    ANIMATION_VERSION = "1.0"
    
    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self._frame = np.full((self.get_pixel_count(), 3), (255, 0, 0), dtype=np.uint8)

    def generate_frame(self, time_elapsed: float, frame_count: int):
        """Generate test frame"""
        return self.rendered_frame(self._frame, changed=frame_count == 0)
