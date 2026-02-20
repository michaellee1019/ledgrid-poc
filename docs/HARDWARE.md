# SPI Wiring Guide: Raspberry Pi to ESP32 XIAO S3

## Connection Table

| Signal | Raspberry Pi GPIO | **RPi Physical Pin** | ESP32 XIAO S3 Pin | Notes |
|--------|------------------|---------------------|-------------------|-------|
| **SCLK** | **GPIO 11** | **🔴 Pin 23** | **GPIO 7** | **CLOCK - Most common issue!** |
| **MOSI** | **GPIO 10** | **🔴 Pin 19** | **GPIO 9** | **Data from Pi to ESP32** |
| CS/CE0 | GPIO 8 | **Pin 24** | **GPIO 44** | Chip Select (active LOW) |
| MISO | GPIO 9 | Pin 21 | **GPIO 8** | Data from ESP32 to Pi (optional) |
| GND | GND | Pin 6, 9, 14, 20, 25, 30, 34, or 39 | **GND** | **CRITICAL: Common ground** |

### Quick Reference - Raspberry Pi Physical Pins:
```
        3.3V [ 1] [ 2] 5V
       GPIO2 [ 3] [ 4] 5V
       GPIO3 [ 5] [ 6] GND      ← Any GND works
       GPIO4 [ 7] [ 8] GPIO14
         GND [ 9] [10] GPIO15
      GPIO17 [11] [12] GPIO18
      GPIO27 [13] [14] GND
      GPIO22 [15] [16] GPIO23
        3.3V [17] [18] GPIO24
🔴 GPIO10 MOSI [19] [20] GND
      GPIO9 MISO [21] [22] GPIO25
🔴 GPIO11 SCLK [23] [24] GPIO8 CE0  ← Use this for CS
         GND [25] [26] GPIO7 CE1
```

### ESP32 XIAO S3 Pin Layout:
- **D0-D6**: GPIO1-6, GPIO43 (LED strip outputs)
- **SPI Pins**: GPIO7 (SCK), GPIO8 (MISO), GPIO9 (MOSI), GPIO44 (CS)
- **Power**: 5V, 3.3V, GND

## Physical Setup

1. **Power the ESP32 XIAO S3** - Connect USB-C cable to XIAO S3 for power and debugging
2. **Connect SPI wires** - Use female-to-female jumper wires
3. **Connect GND** - Mandatory for signal reference
4. **Verify connections** - Double-check each wire before powering on

## Verification Steps

### On Raspberry Pi:
```bash
# 1. Check SPI is enabled
ls -l /dev/spidev*
# Should see: /dev/spidev0.0 and /dev/spidev0.1

# 2. Enable SPI if not found
sudo raspi-config
# Interface Options -> SPI -> Enable -> Reboot

# 3. Test SPI master
python3 -c "import spidev; spi=spidev.SpiDev(); spi.open(0,0); print('SPI OK')"

# 4. Run LED controller
python3 -m drivers.spi_controller rainbow
```

### On ESP32 XIAO S3 (via Serial Monitor):
You should see:
```
========================================
ESP32 XIAO S3 SPI Slave LED Controller
========================================
Board: ESP32-S3FN8
Strips: 7 x 140 LEDs = 980 total

Pin mapping:
SPI:
  MOSI: GPIO 9
  MISO: GPIO 8
  SCK:  GPIO 7
  CS:   GPIO 44
LED Strips (D0-D6):
  Strip 0 (D0): GPIO 1
  Strip 1 (D1): GPIO 2
  Strip 2 (D2): GPIO 3
  Strip 3 (D3): GPIO 4
  Strip 4 (D4): GPIO 5
  Strip 5 (D5): GPIO 6
  Strip 6 (D6): GPIO 43

✅ SPI slave ready
```

## Troubleshooting

### Problem: "SCK never toggled! Check SCK wire (GPIO 7)" (MOST COMMON)
**This is the most common wiring issue!**

The ESP32 XIAO S3 detects CS assertions but never sees clock pulses.

**Solution:**
1. **Verify physical connection:**
   - Raspberry Pi **Physical Pin 23** (GPIO 11) → ESP32 XIAO S3 **GPIO 7** (SCK)
   - This is Pin 23 on the Pi (bottom row, 12th pin from the left)
   - Double-check you're counting correctly on the Pi header
   
