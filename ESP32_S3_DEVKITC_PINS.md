# ESP32-S3-N16R8 DevKitC Pin Configuration

## Board Specifications
- **Chip:** ESP32-S3FH4R2
- **Flash:** 16MB (N16)
- **PSRAM:** 8MB Octal SPI RAM (R8)
- **USB:** Native USB support (GPIO19/20)
- **Built-in LED:** GPIO 48 (RGB LED)

## Pin Configuration for LED Controller

### SPI Pins (VSPI - Hardware SPI2)
| Function | GPIO | Notes |
|----------|------|-------|
| MOSI | GPIO 11 | Master Out, Slave In (data from Pi) |
| MISO | GPIO 13 | Master In, Slave Out (data to Pi) |
| SCLK | GPIO 12 | SPI Clock |
| CS | GPIO 10 | Chip Select |

### LED Strip Pins
| Strip | GPIO | Notes |
|-------|------|-------|
| Strip 0 | GPIO 4 | Safe for output |
| Strip 1 | GPIO 5 | Safe for output |
| Strip 2 | GPIO 6 | Safe for output |
| Strip 3 | GPIO 7 | Safe for output |
| Strip 4 | GPIO 15 | Safe for output |
| Strip 5 | GPIO 16 | Safe for output |
| Strip 6 | GPIO 17 | Safe for output |
| Strip 7 | GPIO 18 | Safe for output |

### Status LED
| Function | GPIO | Notes |
|----------|------|-------|
| Built-in RGB LED | GPIO 48 | Used for status indication |

## Wiring to Raspberry Pi

### SPI Bus Connection:
```
Raspberry Pi          ESP32-S3 DevKitC
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GPIO 10 (Pin 19)  →   GPIO 11 (MOSI)
GPIO 11 (Pin 23)  →   GPIO 12 (SCLK)
GPIO 8  (Pin 24)  →   GPIO 10 (CS)
GPIO 9  (Pin 21)  →   GPIO 13 (MISO) [optional]
GND     (Pin 6)   →   GND
```

### LED Strip Connections:
Connect your WS2812B/NeoPixel LED strips to:
- **Strip 0** → GPIO 4
- **Strip 1** → GPIO 5
- **Strip 2** → GPIO 6
- **Strip 3** → GPIO 7
- **Strip 4** → GPIO 15
- **Strip 5** → GPIO 16
- **Strip 6** → GPIO 17
- **Strip 7** → GPIO 18

## GPIO Notes

### Pins to Avoid:
- **GPIO 0:** Strapping pin (boot mode)
- **GPIO 3:** JTAG (if using debug)
- **GPIO 8, 9:** Strapping pins (affects boot/flash voltage)
- **GPIO 19, 20:** USB D-/D+ (needed for USB serial)
- **GPIO 26-32:** Used for PSRAM/Flash (internal)
- **GPIO 33-37:** Usually not broken out on DevKitC

### Safe GPIO Pins (Available for Future Expansion):
- GPIO 14, 15, 16, 17, 18, 21
- GPIO 38, 39, 40, 41, 42, 45, 46, 47

## Advantages Over XIAO S3

### More Available GPIOs:
- DevKitC: ~30 usable GPIOs
- XIAO S3: ~10 usable GPIOs

### Easier Debugging:
- Full JTAG support
- More broken-out pins

### More Resources:
- 16MB Flash (vs 8MB)
- 8MB PSRAM (vs 2MB)
- Better for complex applications

### Stable Power:
- USB-C power with better regulation
- Can handle more current for LED control

## Power Considerations

### USB Power:
- DevKitC can provide up to 500mA via USB
- For many LEDs, use external 5V power supply
- **Always connect GND** between Pi, ESP32, and LED power

### LED Power:
- Each WS2812B LED: ~60mA max (white at full brightness)
- 7 strips × 140 LEDs = 980 LEDs
- Max current: 980 × 60mA = **58.8A**
- Typical usage (~40%): **23.5A at 5V**
- Use adequate power supply with proper wiring

## Programming

### Upload Method:
1. **USB Serial (Automatic):**
   ```bash
   cd firmware/esp32
   pio run --target upload
   ```

2. **Manual Bootloader Mode (if needed):**
   - Hold BOOT button
   - Press RESET button
   - Release RESET
   - Release BOOT after 2 seconds
   - Run upload command

### Monitor Serial Output:
```bash
pio device monitor
```

## Dual Board Setup

For 14 strips (2 boards × 7 strips):
- Both boards use same pins
- Different CS wires:
  - Board 1: Pi GPIO 8 → ESP32 GPIO 10
  - Board 2: Pi GPIO 7 → ESP32 GPIO 10
- All other SPI pins shared

## Comparison: DevKitC vs XIAO S3

| Feature | XIAO S3 | DevKitC |
|---------|---------|---------|
| Size | Tiny (21×17.5mm) | Standard (55×28mm) |
| Flash | 8MB | 16MB |
| PSRAM | 2MB | 8MB |
| GPIO Count | ~10 usable | ~30 usable |
| USB | USB-C | USB-C |
| Built-in LED | GPIO 21 | GPIO 48 (RGB) |
| SPI Pins | GPIO 7,8,9,44 | GPIO 10,11,12,13 |
| Price | Lower | Higher |
| Best For | Space-constrained | Development, more features |

## Troubleshooting

### Board Not Detected:
1. Check USB cable (must support data)
2. Try different USB port
3. Hold BOOT button while connecting
4. Install USB drivers if needed

### SPI Not Working:
1. Verify pin connections with multimeter
2. Check GND is common
3. Try slower SPI speed (4MHz)
4. Monitor serial output for errors

### LEDs Not Lighting:
1. Check LED strip power (5V)
2. Verify data pin connections
3. Consider level shifter (3.3V→5V)
4. Test with simple animation first

## Performance

### Expected Specifications:
- **SPI Speed:** Up to 20MHz (use 8-12MHz for reliability)
- **Frame Rate:** 30+ FPS possible
- **Latency:** <1ms SPI to LED update
- **FastLED.show():** ~65-75µs per frame

### Optimization Tips:
- Use parallel mode for multi-board setups
- Keep SPI speed at 8MHz for stability
- Monitor packet error rate (<5% is good)
- Use PSRAM for large data buffers if needed


