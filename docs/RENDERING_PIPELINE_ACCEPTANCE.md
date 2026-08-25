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

## Finalized installed topology (2026-08-25)

Current release acceptance targets 33×138 across five receivers with logical
widths `(8,8,8,8,1)`, physical left-to-right IDs `(0,1,3,2,4)`, and native
global offsets by logical ID `(0,8,24,16,32)`. The fifth receiver uses SPI1
CE2. All strict status, dense-streamed, sweep, sparse-overlay, profile, and native
module gates require fresh evidence from all five receivers. Historical 32×138,
four-receiver, and degraded-SPI1 results below remain labeled evidence for the
hardware state on which they ran; they cannot qualify the finalized topology.

Logical width and physical output-lane selection are independent. Acceptance
must prove the added logical column without treating seven padded/mirrored lanes
on the fifth receiver as semantic pixels. The old degraded return-path recipes
are retained only as recovery diagnostics and are not current release gates.

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
  33 x 138 geometry in the headless benchmark.
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
- The production, local-canary, and managed-native-canary firmware images build
  for `esp32-s3-devkitc1-n16r8` from the same pinned pioarduino/ESP-IDF 5 inputs.
  Production keeps all receiver-execution gates off; local canary enables only
  the bounded local-background path; native canary additionally includes the
  dynamic loader/cache/status-v6 surface. A durable allowlisted environment
  selection, not a free-form build name, chooses among them.
- The production image uses no FastLED symbols or dependency.

## Phase 3A one-receiver local canary

Run this only after the feature-off production image passes the ordinary
streamed-frame gates on all five receivers. Flash the named feature-on image to
one explicitly recorded serial port, leave the other four on production, and
prepare the production image and restore command before stopping the service.

The canary passes only when:

- a controller-requested fresh serialized status drain reports v3 plus the
  status-v3 and explicit-ownership capabilities, and receiver-reported logical
  IDs 0 through 4 on the full wall; only the selected receiver must advertise
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

The portable five-receiver compensation test must sample every receiver at one
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

After all five controllers are flashed:

```bash
just receiver-streamed-wall-acceptance duration=60 min_fps=150 target_fps=160
just live-animation-sweep seconds=2
```

The trailing `key=value` arguments are normalized by the Just recipes; literal
strings such as `duration=60` are never forwarded to the Python parsers.

The strict commands above remain the release gates. The following historical
temporary diagnostics were used while the installed SPI1 MISO net was physically
shorted to MOSI; after the reported repair they are recovery tools only and
cannot qualify the finalized wall:

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
every animation. Strict `live-animation-sweep` requires telemetry from all five
receivers and never silently skips a missing return path.
Because metrics can be sampled while the parallel SPI workers are presenting a
frame, per-device host frame deltas may differ by one at a sample boundary; a
spread greater than one fails the degraded sweep.

This temporary gate cannot satisfy the strict all-five status or telemetry
gates, cannot qualify a local-background canary on a write-only receiver, and
cannot authorize all-board receiver-native, sparse-overlay, upload, or other
MISO-acknowledged work. Repair the return path and rerun the strict commands to
close those gates.

### Historical degraded hybrid visual evidence

The retired four-receiver installation could deliberately run the named feature-on
firmware with the explicit `degraded_spi1_01_readable` policy for a compiled
background plus Pi-authored sparse foreground. That schema-v1 product mode is
retained only as a recovery diagnostic and historical evidence source; schema v2
does not allow it as a current selection or release gate. Its reports must remain
honest: `telemetry_complete: false`, `release_acceptance: false`, readable devices
`[0,1]`, and unverified devices `[2,3]`.

Visual acceptance for that mode has independent sub-gates:

- Transport/logical route: status and the live `device_map` preserve logical
  identities and SPI routes.
- Physical receiver order: one receiver color per 8-strip lane establishes the
  left-to-right permutation.
- Host strip direction: 32 distinct strip colors plus boundary-crossing sparse
  content establish local order and old-pixel clearing. Four receiver colors do
  not exercise this property.
