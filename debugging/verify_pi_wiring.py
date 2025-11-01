#!/usr/bin/env python3
"""
Raspberry Pi Wiring Verification Script
Tests that the Pi can control its SPI GPIO pins
"""

try:
    import RPi.GPIO as GPIO
    import time
    
    print("=== Raspberry Pi SPI Pin Verification ===\n")
    print("This script will toggle each SPI pin to verify they work.")
    print("Use a multimeter or LED to check each pin.\n")
    
    # Set up GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    
    # SPI pins on Raspberry Pi
    pins = {
        'CE0 (CS)': 8,   # Physical pin 24
        'MISO': 9,       # Physical pin 21
        'MOSI': 10,      # Physical pin 19
        'SCLK': 11,      # Physical pin 23
    }
    
    print("Setting all pins as outputs...\n")
    for name, pin in pins.items():
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)
    
    print("Pin Mapping:")
    print("  GPIO 8  (CE0/CS)  = Physical Pin 24 → SCORPIO GPIO 13")
    print("  GPIO 9  (MISO)    = Physical Pin 21 → SCORPIO GPIO 15")
    print("  GPIO 10 (MOSI)    = Physical Pin 19 → SCORPIO GPIO 12")
    print("  GPIO 11 (SCLK)    = Physical Pin 23 → SCORPIO GPIO 14")
    print("\n" + "="*50 + "\n")
    
    # Test each pin
    for name, pin in pins.items():
        print(f"Testing {name} (GPIO {pin})...")
        print(f"  Toggling HIGH/LOW 5 times...")
        
        for i in range(5):
            GPIO.output(pin, GPIO.HIGH)
            print(f"  → HIGH", end='', flush=True)
            time.sleep(0.5)
            GPIO.output(pin, GPIO.LOW)
            print(f" → LOW", end='', flush=True)
            time.sleep(0.5)
        
        print(" ✓\n")
    
    print("="*50)
    print("\n✓ All pins tested!")
    print("\nNOTE: This only tests the Raspberry Pi side.")
    print("If a pin doesn't toggle as expected, check:")
    print("  1. The wire is connected to the correct physical pin")
    print("  2. The wire is not loose or damaged")
    print("  3. The SCORPIO end is connected to the correct GPIO")
    
    # Clean up
    GPIO.cleanup()
    
except ImportError:
    print("✗ Error: RPi.GPIO not installed")
    print("  Install: sudo apt-get install python3-rpi.gpio")
    print("  Or: pip3 install RPi.GPIO")
except Exception as e:
    print(f"✗ Error: {e}")
    try:
        GPIO.cleanup()
    except:
        pass





