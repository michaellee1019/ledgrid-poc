# ESP32-S3 LED Receiver Firmware

Firmware built for the PlatformIO `esp32-s3-devkitc1-n16r8` target. Each receiver
accepts RGB frames from a Raspberry Pi over 20 MHz, mode-0 SPI and drives eight
WS2812-compatible lanes in parallel.

## Firmware hardware contract

- PlatformIO board target: `esp32-s3-devkitc1-n16r8`
- Flash: 16 MB
- PSRAM: 8 MB
- Installed default geometry: 8 strips × 138 LEDs
- Maximum buffer geometry: 8 strips × 140 LEDs

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
The exact physical DevKitC revision/module suffix and the carrier electronics are
not recorded in this repository. See [hardware and wiring](../../docs/HARDWARE.md)
for the documented boundary and missing as-built information.

## Architecture

The receiver deliberately separates transport and display work:

1. Boot enters explicit `StartupFallback` and renders the firmware-resident
   45-degree rainbow. `LocalBackground` and `HostFullScene` are separate base
   ownership states; foreground and maintenance state remain orthogonal.
2. Two SPI slave DMA transactions are kept queued.
3. The ESP-IDF `app_main` task consumes completed packets, checks CRC-16, and updates a
   compact RGB working frame.
4. Only a complete valid `SET_ALL` takes host ownership. PING, configuration,
   brightness, status, partial RGB, and presentation-context traffic never
   claims the base. The production build keeps local playback disabled; the
   explicit canary environment enables it for scheduled one-receiver work.
5. A FreeRTOS display task on the other core converts RGB to an eight-bit parallel
   WS2812 waveform.
6. ESP-IDF LCD/I80 DMA emits all eight strips concurrently.

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

# Deliberate one-receiver canary image (never used by ordinary deployment)
pio run -e esp32-s3-devkitc-1-local-canary

# Upload one controller
pio run -e esp32-s3-devkitc-1 -t upload --upload-port /dev/ttyACM0
```

The production target uses the pinned pioarduino platform with ESP-IDF 5.5.4
and the managed `espressif/elf_loader` 1.3.2 component present but disabled.
Dynamic loading and its command surface remain absent. The board target must
remain `esp32-s3-devkitc1-n16r8` unless a replacement receiver has been
physically identified and separately qualified; the repository does not carry
an as-built receiver inventory that justifies another target.

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
| STATUS_QUERY | `0x08` | exactly 320 bytes, bytes 1–319 zero |
| LOCAL_BACKGROUND_START | `0x10` | component u16, cadence u16, global offset u32, seed u32, scene epoch u64 |
| LOCAL_BACKGROUND_STOP | `0x11` | none |
| LOCAL_BACKGROUND_PARAMETERS | `0x12` | cadence u16, global offset u32, seed u32 |
| PRESENTATION_CONTEXT_BEGIN/SET/COMMIT | `0x21`–`0x23` | versioned staged context packets |
| PING | `0xFF` | none |

SET_PIXEL and SET_RANGE modify the working frame. SHOW and CLEAR publish only
when the receiver is already in `HostFullScene`; they cannot take ownership.
Brightness requests refresh the current owner without changing it. Only a
complete accepted SET_ALL publishes and takes host ownership.

Legacy CONFIG packets remain four or five bytes. Six-byte CONFIG appends a
logical receiver ID at byte 5 (0–3); local playback fails closed until this ID
is provisioned. Byte 4 remains the legacy debug byte.

## Receiver status v3

The ESP32 returns a 320-byte `LGS3` snapshot over MISO. Bytes 5–63 preserve the
complete status-v2 field layout and counters. Extended fields include:

- SPI packets, valid CRCs, and CRC errors;
- currently queued transactions;
- accepted, displayed, superseded, and publish-dropped frames;
- SPI queue and display errors;
- CRC, frame-copy, waveform-encode, and LCD/I80 DMA timings;
- last accepted and displayed sequence numbers.
- explicit base/foreground/maintenance state and transition reason;
- local component, global offset, common seed, scene epoch, cadence and misses;
- active/staged presentation revisions, controller sessions, and full digests;
- logical receiver identity and command/result acknowledgement correlation.

Because SPI responses are queued before the command they accompany, the host
uses `last_processed_command` plus `operation_sequence` to bind later status to
the exact CRC-valid operation. Status queries do not advance that sequence.

The host exposes these fields through `/api/status` and `/api/metrics`. Run the
automated canary gate with:

```bash
python tools/benchmarks/receiver_acceptance.py \
  --base-url http://ledgridwall.local:5000 \
  --device 0 --duration 60 --animation rainbow
```

See [rendering acceptance](../../docs/RENDERING_PIPELINE_ACCEPTANCE.md) for the
required thresholds and rollback conditions.

## Local-background rollout boundary

The statically linked rainbow accepts a host-authoritative committed context,
global strip offset, common seed, scene epoch, live cadence/offset/seed updates,
and fixed-point vibe luminance. Receiver hardware brightness remains the final
output limit, so vibe luminance and master brightness are each applied once.
Production keeps this feature compiled off until a deliberately scheduled
one-receiver canary passes.

The `native-animations` branch is the organ donor for the loader-capable ESP-IDF
baseline, ABI, asset upload/cache, typed parameters, receiver control, status,
quarantine, and portable failure tests. Port narrow pieces with their tests; do
not merge it wholesale or preserve its signed envelope, frame tracks, exclusive
playback, single-digest cache model, render-only watchdog, or partial-RGB
takeover semantics. Its physical handoff did not qualify the installed SPI1
wiring, so branch tests do not replace the roadmap's H0 and wall gates.