- Receiver-native direction: an obvious direction-marked diagonal/phase field
  crosses all 8-strip boundaries without a fold, mirror, or phase reset. Clock
  legibility is not evidence for this gate.
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

## Historical Phase 3C portable receiver-profile prerequisites

The first receiver-profile implementation slice was deliberately non-operational:
it added no SPI profile command, persistent receiver cache, status field, optic,
manager mutation, or wall activation. Its acceptance evidence is portable and
must not be described as receiver-profile or physical-wall acceptance.

The slice passed only when:

- four Python-generated 8 x 138 payloads plus one 1 x 138 payload decode in portable
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
  five receivers, a replacement board selects only its current port, and the
  next identical deployment is a true firmware no-op backed by target-owned
  per-device installation evidence;
- focused profile/deployment tests, the complete Python/rendering/firmware and
  deployment gates, both ESP32-S3 builds, fixture regeneration, whitespace,
  deploy dry-run/plan, and an ordinary clean `just deploy` pass.

That slice did not prove receiver staging, activation, restart recovery,
capacity/eviction, status agreement, transform timing, seams, or photographed
evidence. The later gated software now implements those contracts portably, but
their one-receiver and all-five physical gates remain open.

## Phase 4 receiver-native software and physical evidence

The gated Phase 4 software now implements trusted repository-owned unsigned
modules, status v6, the content-addressed receiver cache, exact-five transactions,
typed parameters, watchdog/quarantine handling, managed scene selection, sparse
Clock composition, build-time previews, persistence/adoption, and explicit Python
recovery. Ordinary production and `just deploy` remain feature-off; portable
completion is not physical acceptance.

Use the API-only evidence recipes only after H0/H1 authorize the explicit native
canary:

```bash
just receiver-native-h2-evidence
just receiver-native-h4-default-soak
just receiver-native-h4-maximum-soak
```

Each defaults to 1,800 seconds. The H2 runner slice proves exact IDs `0..4`,
heterogeneous widths/offsets/directions, unanimous bundle/payload/parameter,
context/profile/vibe/plant agreement, counter continuity, and measured start
skew/drift. It does not perform stage/unplug compensation, boundary/lease/restart
repair, the dense streamed canary, or the complete Python animation sweep.

The separate H4 runs exercise authored-default and maximum-work native parameters
with the exact enabled Clock overlay and stale policy. They retain sampled
render/composite/encode/display values without calling them receiver event
histograms, plus SPI, memory/cache, reset/boot, watchdog, error, cadence, skew, and
drift series. Each run must return to the recorded Python full scene without a
reboot or flash. Retained timing distributions and rollback artifacts, plus the
other H4 soak, remain explicit companion evidence.

Short durations are supporting diagnostics only. A complete-gate claim requires
both requested and observed duration of at least 1,800 seconds and validates only
same-release/same-artifact companion reports with nonempty evidence for every
outstanding subgate. Reports enumerate covered and outstanding subgates and remain
failed when any required status field, exact binding, Clock state, reset/cadence
counter, or Python restoration proof is missing. No H2 or H4 gate is accepted by
the portable runner alone.

## Rollback, fallback, and quarantine

Keep the previously validated firmware binaries, recorded Python fallback, and
prior pinned native bundle/payload until full-wall acceptance passes. A failed
canary is restored before further firmware changes are deployed.

Native callback or watchdog failure quarantines the exact payload and selects the
compiled fallback without automatic retry. Clearing quarantine is a separate
digest-bound operator action and must prove the exact five-receiver roster before
reinstall. Explicit receiver-native recovery presents the recorded Python fallback
as a complete frame; manager state is cleared only after host authority is
positively verified. A rejection or exception retains observable native/degraded
ownership and the error so a later recovery can retry safely. App downgrade is
refused while incompatible native state remains active and may proceed only after
that exact Python recovery.

The `native-animations` branch remains an organ donor for portable ABI/loader,
package-validation, status, timing, cache, and quarantine ideas. Its historical
four-receiver tests and passing results are not current main-branch or
physical-wall evidence; the replacement contract is the finalized heterogeneous
five-receiver implementation and the fresh H0-H4 gates above.
