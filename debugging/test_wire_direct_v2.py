#!/usr/bin/env python3
"""
Test if the wires are physically working by manually toggling GPIO pins
This version properly releases pins from SPI first
"""

import os
import time

def gpio_export(pin):
    """Export a GPIO pin for use"""
    if not os.path.exists(f'/sys/class/gpio/gpio{pin}'):
        try:
            with open('/sys/class/gpio/export', 'w') as f:
                f.write(str(pin))
            time.sleep(0.1)
        except:
            pass

def gpio_unexport(pin):
    """Unexport a GPIO pin"""
    if os.path.exists(f'/sys/class/gpio/gpio{pin}'):
        try:
            with open('/sys/class/gpio/unexport', 'w') as f:
                f.write(str(pin))
            time.sleep(0.1)
        except:
            pass

def gpio_set_direction(pin, direction):
    """Set GPIO direction (in/out)"""
    try:
        with open(f'/sys/class/gpio/gpio{pin}/direction', 'w') as f:
            f.write(direction)
    except Exception as e:
        print(f"Error setting direction for GPIO {pin}: {e}")

def gpio_write(pin, value):
    """Write value to GPIO"""
    try:
        with open(f'/sys/class/gpio/gpio{pin}/value', 'w') as f:
            f.write('1' if value else '0')
    except Exception as e:
        print(f"Error writing to GPIO {pin}: {e}")

def main():
    print("=== Direct Wire Test (Using sysfs GPIO) ===\n")
    print("This will manually toggle the GPIO pins to test wire connectivity.")
    print("Watch the SCORPIO serial monitor for GPIO state changes.\n")
    
    # SPI pins
    SCK_PIN = 11   # Physical pin 23 -> SCORPIO GPIO 14
    MOSI_PIN = 10  # Physical pin 19 -> SCORPIO GPIO 12
    CS_PIN = 8     # Physical pin 24 -> SCORPIO GPIO 13
    
    pins = [SCK_PIN, MOSI_PIN, CS_PIN]
    
    print("Step 1: Releasing pins from any previous control...")
    for pin in pins:
        gpio_unexport(pin)
    time.sleep(0.5)
    
    print("Step 2: Exporting and configuring pins...")
    for pin in pins:
        gpio_export(pin)
        gpio_set_direction(pin, 'out')
    
    # Initialize
    gpio_write(SCK_PIN, False)
    gpio_write(MOSI_PIN, False)
    gpio_write(CS_PIN, True)  # CS is active LOW
    
    time.sleep(0.5)
    
    print("\n" + "="*60)
    print("Starting manual toggle test...")
    print("="*60 + "\n")
    
    print("Test 1: Toggle SCK (GPIO 11 -> SCORPIO GPIO 14)")
    print("  Watch SCORPIO serial monitor for 'SCK=' state changes")
    print("  Current debug line format: [DEBUG] SPI SR: ... | CS=...")
    print("  The periodic debug should show SCK changing!\n")
    
    for i in range(10):
        gpio_write(SCK_PIN, True)
        print(f"  [{i+1}] SCK -> HIGH", flush=True)
        time.sleep(1.0)  # Longer delay to see in debug output
        gpio_write(SCK_PIN, False)
        print(f"  [{i+1}] SCK -> LOW", flush=True)
        time.sleep(1.0)
    
    print("\n" + "-"*60 + "\n")
    
    print("Test 2: Toggle MOSI (GPIO 10 -> SCORPIO GPIO 12)")
    print("  Watch SCORPIO serial monitor for MOSI state changes\n")
    
    for i in range(10):
        gpio_write(MOSI_PIN, True)
        print(f"  [{i+1}] MOSI -> HIGH", flush=True)
        time.sleep(1.0)
        gpio_write(MOSI_PIN, False)
        print(f"  [{i+1}] MOSI -> LOW", flush=True)
        time.sleep(1.0)
    
    print("\n" + "-"*60 + "\n")
    
    print("Test 3: Toggle CS (GPIO 8 -> SCORPIO GPIO 13)")
    print("  Watch SCORPIO serial monitor for '[CS] Asserted/Released'\n")
    
    for i in range(5):
        gpio_write(CS_PIN, False)  # Assert (active LOW)
        print(f"  [{i+1}] CS -> LOW (asserted)", flush=True)
        time.sleep(1.0)
        gpio_write(CS_PIN, True)  # Release
        print(f"  [{i+1}] CS -> HIGH (released)", flush=True)
        time.sleep(1.0)
    
    print("\n" + "="*60)
    print("\n✓ Test complete!")
    print("\nResults to check on SCORPIO serial monitor:")
    print("  - SCK should show in periodic debug: [DEBUG] SPI SR: ... | CS=...")
    print("  - If SCK never changed: wire is faulty or wrong pin")
    print("  - If MOSI never changed: wire is faulty or wrong pin")
    print("  - If no '[CS] Asserted': wire is faulty or wrong pin")
    
    # Clean up
    print("\nCleaning up...")
    for pin in pins:
        gpio_unexport(pin)
    
    print("\nIMPORTANT: You may need to reboot the Pi to restore SPI functionality!")

if __name__ == "__main__":
    try:
        main()
    except PermissionError:
        print("\n✗ Permission Error!")
        print("Run with sudo: sudo python3 test_wire_direct_v2.py")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()





