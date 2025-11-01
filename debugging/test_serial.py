#!/usr/bin/env python3
"""Simple serial test - send one command at a time"""

import serial
import time
import glob

def find_serial_port():
    patterns = ['/dev/ttyACM*', '/dev/ttyUSB*', '/dev/cu.usbmodem*', 'COM*']
    for pattern in patterns:
        ports = glob.glob(pattern)
        if ports:
            return sorted(ports)[0]
    return None

port = find_serial_port()
if not port:
    print("No serial port found!")
    exit(1)

print(f"Connecting to {port}...")
ser = serial.Serial(port, 115200, timeout=1)
time.sleep(2)

# Clear any startup messages
while ser.in_waiting:
    print(ser.readline().decode('utf-8', errors='ignore').strip())

print("\n--- Testing Commands ---\n")

# Test 1: Clear all
print("1. Sending CLEAR command (0x04)...")
ser.write(bytes([0x04]))
ser.flush()
time.sleep(0.5)

# Test 2: Set brightness
print("2. Sending SET_BRIGHTNESS command (0x02, 100)...")
ser.write(bytes([0x02, 100]))
ser.flush()
time.sleep(0.5)

# Test 3: Set pixel 0 to RED
print("3. Sending SET_PIXEL command for pixel 0 -> RED...")
ser.write(bytes([
    0x01,  # CMD_SET_PIXEL
    0x00,  # pixel high byte
    0x00,  # pixel low byte  
    0xFF,  # R = 255
    0x00,  # G = 0
    0x00   # B = 0
]))
ser.flush()
time.sleep(0.1)

# Test 4: SHOW
print("4. Sending SHOW command (0x03)...")
ser.write(bytes([0x03]))
ser.flush()
time.sleep(1)

print("\n✓ Commands sent!")
print("Check if pixel 0 (first LED) is RED")
print("\nWaiting for serial responses...")
time.sleep(2)

while ser.in_waiting:
    print(ser.readline().decode('utf-8', errors='ignore').strip())

ser.close()
print("\nDone!")





