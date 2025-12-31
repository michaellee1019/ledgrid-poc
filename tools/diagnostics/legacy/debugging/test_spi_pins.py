#!/usr/bin/env python3
"""
Test which SPI pins the Raspberry Pi is actually using
"""

import spidev
import time

spi = spidev.SpiDev()
spi.open(0, 0)  # Bus 0, Device 0 (CE0)
spi.mode = 3
spi.max_speed_hz = 1_000_000

print("=" * 60)
print("Raspberry Pi SPI Pin Test")
print("=" * 60)
print(f"SPI Device: /dev/spidev0.0")
print(f"Mode: {spi.mode}")
print(f"Speed: {spi.max_speed_hz / 1_000_000:.1f} MHz")
print()
print("This will send data on:")
print("  GPIO 10 (MOSI) - Physical pin 19")
print("  GPIO 11 (SCLK) - Physical pin 23") 
print("  GPIO 8  (CE0)  - Physical pin 24")
print()
print("Sending 10 test packets...")
print("=" * 60)

try:
    for i in range(10):
        data = [0xFF, 0xAA, 0x55, i]
        print(f"Sending packet {i+1}: {[hex(x) for x in data]}")
        result = spi.xfer2(data)
        print(f"  Response: {[hex(x) for x in result]}")
        time.sleep(0.5)
    
    print("\n" + "=" * 60)
    print("If ESP32 received nothing, check:")
    print("  1. Wiring - especially GND connection")
    print("  2. Pin mapping - BCM vs Board numbering")
    print("  3. ESP32 CS pin - should see activity on GPIO2")
    print("=" * 60)

except Exception as e:
    print(f"\nERROR: {e}")
finally:
    spi.close()

