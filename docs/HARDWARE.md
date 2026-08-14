# Hardware and wiring

This document records the software-visible hardware contract for the installed
wall. It is sufficient to configure, flash, and troubleshoot known assembled
hardware; it is not a schematic, PCB design package, BOM, or complete as-built
record.

## Documentation status and sources of truth

The supported installed-wall configuration (`LEDGRID_HAT=0`) uses one Raspberry
Pi host and four ESP32-S3 receivers. Each receiver drives eight WS2812-compatible
lanes of 138 LEDs, for a camera-verified logical geometry of 32 x 138 (4,416
pixels). Receiver buffers retain capacity for 140 LEDs per lane.

Use the runtime sources below when a copied value in prose disagrees:

| Contract | Authoritative source |
| --- | --- |
| Installed geometry | [`drivers/led_layout.py`](../drivers/led_layout.py) and production calibration under [`config/`](../config/) |
| Receiver SPI and LED GPIOs | [`firmware/esp32/src/main.cpp`](../firmware/esp32/src/main.cpp) |
| Receiver build target | [`firmware/esp32/platformio.ini`](../firmware/esp32/platformio.ini) |
| SPI speed and mode | [`drivers/spi_controller.py`](../drivers/spi_controller.py) |
| Logical receiver-to-`spidev` mapping | [`drivers/multi_device.py`](../drivers/multi_device.py), overridden by `LEDGRID_DEVICE_MAP`; the live `device_map` metric is authoritative for a running installation |

The production build target is PlatformIO board
`esp32-s3-devkitc1-n16r8`, configured for 16 MB flash and 8 MB PSRAM. The
repository does not contain an as-built inventory or photographs that verify the
exact DevKitC PCB revision, module marking, or previously used `N16R8V` suffix.
Treat the PlatformIO name as the required firmware target, not as a complete BOM
entry for the physical receiver.

## Receiver pins

All four receivers run the same firmware and GPIO map.

| Function | ESP32-S3 GPIO |
| --- | ---: |
| SPI MOSI | 11 |
| SPI MISO | 13 |
| SPI SCLK | 12 |
| SPI CS | 10 |
| LED lanes 0-7 | 18, 17, 16, 15, 7, 6, 5, 4 |
| Status LED | 48 |

The production host transport is SPI mode 0 at 20 MHz. CRC-16 protects command
and frame payloads but does not replace signal-integrity qualification.

See [receiver firmware](../firmware/esp32/README.md) for the build, waveform, and
protocol details.

## Raspberry Pi buses

Boards on the same bus share clock, MOSI, and MISO; each board has its own chip
select. MOSI, clock, chip select, and common ground are required for display
traffic. MISO is required for `LGS3` status, receiver identity, and the full
acceptance gates; only explicitly degraded write-only operation can omit it.

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

The four-device layout expects these device nodes:

```text
/dev/spidev0.0
/dev/spidev0.1
/dev/spidev1.0
/dev/spidev1.1
```

For the expected topology, when `/dev/spidev0.2` is absent, SPI1 is present, and
`LEDGRID_DEVICE_MAP` is unset, the host maps logical receivers 0-3 to
`spidev0.0`, `spidev0.1`, `spidev1.1`, and `spidev1.0`, respectively. A custom
device-node topology can select a different fallback. No software default is
proof of physical board labels or cable order; use the live `device_map` metric
and a lane-order test for the running installation. The full deployment
configures `dtparam=spi=on` and `dtoverlay=spi1-2cs` idempotently. A boot-config
change requires a Pi reboot before all four device nodes appear.

### Installed lane and strip orientation

Transport identity, wall position, and pixel direction are separate hardware
facts. The camera-verified installed contract on 2026-08-14 is:

| Logical receiver | SPI route | Physical lane from left | Host strip order | Native coordinate order | Native global offset |
| ---: | --- | ---: | --- | --- | ---: |
| 0 | `spidev0.0` | 0 | forward | forward | 0 |
| 1 | `spidev0.1` | 1 | forward | forward | 8 |
| 2 | `spidev1.1` | 3 | reversed | reversed | 24 |
| 3 | `spidev1.0` | 2 | reversed | reversed | 16 |

In config form, physical left-to-right logical order is `(0,1,3,2)`, while
both the host-frame and receiver-native reversal maps are
`(false,false,true,true)`. The durable runtime authority is
`run_state/receiver_hybrid.json`; software defaults and this copied table are
not substitutes for reading that file after a cable change.

