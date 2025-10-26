#!/usr/bin/env python3
"""
Test if Raspberry Pi SPI hardware is actually working
By connecting MOSI to MISO, we can test loopback
"""

import spidev
import time

spi = spidev.SpiDev()
spi.open(0, 0)
spi.mode = 0
spi.max_speed_hz = 1_000_000

print("=" * 60)
print("Raspberry Pi SPI Loopback Test")
print("=" * 60)
print("To test: Connect GPIO 10 (MOSI) to GPIO 9 (MISO) with a wire")
print("This will let us verify RPi is actually sending SPI signals")
print()
print("Current test: Sending data WITHOUT loopback connected")
print("Expected: Receive all 0x00 or 0xFF (floating MISO)")
print()

try:
    for i in range(3):
        print(f"\nTest {i+1}:")
        send_data = [0xAA, 0x55, 0xFF, 0x00, 0x12, 0x34]
        print(f"  Sending:  {[hex(x) for x in send_data]}")
        
        result = spi.xfer2(send_data)
        print(f"  Received: {[hex(x) for x in result]}")
        
        time.sleep(0.5)
    
    print("\n" + "=" * 60)
    print("If all received bytes are 0x00 or 0xFF:")
    print("  → RPi SPI is working, but MISO is floating")
    print("  → Try the test again with MOSI→MISO jumper wire")
    print()
    print("If received bytes match sent bytes (with jumper):")
    print("  → RPi SPI hardware is working perfectly!")
    print()
    print("If nothing changes or errors:")
    print("  → RPi SPI might be disabled or misconfigured")
    print("  → Run: sudo raspi-config → Interface Options → SPI → Enable")
    print("=" * 60)

except Exception as e:
    print(f"\nERROR: {e}")
    print("\nThis usually means SPI is not enabled on the Raspberry Pi!")
    print("Enable it with: sudo raspi-config")
    print("  → Interface Options → SPI → Enable")
    print("Then reboot: sudo reboot")

finally:
    spi.close()

