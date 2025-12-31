#!/usr/bin/env python3
"""
I2C Scanner - Detect all I2C devices on the bus
"""

import sys
from smbus2 import SMBus

def scan_i2c_bus(bus_number):
    """Scan I2C bus for devices"""
    print(f"Scanning I2C bus {bus_number}...")
    print("     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f")
    
    found_devices = []
    
    try:
        bus = SMBus(bus_number)
        
        for row in range(0, 128, 16):
            print(f"{row:02x}: ", end="")
            for col in range(16):
                addr = row + col
                if addr < 0x03 or addr > 0x77:
                    print("   ", end="")
                    continue
                
                try:
                    bus.read_byte(addr)
                    print(f"{addr:02x} ", end="")
                    found_devices.append(addr)
                except:
                    print("-- ", end="")
            print()
        
        bus.close()
        
        if found_devices:
            print(f"\nFound {len(found_devices)} device(s):")
            for addr in found_devices:
                print(f"  0x{addr:02X} ({addr})")
        else:
            print("\nNo I2C devices found!")
            print("\nTroubleshooting:")
            print("1. Check if I2C is enabled: ls /dev/i2c-*")
            print("2. Check connections (SDA, SCL, GND, power)")
            print("3. Verify SCORPIO board is powered and running firmware")
            print("4. Check if using correct I2C bus (try --bus 0 or --bus 1)")
        
        return found_devices
        
    except FileNotFoundError:
        print(f"\nError: I2C bus {bus_number} not found!")
        print("\nAvailable I2C buses:")
        import os
        for i in range(10):
            if os.path.exists(f"/dev/i2c-{i}"):
                print(f"  /dev/i2c-{i}")
        return []
    except PermissionError:
        print(f"\nError: Permission denied!")
        print("Try running with sudo: sudo python3 i2c_scan.py")
        return []
    except Exception as e:
        print(f"\nError: {e}")
        return []

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Scan I2C bus for devices')
    parser.add_argument('--bus', type=int, default=1, help='I2C bus number (default: 1)')
    args = parser.parse_args()
    
    print("I2C Scanner")
    print("=" * 60)
    
    devices = scan_i2c_bus(args.bus)
    
    # Check if our SCORPIO board is present
    SCORPIO_ADDRESS = 0x42
    if SCORPIO_ADDRESS in devices:
        print(f"\n✓ SCORPIO board found at address 0x{SCORPIO_ADDRESS:02X}!")
    else:
        print(f"\n✗ SCORPIO board not found at expected address 0x{SCORPIO_ADDRESS:02X}")
        print("\nPossible issues:")
        print("1. SCORPIO board firmware not uploaded or not running")
        print("2. Check I2C connections (GPIO 4=SDA, GPIO 5=SCL on SCORPIO)")
        print("3. Make sure SCORPIO board is powered")
        print("4. Verify firmware I2C_ADDRESS matches (check Serial Monitor)")

if __name__ == '__main__':
    main()

