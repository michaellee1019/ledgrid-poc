#!/usr/bin/env python3
"""
LED Controller SPI Test - Standalone
EXACT copy of the working led_controller_spi.py test_strips() function
"""

import time
import sys
from pathlib import Path

# Add repo root to path so drivers package can be imported.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from drivers.spi_controller import LEDController
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


def test_strips_standalone():
    """EXACT copy of the working test_strips() function"""
    print("🚀 Starting standalone LED Controller SPI test...")
    
    # Create controller exactly like the working version
    controller = LEDController(
        bus=0,
        device=0,
        speed=10000000,
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


if __name__ == "__main__":
    test_strips_standalone()
