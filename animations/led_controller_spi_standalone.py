#!/usr/bin/env python3
"""
LED Controller SPI Test - Animation Plugin
EXACT copy of the working led_controller_spi.py test_strips() function
"""

import time
import sys
import os
import threading

# Add parent directory to path to import led_controller_spi
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from led_controller_spi import LEDController
from animation_system import StatefulAnimationBase
from led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


def test_strips_standalone():
    """EXACT copy of the working test_strips() function"""
    print("🚀 Starting standalone LED Controller SPI test...")
    
    # Create controller exactly like the working version
    controller = LEDController(
        bus=0,
        device=0,
        speed=5000000,
        mode=3,
        strips=DEFAULT_STRIP_COUNT,
        leds_per_strip=DEFAULT_LEDS_PER_STRIP,
        debug=True
    )
    
    print("🔍 Controller created successfully")
    print(f"   Strips: {controller.strip_count}")
    print(f"   LEDs per strip: {controller.leds_per_strip}")
    print(f"   Total LEDs: {controller.total_leds}")
    print(f"   Debug: {controller.debug}")
    
    # EXACT copy of test_strips() function
    if controller.debug:
        print("Testing each strip individually...")
    
    colors = [
        (255, 0, 0),      # Red
        (255, 127, 0),    # Orange  
        (255, 255, 0),    # Yellow
        (0, 255, 0),      # Green
        (0, 255, 255),    # Cyan
        (0, 0, 255),      # Blue
        (255, 0, 255),    # Magenta
    ]
    
    pixel_buffer = [(0, 0, 0)] * controller.total_leds

    for strip in range(controller.strip_count):
        if controller.debug:
            print(f"Testing strip {strip}...")
        r, g, b = colors[strip % len(colors)]

        for pixel in range(controller.leds_per_strip):
            pixel_index = strip * controller.leds_per_strip + pixel
            pixel_buffer[pixel_index] = (r, g, b)

        controller.set_all_pixels(pixel_buffer)
        time.sleep(0.5)

        # Clear this strip in the local buffer for the next iteration
        for pixel in range(controller.leds_per_strip):
            pixel_index = strip * controller.leds_per_strip + pixel
            pixel_buffer[pixel_index] = (0, 0, 0)
    
    if controller.debug:
        print("Test complete!")
    
    print("🎉 Standalone test completed successfully!")


class LEDControllerSPIAnimation(StatefulAnimationBase):
    """Animation plugin that runs the EXACT working test_strips() function"""

    ANIMATION_NAME = "LED Controller SPI Test"
    ANIMATION_DESCRIPTION = "EXACT copy of working led_controller_spi.py test_strips()"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "4.0"

    def run_animation(self):
        """Run the EXACT working test_strips() function"""
        try:
            # CRITICAL: Configure controller exactly like the working version does
            if self.controller.debug:
                print("🔍 Setting brightness to 50 (like working version)")
            self.controller.set_brightness(50)

            if self.controller.debug:
                print("🔍 Calling configure() (like working version)")
            self.controller.configure()

            # EXACT copy of test_strips() function
            if self.controller.debug:
                print("Testing each strip individually...")

            colors = [
                (255, 0, 0),      # Red
                (255, 127, 0),    # Orange
                (255, 255, 0),    # Yellow
                (0, 255, 0),      # Green
                (0, 255, 255),    # Cyan
                (0, 0, 255),      # Blue
                (255, 0, 255),    # Magenta
            ]

            pixel_buffer = [(0, 0, 0)] * self.controller.total_leds

            # Continuous loop like the working version
            while not self.stop_event.is_set():
                for strip in range(self.controller.strip_count):
                    # Check for stop request
                    if self.stop_event.is_set():
                        break

                    if self.controller.debug:
                        print(f"Testing strip {strip}...")
                    r, g, b = colors[strip % len(colors)]

                    for pixel in range(self.controller.leds_per_strip):
                        pixel_index = strip * self.controller.leds_per_strip + pixel
                        pixel_buffer[pixel_index] = (r, g, b)

                    self.controller.set_all_pixels(pixel_buffer)

                    # Use stop_event.wait() instead of time.sleep() for clean shutdown
                    if self.stop_event.wait(0.5):  # Returns True if stop was requested
                        break

                    # Clear this strip in the local buffer for the next iteration
                    for pixel in range(self.controller.leds_per_strip):
                        pixel_index = strip * self.controller.leds_per_strip + pixel
                        pixel_buffer[pixel_index] = (0, 0, 0)

                if self.controller.debug:
                    print("Test complete!")

        except Exception as e:
            print(f"❌ Animation error: {e}")


if __name__ == "__main__":
    test_strips_standalone()
