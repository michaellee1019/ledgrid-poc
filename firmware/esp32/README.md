# ESP32-S3 DevKitC LED Controller Firmware

SPI slave firmware for controlling 8 strips of NeoPixel LEDs from a Raspberry Pi master.

## Hardware

**Board:** ESP32-S3-N16R8 DevKitC-1
- 16MB Flash
- 8MB PSRAM
- 240MHz CPU

## Pin Configuration

### SPI (VSPI Interface)
| Function | GPIO | Description |
|----------|------|-------------|
| MOSI | 11 | Data from Pi → ESP32 |
| MISO | 13 | Data from ESP32 → Pi (optional) |
| SCK | 12 | Clock from Pi |
| CS | 10 | Chip select from Pi |

### LED Data Pins
| Strip | GPIO | Max LEDs |
|-------|------|----------|
| 0 | 4 | 500 |
| 1 | 5 | 500 |
| 2 | 6 | 500 |
| 3 | 7 | 500 |
| 4 | 15 | 500 |
| 5 | 16 | 500 |
| 6 | 17 | 500 |
| 7 | 18 | 500 |

### Status LED
| Function | GPIO | Description |
|----------|------|-------------|
| RGB LED | 48 | Built-in DevKitC RGB LED |

## Building and Flashing

### Standard Build (Default)
```bash
cd firmware/esp32

# Build and upload
pio run -t upload

# Build, upload, and monitor
pio run -t upload && pio device monitor
```

### Debug Build (Verbose Logging)
For debugging SPI communication issues, use the debug environment:

```bash
cd firmware/esp32

# Build and upload with debug enabled
pio run -e esp32-s3-devkitc-1-debug -t upload

# Build, upload, and monitor with debug
pio run -e esp32-s3-devkitc-1-debug -t upload && pio device monitor
```

**Debug mode shows:**
- 🔧 Debug logging enabled message at startup
- 📥 Individual command reception (PING, CONFIG, SET_ALL, etc.)
- 📐 Detailed configuration changes
- ⚠️ Packet size mismatches and errors
- More verbose statistics

### Monitor Serial Output
```bash
pio device monitor
```

Press `Ctrl+C` to exit monitor.

## Wiring to Raspberry Pi

### SPI Connection
| Signal | Pi Pin | Pi GPIO | → | ESP32 GPIO |
|--------|--------|---------|---|------------|
| MOSI | 19 | GPIO 10 | → | GPIO 11 |
| SCLK | 23 | GPIO 11 | → | GPIO 12 |
| CS | 24 | GPIO 8 | → | GPIO 10 |
| MISO | 21 | GPIO 9 | → | GPIO 13 (optional) |
| GND | 6 | GND | → | GND |

**Critical:** GND connection is required!

### LED Strips
- **Power:** 5V (separate power supply recommended for >100 LEDs)
- **Data:** Connect to GPIO pins 4,5,6,7,15,16,17,18
- **Type:** WS2812B/NeoPixel compatible
- **Order:** GRB (handled by FastLED)

## Startup Sequence

1. **Serial init** (115200 baud)
2. **Pin configuration display**
3. **Rainbow animation** (10 seconds) - Tests LED hardware
4. **SPI initialization**
5. **Ready for commands**

During rainbow test, verify:
- All strips light up
- Colors cycle smoothly (Red → Orange → Yellow → Green → Cyan → Blue → Purple)
- No flickering or gaps

## SPI Protocol

### Commands
| Command | Code | Description |
|---------|------|-------------|
| SET_PIXEL | 0x01 | Set single pixel RGB |
| SET_BRIGHTNESS | 0x02 | Set global brightness (0-255) |
| SHOW | 0x03 | Update LEDs (not needed with SET_ALL) |
| CLEAR | 0x04 | Clear all LEDs to black |
| SET_RANGE | 0x05 | Set range of pixels |
| SET_ALL | 0x06 | Set all pixels (auto-shows) |
| CONFIG | 0x07 | Configure strips/LEDs/debug |
| STATS | 0x08 | Request statistics |
| PING | 0xFF | Connectivity test |

