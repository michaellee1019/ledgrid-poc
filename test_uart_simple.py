#!/usr/bin/env python3
"""
Simple UART Test Script
======================

Quick test to verify ESP32 UART communication works.
Run this BEFORE copying to Pi to ensure everything is working.

Usage:
    python3 test_uart_simple.py [PORT]
    
Examples:
    python3 test_uart_simple.py /dev/ttyACM0          # Linux/Pi
    python3 test_uart_simple.py /dev/cu.usbmodem101   # Mac
"""

import serial
import time
import sys

# Packet framing
PACKET_START = 0xAA
PACKET_END = 0x55

# Commands
CMD_PING = 0xFF
CMD_CONFIG = 0x07
CMD_SET_BRIGHTNESS = 0x02
CMD_CLEAR = 0x04
CMD_SET_ALL = 0x06


def send_packet(ser, payload):
    """Send packet with framing"""
    packet = bytearray([PACKET_START])
    packet.extend(len(payload).to_bytes(2, 'little'))
    packet.extend(payload)
    packet.append(PACKET_END)
    ser.write(packet)
    ser.flush()


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_uart_simple.py <PORT>")
        print("\nAvailable ports:")
        import glob
        ports = glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*') + glob.glob('/dev/cu.usbmodem*')
        for p in ports:
            print(f"  {p}")
        return 1
    
    port = sys.argv[1]
    baudrate = 115200
    
    print("=" * 60)
    print("UART Test Script")
    print("=" * 60)
    print(f"Port: {port}")
    print(f"Baudrate: {baudrate}")
    print()
    
    try:
        print("Connecting...")
        ser = serial.Serial(port, baudrate, timeout=1)
        print("✓ Connected!\n")
        time.sleep(2)  # Let ESP32 boot
        
        # Test 1: PING
        print("Test 1: Sending PING...")
        send_packet(ser, [CMD_PING])
        time.sleep(0.2)
        print("✓ PING sent\n")
        
        # Test 2: CONFIG (8 strips, 140 LEDs)
        print("Test 2: Sending CONFIG (8 strips x 140 LEDs)...")
        send_packet(ser, [CMD_CONFIG, 8, 0x00, 0x8C])  # 140 = 0x008C
        time.sleep(0.2)
        print("✓ CONFIG sent\n")
        
        # Test 3: SET BRIGHTNESS
        print("Test 3: Setting brightness to 50...")
        send_packet(ser, [CMD_SET_BRIGHTNESS, 50])
        time.sleep(0.2)
        print("✓ BRIGHTNESS sent\n")
        
        # Test 4: CLEAR
        print("Test 4: Clearing all LEDs...")
        send_packet(ser, [CMD_CLEAR])
        time.sleep(0.2)
        print("✓ CLEAR sent\n")
        
        # Test 5: Set all LEDs to RED
        print("Test 5: Setting all LEDs to RED...")
        total_leds = 8 * 140  # 1120
        payload = bytearray([CMD_SET_ALL])
        for i in range(total_leds):
            payload.extend([255, 0, 0])  # Red
        send_packet(ser, payload)
        time.sleep(1)
        print("✓ All LEDs should be RED now!\n")
        
        # Test 6: GREEN
        print("Test 6: Setting all LEDs to GREEN...")
        payload = bytearray([CMD_SET_ALL])
        for i in range(total_leds):
            payload.extend([0, 255, 0])  # Green
        send_packet(ser, payload)
        time.sleep(1)
        print("✓ All LEDs should be GREEN now!\n")
        
        # Test 7: BLUE
        print("Test 7: Setting all LEDs to BLUE...")
        payload = bytearray([CMD_SET_ALL])
        for i in range(total_leds):
            payload.extend([0, 0, 255])  # Blue
        send_packet(ser, payload)
        time.sleep(1)
        print("✓ All LEDs should be BLUE now!\n")
        
        # Test 8: CLEAR
        print("Test 8: Clearing LEDs...")
        send_packet(ser, [CMD_CLEAR])
        time.sleep(0.5)
        print("✓ LEDs cleared\n")
        
        print("=" * 60)
        print("✅ All tests completed successfully!")
        print("=" * 60)
        print("\nIf LEDs changed colors, UART communication is working!")
        print("You can now copy led_controller_uart.py to the Pi.\n")
        
    except serial.SerialException as e:
        print(f"✗ Serial error: {e}")
        print("\nTroubleshooting:")
        print("  1. Check USB cable connection")
        print("  2. Check port name (ls /dev/tty* or ls /dev/cu.*)")
        print("  3. Check permissions (sudo usermod -a -G dialout $USER)")
        print("  4. Press RESET on ESP32")
        return 1
    
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

