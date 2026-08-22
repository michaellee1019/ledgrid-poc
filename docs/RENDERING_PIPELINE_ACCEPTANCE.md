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

- Host unit tests pass, including receiver-status v1/v2/v3 compatibility,
  acknowledgement correlation, logical identity, and frame-counter aggregation.
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
- The dedicated local-canary image builds from the same pinned inputs with only
  `LEDGRID_ENABLE_LOCAL_BACKGROUND=1`; compilation evidence proves the ordinary
  production image uses `0` and the canary uses `1`.
- The production image uses no FastLED symbols or dependency.

## Phase 3A one-receiver local canary

Run this only after the feature-off production image passes the ordinary
streamed-frame gates on all four receivers. Flash the named feature-on image to
one explicitly recorded serial port, leave the other three on production, and
prepare the production image and restore command before stopping the service.

The canary passes only when:

- a controller-requested fresh serialized status drain reports v3 plus the
  status-v3 and explicit-ownership capabilities, and receiver-reported logical
  IDs 0 through 3 on the full wall; only the selected receiver must advertise
  the static-background and presentation-context capabilities;
- exact staged context plus explicit start enters `LocalBackground`, then frame
  and cadence counters advance for at least 60 seconds after the Pi/controller
  connection is closed, with no render miss, transition failure, DMA failure,
  reset, panic, or watchdog evidence;
- a live cadence/offset/seed update preserves the active scene binding and the
  next frame uses the new values; an exact retry is idempotent while stale or
  conflicting context and parameter updates fail closed;
- a complete host frame reclaims `HostFullScene` without reboot or flash, while
  status/config/brightness and partial frame commands never claim ownership;
- receiver restart returns to `StartupFallback`, and restarting the ordinary
  controller service presents a complete host frame and restores the desired
  Python scene;
- the selected receiver is reflashed with the feature-off production image and
  the full-wall status, dense 60-second streamed canary, and animation sweep pass
  again before the canary is closed.

The portable four-board compensation test must sample every receiver at one
shared host instant and keep scene-time skew at or below 5 ms. That bound is a
protocol/orchestration gate, not a claim that sequential SPI commands activate
on the same microsecond. Visual lane/seam inspection remains mandatory because
receiver telemetry cannot observe downstream LED wiring.

## Single-controller electronic capacity gates

Run a dense, changing animation for at least 60 seconds at the installed 160 FPS
full-frame target. This proves production pipeline capacity; it does not qualify
the downstream physical strip links. Use
`just receiver-acceptance device=0 duration=60 min_fps=150 target_fps=160`;
the recipe also accepts positional arguments and defaults. The capacity gate
temporarily neutralizes manager-global plant modifiers so operator optics cannot
turn a transport measurement into an animation-cost measurement, then restores
and verifies the exact prior target, modifier state, and scene after either
success or failure.
The reported rate uses the monotonic interval between the first and last
receiver-counter samples; HTTP request time before the first sample and cleanup
time cannot dilute the measured cadence.

The installed timing budget is explicit. One receiver SET_ALL is 3,315 bytes
(one command byte, 8 x 138 x RGB8, and two CRC bytes), or 1,326 us at 20 MHz.
The nominal 138-pixel WS2812 transaction is 4,440 us including the 300 us reset.
The installed target-200 run measured 490 us encode p95, 4,442 us display p95,
and 9,362 accepted and displayed frames over 60.279 seconds: 155.31 FPS with no
integrity or accounting error. That agrees with the current effective serial
SPI-plus-encode-plus-display budget and does not support a 180 FPS release gate.
Target 200 remains an output-rate saturation characterization, not production
capacity acceptance.

The capacity gate passes only when receiver telemetry shows:

- no reset, panic, watchdog, or service failure;
- CRC-error delta of zero after warm-up;
- SPI queue-overrun delta of zero;
- receiver display DMA p95 at or below 4.8 ms;
- receiver frame-encode p95 at or below 1.0 ms;
- at least 150 displayed frames per second;
- at least 99% of accepted frames are either displayed or explicitly counted as
  superseded, with no unexplained frame loss;
