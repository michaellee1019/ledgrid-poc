# Simple UART LED Controller

A stripped-down, easy-to-test LED controller using UART communication between Raspberry Pi and ESP32-S3.

## Components

1. **ESP32 Firmware** (`firmware/esp32/src/main.cpp`)
   - UART (USB-CDC) communication @ 921600 bps
   - Controls 8 LED strips (GPIO 4, 5, 6, 7, 15, 16, 17, 18)
   - 140 LEDs per strip = 1120 total LEDs
   - Packet framing: `[0xAA][LEN_LOW][LEN_HIGH][PAYLOAD][0x55]`

2. **Python Controller** (`led_controller_uart.py`)
   - Simple standalone script
   - Hardcoded sparkle animation
   - No web server, no systemd - just run it!

## Quick Start

### 1. Flash ESP32 Firmware

```bash
# On your dev machine (Mac/Linux)
cd firmware/esp32
pio run --target upload
pio device monitor --baud 921600
```

**Expected output:**
```
========================================
ESP32-S3 DevKitC UART LED Controller
========================================
Board: ESP32-S3 DevKitC (8MB Flash)
Strips: 8 x 140 LEDs = 1120 total
Protocol: UART (USB-CDC) @ 921600 bps

🌈 Running rainbow animation for 1 second...
✅ Rainbow complete, entering UART mode

Waiting for packets...
```

Press `Ctrl+C` then `Ctrl+]` to exit monitor.

### 2. Copy Python Script to Raspberry Pi

```bash
# From your dev machine
scp led_controller_uart.py pi@<pi-ip>:~/
```

### 3. Install PySerial on Pi

```bash
# SSH to Raspberry Pi
ssh pi@<pi-ip>

# Install pyserial
pip3 install pyserial
```

### 4. Run the Sparkle Animation

```bash
# On Raspberry Pi
python3 ~/led_controller_uart.py
```

**Options:**
```bash
# Specify port and baudrate
python3 ~/led_controller_uart.py --port /dev/ttyACM0 --baudrate 921600

# If ESP32 is on different port
python3 ~/led_controller_uart.py --port /dev/ttyUSB0
```

## Troubleshooting

### No `/dev/ttyACM0` device

**Check USB connection:**
```bash
ls -l /dev/ttyACM* /dev/ttyUSB*
```

**If no devices found:**
- Unplug and replug USB cable
- Try different USB port
- Check if ESP32 power LED is on

### Permission denied

**Add user to dialout group:**
```bash
sudo usermod -a -G dialout $USER
# Logout and login again
```

### ESP32 not responding

1. **Press RESET button on ESP32**
2. **Check ESP32 serial monitor:**
   ```bash
   cd firmware/esp32
   pio device monitor --baud 921600
   ```
   Should see "Waiting for packets..." message

3. **Send manual ping:**
   ```bash
   python3 -c "
   import serial, time
   s = serial.Serial('/dev/ttyACM0', 921600, timeout=1)
   time.sleep(1)
   # Send PING: [0xAA][0x01][0x00][0xFF][0x55]
   s.write(bytes([0xAA, 0x01, 0x00, 0xFF, 0x55]))
   time.sleep(0.5)
   print(s.read(100))
   "
   ```

### LEDs not lighting up

**Check wiring:**
- **LED Power:** 5V power supply connected
- **LED Ground:** Common ground with ESP32
- **LED Data Pins:**
  - Strip 0 → GPIO 4
  - Strip 1 → GPIO 5
  - Strip 2 → GPIO 6
  - Strip 3 → GPIO 7
  - Strip 4 → GPIO 15
  - Strip 5 → GPIO 16
  - Strip 6 → GPIO 17
  - Strip 7 → GPIO 18

**Test with single strip first:**
- Connect just Strip 0 to GPIO 4
- LEDs should sparkle

### Low FPS

**Check ESP32 stats** (in serial monitor):
```
📊 Pkts=1234 Frames=1200 FPS=60.0 | Throughput=3000.0kb/s | Errors=0
```

**Good values:**
- FPS: 60+
- Errors: 0
- Show time: < 5000µs

**If low:**
- Verify baudrate is 921600 on both sides
- Check USB cable quality
- Try lowering `TARGET_FPS` in Python script

## Protocol Details

### Packet Format
```
[0xAA] [LEN_LOW] [LEN_HIGH] [PAYLOAD...] [0x55]
```

### Commands

| Command | Code | Payload | Description |
|---------|------|---------|-------------|
| PING | 0xFF | None | Test connection |
| CONFIG | 0x07 | `[strips][len_high][len_low][debug?]` | Set strip config |
| SET_BRIGHTNESS | 0x02 | `[brightness]` | Set global brightness (0-255) |
| CLEAR | 0x04 | None | Clear all LEDs |
| SET_ALL | 0x06 | `[R0][G0][B0][R1][G1][B1]...` | Set all LEDs at once |

### Example: Set all LEDs to red

```python
import serial

ser = serial.Serial('/dev/ttyACM0', 921600)

# Packet: [START][LEN][PAYLOAD][END]
# Payload: [CMD_SET_ALL][RGB data for all LEDs]
num_leds = 1120
payload = bytearray([0x06])  # CMD_SET_ALL
for i in range(num_leds):
    payload.extend([255, 0, 0])  # Red

packet = bytearray([0xAA])  # Start
packet.extend(len(payload).to_bytes(2, 'little'))  # Length
packet.extend(payload)
packet.append(0x55)  # End

ser.write(packet)
```

## Customizing the Animation

Edit `led_controller_uart.py` to change sparkle parameters:

```python
# Near top of file
SPARKLE_DECAY = 0.92   # How fast sparkles fade (0.92 = slow fade)
SPARKLE_RATE = 0.02    # Probability of new sparkle (0.02 = 2% per LED per frame)
SPARKLE_COLOR = (255, 200, 150)  # RGB color (warm white)
TARGET_FPS = 60        # Target frame rate
```

Or replace the `SparkleAnimation` class entirely with your own animation!

## Performance

**Theoretical max:**
- Baudrate: 921600 bps = 115200 bytes/sec
- Frame size: 1 + (1120 × 3) = 3361 bytes
- Max FPS: 115200 / 3361 ≈ **34 FPS**

**Actual performance:**
- Expected: **30-34 FPS** sustained
- Packet overhead reduces max slightly
- USB-CDC is efficient, minimal latency

**For higher FPS:**
- Reduce number of LEDs
- Use multiple ESP32s (split strips across devices)
- Or switch to native LED control on ESP32 (generate animations on ESP32 itself)

## Next Steps

Once this works, you can:
1. Add more animations to the Python script
2. Create a simple menu to switch between animations
3. Add command-line arguments for animation parameters
4. Eventually integrate back into the full system

But for now - keep it simple! ✨