The two reversal columns deliberately remain independent. Host reversal maps
complete RGB frames and sparse RGBA foreground into the receiver's local output
buffer. Native reversal is a six-byte CONFIG flag used by firmware when turning
its local strip index into a global procedural coordinate. A correct clock or
host diagnostic therefore does not prove that a receiver-native background has
the right orientation.

After changing cables, establish the domains in this order:

1. Verify the unchanged or new logical-to-`spidev` routes and readable roles.
2. Show one color per receiver to establish physical lane permutation.
3. Show one distinct color per physical strip to establish host local direction;
   four lane colors are insufficient for this step.
4. Use boundary-crossing host content to verify sparse slicing and old-pixel
   clears.
5. Independently run a receiver-native signed diagonal/phase pattern and inspect
   every 8-strip boundary for a reversal or phase fold.
6. Photograph the final state after service restart and again after ordinary
   deployment. Retain rejected frames as rejected evidence rather than
   overwriting or reinterpreting them.

Logical receivers 2 and 3 currently have no usable MISO return. A photograph can
prove that their lanes displayed a visible pattern, but outbound counters and
camera evidence cannot prove receiver acknowledgement, identity, integrity, or
timing. Full receiver-native release acceptance remains blocked on the SPI1
return-path repair.

## Alternate HAT compatibility mode

The repository also retains an alternate `LEDGRID_HAT=1` software mode. It
configures two receivers on SPI0 CE0/CE1 and exposes 16 lanes. It is not the
32-lane installed-wall configuration described above. No HAT schematic, PCB
layout, connector pinout, BOM, or fabrication output is checked in, so the code
and diagnostic utility establish only the host-side software mapping. Do not use
them as manufacturing documentation for a carrier board.

## Power and signal integrity requirements

The following are design and operating requirements, not a verified description
of the parts currently assembled.

Do not power the wall from the ESP32 USB or Pi header. Supply the LED strips from
a separately fused 5 V distribution system sized for the installation, and join
the Pi, receivers, level shifters, and LED supply grounds.

The ESP32 outputs 3.3 V logic while the LED lanes use a 5 V supply. Provide
enough 3.3-to-5 V, AHCT-compatible buffer channels for all eight lanes near each
receiver and keep data/ground pairs short. For example, an eight-lane receiver
would require two four-channel 74AHCT125 packages; the repository does not
establish whether that is the circuit actually installed. Long unpaired wires,
missing ground reference, or marginal connectors can produce visible flashes
even when SPI CRC and receiver counters are clean.

Never use maximum-white current as an ordinary operating condition. Apply both
hardware current protection and conservative software brightness limits.

## Missing physical design information

The repository does not contain:

- a schematic or netlist;
- PCB source files, board stack-up, design rules, or a confirmed board revision;
- Gerbers, drill files, fabrication drawings, or pick-and-place data;
- a BOM with manufacturer part numbers and substitutions;
- an assembly drawing, connector pinout, cable schedule, or as-built photographs;
- the Raspberry Pi model/revision or exact ESP32 module markings;
- the LED strip manufacturer/part number, connector family, wire gauges, or cable
  lengths;
- power-supply ratings, fuse types/values, branch-current allocation, power
  injection locations, grounding topology, or decoupling details; or
- the installed level-shifter circuit, channel count, enable wiring, series
  resistors, or protection components.

Git history contains commit messages referring to a "PCB v4" and a "latest
PCB," but no EDA or fabrication artifacts for those boards are present in the
current tree or other reachable Git objects. An older `WIRING.md` in history
describes a seven-lane Seeed XIAO ESP32-S3 prototype and is not applicable to the
current four-receiver pin map.

Do not infer any of the missing electrical or mechanical details from firmware
GPIO assignments. Capture them from the physical installation before repairing,
reproducing, or replacing a carrier or power assembly.

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

## Receiver-native roadmap hardware gate

The [unified roadmap](plan-revamped-animation-pipeline.md) uses the
`native-animations` branch as an implementation organ donor, but the branch's
handoff recorded unresolved SPI1 MISO/MOSI coupling. That branch is not evidence
that the installed wall can safely stage, verify, or reconcile four receiver
artifacts.

Before any all-wall receiver-native release, power down and verify SPI1 MISO and
MOSI are isolated and correctly routed, then obtain fresh identity/status from
all four receivers with no TX echo and rerun the existing streamed one-receiver
and full-wall canaries. Only after that H0 baseline may ported loader/cache or
sparse-overlay code proceed to the roadmap's physical gates.
