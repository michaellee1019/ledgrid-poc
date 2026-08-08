# ESP32-S3 LED Receiver Firmware

Firmware for an ESP32-S3-N16R8 that receives RGB frames from a Raspberry Pi
over 20 MHz SPI and drives eight WS2812 lanes in parallel.

## Hardware

- Board: ESP32-S3-DevKitC-1-N16R8V
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

> **Installed-wall status (2026-08-08):** SPI1's shared Pi MISO net (GPIO 19,
> pin 35) is electrically coupled/shorted to MOSI (GPIO 20, pin 38). This lets
> ordinary Pi-to-ESP frame streaming work while exact LGS3 identity and native
> operation acknowledgements fail. Do not weaken the fail-closed protocol or
> deployment readiness checks. Follow the
> [cold-resume handoff](../../docs/plan-native-animations.md#cold-resume-handoff-2026-08-08)
> for decisive evidence, powered-off isolation, and post-repair acceptance.

## Architecture

The receiver deliberately separates transport and display work:

1. As soon as the parallel LED driver is ready, the display task renders a
   firmware-resident 45-degree rainbow continuously with no software frame
   cap. The field moves up and right and completes one spectrum cycle per
   second.
2. Two SPI slave DMA transactions are kept queued.
3. The native ESP-IDF `app_main` consumes completed packets, checks CRC-16, and
   updates a compact RGB working frame.
4. Only a complete host frame (`SHOW`, `CLEAR`, or `SET_ALL`) takes display
   ownership. PING, status/capability queries, brightness/configuration, partial
   pixel writes, and upload chunks leave the current display mode unchanged.
5. A FreeRTOS display task on the other core converts RGB to an eight-bit parallel
   WS2812 waveform.
6. ESP-IDF LCD/I80 DMA emits all eight strips concurrently.

The firmware does not use FastLED. At 2.4 MHz, each WS2812 bit is encoded as three
samples (`100` for zero and `110` for one). A 140-pixel frame contains 4.2 ms of
pixel data followed by 300 us reset-low time.

## Building and testing

```bash
# From the repository root: standalone examples, portable tests, and target build
just test-native-animations
just test-firmware

# Or run the underlying PlatformIO targets directly
cd firmware/esp32

# Portable encoder, mailbox, and status-protocol tests
pio test -e native

# Exact production target
pio run -e esp32-s3-devkitc-1

# Upload one controller
pio run -e esp32-s3-devkitc-1 -t upload --upload-port /dev/ttyACM0
```

The production target uses native ESP-IDF 5.5.4 and pins Espressif `elf_loader`
1.3.2 through `src/idf_component.yml` and `dependencies.lock`. The board target
must remain `esp32-s3-devkitc1-n16r8` so PSRAM and flash timing match the
installed controllers. `partitions.csv` retains two 6 MiB OTA slots and a
3.875 MiB disposable animation-cache partition.

The default checked-in configuration is a fail-closed software-build profile:
it has no production trust key and uses logical device 0. A production
receiver-animation rollout requires four builds from the same source, each with
its own `LEDGRID_LOGICAL_DEVICE` value from 0 through 3 and all with the same
trusted P-256 public key. `just provision-native-animations` creates ignored
authoring state and an explicit stable-port map; `just deploy` generates and
validates the four `sdkconfig.receiver-N` inputs, flashes each mapped board, and
requires matching identity/capability readback. It never copies the private key
or falls back to mutable `/dev/ttyACM*` discovery.

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
| CAPABILITIES_QUERY | `0x20` | none |
| ASSET_PROBE | `0x21` | SHA-256 digest (32 bytes) |
| ASSET_BEGIN | `0x22` | v1 signed verification envelope; 313 bytes including command byte |
| ASSET_CHUNK | `0x23` | offset u32, up to 4089 ordered bytes |
| ASSET_COMMIT | `0x24` | SHA-256 digest |
| ASSET_REMOVE | `0x25` | SHA-256 digest |
| ANIMATION_START | `0x26` | digest, global strip offset u16, parameter length u16, typed blob |
| ANIMATION_STOP | `0x27` | none |
| ANIMATION_RESTART | `0x28` | none |
| ANIMATION_PARAMETERS | `0x29` | parameter length u16, typed blob |
| ASSET_ABORT | `0x2A` | none; discard staging and restore the pre-maintenance mode |
| PING | `0xFF` | none |

SET_PIXEL and SET_RANGE modify the host working frame. SHOW publishes their
combined result and takes display ownership. SET_ALL and CLEAR also publish and
take ownership. Brightness and geometry changes may refresh the host mailbox,
but do not stop startup/fallback or receiver-local playback.

The complete SPI transaction is limited to 4096 bytes including the two CRC
bytes; the command body is therefore at most 4094 bytes and an asset chunk at
most 4089 bytes. The receiver has a two-deep queued-response pipeline: after
sending a command, the host clocks **two complete 128-byte status transfers**
before interpreting that command's acknowledgement. Neither the concurrent
response nor only one following snapshot is the acknowledgement.

ASSET_BEGIN envelope v1 is fixed and big-endian: command and version bytes,
envelope length u16, selected payload size u32 and digest, kind u8, ABI u16,
target u16, local strips u8, LEDs u16, logical device u8, fixed 20-byte key ID,
fixed 176-byte canonical LGIX, and fixed 64-byte raw P-256 `r||s` signature.
The 313-byte command occupies 315 bytes including CRC. LGIX v1 signs kind,
ABI/target IDs, 4×8/32×138 geometry, manifest digest, and four device payload
digests. The receiver binds the selected device digest and every duplicated
invariant before calling its configured trust verifier or creating `.part`.

Typed parameter blobs use version 1: version u8, count u8, then name length u8,
printable UTF-8/ASCII name bytes, type u8, and a type-specific value. Types are
int32 and IEEE float32 (four big-endian bytes), canonical bool (0/1), enum
(length u8 plus value bytes), and RGB color (three bytes). Blobs are bounded to
1024 bytes, 32 unique names, and 63 bytes per name/enum.

## Receiver status v3

The ESP32 returns a 128-byte `LGS3` snapshot over MISO alongside normal writes.
LGS3 is the sole status contract for this firmware-animation baseline; LGS1,
LGS2, and mixed-version deployments are intentionally unsupported. The layout
is stable and big-endian:

Successful `SET_ALL` streaming proves only the MOSI/clock/select receive path.
If frames continue but LGS3 is absent or exactly echoes transmitted bytes,
isolate the MISO net electrically—especially with CS disabled/high—before
changing protocol code or SPI rate.

| Offset | Field |
|---:|---|
| 0–3 | ASCII `LGS3` |
| 4 | status version `3` |
| 5–6 | flags, active strips |
| 7 | reserved |
| 8, 10 | LEDs per strip and queued transactions u16 |
| 12–40 | packet, CRC, accepted/displayed/superseded, publish-drop, and SPI-queue counters u32 |
| 44–50 | last CRC, copy, encode, and show times u16 |
| 52–60 | last accepted/displayed sequence and display-error count u32 |
| 64 | capabilities u32 |
| 68–71 | display mode, asset kind, upload state, last operation result |
| 72 | full active SHA-256 digest (32 bytes) |
| 104, 108 | cache free and used bytes u32 |
| 112, 116 | upload received and total bytes u32 |
| 120, 122 | last and maximum render/decode time u16 |
| 124 | missed deadlines u16 |
| 126, 127 | saturating watchdog count and quarantine state |

Display modes are startup/fallback (0), host frames (1), firmware animation
(2), and maintenance/frozen (3). Asset kinds are none (0), native (1), and frame
track (2). Upload states are idle, receiving, verifying, committed, and failed.

## Upload and playback foundations

`UploadManager` enforces ordered chunks, byte-identical retry idempotence,
receiver-size and free-space limits, SHA-256 verification, validation hooks, and
atomic `.part`-to-committed semantics. A failed hash/signature/ABI/geometry/device
validation discards staging and cannot make it probeable or active. Removing a
missing inactive digest is idempotent; removing the active digest is rejected.
`AssetSignatureVerifier` is the production trust boundary: unknown key,
altered index/signature, noncanonical envelope, wrong target/ABI/geometry/device,
or selected-digest mismatch all fail before storage becomes visible.

`FrameTrackDecoder` consumes `LGT1` tracks containing bounded RGB565 keyframe,
unchanged/delta, and fill runs. It validates every header, duration, opcode,
encoded length, pixel count, and run before touching the output buffer. The
player preserves authored durations and supports bounded one-frame catch-up,
loop, pause, 0.1–4.0x speed, and asset brightness.

`LGT1` v1 requires its serialized loop flag and `loop_count == 0`; the SDK and
receiver reject any other on-wire repeat count. A package's public `loop=false`
control holds the last frame after one pass, while `loop=true` repeats forever.
Fixed N-pass playback is intentionally not represented in the v1 track header.

Frame packages expose `pause`, `loop`, `playback_speed`, and
`asset_brightness`; the manager injects the global `time_scale`. Frame-track
cadence composes `playback_speed * time_scale` and clamps the result to
0.1–4.0x; asset brightness is a float from 0.0–1.0. Native modules receive both
real elapsed time and `scaled_elapsed_us` after the same bounded `time_scale` is
applied. A module applies its own speed parameter to `scaled_elapsed_us` once.

`animation_abi.h` is the versioned, unmangled native-module boundary. Modules
receive local geometry, global strip offset, unscaled/scaled time, frame index,
typed parameters, caller-owned RGB output, and bounded helpers—never driver or
peripheral handles. Callbacks return `LEDGRID_ANIMATION_OK` (`0`) on success;
target modules are import-free and use the passed helper table for RNG, color,
and trigonometry. The canonical examples live under `src/native_examples/`:
startup rainbow, aurora ribbons, and meteor shower. The startup fallback invokes
that same callback table directly from the baseline image, independent of the
cache and dynamic loader.

The hardware entrypoint mounts the `animcache` SPIFFS partition as a disposable
content-addressed cache. Payload and descriptor metadata remain under `.part`
names until payload validation succeeds; the metadata rename is the atomic
visibility point. The cache retains a 512 KiB reserve and evicts only the least
recently used inactive asset. A 32-file descriptor bound supports multiple
committed assets plus staging files without unbounded VFS allocation.

Native modules load through pinned `elf_loader` 1.3.2 into PSRAM and resolve the
versioned ABI entrypoint. Frame tracks load into PSRAM and run through the same
bounded decoder used by native tests. The display task enforces a 25 ms render
guard: expiry records the active digest in RTC memory and resets; boot then
persists quarantine in NVS and renders the compiled startup fallback. Active
digests, quarantine, and reset reason are persisted before execution or state
transition as appropriate.

Production trust is configured in ESP-IDF menuconfig with
`LEDGRID_TRUSTED_KEY_ID` and a 130-character uncompressed SEC1
`LEDGRID_TRUSTED_P256_PUBLIC_KEY_HEX`. mbedTLS verifies raw low-S P-256 `r||s`
over the canonical 176-byte LGIX index. With no valid key, signed upload and
asset-upload capabilities remain off and ASSET_BEGIN fails closed; cached assets
can still be probed or played. `LEDGRID_ALLOW_UNSIGNED_DEVELOPMENT` is disabled
by default and deliberately conspicuous when enabled through both an error-level
boot warning and the `UNSIGNED_DEVELOPMENT` status capability. It must never be
enabled on an installed receiver. Each board must also configure its immutable
`LEDGRID_LOGICAL_DEVICE` index from 0 through 3.

The Pi library must trust the same public key. `scripts/start_server.py` reads a
path-separated `LEDGRID_LGA_TRUSTED_KEYS` value whose entries are
`key-id=/absolute/path/to/public.pem`. Both the controller and web processes
need that environment. `just provision-native-animations` creates the ignored
workstation authoring configuration; `just deploy` copies its public key and
public environment file to the Pi, configures systemd with the environment, and
installs freshly verified signed examples. The private key remains on the
workstation.

If a begun transfer fails before commit, the host sends idempotent `ASSET_ABORT`
to every receiver that may have entered maintenance. The receiver deletes any
`.part` state and restores the pre-maintenance display mode; the host then
accepts the acknowledgement only if the accompanying LGS3 also proves upload
state `idle` and a non-maintenance display mode. It then removes and re-probes
any possibly committed payload and publishes abort, removal, or residual
failures.

The host exposes these fields through `/api/status` and `/api/metrics`. After a
receiver has been explicitly flashed and put in scope for hardware testing, run
the streamed-frame electronic canary from the repository root with:

```bash
just receiver-acceptance 0 60
```

That command validates the host-frame transport path; it does not measure
receiver-local native render or frame-track decode latency. The separate
on-device timing, fallback, restart, skew/drift, and soak checklist remains open
in [the implementation record](../../docs/plan-native-animations.md). See
[rendering acceptance](../../docs/RENDERING_PIPELINE_ACCEPTANCE.md) for the
streaming thresholds and rollback conditions.
