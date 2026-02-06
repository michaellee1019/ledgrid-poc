# UART Debugging Guide

## Added Bidirectional Communication

The ESP32 now sends response packets back to the Raspberry Pi, allowing you to see exactly what's happening.

## ESP32 Debug Output

The ESP32 firmware now logs:

### 1. **First 20 Packets** (always shown)
```
🔍 Pkt#0: cmd=0x07 len=5 bytes: 07 08 00 8C 01 ...
🔍 Pkt#1: cmd=0x06 len=3361 bytes: 06 FF 00 00 12 34 56 ...
```
- Shows command byte, length, and first 32 bytes of payload
- Helps verify the Pi is sending correct data

### 2. **Configuration Changes**
```
📐 Config changed: strips=8, length=140, total=1120 (cleared LEDs)
```
or
```
📐 Config refresh: strips=8, length=140, total=1120 (no change)
```

### 3. **SET_ALL Commands** (first 3 frames)
```
✅ CMD_SET_ALL #1: 3361 bytes, first RGB: (FF,00,00)
✅ CMD_SET_ALL #2: 3361 bytes, first RGB: (00,FF,00)
✅ CMD_SET_ALL #3: 3361 bytes, first RGB: (00,00,FF)
```
- Shows frame number, size, and first pixel's RGB values
- Verifies animation data is being received

### 4. **PING Responses**
```
📥 CMD_PING received
```

### 5. **Errors**
```
⚠️ CMD_SET_ALL expected 3361 bytes, got 2944 (strips=8, leds=140)
⚠️ Invalid strips: 9 (max 8)
⚠️ Invalid LEDs/strip: 600 (max 500)
```

## Python Controller Debug Output

The Python controller now shows:

### 1. **Initialization**
```
UART Controller initialized
  Port: /dev/ttyACM0
  Baudrate: 921600
  Number of strips: 8
  LEDs per strip: 140
  Total LEDs: 1120
✓ UART connection OK - ESP32 responded: [{'code': 0, 'message': 'PONG'}]
```

### 2. **Configuration Sent**
```
Config sent: 8 strips x 140 LEDs - ESP32: CONFIG_OK
```

### 3. **First 3 Frames**
```
Sending frame #1: 3361 bytes, first RGB: (255,0,0)
  ESP32 response: code=0 msg='FRAME_OK'
Sending frame #2: 3361 bytes, first RGB: (0,255,0)
  ESP32 response: code=0 msg='FRAME_OK'
```

## Response Codes

| Code | Meaning | Description |
|------|---------|-------------|
| 0x00 | RESP_OK | Command successful |
| 0x01 | RESP_ERROR | Command failed |
| 0x02 | RESP_STATUS | Status information |

## Common Error Messages

### Size Mismatch
```
⚠️ CMD_SET_ALL expected 3361 bytes, got 2944 (strips=8, leds=140)
ESP32 response: code=1 msg='SIZE_MISMATCH'
```
**Cause**: Pi is sending data for wrong number of LEDs  
**Fix**: Check `--strips` and `--leds-per-strip` arguments match

### Config Too Short
```
ESP32 response: code=1 msg='CONFIG_TOO_SHORT'
```
**Cause**: Configuration packet truncated  
**Fix**: Check UART connection and baudrate

### Invalid Configuration
```
⚠️ Invalid strips: 9 (max 8)
ESP32 response: code=1 msg='INVALID_STRIPS'
```
**Cause**: Trying to configure more strips than supported  
**Fix**: Maximum is 8 strips per ESP32

## Monitoring Live Output

### View ESP32 Serial Output
```bash
cd ~/ledgrid-pod/firmware/esp32
pio device monitor --baud 921600
```

Press `Ctrl+C` to exit monitoring.

### View Controller Log (on Pi)
```bash
tail -f ~/ledgrid-pod/controller.log
```

### View Both Simultaneously
```bash
# Terminal 1: ESP32 output
pio device monitor --baud 921600

# Terminal 2: Controller log
tail -f ~/ledgrid-pod/controller.log
```

## Debugging No Animation

If LEDs are blinking but no animation shows:

### 1. Check ESP32 is receiving packets
```
🔍 Pkt#0: cmd=0x07 len=5 bytes: ...  ← Should see this
🔍 Pkt#1: cmd=0x06 len=3361 bytes: ...  ← Should see SET_ALL
```

### 2. Verify packet size matches
```
✅ CMD_SET_ALL #1: 3361 bytes  ← Should be 3361 for 8×140 LEDs
```
**Expected size = 1 + (strips × leds_per_strip × 3)**
- 8 strips × 140 LEDs = 1120 LEDs
- 1 + (1120 × 3) = **3361 bytes**

### 3. Check RGB values are not all zero
```
first RGB: (FF,00,00)  ← Good: Red color
first RGB: (00,00,00)  ← Bad: All black
```

### 4. Verify FastLED.show() is working
```
Show=2500µs  ← Should be 1000-5000µs typical
```

### 5. Check FPS is reasonable
```
FPS=120.5  ← Good: Animation updating
FPS=0.0    ← Bad: No frames being rendered
```

## Performance Metrics

Every 5 seconds, ESP32 prints:
```
📊 Pkts=1234 Frames=1200 FPS=120.5 | Throughput=3852.3kb/s | Errors=0 | Show=2500µs | Heap=245000
    Configs=1 SetAlls=1200 | 8x140 LEDs
```

**Good Values:**
- FPS: 60-150+ (depends on animation complexity)
- Throughput: 3000-4000 kb/s at 921600 baud
- Errors: 0
- Show: 1000-5000µs (FastLED output time)
- SetAlls ≈ Frames (should be close)

**Bad Values:**
- FPS: 0 or very low (<10)
- Errors: > 0
- SetAlls = 0 (not receiving frame data)
- Show: > 10000µs (LED output too slow)

## Quick Diagnostic Command

Run this to test the UART controller directly:
```bash
cd ~/ledgrid-pod
python3 drivers/uart_controller.py --port /dev/ttyACM0 --baudrate 921600 --debug
```

You should see:
1. Initialization message
2. ESP32 PONG response
3. Color tests (red/green/blue)
4. Rainbow animation
5. Statistics

If this works but animation doesn't, the issue is with the animation system, not UART.

## Expected Controller.log Output

When running normally, you should see:
```
🎨 LED Grid Animation Server
========================================
Mode: controller
Animations: /home/ledwallleft/ledgrid-pod/animation/plugins/
Layout: 8 strips × 140 LEDs = 1120 total

UART: /dev/ttyACM0 @ 921600 bps
Target FPS: 150

🎛️ Controller mode
  Control file: run_state/control.json
  Status file : run_state/status.json
  Poll every  : 0.5s
  Status every: 0.5s

UART Controller initialized
  Port: /dev/ttyACM0
  Baudrate: 921600
  Number of strips: 8
  LEDs per strip: 140
  Total LEDs: 1120
✓ UART connection OK - ESP32 responded: [{'code': 0, 'message': 'PONG'}]
✓ Controller initialized: 8 strips × 140 LEDs

Config sent: 8 strips x 140 LEDs - ESP32: CONFIG_OK
Sending frame #1: 3361 bytes, first RGB: (12,34,56)
  ESP32 response: code=0 msg='FRAME_OK'
```

Then frames should continue without further debug output (unless errors occur).

