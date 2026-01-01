#!/usr/bin/env python3
"""
Simple SPI test script - sends recognizable patterns to help debug
"""

import spidev
import time

# SPI Configuration
SPI_BUS = 0
SPI_DEVICE = 0
SPI_SPEED_HZ = 100000  # Reduced to 100 kHz for easier debugging

def test_basic_patterns():
    """Send simple recognizable patterns"""
    try:
        spi = spidev.SpiDev()
        spi.open(SPI_BUS, SPI_DEVICE)
        spi.max_speed_hz = SPI_SPEED_HZ
        spi.mode = 0b00  # SPI mode 0 (CPOL=0, CPHA=0)
        
        print(f"SPI Test initialized at {SPI_SPEED_HZ / 1000:.1f} kHz")
        print(f"Device: /dev/spidev{SPI_BUS}.{SPI_DEVICE}")
        print("\nSending test patterns...\n")
        
        patterns = [
            ([0xFF], "All ones (0xFF)"),
            ([0x00], "All zeros (0x00)"),
            ([0xAA], "Alternating bits (0xAA = 10101010)"),
            ([0x55], "Alternating bits (0x55 = 01010101)"),
            ([0x01, 0x02, 0x03, 0x04, 0x05], "Counting sequence (0x01..0x05)"),
            ([0xFF, 0x00, 0xFF, 0x00], "Alternating bytes (0xFF 0x00)"),
            ([0x12, 0x34, 0x56, 0x78, 0x9A], "Magic sequence"),
        ]
        
        for i, (data, description) in enumerate(patterns, 1):
            print(f"Test {i}: {description}")
            print(f"  Sending: {' '.join(f'0x{b:02X}' for b in data)}")
            
            # Send data
            spi.xfer2(data)
            
            # Wait for SCORPIO to process
            time.sleep(1.0)
            
            print()
        
        print("\n✓ All patterns sent!")
        print("Check SCORPIO serial monitor for received data")
        
        spi.close()
        
    except FileNotFoundError:
        print(f"✗ Error: SPI device /dev/spidev{SPI_BUS}.{SPI_DEVICE} not found")
        print("  Check if SPI is enabled: ls /dev/spidev*")
    except PermissionError:
        print(f"✗ Error: Permission denied")
        print(f"  Try: sudo python3 {__file__}")
    except Exception as e:
        print(f"✗ Error: {e}")

if __name__ == "__main__":
    test_basic_patterns()