2. **Test with verification script:**
   ```bash
   python3 verify_pi_wiring.py
   ```
   This will toggle each pin so you can verify with a multimeter

3. **Common mistakes:**
   - Using Pin 22 instead of Pin 23 (easy to miscount)
   - Wire connected to wrong header pin
   - Loose connection
   - ESP32 side connected to wrong GPIO

### Problem: CS detected but no data after SCK toggles
**Solution:**
- **Most likely:** MOSI (GPIO 10 → GPIO 9) not connected
- **Check:** Raspberry Pi **Physical Pin 19** (GPIO 10) → ESP32 XIAO S3 **GPIO 9** (MOSI)
- **Verify:** Use multimeter in continuity mode

### Problem: No CS activity detected
**Solution:**
- **Most likely:** CS (GPIO 8 → GPIO 44) not connected
- **Check:** Raspberry Pi **Physical Pin 24** (GPIO 8) → ESP32 XIAO S3 **GPIO 44** (CS)

### Problem: Device not detected or erratic behavior
- **Most likely:** No common ground
- **Fix:** Connect any GND pin from Pi to GND on ESP32 XIAO S3
- **Critical:** GND MUST be connected for any signals to work

## Notes on ESP32 XIAO S3 GPIO Pins

The ESP32 XIAO S3 uses specific pins for different functions:
- **D0-D6 (GPIO 1-6, 43)**: NeoPixel LED outputs (7 strips total)
- **SPI Pins**: GPIO 7, 8, 9, 44
  - GPIO 7: **SCK** (SPI Clock)
  - GPIO 8: **MISO** (SPI Master In, Slave Out)
  - GPIO 9: **MOSI** (SPI Master Out, Slave In - receives data from Pi)
  - GPIO 44: **CS** (Chip Select)
- **GPIO 21**: Built-in LED (used for status indication)
- **Other pins**: Available for future expansion

**CRITICAL:** 
1. Do not use GPIO 7, 8, 9, 44 for LED strips - they are reserved for SPI communication
2. The XIAO S3 has limited GPIO pins, so we use 7 strips instead of 8
3. GPIO 43 (D6) is used for the 7th LED strip

## Multi-Device Setup (4 ESP32 Boards)

The system supports multiple ESP32 XIAO S3 boards for more LED strips.

### Configuration for 4 Boards (SPI0 + SPI1):

The system uses a dual-bus configuration since most Raspberry Pi OS versions only expose 2 CS lines per bus:
- **Board 1:** `/dev/spidev0.0` (SPI0 CE0)
- **Board 2:** `/dev/spidev0.1` (SPI0 CE1)
- **Board 3:** `/dev/spidev1.0` (SPI1 CE0)
- **Board 4:** `/dev/spidev1.1` (SPI1 CE1)

> Note: The deployment script automatically configures `dtoverlay=spi0-4cs` and `dtoverlay=spi1-2cs` in `/boot/firmware/config.txt`.

### Wiring Table for Boards 1–2 (SPI0):

| Signal | Pi GPIO | Pi Pin | ESP32 #1 | ESP32 #2 | Notes |
|--------|---------|--------|----------|----------|-------|
| MOSI | GPIO 10 | Pin 19 | GPIO 9 | GPIO 9 | SPI0 MOSI |
| SCLK | GPIO 11 | Pin 23 | GPIO 7 | GPIO 7 | SPI0 SCLK |
| MISO | GPIO 9 | Pin 21 | GPIO 8 | GPIO 8 | SPI0 MISO (optional) |
| CE0 | GPIO 8 | Pin 24 | GPIO 44 | - | Board 1 CS |
| CE1 | GPIO 7 | Pin 26 | - | GPIO 44 | Board 2 CS |
| GND | GND | Multiple | GND | GND | **Must be common!** |

## Troubleshooting

### Brightness Flickering / Random Brightness Changes

**Symptoms:** LEDs randomly get brighter or dimmer, especially during periods of activity.

