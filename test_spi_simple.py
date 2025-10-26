#!/usr/bin/env python3
"""
Simple SPI test to verify SCORPIO is receiving data
With MOSI tied to 3V3 (HIGH), we should receive 0xFF bytes
"""

import spidev
import time

# Create SPI object
spi = spidev.SpiDev()
spi.open(0, 0)  # Bus 0, Device 0 (CE0)

# Configure SPI
spi.mode = 3  # Mode 3 (CPOL=1, CPHA=1) - more reliable for RP2040 slave!
spi.max_speed_hz = 1_000_000  # 1 MHz for testing
spi.bits_per_word = 8

print("=" * 60)
print("Simple SPI Test")
print("=" * 60)
print(f"SPI Device: /dev/spidev0.0")
print(f"Mode: {spi.mode}")
print(f"Speed: {spi.max_speed_hz / 1_000_000:.1f} MHz")
print(f"\nWith MOSI tied to 3V3 (HIGH):")
print(f"SCORPIO should receive 0xFF for all bytes\n")

try:
    for i in range(5):
        print(f"\n--- Test {i+1} ---")
        
        # Send PING command (1 byte)
        print("Sending: [0xFF] (PING)")
        result = spi.xfer2([0xFF])
        print(f"Response: {result}")
        
        time.sleep(0.5)
        
        # Send 8 bytes
        print("Sending: [0x01, 0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0x00]")
        data = [0x01, 0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0x00]
        result = spi.xfer2(data)
        print(f"Response: {[hex(b) for b in result]}")
        
        time.sleep(1)
    
    print("\n" + "=" * 60)
    print("Check SCORPIO serial output to see what it received!")
    print("Expected: All 0xFF bytes (since MOSI is tied HIGH)")
    print("=" * 60)

except KeyboardInterrupt:
    print("\n\nStopped by user")
except Exception as e:
    print(f"\nError: {e}")
    import traceback
    traceback.print_exc()
finally:
    spi.close()
    print("SPI closed")

