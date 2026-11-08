# Installed hardware and wiring

A Raspberry Pi 5 Model B sends full RGB frames over two SPI buses to five
ESP32-S3 receivers on V6 carriers. Four receivers drive eight WS2812-compatible
strips each. The fifth drives the rightmost strip, for 33 × 138 LEDs (4,554
pixels). The fifth receiver receives one logical strip and repeats its signal
on all eight output pins. Every receiver uses SPI mode 0 at 20 MHz, with CRC16
on payloads and CRC32 on status replies.

## SPI routes and strip order

| Logical receiver | SPI route | Chip-select GPIO (header pin) | Logical columns, zero based | Strip order |
| ---: | --- | --- | --- | --- |
| 0 | `spidev1.0` | GPIO22 (15) | 8–15 | Forward |
| 1 | `spidev1.1` | GPIO17 (11) | 0–7 | Forward |
| 2 | `spidev0.0` | GPIO8 (24) | 24–31 | Reversed |
| 3 | `spidev0.1` | GPIO7 (26) | 16–23 | Reversed |
| 4 | `spidev0.3` | GPIO6 (31) | 32 | Single strip |

The physical left-to-right receiver order is `(1, 0, 3, 2, 4)`. The host maps
each strip before sending it. USB flashing identity is a separate mapping in
`data/run_state/receiver_identity_mapping.json`. The table follows
[the installed layout](../drivers/led_layout.py), established with the
column-sequence pattern.

| Signal | Pi SPI0 GPIO (header pin) | Pi SPI1 GPIO (header pin) | ESP32-S3 GPIO |
| --- | --- | --- | ---: |
| MOSI | GPIO10 (19) | GPIO20 (38) | 11 |
| MISO | GPIO9 (21) | GPIO19 (35) | 13 |
| SCLK | GPIO11 (23) | GPIO21 (40) | 12 |
| CS | See route table | See route table | 10 |

The carrier jumpers route `SJ_SPI1_CE0` to GPIO22 and `SJ_SPI0_CE3` to GPIO6.
The `SJ_SPI0_CE2` position on GPIO5 (header pin 29) is unpopulated. Linux exposes
`/dev/spidev0.2` through the same overlay.

Deployment configures `dtparam=spi=on`, `dtoverlay=spi0-4cs`, and
`dtoverlay=spi1-2cs,cs0_pin=22`. Reboot if deployment reports that the boot
configuration changed. See [Deployment](DEPLOYMENT.md).

## Receiver hardware

All five receivers use the `esp32-s3-devkitc-1` PlatformIO environment for
ESP32-S3-WROOM-1U-N16R8 (16 MB flash, 8 MB octal PSRAM). The USB-C port connects
to the ESP32-S3 USB Serial/JTAG peripheral on GPIO19 (D−) and GPIO20 (D+).
The firmware LED output GPIOs in lane order are 18, 17, 16, 15, 7, 6, 5, and 4.
GPIO48 drives the status LED. See [receiver firmware](../firmware/esp32/README.md).

The two SPI buses run concurrently. Each bus shares clock, MOSI, and MISO
among its receivers, with a dedicated chip select for each. MISO returns the
protected LGS8 status snapshot, including accepted and displayed frame counts
and error counters.

The installed controller uses three-phase LED edge staggering. At brightness
80, both three-phase Sparkle trials exceeded 150 FPS on all five receiver
display counters with zero new errors. One of two one-phase trials recorded
two payload CRC errors. The three-phase setting meets the output-rate goal
with clean transport in the measured conditions.

Supply the LED strips from separately fused 5 V distribution sized for the
installation. Join the Pi, receivers, level shifters, and LED supply grounds.
The Pi-to-ESP SPI bus uses 3.3 V logic. Use 3.3-to-5 V level shifting on LED data
and keep each data wire close to its ground return. Verify actual parts, fuses,
power injection, and current limits on the assembled hardware before repair
or reproduction.

## Bring-up and diagnostics

1. With LED power off, continuity-check ground, chip select, and both SPI buses.
2. Verify the five mapped USB serials and SPI routes before flashing. Use
   `just test-firmware` to build and test the installed target locally.
3. Check the five expected `/dev/spidev*` routes after boot. Check fresh protected
   status on each route.
4. After wiring changes, verify receiver order with distinct colors, then use
   a within-receiver strip ramp and moving content across receiver boundaries.
5. Use `just output-rate-observation 15` to record controller cadence and
   per-receiver output-counter rates and errors. Observe the wall for visible
   flicker, orientation, and stutter.

For rising CRC or status errors, check signal/ground pairs and chip-select
isolation. For a dark or flickering lane with clean receiver counters, inspect
its LED data wiring, level shifter, power injection, and ground. See
[Debugging](DEBUGGING.md) for the telemetry endpoints and
[Rendering acceptance](RENDERING_PIPELINE_ACCEPTANCE.md) for performance checks.
