#!/usr/bin/env python3
"""
ESP32 Echo Test - Verify 2-Way UART Communication
=================================================

This script tests bidirectional UART communication by:
1. Sending test packets to ESP32
2. Reading back the echo response
3. Verifying data integrity

Usage:
    python3 test_echo.py [PORT]
"""

import serial
import time
import sys

# Packet framing
PACKET_START = 0xAA
PACKET_END = 0x55

# Commands
CMD_ECHO = 0xFE
CMD_PING = 0xFF

# Response codes
RESP_OK = 0x00


def send_packet(ser, payload):
    """Send a packet with framing"""
    packet = bytearray([PACKET_START])
    packet.extend(len(payload).to_bytes(2, 'little'))
    packet.extend(payload)
    packet.append(PACKET_END)
    
    print(f"  TX: {' '.join(f'{b:02X}' for b in packet[:32])}{'...' if len(packet) > 32 else ''}")
    ser.write(packet)
    ser.flush()
    return len(packet)


def read_packet(ser, timeout=2.0):
    """Read a packet with framing"""
    start_time = time.time()
    
    # Wait for start byte
    while time.time() - start_time < timeout:
        if ser.in_waiting >= 1:
            start_byte = ser.read(1)[0]
            if start_byte == PACKET_START:
                break
        time.sleep(0.01)
    else:
        return None, "Timeout waiting for start byte"
    
    # Read length (2 bytes)
    if ser.in_waiting < 2:
        time.sleep(0.1)
    if ser.in_waiting < 2:
        return None, "Timeout waiting for length bytes"
    
    len_bytes = ser.read(2)
    payload_len = int.from_bytes(len_bytes, 'little')
    
    # Read payload
    timeout_remaining = timeout - (time.time() - start_time)
    if timeout_remaining <= 0:
        return None, "Timeout before reading payload"
    
    payload = bytearray()
    while len(payload) < payload_len and time.time() - start_time < timeout:
        if ser.in_waiting > 0:
            payload.extend(ser.read(min(ser.in_waiting, payload_len - len(payload))))
        else:
            time.sleep(0.01)
    
    if len(payload) < payload_len:
        return None, f"Incomplete payload: got {len(payload)}, expected {payload_len}"
    
    # Read end byte
    if ser.in_waiting < 1:
        time.sleep(0.1)
    if ser.in_waiting < 1:
        return None, "Timeout waiting for end byte"
    
    end_byte = ser.read(1)[0]
    if end_byte != PACKET_END:
        return None, f"Invalid end byte: 0x{end_byte:02X}"
    
    return payload, None


def test_echo(ser, test_data):
    """Send test data and verify echo response"""
    print(f"\n{'='*70}")
    print(f"Test: Echo {len(test_data)} bytes")
    print(f"{'='*70}")
    
    # Send echo command with test data
    payload = bytearray([CMD_ECHO]) + bytearray(test_data)
    tx_bytes = send_packet(ser, payload)
    
    # Read response
    print(f"\n  Waiting for echo response...")
    response, error = read_packet(ser)
    
    if error:
        print(f"  ✗ ERROR: {error}")
        return False
    
    print(f"  RX: {' '.join(f'{b:02X}' for b in response[:32])}{'...' if len(response) > 32 else ''}")
    
    # Check response
    if len(response) < 1:
        print(f"  ✗ ERROR: Empty response")
        return False
    
    resp_code = response[0]
    resp_data = response[1:]
    
    print(f"\n  Response code: 0x{resp_code:02X} ({'OK' if resp_code == RESP_OK else 'ERROR'})")
    print(f"  Sent:     {len(test_data)} bytes")
    print(f"  Received: {len(resp_data)} bytes")
    
    # Verify data integrity
    original = bytearray([CMD_ECHO]) + bytearray(test_data)
    if resp_data == original:
        print(f"  ✅ Data integrity: PERFECT MATCH")
        return True
    else:
        print(f"  ✗ Data integrity: MISMATCH")
        print(f"\n  Expected: {' '.join(f'{b:02X}' for b in original[:32])}{'...' if len(original) > 32 else ''}")
        print(f"  Got:      {' '.join(f'{b:02X}' for b in resp_data[:32])}{'...' if len(resp_data) > 32 else ''}")
        
        # Find first difference
        for i in range(min(len(original), len(resp_data))):
            if original[i] != resp_data[i]:
                print(f"  First difference at byte {i}: expected 0x{original[i]:02X}, got 0x{resp_data[i]:02X}")
                break
        
        return False


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_echo.py <PORT>")
        print("\nExample:")
        print("  python3 test_echo.py /dev/ttyACM0")
        return 1
    
    port = sys.argv[1]
    baudrate = 115200
    
    print("=" * 70)
    print("ESP32 Echo Test - 2-Way UART Communication")
    print("=" * 70)
    print(f"Port: {port}")
    print(f"Baudrate: {baudrate}")
    print()
    
    try:
        print("Connecting...")
        ser = serial.Serial(port, baudrate, timeout=2.0)
        print("✓ Connected!\n")
        time.sleep(2)  # Let ESP32 boot
        
        # Flush any startup messages
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
        tests_passed = 0
        tests_failed = 0
        
        # Test 1: Single byte
        if test_echo(ser, [0x42]):
            tests_passed += 1
        else:
            tests_failed += 1
        time.sleep(0.5)
        
        # Test 2: Pattern
        if test_echo(ser, [0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF]):
            tests_passed += 1
        else:
            tests_failed += 1
        time.sleep(0.5)
        
        # Test 3: All zeros
        if test_echo(ser, [0x00] * 10):
            tests_passed += 1
        else:
            tests_failed += 1
        time.sleep(0.5)
        
        # Test 4: All ones
        if test_echo(ser, [0xFF] * 10):
            tests_passed += 1
        else:
            tests_failed += 1
        time.sleep(0.5)
        
        # Test 5: ASCII text
        if test_echo(ser, list(b"Hello ESP32!")):
            tests_passed += 1
        else:
            tests_failed += 1
        time.sleep(0.5)
        
        # Test 6: Larger payload (100 bytes)
        if test_echo(ser, list(range(100))):
            tests_passed += 1
        else:
            tests_failed += 1
        
        # Summary
        print(f"\n{'='*70}")
        print(f"Test Summary")
        print(f"{'='*70}")
        print(f"✅ Passed: {tests_passed}")
        print(f"✗ Failed: {tests_failed}")
        print(f"{'='*70}")
        
        if tests_failed == 0:
            print("\n🎉 All tests passed! 2-way UART communication is working perfectly!")
            return 0
        else:
            print(f"\n⚠️ {tests_failed} test(s) failed. Check ESP32 serial output for details.")
            return 1
        
    except serial.SerialException as e:
        print(f"✗ Serial error: {e}")
        return 1
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()


if __name__ == "__main__":
    sys.exit(main())


