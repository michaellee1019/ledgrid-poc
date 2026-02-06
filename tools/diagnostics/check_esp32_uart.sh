#!/bin/bash
# Quick diagnostic script to check ESP32 UART connection

echo "🔍 ESP32 UART Diagnostic"
echo "========================"
echo ""

echo "1. Checking USB serial devices..."
if ls /dev/ttyACM* 2>/dev/null; then
    echo "✓ Found USB serial devices"
else
    echo "✗ No /dev/ttyACM* devices found"
fi

if ls /dev/ttyUSB* 2>/dev/null; then
    echo "✓ Found USB serial devices"
else
    echo "✗ No /dev/ttyUSB* devices found"
fi
echo ""

echo "2. Checking device details..."
for port in /dev/ttyACM* /dev/ttyUSB*; do
    if [ -e "$port" ]; then
        echo "Device: $port"
        ls -l "$port"
        if command -v udevadm &> /dev/null; then
            udevadm info "$port" | grep -E "(ID_VENDOR|ID_MODEL|ID_SERIAL)" || true
        fi
        echo ""
    fi
done

echo "3. Checking permissions..."
for port in /dev/ttyACM* /dev/ttyUSB*; do
    if [ -e "$port" ]; then
        if [ -r "$port" ] && [ -w "$port" ]; then
            echo "✓ $port is readable and writable"
        else
            echo "✗ $port has permission issues"
            echo "  Run: sudo usermod -a -G dialout \$USER"
            echo "  Then logout/login"
        fi
    fi
done
echo ""

echo "4. Testing connection..."
for port in /dev/ttyACM* /dev/ttyUSB*; do
    if [ -e "$port" ]; then
        echo "Testing $port at 921600 baud..."
        if command -v python3 &> /dev/null; then
            timeout 2 python3 - <<'PY' "$port" || echo "  No response (timeout or error)"
import sys
import serial
import time

port = sys.argv[1]
try:
    ser = serial.Serial(port, 921600, timeout=1)
    time.sleep(0.5)
    # Check if any data is coming in
    ser.reset_input_buffer()
    time.sleep(0.5)
    if ser.in_waiting > 0:
        data = ser.read(min(100, ser.in_waiting))
        print(f"  ✓ Received {len(data)} bytes: {data[:50]}")
    else:
        print("  ⚠ No data received (ESP32 might be waiting for commands)")
    ser.close()
except Exception as e:
    print(f"  ✗ Error: {e}")
    sys.exit(1)
PY
        fi
        echo ""
    fi
done

echo "5. Recommendation:"
echo "  If ESP32 found but not responding:"
echo "    - Press RESET button on ESP32"
echo "    - Check if ESP32 is powered (USB connected properly)"
echo "    - Try: pio device monitor --baud 921600"
echo ""
echo "  If no devices found:"
echo "    - Check USB cable connection"
echo "    - Try different USB port"
echo "    - Check if ESP32 LED is on"

