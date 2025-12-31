# ESP32 XIAO S3 SPI Slave LED Controller

High-performance LED controller using ESP32-S3's hardware SPI slave with DMA.

## Hardware

- **Board**: Seeed XIAO ESP32-S3
- **LEDs**: WS2812B/NeoPixels (7 strips on D0-D6, default 140 LEDs per strip = 980 total)
- **SPI Master**: Raspberry Pi

## Wiring

```
Raspberry Pi          →  XIAO ESP32-S3
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GPIO 10 (MOSI)       →  GPIO9  (SPI MOSI)
GPIO 11 (SCLK)       →  GPIO7  (SPI SCK)
GPIO 8  (CE0)        →  GPIO44 (SPI CS)
GPIO 9  (MISO)       →  GPIO8  (SPI MISO) [optional]
GND                  →  GND

LED Strips:
Strip 0 Data         →  GPIO1  (D0)
Strip 1 Data         →  GPIO2  (D1)
Strip 2 Data         →  GPIO3  (D2)
Strip 3 Data         →  GPIO4  (D3)
Strip 4 Data         →  GPIO5  (D4)
Strip 5 Data         →  GPIO6  (D5)
Strip 6 Data         →  GPIO43 (D6)
```

**Note**: The XIAO ESP32-S3 uses GPIO 7, 8, 9, 44 for SPI communication, leaving D0-D6 (GPIO 1-6, 43) for LED strips.

## Setup

### 1. Install PlatformIO

```bash
cd firmware/esp32
pio run --target upload
pio device monitor
```

### 2. On Raspberry Pi

Ensure SPI is enabled and set to Mode 3:

```bash
# SPI should already be configured for Mode 3 from previous setup
python3 ../test_hardware_spi.py rainbow 10 10
```

## Features

- ✅ **Hardware SPI Slave** with DMA - no CPU overhead
- ✅ **SPI Mode 3** - Most reliable for ESP32
- ✅ **FastLED** - Optimized LED library
- ✅ **Full command protocol** - All LED commands supported
- ✅ **High performance** - Can handle 60+ FPS
- ✅ **Statistics** - Packet/frame counters

## Performance

- **SPI Speed**: Up to 20 MHz (hardware limited)
- **Frame Rate**: 60+ FPS for full 160 LED updates
- **Latency**: < 1ms from SPI to LED update
- **CPU Usage**: Minimal (DMA handles transfers)

## Notes

### 3.3V Logic Level

The XIAO ESP32-S3 outputs 3.3V logic. WS2812B LEDs expect 5V logic (3.5V+ for HIGH).

**Temporary workaround** (until level shifter):
- May work but colors might be incorrect/dim
- Short wires help (<30cm)
- First LED acts as signal repeater

**Proper solution** (order these):
- 74AHCT125 or 74HCT245 level shifter
- Or SN74LV1T34 (single gate, cheap)
- Or dedicated WS2812 level shifter board

## Troubleshooting

### No data received
- Check wiring (especially GND!)
- Verify SPI enabled on Pi: `ls /dev/spidev*`
- Check serial monitor for errors

### LEDs not working  
- 3.3V logic issue - order level shifter
- Check LED power (5V, adequate amperage)
- Verify data pin connection

### Colors wrong
- Likely 3.3V logic level issue
- Try shorter wire to first LED
- Order level shifter

## Command Protocol

Same as RP2040 version - see `../test_hardware_spi.py` for examples.