- `accepted - displayed - superseded` remains within the three-slot mailbox bound;
- all eight physical lanes show the expected colors and ordering.

WS2812 lanes have no return channel. Receiver CRC and DMA telemetry therefore
cannot detect a flash caused by a marginal data/power connection downstream of
the ESP32. After the electronic gates pass, run `just output-rate-sweep` while
watching the affected strips. Retain the highest target with no visible flash;
the installed wall defaults to 160 FPS until that qualification is
complete. A rate is not accepted merely because receiver counters are clean.

If any integrity criterion fails, do not roll out. Timing thresholds may be revised
only with measured evidence and an updated theoretical budget.

## Full-wall gates

After all four controllers are flashed:

```bash
just receiver-streamed-wall-acceptance duration=60 min_fps=150 target_fps=160
just live-animation-sweep seconds=2
```

The trailing `key=value` arguments are normalized by the Just recipes; literal
strings such as `duration=60` are never forwarded to the Python parsers.

The strict commands above remain the release gates. While the installed SPI1
MISO net is physically shorted to MOSI, this separate temporary diagnostic gate
may qualify only the feature-off streamed path:

```bash
just receiver-phase3a-status-degraded-spi1
just receiver-streamed-wall-acceptance-degraded-spi1 \
  duration=60 min_fps=150 target_fps=160
just live-animation-sweep-degraded-spi1 seconds=2
```

That explicit policy requires full v3 identity, capability, integrity, timing,
and accounting acceptance from readable logical receivers 0 and 1. Logical
receivers 2 and 3 must both remain in the exact known write-only state—status
v0, no parsed status, no capability, and no identity—and must show advancing
host frame, transfer, and byte counters with zero new host SPI errors. The JSON
report names `temporary_degraded_spi1_return_path`, sets
`telemetry_complete: false`, lists the write-only receivers, and states that
their receiver integrity and physical display output are unverified. A human
must visually inspect all SPI1 lanes throughout the run; outbound host counters
do not prove that an ESP32 received, decoded, or displayed a frame.

The separately named degraded live sweep applies that same exact topology on
every animation. Strict `live-animation-sweep` requires telemetry from all four
receivers and never silently skips a missing return path.
Because metrics can be sampled while the parallel SPI workers are presenting a
frame, per-device host frame deltas may differ by one at a sample boundary; a
spread greater than one fails the degraded sweep.

This temporary gate cannot satisfy the strict all-four status or telemetry
gates, cannot qualify a local-background canary on a write-only receiver, and
cannot authorize all-board receiver-native, sparse-overlay, upload, or other
MISO-acknowledged work. Repair the return path and rerun the strict commands to
close those gates.

### Degraded hybrid visual evidence

The installed wall may deliberately run the named feature-on firmware with the
explicit `degraded_spi1_01_readable` policy for a receiver-native background plus
Pi-authored sparse foreground. This is an operational product mode, not a strict
release gate. It must always report `telemetry_complete: false`,
`release_acceptance: false`, readable devices `[0,1]`, and unverified devices
`[2,3]`.

Visual acceptance for that mode has independent sub-gates:

- Transport/logical route: status and the live `device_map` preserve logical
  identities and SPI routes.
- Physical receiver order: one receiver color per 8-strip lane establishes the
  left-to-right permutation.
- Host strip direction: 32 distinct strip colors plus boundary-crossing sparse
  content establish local order and old-pixel clearing. Four receiver colors do
  not exercise this property.
- Receiver-native direction: an obvious signed diagonal/phase field crosses all
  8-strip boundaries without a fold, mirror, or phase reset. Clock legibility is
  not evidence for this gate.
- Combined scene: the final background and foreground both pass in one process
  after restart, then again after ordinary `just deploy` restores the exact
  desired scene and target-owned config.

Use a fresh camera frame taken after the final mutation. Retain the uncropped
source when appropriate, a wall-only crop, SHA-256, config digest, app release,
deploy receipt, and nearby status samples. If the camera moved, reacquire its
homography before using rectified/per-pixel evidence; do not extrapolate the old
calibration. Label every failed or superseded image as rejected. Operator
observation reopens a visual gate even when the assistant or an automated image
metric previously passed it.

