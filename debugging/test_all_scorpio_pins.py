#!/usr/bin/env python3
"""
Test which SCORPIO GPIO pins can receive signals
We'll toggle each pin one at a time and see which ones the SCORPIO detects
"""

import RPi.GPIO as GPIO
import time

# Test each of these Pi GPIOs
TEST_PINS = {
    17: "Pin 11",  # What we're using for SCLK
    27: "Pin 13",  # What we're using for MOSI
    22: "Pin 15",  # What we're using for CS
}

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

print("\n" + "="*60)
print("SCORPIO GPIO Pin Test")
print("="*60)
print("\nThis will toggle each Raspberry Pi GPIO pin HIGH/LOW")
print("Watch the SCORPIO serial monitor to see which pins respond")
print("\nSetup instructions:")
print("  1. Connect ONE jumper wire at a time")
print("  2. Connect to DIFFERENT SCORPIO GPIOs (12, 13, 14, 15)")
print("  3. Watch SCORPIO monitor to see which respond")
print("\n" + "="*60)

for gpio_num, pin_name in TEST_PINS.items():
    GPIO.setup(gpio_num, GPIO.OUT)
    GPIO.output(gpio_num, GPIO.LOW)

try:
    while True:
        for gpio_num, pin_name in TEST_PINS.items():
            print(f"\n{'='*60}")
            print(f"Testing RPi GPIO {gpio_num} ({pin_name})")
            print(f"{'='*60}")
            print("Connect this pin to SCORPIO and watch the monitor...")
            print("Press Ctrl+C to stop\n")
            
            for cycle in range(20):
                GPIO.output(gpio_num, GPIO.HIGH)
                print(f"  [{cycle+1}] GPIO {gpio_num} -> HIGH", end="\r")
                time.sleep(0.5)
                
                GPIO.output(gpio_num, GPIO.LOW)
                print(f"  [{cycle+1}] GPIO {gpio_num} -> LOW ", end="\r")
                time.sleep(0.5)
            
            print()  # New line after cycles
            
except KeyboardInterrupt:
    print("\n\nTest interrupted")
finally:
    GPIO.cleanup()
    print("GPIO cleanup complete")





