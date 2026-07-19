# ESP32-S3 LED Receiver Firmware

Firmware for an ESP32-S3-N16R8 that receives RGB frames from a Raspberry Pi
over 20 MHz SPI and drives eight WS2812 lanes in parallel.

## Hardware

- Board: ESP32-S3-DevKitC-1-N16R8V
- Flash: 16 MB
- PSRAM: 8 MB
- Default geometry: 8 strips × 140 LEDs
- Maximum geometry: 8 strips × 500 LEDs

| Function | GPIO |
|---|---:|
| SPI MOSI | 11 |
| SPI MISO | 13 |
| SPI SCLK | 12 |
| SPI CS | 10 |
| LED strip 0 | 18 |
| LED strip 1 | 17 |
| LED strip 2 | 16 |
| LED strip 3 | 15 |
| LED strip 4 | 7 |
| LED strip 5 | 6 |
| LED strip 6 | 5 |
| LED strip 7 | 4 |
| Status LED | 48 |

The Raspberry Pi and ESP32 must share ground. WS2812 power is supplied separately.

## Architecture

The receiver deliberately separates transport and display work:

1. Two SPI slave DMA transactions are kept queued.
2. The Arduino loop consumes completed packets, checks CRC-16, and updates a
   compact RGB working frame.
3. Complete frames are published to a three-slot latest-frame-wins mailbox.
4. A FreeRTOS display task on the other core converts RGB to an eight-bit parallel
   WS2812 waveform.
5. ESP-IDF LCD/I80 DMA emits all eight strips concurrently.

The firmware does not use FastLED. At 2.4 MHz, each WS2812 bit is encoded as three
samples (`100` for zero and `110` for one). A 140-pixel frame contains 4.2 ms of
pixel data followed by 300 us reset-low time.

## Building and testing

```bash
cd firmware/esp32

# Portable encoder, mailbox, and status-protocol tests
pio test -e native

# Exact production target
pio run -e esp32-s3-devkitc-1

# Upload one controller
pio run -e esp32-s3-devkitc-1 -t upload --upload-port /dev/ttyACM0
```

The production target uses the pioarduino stable platform with Arduino 3.3.9 and
ESP-IDF 5.5.4. The board target must remain `esp32-s3-devkitc1-n16r8` so PSRAM and
flash timing match the installed controllers.

## SPI commands

Every command is followed by a big-endian CRC-16/CCITT-FALSE.

| Command | Code | Payload |
|---|---:|---|
| SET_PIXEL | `0x01` | pixel high, pixel low, R, G, B |
| SET_BRIGHTNESS | `0x02` | brightness 0–255 |
| SHOW | `0x03` | none; publish the working frame |
| CLEAR | `0x04` | none; clear and publish |
| SET_RANGE | `0x05` | start high, start low, count, RGB bytes |
| SET_ALL | `0x06` | tightly packed RGB bytes; publishes inline |
| CONFIG | `0x07` | strips, length high, length low, optional debug byte |
| PING | `0xFF` | none |

SET_PIXEL and SET_RANGE modify the working frame. SHOW publishes their combined
result. SET_ALL, CLEAR, brightness changes, and geometry changes publish inline.

## Receiver status v2

The ESP32 returns a 64-byte `LGS2` snapshot over MISO alongside normal writes.
It includes:

- SPI packets, valid CRCs, and CRC errors;
- currently queued transactions;
- accepted, displayed, superseded, and publish-dropped frames;
- SPI queue and display errors;
- CRC, frame-copy, waveform-encode, and LCD/I80 DMA timings;
- last accepted and displayed sequence numbers.

The host exposes these fields through `/api/status` and `/api/metrics`. Run the
automated canary gate with:

```bash
python tools/benchmarks/receiver_acceptance.py \
  --base-url http://ledgridwall.local:5000 \
  --device 0 --duration 60 --animation rainbow
```

See [rendering acceptance](../../docs/RENDERING_PIPELINE_ACCEPTANCE.md) for the
required thresholds and rollback conditions.
