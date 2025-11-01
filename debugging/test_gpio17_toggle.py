#!/usr/bin/env python3
"""
Ultra-simple test: Just toggle GPIO 17 and watch SCORPIO monitor
"""

import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# The NEW SCK pin (not the broken GPIO 11)
TEST_PIN = 17  # Physical Pin 11

try:
    GPIO.setup(TEST_PIN, GPIO.OUT)
    
    print("="*60)
    print("Testing GPIO 17 (Physical Pin 11)")
    print("="*60)
    print("\nWiring:")
    print("  Raspberry Pi Pin 11 (GPIO 17) --> SCORPIO GPIO 14 (SCK)")
    print("  Raspberry Pi GND --> SCORPIO GND")
    print("\nWatch SCORPIO monitor for SCK state changes!")
    print("You should see: SCK=HIGH and SCK=LOW alternating")
    print("\nToggling GPIO 17 slowly (1 second intervals)...")
    print("="*60 + "\n")
    
    for i in range(20):
        GPIO.output(TEST_PIN, GPIO.HIGH)
        print(f"[{i+1}] GPIO 17 -> HIGH")
        time.sleep(1)
        
        GPIO.output(TEST_PIN, GPIO.LOW)
        print(f"[{i+1}] GPIO 17 -> LOW")
        time.sleep(1)
    
    print("\n" + "="*60)
    print("Test complete!")
    print("="*60)
    print("\nDid you see SCK toggle on SCORPIO monitor?")
    print("  YES: Pin works! We can proceed with SPI")
    print("  NO:  Either wiring is wrong or GPIO 17 is also broken")
    
finally:
    GPIO.cleanup()





