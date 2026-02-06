# Quick Start & Troubleshooting

## After Deployment

### 1. Check if ESP32 is Connected

```bash
# On Raspberry Pi
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

**Expected output:**
```
crw-rw---- 1 root dialout 166, 0 Jan 21 22:30 /dev/ttyACM0
```

**If no devices found:**
- Unplug and replug USB cable
- Try different USB port
- Check if ESP32 power LED is on

### 2. Run Diagnostic Script

```bash
cd ~/ledgrid-pod
bash tools/diagnostics/check_esp32_uart.sh
```

### 3. View ESP32 Serial Output

```bash
cd ~/ledgrid-pod/firmware/esp32
pio device monitor --baud 921600
```

**Expected output:**
```
========================================
ESP32-S3 DevKitC UART LED Controller
========================================
Board: ESP32-S3 DevKitC (8MB Flash)
Strips: 8 x 140 LEDs = 1120 total
Protocol: UART (USB-CDC) @ 921200 bps
```

Press `Ctrl+C` then `Ctrl+]` to exit.

### 4. Check Controller Log

```bash
tail -f ~/ledgrid-pod/controller.log
```

**Expected output:**
```
UART Controller initialized
  Port: /dev/ttyACM0
  Baudrate: 921600
  Number of strips: 8
  LEDs per strip: 140
  Total LEDs: 1120
Sending PING to ESP32...
Waiting for response...
✓ UART connection OK - ESP32 responded: [{'code': 0, 'message': 'PONG'}]
```

### 5. Test UART Controller Directly

```bash
cd ~/ledgrid-pod
source venv/bin/activate
python3 drivers/uart_controller.py --port /dev/ttyACM0 --baudrate 921600 --debug
```

This will run a test sequence:
- Ping test
- Clear
- Red/Green/Blue colors
- Rainbow animation

**If this works**, the UART communication is fine and any issues are with the animation system.

## Common Issues

### Controller Log Hangs After "Total LEDs: 1120"

**Cause:** ESP32 not responding or wrong port

**Fix:**
```bash
# 1. Check if ESP32 is connected
ls /dev/ttyACM*

# 2. If no device found, check USB connection
# 3. If device found, check permissions
groups | grep dialout

# 4. If not in dialout group:
sudo usermod -a -G dialout $USER
# Then logout and login again

# 5. Press RESET button on ESP32
# 6. Restart controller:
sudo systemctl restart ledgrid.service
```

### "Permission denied" on /dev/ttyACM0

**Fix:**
```bash
sudo usermod -a -G dialout $USER
# Logout and login, then try again
```

### ESP32 Responds but No LEDs Light Up

**Check wiring:**
- LED strip power: Connected to 5V power supply
- LED strip ground: Common ground with ESP32
- LED strip data: Connected to ESP32 GPIO pins (4,5,6,7,15,16,17,18)

**Check in ESP32 monitor:**
```bash
pio device monitor --baud 921600
```

Look for:
```
✅ CMD_SET_ALL #1: 3361 bytes, first RGB: (FF,00,00)
```

If you see `first RGB: (00,00,00)`, the animation is sending all black.

### Wrong Baudrate Mismatch

**Symptoms:** Garbage in ESP32 monitor, no response

**Fix:** Make sure ESP32 firmware and Python controller use same baudrate (921600)

**ESP32:** `firmware/esp32/src/main.cpp` line 267: `Serial.begin(921600);`  
**Python:** `--baudrate 921600` argument

### LEDs Show Wrong Animation

**Check which animation is running:**
```bash
cat ~/ledgrid-pod/run_state/control.json
```

**Start specific animation via web UI:**
```
http://<pi-ip>:5000/control
```

## Viewing Live Logs

### Both logs simultaneously:
```bash
cd ~/ledgrid-pod
tail -f web.log controller.log
```

### Just errors:
```bash
cd ~/ledgrid-pod
tail -f controller.log | grep -E "(error|Error|ERROR|⚠|✗)"
```

## Restart System

```bash
# Via systemd (recommended):
sudo systemctl restart ledgrid.service

# Or manually:
cd ~/ledgrid-pod
pkill -f start_server.py
./start.sh
```

## Performance Check

After system is running, check FPS:

**In ESP32 monitor** (every 5 seconds):
```
📊 Pkts=1234 Frames=1200 FPS=120.5 | Throughput=3852.3kb/s | Errors=0
```

**Good values:**
- FPS: 60-150+
- Errors: 0
- Throughput: 3000-4000 kb/s

**If FPS < 60:**
- Check `controller.log` for errors
- Verify baudrate is 921600 (not 115200)
- Check ESP32 `Show=XXXµs` (should be < 5000)

## Getting Help

When reporting issues, provide:
1. Output of `bash tools/diagnostics/check_esp32_uart.sh`
2. Last 50 lines of `controller.log`
3. Last 20 lines from `pio device monitor`
4. Photo of wiring (if LED issue)