**Root Cause:** SPI packet corruption can cause the ESP32 to misinterpret data bytes as brightness commands. When `CMD_SET_ALL` packets are corrupted or truncated, data bytes (which can be any value 0-255) may be accidentally interpreted as `CMD_SET_BRIGHTNESS` commands.

**Diagnosis:**
1. Monitor ESP32 serial output for warnings like:
   ```
   ⚠️ CMD_SET_ALL expected 3361 bytes, got 2333
   ```
2. Measure current draw during flickering - should remain stable if it's a software issue
3. Measure 5V rail voltage - should remain stable at ~4.7-5.0V

**Solutions:**

1. **Software mitigation (implemented):**
   - Disabled periodic brightness refresh commands in `spi_controller.py` to reduce opportunities for corruption
   - Brightness is now only set at initialization, not refreshed every 30 seconds

2. **Firmware fix (implemented):**
   - Added packet validation to reject corrupted brightness commands (length > 10 bytes)
   - Legitimate brightness commands are exactly 2 bytes; longer packets are rejected
   - Check ESP32 serial logs for `⚠️ Rejecting corrupt brightness packet` messages

3. **Improved SPI reliability (implemented):**
   - **Default SPI speed lowered to 2 MHz** in deployment scripts
   - To adjust SPI speed, set the `SPI_SPEED` environment variable before deploying:
     ```bash
     SPI_SPEED=1000000 just deploy  # 1 MHz (very conservative)
     SPI_SPEED=2000000 just deploy  # 2 MHz (default)
     SPI_SPEED=4000000 just deploy  # 4 MHz (if cables are good)
     ```
   - Or manually when running: `python scripts/start_server.py --mode controller --spi-speed 2000000`
   - Use shorter, high-quality SPI cables (under 20cm)
   - Ensure common ground connection between all devices
   - Add pull-up/pull-down resistors on CS lines if needed

4. **Power supply sizing:**
   - Test setup (4 strips × 20 LEDs = 80 LEDs): 2A @ 5V is adequate for low brightness
   - Production setup (32 strips × 140 LEDs = 4,480 LEDs): Requires 20-30A minimum for typical use, up to 200A+ for full white at maximum brightness
   - The provided 600A @ 5V power supply is appropriately sized for the full installation

### Wiring Table for Boards 3–4 (SPI1):

| Signal | Pi GPIO | Pi Pin | ESP32 #3 | ESP32 #4 | Notes |
|--------|---------|--------|----------|----------|-------|
| MOSI | GPIO 20 | Pin 38 | GPIO 9 | GPIO 9 | SPI1 MOSI |
| SCLK | GPIO 21 | Pin 40 | GPIO 7 | GPIO 7 | SPI1 SCLK |
| MISO | GPIO 19 | Pin 35 | GPIO 8 | GPIO 8 | SPI1 MISO (optional) |
| CE0 | GPIO 17 | Pin 11 | GPIO 44 | - | SPI1 CE0 |
| CE1 | GPIO 18 | Pin 12 | - | GPIO 44 | SPI1 CE1 |
| GND | GND | Multiple | GND | GND | **Must be common!** |

### Key Points:
- ✅ MOSI, SCLK, MISO are **wired to all boards in parallel**
- ✅ Each board has its own CS (Chip Select) wire
- ✅ All boards run the **same firmware**
- ✅ GND **must be common** across all devices
- ✅ System automatically manages which board to talk to

### Testing 4 Boards:
Run the SPI verification steps and confirm `/dev/spidev0.0` through `/dev/spidev0.3` respond.

## LED Strip Connections

Connect your WS2812B/NeoPixel LED strips to the following pins on the ESP32 XIAO S3:
- **Strip 0** → D0 (GPIO 1)
- **Strip 1** → D1 (GPIO 2)
- **Strip 2** → D2 (GPIO 3)
- **Strip 3** → D3 (GPIO 4)
- **Strip 4** → D4 (GPIO 5)
- **Strip 5** → D5 (GPIO 6)
- **Strip 6** → D6 (GPIO 43)

Each strip should also have:
- **Power**: Connect 5V and GND to your LED power supply
- **Data**: Connect to the corresponding GPIO pin above
- **Ground**: Ensure all grounds are connected together (Pi, ESP32, LED power supply)
