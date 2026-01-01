#!/usr/bin/env python3
"""
Diagnose Raspberry Pi SPI configuration and test actual pin states
"""

import spidev
import time
import sys

def check_spi_config():
    """Check SPI device configuration"""
    print("=== Raspberry Pi SPI Diagnostic ===\n")
    
    try:
        spi = spidev.SpiDev()
        spi.open(0, 0)
        
        print("✓ SPI device opened: /dev/spidev0.0\n")
        
        print("Current Configuration:")
        print(f"  Max speed: {spi.max_speed_hz} Hz ({spi.max_speed_hz/1000000:.1f} MHz)")
        print(f"  Mode: {spi.mode} (CPOL={spi.mode >> 1}, CPHA={spi.mode & 1})")
        print(f"  Bits per word: {spi.bits_per_word}")
        print(f"  CS active high: {spi.cshigh}")
        print(f"  LSB first: {spi.lsbfirst}")
        print(f"  3-wire mode: {spi.threewire}")
        print(f"  Loop mode: {spi.loop}")
        
        print("\n" + "="*60)
        print("Testing SPI transmission with different settings...")
        print("="*60 + "\n")
        
        # Test 1: Very slow speed
        print("Test 1: Ultra-slow speed (10 kHz)")
        spi.max_speed_hz = 10000
        spi.mode = 0
        print(f"  Config: {spi.max_speed_hz} Hz, Mode {spi.mode}")
        print("  Sending: [0xAA, 0x55]")
        result = spi.xfer2([0xAA, 0x55])
        print(f"  Response: {[hex(x) for x in result]}")
        time.sleep(1)
        
        # Test 2: Standard speed
        print("\nTest 2: Standard speed (1 MHz)")
        spi.max_speed_hz = 1000000
        spi.mode = 0
        print(f"  Config: {spi.max_speed_hz} Hz, Mode {spi.mode}")
        print("  Sending: [0xFF, 0x00, 0xFF]")
        result = spi.xfer2([0xFF, 0x00, 0xFF])
        print(f"  Response: {[hex(x) for x in result]}")
        time.sleep(1)
        
        # Test 3: Different SPI modes
        for mode in [0, 1, 2, 3]:
            print(f"\nTest {3+mode}: SPI Mode {mode}")
            spi.max_speed_hz = 100000
            spi.mode = mode
            print(f"  Config: {spi.max_speed_hz} Hz, Mode {mode}")
            print(f"  Sending: [0x{mode:02X}]")
            result = spi.xfer2([mode])
            print(f"  Response: {[hex(x) for x in result]}")
            time.sleep(1)
        
        print("\n" + "="*60)
        print("\nCheck SCORPIO serial monitor:")
        print("  - Look for [CS] Asserted messages")
        print("  - Look for SCK toggles in the debug output")
        print("  - Check if [FIFO] Byte received appears")
        print("\nIf you see CS but no SCK toggles:")
        print("  → Raspberry Pi SPI hardware issue")
        print("  → Try: sudo raspi-config → disable/re-enable SPI")
        print("  → Try: Reboot the Raspberry Pi")
        
        spi.close()
        
    except FileNotFoundError:
        print("✗ Error: /dev/spidev0.0 not found")
        print("\nSPI is not enabled! Enable it:")
        print("  1. sudo raspi-config")
        print("  2. Interface Options → SPI → Enable")
        print("  3. Reboot")
        return False
    
    except PermissionError:
        print("✗ Error: Permission denied")
        print("\nRun with sudo:")
        print("  sudo python3 diagnose_pi_spi.py")
        return False
    
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

def check_kernel_module():
    """Check if SPI kernel module is loaded"""
    print("\n" + "="*60)
    print("Checking SPI kernel module...")
    print("="*60 + "\n")
    
    import subprocess
    
    try:
        result = subprocess.run(['lsmod'], capture_output=True, text=True)
        if 'spi_bcm2835' in result.stdout:
            print("✓ SPI kernel module loaded: spi_bcm2835")
        else:
            print("✗ SPI kernel module NOT loaded!")
            print("\nTo load:")
            print("  sudo modprobe spi_bcm2835")
    except Exception as e:
        print(f"Could not check kernel modules: {e}")

if __name__ == "__main__":
    check_kernel_module()
    check_spi_config()
    
    print("\n" + "="*60)
    print("Next steps if SCLK still not working:")
    print("="*60)
    print("1. Check /boot/config.txt has: dtparam=spi=on")
    print("2. Reboot the Raspberry Pi")
    print("3. Try a different SPI device (spidev0.1)")
    print("4. Check dmesg for SPI errors: dmesg | grep spi")
    print("")





