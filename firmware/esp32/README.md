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
2. Two SPI slave DMA transactions are kept queued. Current hosts clock only
   four-byte-multiple transactions after capability discovery, as required by
   ESP32-S3 SPI-slave DMA.
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

The production target uses the pinned pioarduino `55.03.39` platform with the
`espidf` framework and ESP-IDF 5.5.4. Do not point `platformio.ini` at the
floating `stable` zip. The managed `espressif/elf_loader` 1.3.2 component is
present but disabled; dynamic loading and its command surface remain absent.
The board target must remain `esp32-s3-devkitc1-n16r8` so PSRAM and flash timing
match the installed controllers, unless a replacement receiver has been
physically identified and separately qualified. The repository does not carry
an as-built receiver inventory that justifies another target.

## SPI commands

The command rows below describe semantic bytes. Current firmware additionally
accepts aligned transport envelope v1 (`0x0b`): command `u8`, envelope version
`u8=1`, semantic length `u16`, the exact semantic command, zero padding, then
one big-endian CRC-16/CCITT-FALSE covering the entire envelope and padding. The
total wire size must be a multiple of four and no more than 4,096 bytes; the
semantic size is therefore at most 4,090 bytes. Nonzero padding, an unknown
version, an inconsistent size, a bad CRC, or an unaligned envelope fails closed.

For rolling compatibility, firmware still decodes the legacy
`semantic || CRC-16` packet. A new host sends only legacy discovery traffic
until three consecutive valid status-v3+ snapshots advertise aligned-envelope
capability `1<<14` with a strictly advancing receiver-owned `receiver_packets`
counter. Repeated stale, malformed, truncated, or bad-magic snapshots reset the
pending streak without changing the active framing state; a counter rollback
starts a new three-observation epoch. Once enabled, downgrade likewise requires
three fresh consecutive capability-absent observations, so one corrupt MISO
snapshot cannot flip transport state. Every later command, including the full
negotiated status query, is enveloped. Deployment health requires both the bit
and Host envelope-enabled state on all five receivers before accepting the new
firmware, so new-host/old-firmware traffic remains legacy and
old-host/new-firmware traffic remains decodable. CRC-error accounting is
unchanged for legacy and v1 traffic.

Aligned-envelope v5 is the active per-receiver FEC fallback. Two separated raw
headers identify `0x0b, 5, inner_v1_wire_bytes:u16`; the protected payload
contains another header plus the complete canonical v1 envelope, including
alignment and CRC. Each shortened systematic Reed-Solomon codeword has 50
data symbols and ten parity symbols at distinct GF(256) evaluation points. It
corrects five arbitrary bytes per codeword; 68-way byte interleaving corrects
any contiguous burst through 340 bytes. The installed eight-strip `SET_ALL` is
68 codewords/4,088 bytes, the one-strip form is 12 codewords/728 bytes, and the
maximum is 68 codewords/4,088 bytes and 3,390 semantic bytes. The v2, v3, and
v4 decoders/capabilities remain accepted for old-
Host rollback compatibility. Valid legacy and v1 packets remain accepted.

Capability `fec_envelope_v5 = 1<<18` and three fresh, counter-advancing status
observations gate new-Host use. The maintained service requests it only for logical
receiver 3 through `LEDGRID_FEC_RECEIVER_IDS=3`; every other receiver remains
v1. Status v7 is 1,248 bytes and reports received, accepted, corrected packet
and codeword, uncorrectable, semantic-CRC, framing, and last/maximum decode-time
counters. The terminal outcomes exactly partition received FEC packets. Host
sent/codeword/parity/padding counters advance only after a successful SPI I/O.

