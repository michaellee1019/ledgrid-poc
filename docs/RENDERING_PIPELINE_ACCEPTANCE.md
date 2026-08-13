# Rendering Pipeline Architecture and Acceptance Criteria

## Decision

The Raspberry Pi remains responsible for animation generation and frame scheduling.
The host overlaps generation of frame N+1 with presentation of frame N, resolving
the presentation before either of the animation's two reusable frame buffers can
be reused. Each ESP32-S3 is a transport and display coprocessor with three
independent stages:

1. Two SPI DMA receive transactions remain queued continuously.
2. A receive task validates packets and publishes complete RGB frames to a
   three-slot, latest-frame-wins mailbox.
3. A display task encodes and submits the newest frame to the ESP32-S3 LCD/I80
   peripheral, which drives eight WS2812 lanes in parallel using DMA.

The receiver has no FastLED dependency. It uses the public ESP-IDF LCD/I80 API
directly so buffering, completion, timing, and overload behavior are explicit.

At 2.4 MHz, each WS2812 data bit is represented by three parallel samples:

- zero: `100`
- one: `110`

For 138 RGB pixels per lane, wire time is 4.14 ms plus a 300 us low reset period,
for a nominal 4.44 ms display transaction.

## Automated acceptance gates

All of these must pass before a hardware flash:

- Host unit tests pass, including both receiver-status versions and frame-counter
  aggregation.
- Host transport tests prove SPI0 and SPI1 bus groups overlap while chip selects
  sharing one bus remain serialized; live telemetry reports the complete logical
  device-to-bus/chip-select map.
- Every active frame-based animation returns a canonical contiguous `uint8`
  frame without errors and renders at or below 4.0 ms p95 for the installed
  32 x 138 geometry in the headless benchmark.
- The three accepted host scenes (Gradient, Aurora Curtains, and Sparkle under
  Clock Overlay) meet the same 4.0 ms p95 generation-plus-composition gate.
  The benchmark also reports p50/p99/max, scene and overlay changed ratios,
  overlay dirty pixels/ranges, and RGB payload bytes observed through the
  manager/controller presentation boundary. SPI/receiver byte counters remain
  physical-wall evidence.
- Host-scene contract tests prove premultiplied fixed-point source-over and
  per-fold rounding, ordered overlap, translation/clipping, previous/new dirty
  coverage, transparent versus opaque black, two-buffer ownership, independent
  lifecycle/cadence, preview/live parity, targeted interactions, and exact-once
  plant-optics and vibe-luminance ordering.
- The fixed host scene flattens into the existing complete RGB presentation
  path. Phase 2B changes no receiver packet, firmware, display-mode, or takeover
  behavior, and the original Clock presets remain byte-compatible.
- Native firmware tests prove:
  - GRB channel order for all eight lanes;
  - exact `100` and `110` waveform samples;
  - brightness scaling at 0, intermediate, and 255 levels;
  - at least 300 us of encoded reset-low samples;
  - bounds rejection for invalid strip counts, lengths, and output buffers;
  - latest-frame-wins mailbox behavior without overwriting a frame being read;
  - deterministic accounting of accepted, displayed, and superseded frames.
- The production firmware builds for `esp32-s3-devkitc1-n16r8` using the pinned
  pioarduino/ESP-IDF 5 toolchain.
- The production image uses no FastLED symbols or dependency.

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

## Rollback

Keep the previously validated firmware binaries until full-wall acceptance passes.
A failed canary is restored before further firmware changes are deployed.

## Future receiver-native modes

The [unified roadmap](plan-revamped-animation-pipeline.md) adds acceptance for a
compiled local-background canary, sparse RGBA foreground, installation profile,
and eventually trusted unsigned native modules. Those gates extend this file's
streamed-frame requirements; they never replace or lower them.

The `native-animations` branch remains an organ donor for portable ABI/loader,
package-validation, status, timing, cache, quarantine, and four-board
failure-injection tests. Its historical passing software tests are not current
main-branch or physical-wall evidence. Re-port each relevant test with its
implementation, update it for the unsigned bundle and base-plus-foreground
contracts, then re-run the roadmap's one-receiver and four-receiver gates. The
branch's unresolved SPI1 MISO/MOSI coupling fault makes fresh Gate H0 wiring and
streamed-baseline evidence mandatory before receiver-native wall acceptance.
