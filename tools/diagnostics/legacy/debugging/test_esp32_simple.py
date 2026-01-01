#!/usr/bin/env python3
"""
Simple ESP32 SPI test - send PING and CLEAR commands
"""

import spidev
import time

print("=" * 60)
print("ESP32-S3 SPI Communication Test")
print("=" * 60)

# Initialize SPI
spi = spidev.SpiDev()
spi.open(0, 0)  # Bus 0, Device 0 (CE0)
spi.mode = 3     # Mode 3 (CPOL=1, CPHA=1)
spi.max_speed_hz = 1_000_000  # Start slow: 1 MHz

print("✓ SPI initialized")
print(f"  Mode: {spi.mode}")
print(f"  Speed: {spi.max_speed_hz:,} Hz")
print()

# Test 1: Send PING (0xFF)
print("Test 1: Sending PING command (0xFF)...")
print("  → Watch ESP32 monitor for: 📥 Received 1 bytes | First byte: 0xFF")
response = spi.xfer2([0xFF])
print(f"  ✓ Sent, response: {response}")
time.sleep(0.5)

# Test 2: Send CLEAR (0x04)
print()
print("Test 2: Sending CLEAR command (0x04)...")
print("  → Watch ESP32 monitor for: 📥 Received 1 bytes | First byte: 0x04")
response = spi.xfer2([0x04])
print(f"  ✓ Sent, response: {response}")
time.sleep(0.5)

# Test 3: Send SHOW (0x03)
print()
print("Test 3: Sending SHOW command (0x03)...")
print("  → Watch ESP32 monitor for: 📥 Received 1 bytes | First byte: 0x03")
response = spi.xfer2([0x03])
print(f"  ✓ Sent, response: {response}")

spi.close()

print()
print("=" * 60)
print("Test complete!")
print()
print("Did you see messages on the ESP32 monitor?")
print("  YES → Communication is working! 🎉")
print("  NO  → Check physical wiring (especially GND)")
print("=" * 60)

