#!/usr/bin/env python3
"""
Simple script to verify which Raspberry Pi pins are being used for SPI
and test them one at a time
"""

import spidev
import time
import RPi.GPIO as GPIO

# Raspberry Pi SPI0 pins:
# Physical Pin 19 = GPIO 10 = MOSI
# Physical Pin 21 = GPIO  9 = MISO  
# Physical Pin 23 = GPIO 11 = SCLK
# Physical Pin 24 = GPIO  8 = CE0 (CS)

print("=" * 60)
print("Raspberry Pi SPI Pin Test")
print("=" * 60)
print("\nRaspberry Pi SPI0 Hardware Pins:")
print("  Pin 19 (GPIO 10) = MOSI  → should go to XIAO D10")
print("  Pin 23 (GPIO 11) = SCLK  → should go to XIAO D8")
print("  Pin 24 (GPIO  8) = CE0   → should go to XIAO D1")
print("  Pin 6/9/14/20/25 = GND   → should go to XIAO GND")
print("=" * 60)

# Test 1: Check if we can initialize SPI
print("\nTest 1: Initializing SPI...")
try:
    spi = spidev.SpiDev()
    spi.open(0, 0)  # Bus 0, Device 0 (CE0)
    spi.mode = 3
    spi.max_speed_hz = 1_000_000  # Start slow: 1 MHz
    print("  ✓ SPI initialized successfully")
    print(f"    Mode: {spi.mode}")
    print(f"    Speed: {spi.max_speed_hz:,} Hz")
except Exception as e:
    print(f"  ✗ Failed to initialize SPI: {e}")
    exit(1)

# Test 2: Send a simple PING command
print("\nTest 2: Sending PING command (0xFF)...")
print("  Watch ESP32 serial monitor for '📥 PING' message")
try:
    response = spi.xfer2([0xFF])  # PING command
    print(f"  ✓ Sent PING, response: {response}")
    print("  Check ESP32 monitor - did you see PING?")
except Exception as e:
    print(f"  ✗ Failed to send: {e}")

time.sleep(1)

# Test 3: Send a CLEAR command
print("\nTest 3: Sending CLEAR command (0x04)...")
print("  Watch ESP32 serial monitor for '📥 CLEAR' message")
try:
    response = spi.xfer2([0x04])  # CLEAR command
    print(f"  ✓ Sent CLEAR, response: {response}")
    print("  Check ESP32 monitor - did you see CLEAR?")
except Exception as e:
    print(f"  ✗ Failed to send: {e}")

spi.close()

print("\n" + "=" * 60)
print("Test complete!")
print("\nIf ESP32 didn't receive anything:")
print("  1. CS is likely not connected (it should idle HIGH)")
print("  2. Check wires are on correct physical pins")
print("  3. Check GND is common between RPi and ESP32")
print("=" * 60)