The 2026-08-14 accepted combined-scene crop is
`run_state/physical-acceptance/20260814-rainbow-clock-continuous-native.png`
with SHA-256
`7c04792eafd64f33c90e2fe6c2f2aba0829ac1a48640b46f0fc69dfcd373bfa9`.
It proves visible continuity for the then-current installed mapping; it does not
prove receiver acknowledgement, integrity counters, timing, or release
acceptance on logical receivers 2/3.

- run the dense canary load for 60 seconds, then the complete animation sweep;
- the live animation sweep starts every registered plugin and observes no host
  SPI or receiver integrity-counter increase while it runs;
- host SPI errors remain zero;
- every receiver with a connected MISO path meets the canary integrity criteria;
- no controller visibly freezes, tears, changes brightness, or reorders lanes;
- the configured target does not exceed the visually qualified output ceiling;
- record host generation, SPI send, receiver encode, receiver DMA, accepted,
  displayed, and superseded rates in the deployment report.

## Phase 3C portable receiver-profile prerequisites

The first receiver-profile implementation slice is deliberately non-operational:
it adds no SPI profile command, persistent receiver cache, status field, optic,
manager mutation, or wall activation. Its acceptance evidence is portable and
must not be described as receiver-profile or physical-wall acceptance.

The slice passes only when:

- four Python-generated installed-topology 8 x 138 payloads decode in portable
  C++ with exact origin/direction, header/table, CRC/content digest, section,
  range, and cross-section semantic parity; every malformed case clears the
  output view before exposing a pointer;
- the shared transaction engine proves exact candidate and rollback bindings,
  active/staged/rollback payload validity, every receiver-by-phase failure,
  timeout compensation, best-effort all-board recovery, idempotent retry, and
  a degraded/changed result whenever recovery cannot be re-proven;
- firmware installation identity includes every PlatformIO flash-map artifact
  and offset plus layout inputs, rejects artifact drift before skip/upload, and
  produces identical bootloader, partition, application, and installation
  digests for identical pinned inputs in independent clean workspaces;
- the isolated rollback helper includes every imported deployment module, a
  pre-existing shared-marker symlink/non-regular file is rejected without
  touching its target, a legacy marker triggers one explicit migration flash,
  the Pi requires unique factory USB identities and physical locations for all
  four receivers, a replacement board selects only its current port, and the
  next identical deployment is a true firmware no-op backed by target-owned
  per-device installation evidence;
- focused profile/deployment tests, the complete Python/rendering/firmware and
  deployment gates, both ESP32-S3 builds, fixture regeneration, whitespace,
  deploy dry-run/plan, and an ordinary clean `just deploy` pass.

Receiver staging, activation, restart recovery, capacity/eviction, status
agreement, transform timing, seams, and photographed evidence remain open. They
require the later default-off protocol/storage slice and the strict one-receiver
then all-four hardware gates after the SPI1 return path is repaired.

## Rollback

Keep the previously validated firmware binaries until full-wall acceptance passes.
A failed canary is restored before further firmware changes are deployed.

## Later receiver-native modes

The [unified roadmap](plan-revamped-animation-pipeline.md) next adds sparse RGBA
foreground, an installation profile, and eventually trusted unsigned native
modules. Those gates extend this file's streamed-frame requirements; they never
replace or lower them.

The `native-animations` branch remains an organ donor for portable ABI/loader,
package-validation, status, timing, cache, quarantine, and four-board
failure-injection tests. Its historical passing software tests are not current
main-branch or physical-wall evidence. Re-port each relevant test with its
implementation, update it for the unsigned bundle and base-plus-foreground
contracts, then re-run the roadmap's one-receiver and four-receiver gates. The
branch's unresolved SPI1 MISO/MOSI coupling fault makes fresh Gate H0 wiring and
streamed-baseline evidence mandatory before receiver-native wall acceptance.
