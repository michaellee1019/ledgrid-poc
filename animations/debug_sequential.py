#!/usr/bin/env python3
"""
Debug Sequential Animation Plugin

Turns on each LED one by one in sequence to help diagnose which strips are working.
Useful for testing LED strip connectivity and identifying dead strips or LEDs.
"""

import time
from typing import List, Tuple, Dict, Any
from animation_system import AnimationBase
from led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


class DebugSequentialAnimation(AnimationBase):
    """Sequential LED test animation for debugging"""
    
    ANIMATION_NAME = "Debug Sequential"
    ANIMATION_DESCRIPTION = "Turns on each LED one by one to test strip connectivity"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    
    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        
        # Animation parameters - much slower for better visibility and efficiency
        self.led_delay = self.config.get('led_delay', 0.2)   # 200ms delay between LEDs (5 FPS)
        self.hold_time = self.config.get('hold_time', 0.0)   # No hold time for efficiency
        self.brightness = self.config.get('brightness', 255) # LED brightness (0-255)
        self.color = self.config.get('color', (255, 255, 255))  # RGB color
        self.show_strip_info = self.config.get('show_strip_info', True)

        # State tracking
        self.current_strip = 0
        self.current_led = 0
        self.last_update = 0
        self.phase = 'lighting'  # 'lighting' or 'holding'
        self.hold_start = 0

        # Efficiency tracking - only send data when LED changes
        self.last_frame = None
        self.frame_changed = True
        
        # Get controller dimensions
        self.num_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        
        print(f"🔍 Debug Sequential Animation initialized:")
        print(f"   Strips: {self.num_strips}")
        print(f"   LEDs per strip: {self.leds_per_strip}")
        print(f"   Total LEDs: {self.num_strips * self.leds_per_strip}")
        print(f"   LED delay: {self.led_delay}s")
        print(f"   Color: RGB{self.color}")
        print(f"   Controller attributes: strip_count={getattr(controller, 'strip_count', 'N/A')}, leds_per_strip={getattr(controller, 'leds_per_strip', 'N/A')}")
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        """Return configurable parameters"""
        return {
            'led_delay': {
                'type': 'float',
                'min': 0.05,
                'max': 2.0,
                'default': 0.2,
                'description': 'Delay between LEDs (seconds) - slower is more efficient'
            },
            'hold_time': {
                'type': 'float', 
                'min': 0.0,
                'max': 2.0,
                'default': 0.1,
                'description': 'How long to keep each LED on (seconds)'
            },
            'brightness': {
                'type': 'int',
                'min': 1,
                'max': 255,
                'default': 255,
                'description': 'LED brightness (1-255)'
            },
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
                'default': 255,
                'description': 'Green component (0-255)'
            },
            'blue': {
                'type': 'int',
                'min': 0,
                'max': 255,
                'default': 255,
                'description': 'Blue component (0-255)'
            },
            'show_strip_info': {
                'type': 'bool',
                'default': True,
                'description': 'Print strip progress info'
            }
        }
    
    def update_parameters(self, params: Dict[str, Any]):
        """Update animation parameters"""
        if 'led_delay' in params:
            self.led_delay = max(0.01, float(params['led_delay']))
        if 'hold_time' in params:
            self.hold_time = max(0.0, float(params['hold_time']))
        if 'brightness' in params:
            self.brightness = max(1, min(255, int(params['brightness'])))
        
        # Update color from individual RGB components
        r = int(params.get('red', self.color[0]))
        g = int(params.get('green', self.color[1]))
        b = int(params.get('blue', self.color[2]))
        self.color = (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
        
        if 'show_strip_info' in params:
            self.show_strip_info = bool(params['show_strip_info'])
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        """Generate animation frame - only when LED changes for efficiency"""
        current_time = time.time()

        # Check if it's time to advance to next LED
        if current_time - self.last_update >= self.led_delay:
            # Mark that frame has changed
            self.frame_changed = True

            # Advance to next LED
            self._advance_to_next_led()
            self.last_update = current_time

            # Print progress info
            if self.show_strip_info:
                total_led = self.current_strip * self.leds_per_strip + self.current_led + 1
                total_leds = self.num_strips * self.leds_per_strip
                print(f"🔍 Strip {self.current_strip + 1}/{self.num_strips}, "
                      f"LED {self.current_led + 1}/{self.leds_per_strip} "
                      f"(Total: {total_led}/{total_leds})")

        # Only generate new frame if LED changed
        if self.frame_changed:
            # Initialize frame with all LEDs off (flat list)
            total_pixels = self.num_strips * self.leds_per_strip
            frame = [(0, 0, 0)] * total_pixels

            # Turn on current LED
            if self.current_strip < self.num_strips and self.current_led < self.leds_per_strip:
                # Apply brightness scaling
                r = int(self.color[0] * self.brightness / 255)
                g = int(self.color[1] * self.brightness / 255)
                b = int(self.color[2] * self.brightness / 255)

                # Calculate flat pixel index
                pixel_index = self.current_strip * self.leds_per_strip + self.current_led
                frame[pixel_index] = (r, g, b)

                # Debug output for first few LEDs and strip transitions
                if (self.current_strip == 0 and self.current_led < 10) or self.current_led == 0:
                    print(f"🔍 Lighting LED: Strip {self.current_strip}, LED {self.current_led}, Pixel {pixel_index}, Color {(r,g,b)}")

            self.last_frame = frame
            self.frame_changed = False
            return frame
        else:
            # Return cached frame if no change
            return self.last_frame if self.last_frame else [(0, 0, 0)] * (self.num_strips * self.leds_per_strip)
    
    def _advance_to_next_led(self):
        """Advance to the next LED in sequence"""
        self.current_led += 1
        
        # Move to next strip if we've reached the end of current strip
        if self.current_led >= self.leds_per_strip:
            self.current_led = 0
            self.current_strip += 1
            
            if self.show_strip_info and self.current_strip < self.num_strips:
                print(f"✅ Strip {self.current_strip - 1} completed, moving to strip {self.current_strip}")
        
        # Reset to beginning if we've gone through all LEDs
        if self.current_strip >= self.num_strips:
            self.current_strip = 0
            self.current_led = 0
            if self.show_strip_info:
                print("🔄 All strips completed, restarting sequence")
    
    def reset(self):
        """Reset animation to beginning"""
        self.current_strip = 0
        self.current_led = 0
        self.last_update = 0
        self.phase = 'lighting'
        self.hold_start = 0
        self.last_frame = None
        self.frame_changed = True
        print("🔄 Debug Sequential Animation reset")
