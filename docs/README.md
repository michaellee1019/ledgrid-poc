# LED Grid - ESP32 XIAO S3

A high-performance SPI-controlled LED system using the Seeed XIAO ESP32-S3 board with 7-channel NeoPixel control.

## System Architecture

- **ESP32 XIAO S3**: Acts as SPI slave, receives commands and drives 7 LED strips (D0-D6)
- **Controller (Python)**: Acts as SPI master, sends pixel data and commands

## Hardware

- **Board**: Seeed XIAO ESP32-S3
- **Features**: 7 parallel NeoPixel outputs using FastLED
- **Communication**: SPI slave with DMA
  - SCK: GPIO 7
  - MISO: GPIO 8
  - MOSI: GPIO 9
  - CS: GPIO 44
- **LED Strips**: D0-D6 (GPIO 1-6, 43)
- **Default Configuration**: 7 strips × 140 LEDs = 980 total LEDs

### Wiring (ESP32 XIAO S3 to Raspberry Pi)
| ESP32 XIAO S3 | Raspberry Pi |
|---------------|--------------|
| GPIO 9 (MOSI) | GPIO 10 (Pin 19, MOSI) |
| GPIO 7 (SCK) | GPIO 11 (Pin 23, SCLK) |
| GPIO 44 (CS) | GPIO 8 (Pin 24, CE0) |
| GPIO 8 (MISO) | GPIO 9 (Pin 21, MISO) - optional |
| GND | GND (Pin 6, 9, 14, 20, 25, 30, 34, or 39) |

**Important:** See [HARDWARE.md](HARDWARE.md) for detailed wiring instructions and troubleshooting.

## Firmware Setup

1. Install [PlatformIO](https://platformio.org/)
2. Build and upload the firmware:
   ```bash
   cd firmware/esp32
   pio run --target upload
   ```
3. Monitor the serial output:
   ```bash
   pio device monitor
   ```

## Python Controller Setup

1. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Enable SPI on Raspberry Pi:
   ```bash
   sudo raspi-config
   # Interface Options -> SPI -> Enable -> Reboot
   ```

## Usage

### Start the Animation Server

The system has two modes:

**Web Mode** (default - provides web interface):
```bash
python3 scripts/start_server.py --mode web
```

**Controller Mode** (drives the LEDs):
```bash
python3 scripts/start_server.py --mode controller
```

For production, run both modes in separate terminals or use the deployment script.

### Direct LED Control (Legacy)

You can also control LEDs directly:

```bash
# Rainbow animation
python3 -m drivers.spi_controller rainbow

# Solid color
python3 -m drivers.spi_controller solid

# Test strips
python3 -m drivers.spi_controller test
```

## SPI Protocol

The firmware supports the following commands:

| Command | Byte | Description | Data Format |
|---------|------|-------------|-------------|
| SET_PIXEL | 0x01 | Set single pixel | `[cmd][pixelH][pixelL][R][G][B]` |
| SET_BRIGHTNESS | 0x02 | Set brightness | `[cmd][brightness]` |
| SHOW | 0x03 | Update display | `[cmd]` |
| CLEAR | 0x04 | Clear all pixels | `[cmd]` |
| SET_RANGE | 0x05 | Set multiple pixels | `[cmd][startH][startL][count][R][G][B]...` |
| SET_ALL | 0x06 | Set all pixels at once | `[cmd][R][G][B][R][G][B]...` |
| CONFIG | 0x07 | Configure strips | `[cmd][strips][lenH][lenL][debug]` |
| PING | 0xFF | Test connection | `[cmd]` |

## Configuration

### Firmware (`firmware/esp32/src/main.cpp`)
- `DEFAULT_STRIPS`: Number of LED strips (default: 7)
- `DEFAULT_LEDS_PER_STRIP`: LEDs per strip (default: 140)
- `MAX_STRIPS`: Maximum strips supported (default: 7)
- `MAX_LEDS_PER_STRIP`: Maximum LEDs per strip (default: 500)
- SPI pins: GPIO 7 (SCK), GPIO 8 (MISO), GPIO 9 (MOSI), GPIO 44 (CS)
- LED pins: GPIO 1-6, 43 (D0-D6)

### Python (`drivers/led_layout.py`)
- `DEFAULT_STRIP_COUNT`: Total number of strips (default: 7)
- `DEFAULT_LEDS_PER_STRIP`: LEDs per strip (default: 140)

## The ESP32 XIAO S3 Board

The ESP32 XIAO S3 uses FastLED to drive 7 NeoPixel strips and hardware SPI slave with DMA for high-performance communication. The board is compact and provides enough GPIO pins for 7 LED strips while reserving pins for SPI communication.

## Troubleshooting

### Check SPI Connection

Verify SPI is enabled on Raspberry Pi:
```bash
ls -l /dev/spidev*
# Should see: /dev/spidev0.0 and /dev/spidev0.1
```

Test SPI communication:
```bash
python3 -c "import spidev; spi=spidev.SpiDev(); spi.open(0,0); print('SPI OK')"
```

### Common Issues

**"SPI device not found"**
1. Enable SPI on Raspberry Pi: `sudo raspi-config` → Interface Options → SPI
2. Reboot after enabling: `sudo reboot`
3. Check if `/dev/spidev0.0` exists

**"No response from ESP32"**
1. Make sure the ESP32 firmware is uploaded and running
2. Check the serial monitor output: `cd firmware/esp32 && pio device monitor`
3. Verify SPI wiring (see [HARDWARE.md](HARDWARE.md)):
   - ESP32 GPIO 9 (MOSI) ← Raspberry Pi GPIO 10 (Pin 19)
   - ESP32 GPIO 7 (SCK) ← Raspberry Pi GPIO 11 (Pin 23)
   - ESP32 GPIO 44 (CS) ← Raspberry Pi GPIO 8 (Pin 24)
   - Common GND connection
4. **Most common issue**: SCK wire not connected properly

**"Permission denied"**
- Run with sudo: `sudo python3 -m drivers.spi_controller rainbow`
- Or add user to spi group: `sudo usermod -a -G spi $USER` (logout/login required)

**"LEDs not lighting up"**
- 3.3V logic level issue - WS2812B LEDs prefer 5V logic
- Use a level shifter (74AHCT125 or similar) between ESP32 and LED data line
- Check LED power supply (5V, adequate amperage)
- Verify data pin connections (D0-D6)

For detailed troubleshooting, see [HARDWARE.md](HARDWARE.md).
