# 🚀 START HERE - Simplified LED Controller

## What You Have Now

**2 files, super simple:**

1. **ESP32 Firmware** - `firmware/esp32/src/main.cpp` (UART-only, no SPI)
2. **Python Controller** - `led_controller_uart.py` (hardcoded sparkle animation)

**No complex system.** No web server. No systemd. Just flash and run! 🎉

---

## Step-by-Step Testing

### Step 1: Flash ESP32 Firmware

```bash
cd firmware/esp32

# Flash it
pio run --target upload

# Monitor it
pio device monitor --baud 921600
```

**You should see:**
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

**✅ If you see rainbow on your LED strips for 1 second, LEDs are wired correctly!**

Press `Ctrl+C` then `Ctrl+]` to exit monitor.

---

### Step 2: Test UART Communication (Optional but Recommended)

This tests if ESP32 responds to commands:

```bash
# Find your ESP32 port
ls /dev/ttyACM*     # Linux/Pi
ls /dev/cu.usbmodem*  # Mac

# Run test
python3 test_uart_simple.py /dev/ttyACM0
# or on Mac:
python3 test_uart_simple.py /dev/cu.usbmodem101
```

**You should see:**
- LEDs turn RED
- LEDs turn GREEN
- LEDs turn BLUE
- LEDs turn OFF

**✅ If colors change, UART communication works!**

---

### Step 3: Copy to Raspberry Pi

```bash
# Copy the Python script to Pi
scp led_controller_uart.py pi@<your-pi-ip>:~/

# Also copy test script (optional)
scp test_uart_simple.py pi@<your-pi-ip>:~/
```

---

### Step 4: Run on Raspberry Pi

```bash
# SSH to Pi
ssh pi@<your-pi-ip>

# Install pyserial (if not already installed)
pip3 install pyserial

# Plug ESP32 into Pi via USB

# Check ESP32 is detected
ls -l /dev/ttyACM*

# Run the sparkle animation!
python3 ~/led_controller_uart.py
```

**You should see:**
```
======================================================================
UART LED Controller - Sparkle Animation
======================================================================
Port: /dev/ttyACM0
Baudrate: 921600
Strips: 8
LEDs per strip: 140
Total LEDs: 1120
======================================================================

Connecting to /dev/ttyACM0...
✓ Connected!

Initializing ESP32...
Sending PING...
Configuring: 8 strips x 140 LEDs = 1120 total
Setting brightness: 50
Clearing LEDs...

✓ Initialization complete!

Starting sparkle animation...
Press Ctrl+C to stop

📊 Frame    100 | FPS:   30.2 avg,   30.1 recent
📊 Frame    200 | FPS:   30.5 avg,   30.8 recent
...
```

**✅ Your LEDs should sparkle! Random pixels lighting up and fading.**

Press `Ctrl+C` to stop.

---

## Troubleshooting

### No `/dev/ttyACM0`

```bash
# Check what's connected
ls /dev/tty* | grep -E "(ACM|USB)"

# Try /dev/ttyUSB0 if ACM0 not found
python3 ~/led_controller_uart.py --port /dev/ttyUSB0
```

### Permission Denied

```bash
sudo usermod -a -G dialout $USER
# Then logout and login
```

### ESP32 Not Responding

1. **Unplug and replug USB**
2. **Press RESET button on ESP32**
3. **Check ESP32 monitor:**
   ```bash
   cd firmware/esp32
   pio device monitor --baud 921600
   ```

### LEDs Not Lighting

**Check wiring:**
- LED power supply: 5V connected
- LED ground: Common with ESP32 ground
- LED data pins:
  ```
  Strip 0 → GPIO 4
  Strip 1 → GPIO 5
  Strip 2 → GPIO 6
  Strip 3 → GPIO 7
  Strip 4 → GPIO 15
  Strip 5 → GPIO 16
  Strip 6 → GPIO 17
  Strip 7 → GPIO 18
  ```

**Test one strip first!**

---

## What's Next?

Once sparkle is working:

1. **Verify all 8 strips** - Check each one lights up
2. **Measure FPS** - Should see ~30 FPS in output
3. **Customize** - Edit `led_controller_uart.py` to change:
   - `SPARKLE_DECAY` - Fade speed
   - `SPARKLE_RATE` - How many sparkles
   - `SPARKLE_COLOR` - Sparkle color
   - `TARGET_FPS` - Frame rate

4. **Add more animations** - Copy the `SparkleAnimation` class and make your own!

---

## Files Summary

```
ledgrid/
├── firmware/esp32/
│   ├── platformio.ini
│   └── src/
│       └── main.cpp              ⭐ ESP32 firmware (UART)
│
├── led_controller_uart.py        ⭐ Python controller (sparkle)
├── test_uart_simple.py           🧪 Quick test script
│
├── START_HERE.md                 📖 This file
├── SIMPLE_SETUP.md               📖 Detailed setup guide
└── SIMPLIFIED_SYSTEM.md          📖 What changed
```

**Just 2 files to make it work!** Everything else is documentation. 📚

---

## Need Help?

**Check ESP32 serial monitor** to see what's happening:
```bash
cd firmware/esp32
pio device monitor --baud 921600
```

Look for:
- `🔍 Pkt#X` - Packets received
- `✅ CMD_SET_ALL` - Frames being sent
- `📊 FPS=30.2` - Stats every 5 seconds
- `Errors=0` - Should be zero!

**Still stuck?** Check `SIMPLE_SETUP.md` for more detailed troubleshooting.

---

Good luck! ✨


