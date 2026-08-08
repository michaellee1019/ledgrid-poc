# Rendering Pipeline Architecture and Acceptance Criteria

## Decision

For Python plugins, the Raspberry Pi remains responsible for animation
generation and frame scheduling. The host overlaps generation of frame N+1 with
presentation of frame N, resolving the presentation before either of the
animation's two reusable frame buffers can be reused. Each ESP32-S3 handles the
streamed transport/display path with three independent stages:

1. Two SPI DMA receive transactions remain queued continuously.
2. A receive task validates packets and publishes complete RGB frames to a
   three-slot, latest-frame-wins mailbox.
3. A display task encodes and submits the newest frame to the ESP32-S3 LCD/I80
   peripheral, which drives eight WS2812 lanes in parallel using DMA.

The receiver has no FastLED dependency. It uses the public ESP-IDF LCD/I80 API
directly so buffering, completion, timing, and overload behavior are explicit.

Receiver-local `.lga` playback is a separate explicit mode. In that mode the
ESP32-S3 renders a signed native module or decodes a device-specific frame track
using its local clock; a complete host frame returns ownership to the streamed
path. The receiver-local release gates are listed separately below.

At 2.4 MHz, each WS2812 data bit is represented by three parallel samples:

- zero: `100`
- one: `110`

For 138 RGB pixels per lane, wire time is 4.14 ms plus a 300 us low reset period,
for a nominal 4.44 ms display transaction.

## Automated acceptance gates

All of these must pass before a hardware flash:

- Host unit tests pass, including the sole LGS3 receiver-status contract and
  frame-counter aggregation. LGS1/LGS2 compatibility is intentionally absent.
- Host transport tests prove SPI0 and SPI1 bus groups overlap while chip selects
  sharing one bus remain serialized; live telemetry reports the complete logical
  device-to-bus/chip-select map.
- Every active frame-based animation returns a canonical contiguous `uint8`
  frame without errors and renders at or below 4.0 ms p95 for the installed
  32 x 138 geometry in the headless benchmark.
- Native firmware tests prove:
  - GRB channel order for all eight lanes;
  - exact `100` and `110` waveform samples;
  - brightness scaling at 0, intermediate, and 255 levels;
  - at least 300 us of encoded reset-low samples;
  - bounds rejection for invalid strip counts, lengths, and output buffers;
  - latest-frame-wins mailbox behavior without overwriting a frame being read;
  - deterministic accounting of accepted, displayed, and superseded frames.
- The production firmware builds for `esp32-s3-devkitc1-n16r8` using the pinned
  native ESP-IDF 5.5.4 toolchain and `elf_loader` 1.3.2.
- The production image uses no FastLED symbols or dependency.
- `just test-native-animations` cross-builds every native example, enforces the
  import-free ABI/export contract, renders default and stress profiles at exact
  8 x 138 local geometry, and passes the 4.0 ms desktop p95 proxy.
- Package, orchestration, API, and portable receiver tests pass for signed
  envelopes, bounded frame tracks and loop metadata, typed controls, local
  playback, upload abort/cache transactions, interprocess library locking,
  fail-closed all-four runtime reconciliation, fallback, and quarantine.

## Single-controller electronic capacity gates

Run a dense, changing animation for at least 60 seconds at a 200 FPS host target.
This proves pipeline capacity; it does not qualify the hand-wired strip links.
The capacity gate passes only when receiver telemetry shows:

- no reset, panic, watchdog, or service failure;
- CRC-error delta of zero after warm-up;
- SPI queue-overrun delta of zero;
- receiver display DMA p95 at or below 4.8 ms;
- receiver frame-encode p95 at or below 1.0 ms;
- at least 180 displayed frames per second;
- at least 99% of accepted frames are either displayed or explicitly counted as
  superseded, with no unexplained frame loss;
- `accepted - displayed - superseded` remains within the three-slot mailbox bound;
- all eight physical lanes show the expected colors and ordering.

WS2812 lanes have no return channel. Receiver CRC and DMA telemetry therefore
cannot detect a flash caused by a marginal data/power connection downstream of
the ESP32. After the electronic gates pass, run `just output-rate-sweep` while
watching the affected strips. Retain the highest target with no visible flash;
the installed hand-wired wall defaults to 160 FPS until that qualification is
complete. A rate is not accepted merely because receiver counters are clean.

If any integrity criterion fails, do not roll out. Timing thresholds may be revised
only with measured evidence and an updated theoretical budget.

## Full-wall gates

After all four controllers are flashed:

- run the dense canary load for 60 seconds, then the complete animation sweep;
- the live animation sweep starts every registered plugin and observes no host
  SPI or receiver integrity-counter increase while it runs;
- host SPI errors remain zero;
- every receiver with a connected MISO path meets the canary integrity criteria;
- no controller visibly freezes, tears, changes brightness, or reorders lanes;
- the configured target does not exceed the visually qualified output ceiling;
- record host generation, SPI send, receiver encode, receiver DMA, accepted,
  displayed, and superseded rates in the deployment report.

## Receiver-local firmware-animation gates

The following gates are additional to the streamed-frame criteria:

The installed wall is currently blocked before these gates by a confirmed SPI1
MISO-to-MOSI electrical coupling/short. Streamed frames may still work because
they use MOSI; native identity, acknowledgements, and control require MISO. Use
the authoritative [cold-resume evidence and repair sequence](plan-native-animations.md#cold-resume-handoff-2026-08-08),
and do not bypass readiness in code.

- Provision one canary receiver with the production trust key and its immutable
  logical-device index before provisioning all four. The four receivers share
  one trust key but use logical-device values 0 through 3.
- Configure the controller and web processes with matching public-key entries in
  `LEDGRID_LGA_TRUSTED_KEYS`; verify that an empty/unknown key fails closed and
  unsigned-development mode is off.
- Install and play a signed native package and a signed frame-track package;
  verify the exact device track/offset on each receiver and return to streamed
  Python frames without a flash or reboot.
- Measure native render p95 below 4 ms and frame decode p95 below 2 ms at each
  package's declared cadence. Retain p99 and maximum values as diagnostic
  evidence even though p95 is the numerical gate.
- Interrupt uploads and power around staging/metadata rename; verify that no
  partial asset becomes active and that any rollback residue is reported.
- Force a native callback failure and watchdog reset; verify compiled-rainbow
  fallback, persisted quarantine, and explicit reinstall before retry.
- Disconnect and restart the Pi processes while local playback is active;
  verify continued output and correct state adoption.
- Run 30-minute native and frame-track soaks with no reset or missed deadline.
- Record sequential-start skew and clock drift for diagnosis. Strict v-sync and
  a common start clock are intentionally not v1 gates.

`just test-native-animations` is host timing and `just test-firmware` is a
portable suite plus cross-build. Neither satisfies any physical item above. The
`just deploy` provisions four logical-device-specific signed images and the Pi
trust environment, then gates on all four identities and capabilities. It still
does not satisfy the physical timing, fault-injection, or soak items above.

## Rollback

Keep the previously validated firmware binaries until full-wall acceptance passes.
A failed canary is restored before further firmware changes are deployed.
