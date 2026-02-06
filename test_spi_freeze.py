#!/usr/bin/env python3
"""
Minimal SPI test to diagnose where the freeze is happening
"""

import sys
import time

print("Step 1: Importing spidev...")
try:
    import spidev
    print("✓ spidev imported")
except ImportError as e:
    print(f"✗ Failed to import spidev: {e}")
    sys.exit(1)

print("\nStep 2: Creating SpiDev object...")
try:
    spi = spidev.SpiDev()
    print("✓ SpiDev object created")
except Exception as e:
    print(f"✗ Failed to create SpiDev: {e}")
    sys.exit(1)

print("\nStep 3: Opening SPI device 0.0...")
try:
    spi.open(0, 0)
    print("✓ SPI device opened")
except Exception as e:
    print(f"✗ Failed to open SPI device: {e}")
    print("\nPossible issues:")
    print("  - SPI not enabled on this Raspberry Pi")
    print("  - Another process is using SPI")
    print("  - Permission issue")
    sys.exit(1)

print("\nStep 4: Configuring SPI...")
try:
    spi.max_speed_hz = 8000000
    spi.mode = 3
    spi.bits_per_word = 8
    print("✓ SPI configured (8 MHz, Mode 3)")
except Exception as e:
    print(f"✗ Failed to configure SPI: {e}")
    spi.close()
    sys.exit(1)

print("\nStep 5: Sending test data...")
try:
    result = spi.xfer2([0xFF, 0x00, 0x00, 0x00])
    print(f"✓ Data sent: [0xFF, 0x00, 0x00, 0x00]")
    print(f"  Received: {[hex(x) for x in result]}")
except Exception as e:
    print(f"✗ Failed to send data: {e}")
    spi.close()
    sys.exit(1)

print("\nStep 6: Closing SPI...")
try:
    spi.close()
    print("✓ SPI closed")
except Exception as e:
    print(f"✗ Failed to close SPI: {e}")

print("\n✓ All tests passed! SPI is working on this Raspberry Pi")
print("\nIf this works but led_controller_spi.py freezes, the issue is in the script logic.")

