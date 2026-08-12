# Phase 1 Clock Baseline

Baseline schema: `1`. Acceptance: **PASS**.

This report characterizes the existing compatibility full-scene Clock. Wall
time comes from an overridden `_clock_now` fixed to one UTC start and advanced
from the same deterministic elapsed timeline used by the background. Therefore
cadence, changed-frame, dirty-pixel, and preview results are deterministic.
Latencies are measurements
from the development host below; they are not Raspberry Pi or ESP32 evidence.

Regenerate and enforce the functional/performance gates with:

```bash
uv run --with numpy --with pillow tools/benchmarks/clock_baseline.py --check --markdown-output docs/clock-phase1-baseline.md
```

## Measurement envelope

- Geometry: 32 strips x 138 LEDs = 4416 RGB pixels.
- Manager timeline: 200 Hz for 10.0
  simulated seconds (2000 back-to-back measured calls) per
  scenario after one untimed warm-up.
- Fixed timeline start: `2026-07-21T13:47:36+00:00`.
- Host: `macOS-26.5.2-arm64-arm-64bit` / `arm64` / `arm`.
- Runtime: Python `3.10.5`, NumPy `2.2.6`.
- Timer: `time.perf_counter_ns`; latency covers `ClockAnimation.generate_frame` only.

## Cadence, latency, and changed pixels

Latency columns are p50/p95/p99/max in milliseconds. Dirty pixels and ranges
are derived by byte-comparing consecutive presented frames in canonical flat
strip-major order. This full-scene diff is only a proxy for future sparse-overlay
potential; it is not an RGBA overlay measurement. The current Clock
declares no `dirty_ranges`, so every changed tick still sends complete RGB.

| Scenario | Changed/calls | Changed ratio | Observed cadence | All-call ms | Changed-call ms | Dirty pixels | Derived ranges | Declared dirty frames |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| normal | 10/2000 | 0.500% | 1.0 Hz | 0.0028/0.0037/0.0095/0.2344 | 0.2057/0.2344/0.2344/0.2344 | 11.0/19.0/19.0/19 | 5.0/9.0/9.0/9 | 0 |
| animated | 120/2000 | 6.000% | 12.0 Hz | 0.003/0.4111/0.461/0.6314 | 0.4252/0.528/0.6032/0.6314 | 2476.0/2615.0/2672.0/2700 | 91.0/110.0/117.0/117 | 0 |

The normal scenario is: Default digital face and non-animated gradient background.
The animated scenario is: Existing animation-render stress Clock: aurora background, hourglass face, and maximum supported density/glow/motion/speed.
Both had zero cached-frame byte mismatches and
zero changed signals without a real pixel delta.

## Full-frame payload facts

One complete 32 x 138 RGB frame is **13,248 bytes**. At a
dense 200 Hz that is **2,649,600 bytes/s**
(21,196,800 bits/s) before
command headers, CRC, status, retries, and physical encoding.

| Scenario | Current complete-RGB bytes/s | Idealized byte-diff RGB bytes/s | Diff/full ratio on changed frames |
| --- | ---: | ---: | ---: |
| normal | 13,248.00 | 34.20 | 0.258% |
| animated | 158,976.00 | 89,526.30 | 56.314% |

The idealized byte-diff column is not an on-wire forecast: it counts three RGB
bytes per changed pixel and excludes range/patch headers, alpha, clears, CRC,
status, retries, and repair snapshots. It is retained to size later overlay
acceptance against actual foreground protocol measurements.

## Preview behavior

The manifest has no custom Clock preview profile, so it inherits captures at
`[0.0, 0.5, 1.0, 2.0, 3.5, 5.5, 8.0, 12.0]` and simulates intermediate steps at
30 FPS. Preview wall time is fixed at
`2026-01-15T10:19:00+00:00`. Output is lossless WebP poster plus infinite-loop lossless WebP;
layout is `width=strips, height=leds_per_strip; physical LED 0 is image bottom`.

| Input | Static | Authored frames | Per-frame duration | Encoded loop duration |
| --- | --- | ---: | ---: | ---: |
| default Clock | True | 1 | 500 ms | 500 ms |
| animated stress Clock | False | 8 | 500 ms | 4000 ms |

A fixed-time default Clock collapses to one static WebP because its gradient
background and face do not change. The animated stress Clock retains the eight
authored capture frames. The encoded loop presents every retained frame for 500
ms; capture-time gaps are not represented as variable WebP durations.

## Acceptance criteria

- Both scenarios must stay at or below 4.0 ms for both
  all-call and changed-call p95 on the capture host. Gating changed calls keeps
  a high cache-hit ratio from concealing expensive semantic/source ticks while
  preserving headroom inside the 5 ms manager period.
- The normal case must change exactly at 1 Hz and the animated case at 12 Hz
  over the deterministic ten-second window; manager calls must not multiply
  semantic/source ticks.
- Every cached frame must remain byte-identical, every changed tick must change
  at least one pixel, and frames must remain contiguous `uint8` 32 x 138 RGB.
- A normal Clock full-scene diff must change less than 10 percent of wall pixels.
  This is a useful proxy for choosing the Clock as a sparse-overlay candidate,
  while explicitly retaining that the current full scene transports the complete
  frame and has no alpha foreground contract.
- The actual preview path must collapse the default fixed-time Clock to one
  frame and retain multiple frames for the animated stress Clock.

All portable Clock baseline criteria passed.

## Preserved deployment and identity evidence

These facts are retained repository evidence from the completed Phase 0 gate;
the Clock benchmark does not rerun deployment, flash receivers, or claim fresh
hardware timing:

- The prior portable gate recorded 580 Python unit/plugin tests plus 860
  subtests, 18 rendering tests, 8 native firmware tests, and 138 deployment
  tests plus 63 subtests passing. Source: the Phase 0 portable-evidence section
  of `docs/plan-revamped-animation-pipeline.md`.
- Clean deployment receipt `be50b119fd5948a78112eb6aab7e18e4` validated
  source commit `9d2ac07c75eb80efeb099edd42297ae6308fbfa7`, selected
  app release `c9a0cb505314bf62fa5c59d7334e7d2cdf017d0aa7582bddc41541ee713748bb`,
  used support release `b91f8415cf0e81c5e90db6a8e4bea07122c40c5b4c4e85de624fc6ab540799a2`,
  and reused Pi runtime identity `eed879c054a5c19f470cd12fa00bfd2d8877d6e5ea6787cebecb7f7927d31c97`.
- That clean receipt captured `clock/before-deploy`, activated through systemd
  `current`, restarted, restored `clock/before-deploy`, and accepted two fresh
  exact-release health samples at 32 x 138 with four logical receivers.
- The retained coordinator build artifact reported firmware SHA-256
  `df40542ef55963c1338b3167419eb48b7d5202c254e263f0ad226dd6fdf97fd9`
  from content-addressed build input
  `2f7fd7d95c50c9be8ba4a794d1a1dd75259840d46d3fa707bc16a3d466354024`.
  The clean deployment skipped flashing because firmware was unchanged; these
  are retained build/deployment identities, not a newly read receiver identity.
- Deployment preservation and rollback behavior remains regression-covered in
  `tests/unit/test_deploy_coordinator.py`, `tests/unit/test_deploy_entrypoint.py`,
  `tests/unit/test_app_releases.py`, and `tests/unit/test_preserve_deploy_settings.py`.

No Raspberry Pi render latency, SPI throughput, receiver encode/DMA timing,
start skew, drift, or photographed wall acceptance was collected by this lane.
Gate H0 and later physical gates remain outstanding where the roadmap says so.
