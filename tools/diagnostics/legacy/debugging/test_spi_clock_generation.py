#!/usr/bin/env python3
"""
Test that Raspberry Pi SPI generates clock signals
This sends data and should cause SCK to pulse
"""

import spidev
import time

print("=" * 60)
print("Raspberry Pi SPI Clock Generation Test")
print("=" * 60)
print("This sends SPI data repeatedly at 1 MHz")
print("Watch SCORPIO serial monitor for pin changes:")
print("  - CS(13) should pulse LOW during each transfer")
print("  - SCK(14) should pulse (may be too fast to see)")
print("  - MOSI(12) should show data pattern")
print()
print("If SCK(14) stays at 0, the wire is not connected!")
print("=" * 60)

spi = spidev.SpiDev()
spi.open(0, 0)
spi.mode = 0
spi.max_speed_hz = 100_000  # Slow 100 kHz so you can see individual clocks

try:
    print("\nSending SPI data in 3 seconds...")
    print("Get ready to watch SCORPIO serial monitor!\n")
    time.sleep(3)
    
    for i in range(10):
        print(f"Transfer {i+1}/10...")
        
        # Send alternating pattern
        if i % 2 == 0:
            data = [0xFF] * 10  # All ones
            print("  Sending: 0xFF x10 (MOSI should be HIGH)")
        else:
            data = [0x00] * 10  # All zeros
            print("  Sending: 0x00 x10 (MOSI should be LOW)")
        
        result = spi.xfer2(data)
        print(f"  During this transfer, check SCORPIO:")
        print(f"    - CS(13) went LOW?")
        print(f"    - SCK(14) pulsed? (10 bytes = 80 clock pulses)")
        print(f"    - MOSI(12) was {'HIGH' if i % 2 == 0 else 'LOW'}?")
        print()
        
        time.sleep(1)
    
    print("=" * 60)
    print("Test complete!")
    print()
    print("Results from SCORPIO serial monitor:")
    print("  ✓ CS(13) toggled LOW/HIGH → CS wire is connected")
    print("  ✓ MOSI(12) changed → MOSI wire is connected")
    print("  ✓ SCK(14) pulsed → SCK wire is connected")
    print()
    print("  ✗ SCK(14) stayed at 0 → SCK wire NOT connected!")
    print("    Check: RPi GPIO 11 → SCORPIO GPIO 14")
    print("=" * 60)

except Exception as e:
    print(f"\nERROR: {e}")
    import traceback
    traceback.print_exc()
finally:
    spi.close()

