#!/usr/bin/env python3
"""
LED Controller SPI Test Animation Plugin

EXACT recreation of the working led_controller_spi.py test_strips() function
as a stateful animation that controls its own timing.
"""

import time
from typing import List, Tuple, Dict, Any
from animation import StatefulAnimationBase


class LEDControllerSPIAnimation(StatefulAnimationBase):
    """EXACT recreation of led_controller_spi.py test_strips() function"""

    ANIMATION_NAME = "LED Controller SPI Test"
    ANIMATION_DESCRIPTION = "EXACT recreation of working led_controller_spi.py test_strips()"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "3.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        # EXACT same colors as led_controller_spi.py
        self.colors = [
            (255, 0, 0),      # Red
            (255, 127, 0),    # Orange
            (255, 255, 0),    # Yellow
            (0, 255, 0),      # Green
            (0, 255, 255),    # Cyan
            (0, 0, 255),      # Blue
            (255, 0, 255),    # Magenta
        ]

        if controller and controller.debug:
            print("🔍 LED Controller SPI Animation initialized:")
            print(f"   Strips: {controller.strip_count}")
            print(f"   LEDs per strip: {controller.leds_per_strip}")
            print(f"   Total LEDs: {controller.total_leds}")

    def run_animation(self):
        """
        EXACT recreation of led_controller_spi.py test_strips() function

        This runs in its own thread and controls timing exactly like the working reference.
        Only sends LED data when there are actual changes, just like the working version.
        """
        print("🚀 LED Controller SPI Animation: run_animation() started!")
        print(f"🚀 Controller debug flag: {self.controller.debug if self.controller else 'None'}")
        print("Testing each strip individually...")

        # Create pixel buffer - exactly like the working version
        pixel_buffer = [(0, 0, 0)] * self.controller.total_leds

        # Test each strip - exactly like the working version
        for strip in range(self.controller.strip_count):
            # Check if we should stop
            if self.stop_event.is_set():
                print(f"🛑 Animation stopped at strip {strip}")
                break

            print(f"Testing strip {strip}...")

            # Get color for this strip
            r, g, b = self.colors[strip % len(self.colors)]

            # Light up this strip - exactly like the working version
            for pixel in range(self.controller.leds_per_strip):
                pixel_index = strip * self.controller.leds_per_strip + pixel
                pixel_buffer[pixel_index] = (r, g, b)

            # Send to LEDs - exactly like the working version
            self.controller.set_all_pixels(pixel_buffer)

            # Wait 0.5 seconds - exactly like the working version
            if self.stop_event.wait(0.5):  # Returns True if stop was requested
                print(f"🛑 Animation stop requested during strip {strip}")
                break

            # Clear this strip in the local buffer for the next iteration - exactly like the working version
            for pixel in range(self.controller.leds_per_strip):
                pixel_index = strip * self.controller.leds_per_strip + pixel
                pixel_buffer[pixel_index] = (0, 0, 0)

        print("Test complete!")

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        """No configurable parameters for this test"""
        return {}