| Command | Code | Payload |
|---|---:|---|
| SET_PIXEL | `0x01` | pixel high, pixel low, R, G, B |
| SET_BRIGHTNESS | `0x02` | brightness 0–255 |
| SHOW | `0x03` | none; publish the working frame |
| CLEAR | `0x04` | none; clear and publish |
| SET_RANGE | `0x05` | start high, start low, count, RGB bytes |
| SET_ALL | `0x06` | tightly packed RGB bytes; publishes inline |
| CONFIG | `0x07` | local strips, LEDs/strip `u16`, optional flags, logical ID, and global offset `u16` |
| STATUS_QUERY | `0x08` | 320-byte v3, negotiated 416-byte v4, 768-byte v5, 1,216-byte v6, or 1,248-byte v7 query; all bytes after ID zero |
| LOCAL_BACKGROUND_START | `0x10` | component u16, cadence u16, global offset u32, seed u32, scene epoch u64 |
| LOCAL_BACKGROUND_STOP | `0x11` | none |
| LOCAL_BACKGROUND_PARAMETERS | `0x12` | cadence u16, global offset u32, seed u32 |
| CONTROLLER_SESSION_BEGIN | `0x20` | version, 16-byte session, desired revision, snapshot digest |
| PRESENTATION_CONTEXT_BEGIN/SET/COMMIT | `0x21`–`0x23` | versioned staged context packets |
| OVERLAY_BEGIN/PATCH/COMMIT | `0x30`–`0x32` | generation/CAS binding, sorted RGBA8 patches, scheduled commit |
| OVERLAY_CLEAR/RENEW | `0x33`–`0x34` | generation/revision clear, or active-generation lease renewal |
| OVERLAY_PATCH_BATCH | `0x35` | session/generation, span count, sorted `(start, count, RGBA)` entries |
| PROFILE_PREFLIGHT | `0x40` | global ID, receiver-payload digest, size |
| PROFILE_BEGIN | `0x41` | preflight token, both digests, size, logical ID, strip origin, direction |
| PROFILE_CHUNK | `0x42` | ordered offset u32 and at most 4,085 data bytes from an aligned host; legacy decode retains 4,089 |
| PROFILE_FINALIZE/VERIFY | `0x43`–`0x44` | global ID and receiver-payload digest |
| PROFILE_ACTIVATE | `0x45` | expected generation and staged binding |
| PROFILE_RESTORE | `0x46` | expected generation and exact active/staged/rollback snapshot |
| PROFILE_ABORT | `0x47` | none |
| PING | `0xFF` | none |

SET_PIXEL and SET_RANGE modify the working frame. SHOW and CLEAR publish only
when the receiver is already in `HostFullScene`; they cannot take ownership.
Brightness requests refresh the current owner without changing it. Only a
complete accepted SET_ALL publishes and takes host ownership.

CONFIG retains all four accepted wire lengths. The four-byte legacy form is
`[0x07, local_strips, leds_hi, leds_lo]`; the five-byte form appends the legacy
debug/flags byte. The six-byte installed-direction form additionally appends a
logical receiver ID at byte 5, accepts only IDs 0–3, and uses bit 7 of byte 4 as
the receiver-native strip-reversal flag. It retains the receiver's previously
provisioned global offset; the frozen v1 origins are `0,8,24,16` for logical IDs
`0..3`.

The authoritative heterogeneous-topology form is exactly eight bytes:

| Byte | Field |
| ---: | --- |
| 0 | command `0x07` |
| 1 | active local strip count (`1..8`) |
| 2–3 | LEDs per strip / local height, big-endian `u16` |
| 4 | compatibility flags; bit 7 reverses receiver-native local strip order |
| 5 | installed logical receiver ID (`0..4`) |
| 6–7 | global strip offset, big-endian `u16` |

For the fifth receiver this is
`07 01 00 8a 00 04 00 20`: one 138-LED strip at global offset 32. Local
playback fails closed until identity is provisioned. The eight-byte form is
required for receiver 4 and for an authoritative global offset; legacy six-byte
IDs 0–3 continue to use their frozen origins for compatibility.

The camera-measured installed global offsets by logical receiver ID are
`(0,8,16,24,32)`. A direct unmirrored host light-to-dark diagnostic qualified
the host reversal map as `(false,false,false,false,false)`. Native reversals
remain `(false,false,true,true,false)` pending an independent receiver-native
phase-field diagnostic; a host painter frame cannot qualify this firmware-local
coordinate domain.

When compact-lane mapping is enabled for that one-strip receiver, an output mask
with multiple selected bits broadcasts the same semantic strip to each selected
physical lane. The installed mask is `0xff` because the assembled connector lane
was not recorded; input and status geometry remain exactly one strip and 138
pixels rather than an eight-strip padded frame.

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

