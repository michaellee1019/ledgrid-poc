# Hardware and wiring

The installed wall uses a Raspberry Pi and four ESP32-S3-DevKitC-1-N16R8
receivers. Each receiver drives eight WS2812 lanes of 138 LEDs. Firmware keeps a
140-LED-per-lane buffer ceiling, but the installed host geometry is 32 x 138.

## Receiver pins

All four receivers run the same firmware.

| Function | ESP32-S3 GPIO |
| --- | ---: |
| SPI MOSI | 11 |
| SPI MISO | 13 |
| SPI SCLK | 12 |
| SPI CS | 10 |
| LED lanes 0-7 | 18, 17, 16, 15, 7, 6, 5, 4 |
| Status LED | 48 |

The board target is `esp32-s3-devkitc1-n16r8` with 16 MB flash and 8 MB PSRAM.
See [receiver firmware](../firmware/esp32/README.md) for build and protocol
details.

## Raspberry Pi buses

Boards on the same bus share clock, MOSI, and optional MISO; each board has its
own chip select. All grounds must be common.

| Bus signal | Pi GPIO | Physical pin |
| --- | ---: | ---: |
| SPI0 MOSI | 10 | 19 |
| SPI0 MISO | 9 | 21 |
| SPI0 SCLK | 11 | 23 |
| SPI0 CE0 | 8 | 24 |
| SPI0 CE1 | 7 | 26 |
| SPI1 MOSI | 20 | 38 |
| SPI1 MISO | 19 | 35 |
| SPI1 SCLK | 21 | 40 |
| SPI1 CE0 | 18 | 12 |
| SPI1 CE1 | 17 | 11 |

The four-device layout expects:

```text
/dev/spidev0.0
/dev/spidev0.1
/dev/spidev1.0
/dev/spidev1.1
```

The host may enumerate the two SPI1 receivers in an installation-specific
order; use the live `device_map` metric as the authoritative logical mapping.
The full deployment configures `dtparam=spi=on` and `dtoverlay=spi1-2cs`
idempotently. A boot-config change requires a Pi reboot before all four device
nodes appear.

## Power and signal integrity

Do not power the wall from the ESP32 USB or Pi header. Supply the LED strips from
a separately fused 5 V distribution system sized for the installation, and join
the Pi, receivers, level shifters, and LED supply grounds.

WS2812 data is nominally 5 V logic. Use a 3.3-to-5 V logic buffer such as a
74AHCT125 near each receiver and keep data/ground pairs short. Long unpaired
wires, missing ground reference, or marginal connectors can produce visible
flashes even when SPI CRC and receiver counters are clean.

Never use maximum-white current as an ordinary operating condition. Apply both
hardware current protection and conservative software brightness limits.

## Bring-up

1. With LED power off, continuity-check common ground, every chip select, and
   both bus clock/data pairs.
2. Power and flash one receiver over USB:

   ```bash
   uv run --with platformio pio run -d firmware/esp32 -e esp32-s3-devkitc-1
   uv run --with platformio pio run -d firmware/esp32 -e esp32-s3-devkitc-1 -t upload
   ```

3. On the Pi, verify the expected device nodes:

   ```bash
   ls -l /dev/spidev*
   ```

4. Run the receiver acceptance gate against one controller before connecting
   the full wall.
5. Connect and verify one LED lane at a time, then run the full-wall animation
   and output-rate sweeps.

## Troubleshooting

### Missing `/dev/spidev*`

Run `just deploy`, inspect the reported boot configuration, reboot if requested,
and rerun the deployment. Do not add competing SPI overlays by hand.

### Receiver accepts no packets

- Verify Pi SCLK to ESP32 GPIO 12, Pi MOSI to GPIO 11, selected CE to GPIO 10,
  and a common ground.
- Check that the host and firmware use SPI mode 0 and the configured bus/device.
- Inspect receiver serial output and host `driver_stats`.

### CRC or queue errors increase

- Shorten or pair SPI signal and ground wiring.
- Check CS isolation and ground reference.
- Reduce the configured SPI rate only as a diagnostic; retain a lower production
  value only after rerunning acceptance at that rate.

### Clean metrics but visible flashes

The fault is downstream of receiver telemetry. Check the level shifter, LED data
connector, power injection, supply transients, and shared ground. Run
`just output-rate-sweep` while watching the affected lane and retain the highest
visually clean target.

### Wrong lane order or wall orientation

Use the strip-order and calibration plugins rather than editing frame transforms
blindly. Confirm the logical device map and one lane at a time before changing
host layout code.

The full timing and rollback criteria are in
[Rendering acceptance](RENDERING_PIPELINE_ACCEPTANCE.md).
