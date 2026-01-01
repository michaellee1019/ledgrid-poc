#!/usr/bin/env python3
"""
Simple test: Toggle GPIO 17 (our SCLK pin) and verify with a multimeter
"""

import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

SCLK_PIN = 17  # Physical Pin 11

GPIO.setup(SCLK_PIN, GPIO.OUT)

print("\n" + "="*60)
print("Testing Raspberry Pi GPIO 17 (Physical Pin 11)")
print("="*60)
print("\nThis pin should be connected to SCORPIO GPIO 14")
print("Toggling GPIO 17 HIGH/LOW every second")
print("Check with multimeter or watch SCORPIO serial monitor")
print("\nPress Ctrl+C to stop\n")

try:
    counter = 0
    while True:
        counter += 1
        GPIO.output(SCLK_PIN, GPIO.HIGH)
        print(f"[{counter}] GPIO 17 -> HIGH (should see ~3.3V)", end="\r")
        time.sleep(1.0)
        
        GPIO.output(SCLK_PIN, GPIO.LOW)
        print(f"[{counter}] GPIO 17 -> LOW  (should see ~0V)  ", end="\r")
        time.sleep(1.0)
        
except KeyboardInterrupt:
    print("\n\nTest stopped")
finally:
    GPIO.cleanup([SCLK_PIN])
    print("GPIO cleanup complete")





