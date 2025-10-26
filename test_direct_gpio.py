#!/usr/bin/env python3
"""
Direct GPIO test - bypasses SPI to test raw wire connection
This will prove if the physical wire is connected
"""

import os
import time
import sys

def gpio_export(pin):
    """Export a GPIO pin"""
    if not os.path.exists(f"/sys/class/gpio/gpio{pin}"):
        with open("/sys/class/gpio/export", "w") as f:
            f.write(str(pin))
        time.sleep(0.1)

def gpio_unexport(pin):
    """Unexport a GPIO pin"""
    if os.path.exists(f"/sys/class/gpio/gpio{pin}"):
        with open("/sys/class/gpio/unexport", "w") as f:
            f.write(str(pin))

def gpio_set_direction(pin, direction):
    """Set GPIO direction (in/out)"""
    with open(f"/sys/class/gpio/gpio{pin}/direction", "w") as f:
        f.write(direction)

def gpio_write(pin, value):
    """Write to GPIO"""
    with open(f"/sys/class/gpio/gpio{pin}/value", "w") as f:
        f.write(str(value))

def gpio_read(pin):
    """Read from GPIO"""
    with open(f"/sys/class/gpio/gpio{pin}/value", "r") as f:
        return int(f.read().strip())

# GPIO pins
SCLK_PIN = 11  # GPIO 11

print("=" * 60)
print("Direct GPIO Wire Test for SCLK")
print("=" * 60)
print("This tests the physical wire connection")
print(f"RPi GPIO {SCLK_PIN} <--> SCORPIO GPIO 14")
print()
print("We'll toggle GPIO 11 HIGH and LOW")
print("Watch SCORPIO serial monitor to see if GPIO 14 changes!")
print("=" * 60)

try:
    # Export and configure GPIO 11 as output
    gpio_export(SCLK_PIN)
    gpio_set_direction(SCLK_PIN, "out")
    
    print("\nNow toggling GPIO 11 slowly...")
    print("Watch SCORPIO serial monitor - GPIO 14 should change!\n")
    
    for i in range(10):
        # Set HIGH
        gpio_write(SCLK_PIN, 1)
        print(f"Round {i+1}: GPIO 11 = HIGH (1)")
        print(f"  → SCORPIO GPIO 14 should show: 1")
        time.sleep(2)
        
        # Set LOW
        gpio_write(SCLK_PIN, 0)
        print(f"Round {i+1}: GPIO 11 = LOW (0)")
        print(f"  → SCORPIO GPIO 14 should show: 0")
        time.sleep(2)
        print()
    
    print("=" * 60)
    print("Test complete!")
    print()
    print("Check SCORPIO serial output:")
    print("  ✓ If GPIO 14 toggled 0→1→0→1: Wire IS connected!")
    print("  ✗ If GPIO 14 stayed at 0: Wire NOT connected or wrong pins")
    print("=" * 60)

except PermissionError:
    print("\nERROR: Need root permissions!")
    print("Run with: sudo python3 test_direct_gpio.py")
except Exception as e:
    print(f"\nERROR: {e}")
    import traceback
    traceback.print_exc()
finally:
    try:
        gpio_unexport(SCLK_PIN)
    except:
        pass

