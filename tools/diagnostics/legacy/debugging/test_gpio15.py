#!/usr/bin/env python3
"""
Test script to toggle GPIO on Raspberry Pi
Connect RPi GPIO 10 (Pin 19) to SCORPIO GPIO 15
This will help verify the physical connection
"""

import RPi.GPIO as GPIO
import time

# Use BCM numbering
GPIO.setmode(GPIO.BCM)

# GPIO 10 is MOSI - we'll toggle it manually
TEST_PIN = 10

# Set as output
GPIO.setup(TEST_PIN, GPIO.OUT)

print("=" * 50)
print("GPIO 10 (MOSI) Toggle Test")
print("=" * 50)
print(f"\nThis will toggle GPIO 10 (Physical Pin 19) on/off")
print(f"Connect it to SCORPIO GPIO 15")
print(f"\nWatch SCORPIO serial monitor for GPIO state changes")
print(f"Press Ctrl+C to stop\n")

try:
    count = 0
    while True:
        GPIO.output(TEST_PIN, GPIO.HIGH)
        print(f"[{count}] GPIO 10 = HIGH")
        time.sleep(1)
        
        GPIO.output(TEST_PIN, GPIO.LOW)
        print(f"[{count}] GPIO 10 = LOW")
        time.sleep(1)
        
        count += 1
        
except KeyboardInterrupt:
    print("\n\nStopped!")
finally:
    GPIO.cleanup()
    print("GPIO cleaned up")





