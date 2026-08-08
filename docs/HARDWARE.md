# Hardware and wiring

The installed wall uses a Raspberry Pi and four ESP32-S3-DevKitC-1-N16R8
receivers. Each receiver drives eight WS2812 lanes of 138 LEDs. Firmware keeps a
140-LED-per-lane buffer ceiling, but the installed host geometry is 32 x 138.

## Receiver pins

All four receivers run the same source baseline. Streamed-frame firmware can use
one common image. Signed receiver-local animations additionally require four
builds with immutable `LEDGRID_LOGICAL_DEVICE` values 0 through 3 and one common
trusted P-256 public key.

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

Boards on the same bus share clock and MOSI; each board has its own chip select.
MISO is optional only for the streamed-frame baseline. Signed package upload,
acknowledgement, identity checks, telemetry, and receiver-local playback require
every receiver's MISO connection. Receivers on one bus share that bus's MISO
line and release it while their chip select is inactive. All grounds must be
common.

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

For the current installed wall, the verified SPI1 mapping is:

| Logical receiver | Linux device | Select |
| ---: | --- | --- |
| 2 | `/dev/spidev1.1` | CE1, Pi GPIO 17 / physical pin 11 |
| 3 | `/dev/spidev1.0` | CE0, Pi GPIO 18 / physical pin 12 |

### Current SPI1 return-path blocker

As of 2026-08-08, Pi GPIO 19 / physical pin 35 (shared SPI1 MISO) is electrically
coupled/shorted to Pi GPIO 20 / physical pin 38 (shared SPI1 MOSI). Direct
stopped-service sweeps on both SPI1 devices returned exact TX echo over every
tested speed and transfer size even with chip select disabled or held high, and
an unmuxed GPIO test made GPIO 19 follow GPIO 20. SPI0 returned valid LGS3 at
the same tested speeds. The service was restored after diagnosis.

This explains the otherwise confusing split: receivers 2 and 3 can still accept
ordinary Pi-to-ESP streamed frames over MOSI, but cannot return LGS3 identities,
capabilities, or operation acknowledgements over MISO. Native package operations
must continue to fail closed. Do not change software readiness to accommodate
this fault. See the authoritative [evidence, powered-off isolation steps, and
post-repair sequence](plan-native-animations.md#decisive-installed-wall-evidence).

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

1. With the Pi, every receiver USB/serial connection, LED supply, and any powered
   intermediary disconnected, continuity-check common ground, every chip select,
   and both bus clock/data pairs.
2. Power and flash one receiver over USB:

   ```bash
   uv run --with platformio pio run -d firmware/esp32 -e esp32-s3-devkitc-1
   uv run --with platformio pio run -d firmware/esp32 -e esp32-s3-devkitc-1 -t upload
   ```

   The checked-in default is intentionally fail-closed: it has no production
   trust key and identifies as logical device 0. It is suitable for baseline
   bring-up, not production signed-package acceptance.

3. On the Pi, verify the expected device nodes:

   ```bash
   ls -l /dev/spidev*
   ```

4. Run the receiver acceptance gate against one controller before connecting
   the full wall.
5. Connect and verify one LED lane at a time, then run the full-wall animation
   and output-rate sweeps.

For receiver-local animations, initialize the ignored authoring configuration
once with `just provision-native-animations` and four stable
`/dev/serial/by-id` paths in logical wall order. `just deploy` then builds four
isolated images with logical identities 0 through 3 and a common public trust
key, flashes only those explicitly mapped receivers, and requires exact LGS3
identity/capability readback before reporting success. The signing private key
never leaves the workstation.

The current workstation is already provisioned. After the SPI1 repair, verify
fresh LGS3 readiness against the already-installed baseline **before** running
`just deploy`; use the exact commands in the
[cold-resume handoff](plan-native-animations.md#first-commands-when-resuming).

## Troubleshooting

### Missing `/dev/spidev*`

Run `just deploy`, inspect the reported boot configuration, reboot if requested,
and rerun the deployment. Do not add competing SPI overlays by hand.

### Receiver accepts no packets

- Verify Pi SCLK to ESP32 GPIO 12, Pi MOSI to GPIO 11, selected CE to GPIO 10,
  and a common ground.
- For LGS3 status or receiver-local animations, also verify ESP32 GPIO 13 to the
  bus MISO pin: Pi GPIO 9 / physical pin 21 on SPI0, or Pi GPIO 19 / physical
  pin 35 on SPI1. A receiver may continue displaying streamed frames with this
  return path missing, but signed animation deployment must fail closed.
- Check that the host and firmware use SPI mode 0 and the configured bus/device.
- Inspect receiver serial output and host `driver_stats`.

If streamed frames work but LGS3/status and signed package operations do not,
diagnose MISO separately from MOSI. A one-way stream proves the outbound frame
path only; exact TX echo on MISO with CS disabled/high points to electrical
coupling, not a receiver parser or SPI-rate problem.

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
