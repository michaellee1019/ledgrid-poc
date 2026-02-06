# ✨ Simplified LED Controller System

## What Changed

**Before:** Complex multi-component system with animation server, web UI, systemd service, plugin system, etc.

**Now:** Just 2 files:
1. **ESP32 Firmware** - `firmware/esp32/src/main.cpp`
2. **Python Script** - `led_controller_uart.py` (with hardcoded sparkle)

## File Overview

### ESP32 Firmware (`firmware/esp32/src/main.cpp`)

**What it does:**
- Receives UART packets over USB-CDC @ 921600 bps
- Controls 8 LED strips (1120 LEDs total)
- Implements packet protocol with framing
- Shows stats every 5 seconds

**Key features:**
- ✅ 8 strips on GPIO 4, 5, 6, 7, 15, 16, 17, 18
- ✅ 140 LEDs per strip
- ✅ Packet framing: `[0xAA][LEN][PAYLOAD][0x55]`
- ✅ Error checking and stats
- ✅ Rainbow animation on boot (visual confirmation)
- ✅ No SPI - pure UART

**Commands supported:**
- `CMD_PING` (0xFF) - Connection test
- `CMD_CONFIG` (0x07) - Set strips/LEDs
- `CMD_SET_BRIGHTNESS` (0x02) - Global brightness
- `CMD_CLEAR` (0x04) - Turn off all LEDs
- `CMD_SET_ALL` (0x06) - Set all LED colors at once

### Python Controller (`led_controller_uart.py`)

**What it does:**
- Connects to ESP32 via `/dev/ttyACM0`
- Sends configuration and initialization
- Runs sparkle animation loop
- Shows FPS stats

**Sparkle animation:**
- Random LEDs light up as "sparkles"
- Sparkles fade gradually
- Warm white color (255, 200, 150)
- ~60 FPS target

**No dependencies except:**
- `pyserial` (install with `pip3 install pyserial`)

## Usage

### 1. Flash ESP32

```bash
cd firmware/esp32
pio run --target upload
```

### 2. Copy to Pi & Run

```bash
# Copy to Pi
scp led_controller_uart.py pi@<ip>:~/

# On Pi
pip3 install pyserial
python3 ~/led_controller_uart.py
```

That's it! 🎉

## Testing Without Pi

You can test on your Mac too:

```bash
# Find ESP32 port
ls /dev/cu.usbmodem*

# Run sparkle
python3 led_controller_uart.py --port /dev/cu.usbmodem101
```

## Monitor ESP32

```bash
cd firmware/esp32
pio device monitor --baud 921600
```

**What you'll see:**
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

🔍 Pkt#0: cmd=0xFF len=1 bytes: FF
📥 CMD_PING received
🔍 Pkt#1: cmd=0x07 len=4 bytes: 07 08 00 8C
📐 Config changed: strips=8, length=140, total=1120
🔍 Pkt#2: cmd=0x02 len=2 bytes: 02 32
📥 Brightness → 50
🔍 Pkt#3: cmd=0x04 len=1 bytes: 04
📥 CMD_CLEAR
🔍 Pkt#4: cmd=0x06 len=3361 bytes: 06 00 00 00 00 00 00 ...
✅ CMD_SET_ALL #1: 3361 bytes, first RGB: (00,00,00)

📊 Pkts=500 Frames=450 FPS=30.2 | Throughput=2850.5kb/s | Errors=0
    Configs=1 SetAlls=450 | 8x140 LEDs
```

## What Was Removed

To simplify debugging, these were removed/bypassed:

- ❌ Web server (`web_interface.py`)
- ❌ Animation plugin system
- ❌ Systemd service
- ❌ Multiple animation files
- ❌ Control channel
- ❌ SPI support
- ❌ Configuration files
- ❌ Deployment scripts

These can all be added back later once UART communication is proven!

## Next Steps

1. **Test on Pi** - Verify sparkle works end-to-end
2. **Check all 8 strips** - Make sure all GPIO pins work
3. **Measure FPS** - Confirm ~30 FPS sustained
4. **Try other animations** - Modify Python script

Once basic UART is solid, we can:
- Add back animation system
- Support multiple animations
- Add web UI for control
- Create systemd service

But first - let's make sure these 2 files work perfectly! 🎯

## Files You Need

```
ledgrid/
├── firmware/esp32/
│   ├── platformio.ini          ← Build config
│   └── src/
│       └── main.cpp             ← ESP32 firmware ⭐
│
├── led_controller_uart.py       ← Python controller ⭐
├── SIMPLE_SETUP.md              ← Usage instructions
└── SIMPLIFIED_SYSTEM.md         ← This file
```

Everything else is optional for now!