### Configuration (CMD_CONFIG)
```
Byte 0: 0x07 (CMD_CONFIG)
Byte 1: Number of strips (1-8)
Byte 2: LEDs per strip MSB
Byte 3: LEDs per strip LSB
Byte 4: Debug flag (0=off, 1=on)
```

The debug flag in CONFIG enables runtime debug logging (can override compile-time setting).

## Performance Metrics

Serial output shows every 5 seconds:
```
📊 Pkts=1234 Frames=567 FPS=15.2 | Success=99.5% Errors=0.5%
    Throughput: 1.2 MB/s | Latency: 12ms | CS=5000 SCK=250000 MOSI=200000
```

### Metrics Explained
- **Pkts:** Total SPI packets received
- **Frames:** Total LED frames rendered (CMD_SET_ALL count)
- **FPS:** Frames per second (last 5s average)
- **Success:** % of valid packets
- **Errors:** % of invalid/incomplete packets
- **Throughput:** MB/s data rate
- **Latency:** Average FastLED.show() time
- **CS/SCK/MOSI:** Edge counts on SPI pins (for wire debugging)

### Troubleshooting with Metrics

| Symptom | Cause | Fix |
|---------|-------|-----|
| `CS=0 SCK=0 MOSI=0` | No SPI connection | Check CS wire (Pi GPIO 8 → ESP32 GPIO 10) |
| `CS=X SCK=0 MOSI=0` | Clock not connected | **Most common!** Check SCK (Pi GPIO 11 → ESP32 GPIO 12) |
| `CS=X SCK=Y MOSI=0` | Data not connected | Check MOSI (Pi GPIO 10 → ESP32 GPIO 11) |
| `Success < 95%` | SPI speed too high | Reduce to 4 MHz on Pi |
| `FPS < 10` | Bottleneck elsewhere | Check Pi CPU/animation code |
| LEDs dark but stats good | Power/data pins | Check 5V power and GPIO 4-7,15-18 |

## Debug Modes Comparison

### Standard Build (Default)
- Shows summary statistics every 5 seconds
- Shows critical errors only
- Lower serial output overhead
- **Use for production**

### Debug Build (Compile-time)
```bash
pio run -e esp32-s3-devkitc-1-debug -t upload
```
- Debug enabled from boot
- Shows every command received
- Shows config changes in detail
- Shows packet validation warnings
- **Use for initial setup and troubleshooting**

### Runtime Debug (CONFIG command)
- Python sends `--debug` flag
- Enables debug after CONFIG received
- Can be toggled without reflashing
- **Use for live debugging**

## Memory Usage

```
RAM:   17.2% (56KB / 327KB)
Flash: 10.3% (345KB / 3.3MB)
```

Plenty of headroom for future features!

## FastLED Configuration

- **LED Type:** WS2812B
- **Color Order:** GRB
- **Max LEDs:** 4000 (8 strips × 500 LEDs)
- **Current Config:** 1120 (8 strips × 140 LEDs)
- **Brightness:** Controlled via CMD_SET_BRIGHTNESS
- **Refresh Rate:** Limited by SPI data rate (~20-30 FPS typical)

## Troubleshooting

### ESP32 Not Detected
1. Hold **BOOT** button while plugging in USB
2. Try different USB cable (data capable)
3. Check device appears: `pio device list`

### Rainbow Works, No SPI Data
1. Check CS connection (most common!)
2. Verify Pi has SPI enabled: `ls -l /dev/spidev*`
3. Check GND connection
4. Monitor edge counts in serial output

### High Error Rate
1. Reduce SPI speed to 4 MHz on Pi
2. Check for loose connections
3. Try shorter wires (<30cm)
4. Verify common ground

### LEDs Don't Light (but SPI works)
1. Check 5V power to LED strips
2. Verify data pins match code
3. Try level shifter (3.3V → 5V)
4. Test one strip at a time

## Links

- [ESP32-S3 Datasheet](https://www.espressif.com/sites/default/files/documentation/esp32-s3_datasheet_en.pdf)
- [FastLED Library](https://github.com/FastLED/FastLED)
- [PlatformIO ESP32](https://docs.platformio.org/en/latest/platforms/espressif32.html)