Aligned-envelope capability bit `1<<14` is present in every current firmware
environment. A 320/416/768/1,216/1,248-byte semantic status query clocks
328/424/776/1,224/1,256 bytes respectively after wrapping, leaving room for the full
MISO snapshot while satisfying the DMA transaction-length rule.

When status-v3 advertises sparse-overlay capability bit `1<<4`, a new host may
switch to a 416-byte query. Batch command `0x35` additionally requires bit
`1<<5`; receivers without it retain the original one-span `0x31` command. The
receiver then returns `LGS4`: bytes 5–319 retain the v3 layout, while bytes
320–415 report the exact foreground result,
update/patch progress, coverage, committed/staged generations, scene/base
binding, scheduled presentation, lease/remaining time, controller session, and
composition timing/counters. A 320-byte query always returns exact `LGS3`, so
discovery remains compatible with v3-only firmware.

Status-v3 capability bits `1<<6` and `1<<7` negotiate installation profiles and
status v5. `LGS5` keeps status-v4 fields at offsets 5–415 and extends the fixed
snapshot to 768 bytes with transfer result/progress, cache capacity/reserve,
preflight token, state generation, distinct global and receiver-payload digests,
active/staged/rollback bindings, and store/transaction counters.

## Installation-profile rollout boundary

`LEDGRID_ENABLE_INSTALLATION_PROFILES` defaults to zero and is enabled only in
the local-canary environment. The production image neither mounts the profile
cache nor accepts profile commands. Both images share the explicit 16 MB layout:
two 6 MB OTA application slots and a `0x3e0000`-byte `profilecache` SPIFFS
partition. The Pi library remains authoritative; receivers keep a disposable
content-addressed cache with a 512 KiB reserve.

Preflight is read-only. Begin consumes its state-bound token and may evict only
inactive least-recently-used entries. Chunks are ordered and exact retries are
idempotent. Finalize checks payload SHA-256 and the complete LGIP decoder before
atomic visibility. Activation and compensation use generation-CAS persistence;
active, staged, and rollback payloads are all pinned. Profile traffic never
changes base ownership, foreground state, render generation, or physical output.

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

The canary feature also owns one bounded aggregate foreground plane. Two fixed
4,416-byte premultiplied-RGBA buffers and two fixed 1,104-byte coverage maps
stage full snapshots or deltas transactionally; feature-off production builds
do not allocate them. The retained legacy semantic decoder accepts canonical
1,016+88-pixel single-span patches. Current aligned hosts use at most 1,015
pixels per single-span patch. Batch-mode snapshots use 1,014+90-pixel spans
because the 28-byte packet header, four-byte span descriptor, and aligned
envelope share the 4,096-byte transaction ceiling. Batch `expected_patches` and
status `accepted_patches`
count logical spans; one operation-sequenced status-v4 result proves the entire
CRC-bound batch. Delta spans may move or clear content with alpha zero, and a
zero-patch delta is a valid generation-agreement no-op. Scheduled commits retain
the prior plane until their scene-time deadline. Each local base cadence tick
recomposes the unchanged foreground; a foreground-only tick reuses the cached
base and does not advance background cadence. Finite leases clear expired
content while the local background continues. Local stop/failure, receiver
restart, and complete `SET_ALL` takeover discard staged and committed
foreground. Lease expiry also requires the next content update to be a full
snapshot because the committed plane was destroyed. Complete host takeover
additionally resets sparse session and generation authority, so later hybrid
re-entry reconciles from a fresh session begin and full snapshot rather than
inheriting pre-takeover counters.

Only `esp32-s3-devkitc-1-local-canary` advertises local/context/sparse capability
bits and accepts these commands. The ordinary production image stays on status
v3, advertises aligned transport plus status/explicit-ownership capabilities,
rejects the feature surface, and remains the image selected by ordinary
deployment.

The `native-animations` branch is the organ donor for the loader-capable ESP-IDF
baseline, ABI, asset upload/cache, typed parameters, receiver control, status,
quarantine, and portable failure tests. Port narrow pieces with their tests; do
not merge it wholesale or preserve its signed envelope, frame tracks, exclusive
playback, single-digest cache model, render-only watchdog, or partial-RGB
takeover semantics. Its physical handoff did not qualify the installed SPI1
wiring, so branch tests do not replace the roadmap's H0 and wall gates.
