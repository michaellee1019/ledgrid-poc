#!/usr/bin/env python3
"""
Wiggle Raspberry Pi GPIO pins to verify SCORPIO connections
This will pulse each SPI pin so you can verify wiring
"""

import RPi.GPIO as GPIO
import time

# Raspberry Pi SPI pins
MOSI_PIN = 10  # GPIO 10 (Physical pin 19)
SCLK_PIN = 11  # GPIO 11 (Physical pin 23)
CE0_PIN = 8    # GPIO 8  (Physical pin 24)

print("=" * 60)
print("Raspberry Pi SPI Pin Wiggle Test")
print("=" * 60)
print("This will pulse each SPI pin so you can verify connections")
print()
print("Wiring should be:")
print("  RPi GPIO 10 (MOSI) → SCORPIO GPIO 12")
print("  RPi GPIO 11 (SCLK) → SCORPIO GPIO 14")
print("  RPi GPIO 8  (CE0)  → SCORPIO GPIO 13")
print("  RPi GND → SCORPIO GND")
print()
print("Watch SCORPIO serial output - pins should change!")
print("=" * 60)

try:
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    
    # Set all as outputs
    GPIO.setup(MOSI_PIN, GPIO.OUT)
    GPIO.setup(SCLK_PIN, GPIO.OUT)
    GPIO.setup(CE0_PIN, GPIO.OUT)
    
    # Start all HIGH
    GPIO.output(MOSI_PIN, GPIO.HIGH)
    GPIO.output(SCLK_PIN, GPIO.HIGH)
    GPIO.output(CE0_PIN, GPIO.HIGH)
    
    print("\nAll pins set HIGH for 2 seconds...")
    time.sleep(2)
    
    print("\nNow pulsing each pin individually...")
    print("(SCORPIO should see pins change)\n")
    
    for i in range(5):
        print(f"Round {i+1}/5:")
        
        # Pulse MOSI
        print("  Pulsing MOSI (GPIO 10)...")
        GPIO.output(MOSI_PIN, GPIO.LOW)
        time.sleep(0.5)
        GPIO.output(MOSI_PIN, GPIO.HIGH)
        time.sleep(0.5)
        
        # Pulse SCLK
        print("  Pulsing SCLK (GPIO 11)...")
        GPIO.output(SCLK_PIN, GPIO.LOW)
        time.sleep(0.5)
        GPIO.output(SCLK_PIN, GPIO.HIGH)
        time.sleep(0.5)
        
        # Pulse CE0
        print("  Pulsing CE0  (GPIO 8)...")
        GPIO.output(CE0_PIN, GPIO.LOW)
        time.sleep(0.5)
        GPIO.output(CE0_PIN, GPIO.HIGH)
        time.sleep(0.5)
    
    print("\n" + "=" * 60)
    print("Test complete!")
    print()
    print("Check SCORPIO serial monitor:")
    print("  - Did MOSI pin change? (should toggle 0→1→0→1...)")
    print("  - Did SCK pin change?  (should toggle 0→1→0→1...)")
    print("  - Did CS pin change?   (should toggle 1→0→1→0...)")
    print()
    print("If a pin DIDN'T change:")
    print("  → That wire is not connected or wrong pin!")
    print("=" * 60)

except KeyboardInterrupt:
    print("\n\nStopped by user")
except Exception as e:
    print(f"\nERROR: {e}")
    import traceback
    traceback.print_exc()
finally:
    GPIO.cleanup()
    print("GPIO cleaned up")

