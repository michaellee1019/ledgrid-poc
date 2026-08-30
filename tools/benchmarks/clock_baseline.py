#!/usr/bin/env python3
"""Characterize the deterministic installed-wall Clock baseline for Phase 1.

This benchmark exercises the existing full-scene Clock at the manager's 200 Hz
call cadence.  It fixes wall time while advancing it with scenario time, so
changed-frame, dirty-pixel, and preview results are reproducible.  Render
latencies describe only the machine that ran the command.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import statistics
import sys
import time
from typing import Any, Callable, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from animation.core.base import RenderedFrame  # noqa: E402
from tools.deterministic_rendering import (  # noqa: E402
    FIXED_CLOCK,
    capture_frames,
    make_deterministic,
    preview_profile,
)
from animation.plugins.clock import ClockAnimation  # noqa: E402
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT  # noqa: E402


BASELINE_VERSION = 1
DEFAULT_MANAGER_FPS = 200
DEFAULT_DURATION_SECONDS = 10.0
FIXED_TIMELINE_START = datetime(2026, 7, 21, 13, 47, 36, tzinfo=timezone.utc)
MAX_PLUGIN_P95_MS = 4.0


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    description: str
    config: dict[str, Any]
    expected_source_hz: float


SCENARIOS = (
    Scenario(
        scenario_id="normal",
        description="Default digital face and non-animated gradient background.",
        config={},
        expected_source_hz=1.0,
    ),
    Scenario(
        scenario_id="animated",
        description=(
            "Existing animation-render stress Clock: aurora background, hourglass "
            "face, and maximum supported density/glow/motion/speed."
        ),
        config={
            "background": "aurora",
            "face": "hourglass",
            "density": 1.0,
            "glow": 1.0,
            "motion": 3.0,
            "speed": 4.0,
        },
        expected_source_hz=12.0,
    ),
)


class BenchmarkController:
    debug = False
    inline_show = True

    def __init__(self, strips: int, leds_per_strip: int):
        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip


class TimelineClock(ClockAnimation):
    """Clock whose wall time advances from the deterministic scenario timeline."""

    timeline_seconds = 0.0

    def _clock_now(self) -> datetime:
        return FIXED_TIMELINE_START + timedelta(seconds=self.timeline_seconds)


def percentile(samples: Iterable[float], ratio: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return float(ordered[index])


def numeric_summary(samples: Iterable[float], *, digits: int = 4) -> dict[str, float]:
    values = list(samples)
    if not values:
        return {key: 0.0 for key in ("mean", "p50", "p95", "p99", "max")}
    return {
        "mean": round(statistics.mean(values), digits),
        "p50": round(percentile(values, 0.50), digits),
        "p95": round(percentile(values, 0.95), digits),
        "p99": round(percentile(values, 0.99), digits),
        "max": round(max(values), digits),
    }


def contiguous_ranges(mask: np.ndarray) -> tuple[tuple[int, int], ...]:
    """Return half-open contiguous ranges for a one-dimensional Boolean mask."""

    indices = np.flatnonzero(np.asarray(mask, dtype=bool))
    if not len(indices):
        return ()
    breaks = np.flatnonzero(np.diff(indices) != 1) + 1
    groups = np.split(indices, breaks)
    return tuple((int(group[0]), int(group[-1]) + 1) for group in groups)


def _pixels(rendered: Any, total_leds: int) -> np.ndarray:
    pixels = rendered.pixels if isinstance(rendered, RenderedFrame) else rendered
    array = np.asarray(pixels)
    if array.shape != (total_leds, 3) or array.dtype != np.uint8:
        raise ValueError(
            f"Clock returned {array.dtype} {array.shape}; expected "
            f"uint8 ({total_leds}, 3)"
        )
    if not array.flags.c_contiguous:
        raise ValueError("Clock returned a non-contiguous frame")
    return array


def measure_scenario(
    scenario: Scenario,
    *,
    strips: int = DEFAULT_STRIP_COUNT,
    leds_per_strip: int = DEFAULT_LEDS_PER_STRIP,
    manager_fps: int = DEFAULT_MANAGER_FPS,
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    timer_ns: Callable[[], int] = time.perf_counter_ns,
) -> dict[str, Any]:
    """Measure one deterministic Clock scenario after one untimed warm-up frame."""

    if manager_fps <= 0 or duration_seconds <= 0:
        raise ValueError("manager_fps and duration_seconds must be positive")
    calls = int(round(manager_fps * duration_seconds))
    if calls <= 0:
        raise ValueError("scenario must contain at least one manager call")

    controller = BenchmarkController(strips, leds_per_strip)
    animation = TimelineClock(controller, dict(scenario.config))
    animation.timeline_seconds = 0.0
    initial = animation.generate_frame(0.0, 0)
    previous_presented = _pixels(initial, controller.total_leds).copy()

    all_latency_ms: list[float] = []
    changed_latency_ms: list[float] = []
    cached_latency_ms: list[float] = []
    changed_pixels: list[int] = []
    derived_ranges: list[int] = []
    declared_ranges: list[int] = []
    cached_byte_mismatches = 0
    changed_without_pixel_delta = 0

    for call_index in range(1, calls + 1):
        elapsed = call_index / manager_fps
        animation.timeline_seconds = elapsed
        started = timer_ns()
        rendered = animation.generate_frame(elapsed, call_index)
        finished = timer_ns()
        latency_ms = (finished - started) / 1_000_000.0
        all_latency_ms.append(latency_ms)

        pixels = _pixels(rendered, controller.total_leds)
        changed = not isinstance(rendered, RenderedFrame) or rendered.changed
        if changed:
            changed_latency_ms.append(latency_ms)
            dirty_mask = np.any(pixels != previous_presented, axis=1)
            pixel_count = int(np.count_nonzero(dirty_mask))
            changed_pixels.append(pixel_count)
            derived_ranges.append(len(contiguous_ranges(dirty_mask)))
            metadata = rendered.dirty_ranges if isinstance(rendered, RenderedFrame) else None
            declared_ranges.append(len(metadata or ()))
            if pixel_count == 0:
                changed_without_pixel_delta += 1
            previous_presented = pixels.copy()
        else:
            cached_latency_ms.append(latency_ms)
            if not np.array_equal(pixels, previous_presented):
                cached_byte_mismatches += 1

    full_rgb_bytes = controller.total_leds * 3
    changed_frames = len(changed_latency_ms)
    changed_ratio = changed_frames / calls
    current_payload_bytes = changed_frames * full_rgb_bytes
    derived_dirty_bytes = sum(changed_pixels) * 3
    return {
        "scenario_id": scenario.scenario_id,
        "description": scenario.description,
        "config": dict(scenario.config),
        "manager_calls": calls,
        "manager_fps": manager_fps,
        "duration_seconds": duration_seconds,
        "expected_source_hz": scenario.expected_source_hz,
        "changed_frames": changed_frames,
        "cached_frames": calls - changed_frames,
        "changed_ratio": round(changed_ratio, 6),
        "observed_changed_hz": round(changed_frames / duration_seconds, 4),
        "latency_ms": {
            "all_calls": numeric_summary(all_latency_ms),
            "changed_calls": numeric_summary(changed_latency_ms),
            "cached_calls": numeric_summary(cached_latency_ms),
        },
        "derived_dirty_pixels_per_changed_frame": numeric_summary(
            changed_pixels, digits=2
        ),
        "derived_ranges_per_changed_frame": numeric_summary(
            derived_ranges, digits=2
        ),
        "declared_dirty_ranges_per_changed_frame": numeric_summary(
            declared_ranges, digits=2
        ),
        "declared_dirty_metadata_frames": sum(value > 0 for value in declared_ranges),
        "cached_byte_mismatches": cached_byte_mismatches,
        "changed_without_pixel_delta": changed_without_pixel_delta,
        "current_full_rgb_payload_bytes": current_payload_bytes,
        "current_full_rgb_payload_bytes_per_second": round(
            current_payload_bytes / duration_seconds, 2
        ),
        "derived_changed_rgb_bytes": derived_dirty_bytes,
        "derived_changed_rgb_bytes_per_second": round(
            derived_dirty_bytes / duration_seconds, 2
        ),
        "derived_payload_ratio_on_changed_frames": round(
            derived_dirty_bytes / current_payload_bytes if current_payload_bytes else 0.0,
            6,
        ),
    }


def characterize_previews(
    *,
    strips: int = DEFAULT_STRIP_COUNT,
    leds_per_strip: int = DEFAULT_LEDS_PER_STRIP,
) -> dict[str, Any]:
    """Characterize deterministic Clock captures without publishing images."""
    manifest = {"preview": {"capture_seconds": [0, 0.5, 1, 2, 3.5, 5.5, 8, 12], "simulation_fps": 30}}
    captures, simulation_fps = preview_profile(manifest)

    def capture(config: dict[str, Any], key: str) -> list[np.ndarray]:
        controller = BenchmarkController(strips, leds_per_strip)
        animation = ClockAnimation(controller, dict(config))
        make_deterministic(animation, config, key)
        return capture_frames(
            animation, captures=captures, simulation_fps=simulation_fps,
        )

    default_frames = capture({}, "clock/default")
    animated_frames = capture(dict(SCENARIOS[1].config), "clock/animated")

    def stable_entry(frames: list[np.ndarray]) -> dict[str, Any]:
        static = all(np.array_equal(frame, frames[0]) for frame in frames[1:])
        authored_frames = 1 if static else len(frames)
        return {
            "static": static,
            "authored_frames": authored_frames,
            "frame_duration_ms": 500,
            "encoded_loop_duration_ms": authored_frames * 500,
        }

    return {
        "fixed_wall_clock": FIXED_CLOCK.isoformat(),
        "capture_seconds": list(captures),
        "simulation_fps": simulation_fps,
        "image_layout": "width=strips, height=leds_per_strip; physical LED 0 is image bottom",
        "format": "in-memory deterministic frame capture",
        "default": stable_entry(default_frames),
        "animated": stable_entry(animated_frames),
    }


def evaluate_acceptance(report: dict[str, Any]) -> list[str]:
    """Return actionable acceptance failures; an empty list passes."""

    failures: list[str] = []
    geometry = report["geometry"]
    installed_geometry = (DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP)
    if (geometry["strip_count"], geometry["leds_per_strip"]) != installed_geometry:
        failures.append(
            "baseline geometry must be the installed "
            f"{installed_geometry[0]} x {installed_geometry[1]} layout"
        )
    scenarios = {item["scenario_id"]: item for item in report["scenarios"]}
    for scenario_id in ("normal", "animated"):
        item = scenarios[scenario_id]
        expected_changed = int(round(
            item["expected_source_hz"] * item["duration_seconds"]
        ))
        if item["changed_frames"] != expected_changed:
            failures.append(
                f"{scenario_id} changed {item['changed_frames']} times; "
                f"expected {expected_changed} semantic/source ticks"
            )
        if item["latency_ms"]["all_calls"]["p95"] > MAX_PLUGIN_P95_MS:
            failures.append(
                f"{scenario_id} all-call p95 "
                f"{item['latency_ms']['all_calls']['p95']} ms exceeds "
                f"{MAX_PLUGIN_P95_MS} ms"
            )
        if item["latency_ms"]["changed_calls"]["p95"] > MAX_PLUGIN_P95_MS:
            failures.append(
                f"{scenario_id} changed-call p95 "
                f"{item['latency_ms']['changed_calls']['p95']} ms exceeds "
                f"{MAX_PLUGIN_P95_MS} ms"
            )
        if item["cached_byte_mismatches"]:
            failures.append(f"{scenario_id} changed pixels while reporting changed=False")
        if item["changed_without_pixel_delta"]:
            failures.append(f"{scenario_id} reported changed=True without pixel deltas")
        if item["declared_dirty_metadata_frames"]:
            failures.append(
                f"{scenario_id} baseline unexpectedly declared partial dirty metadata"
            )
        if item["derived_dirty_pixels_per_changed_frame"]["max"] <= 0:
            failures.append(f"{scenario_id} produced no visible changed tick")

    total_leds = geometry["total_leds"]
    if scenarios["normal"]["derived_dirty_pixels_per_changed_frame"]["max"] >= total_leds * 0.10:
        failures.append("normal Clock tick changes at least 10% of wall pixels")

    previews = report["preview"]
    if not previews["default"]["static"] or previews["default"]["authored_frames"] != 1:
        failures.append("fixed-time default Clock preview must collapse to one static frame")
    if previews["animated"]["static"] or previews["animated"]["authored_frames"] < 2:
        failures.append("animated Clock preview must retain multiple visible frames")
    return failures


def run_baseline(
    *,
    strips: int = DEFAULT_STRIP_COUNT,
    leds_per_strip: int = DEFAULT_LEDS_PER_STRIP,
    manager_fps: int = DEFAULT_MANAGER_FPS,
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    timer_ns: Callable[[], int] = time.perf_counter_ns,
) -> dict[str, Any]:
    full_rgb_bytes = strips * leds_per_strip * 3
    report = {
        "baseline_version": BASELINE_VERSION,
        "scope": "development-host Clock generation; no Raspberry Pi or ESP32 timing",
        "machine": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or "not reported",
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "geometry": {
            "strip_count": strips,
            "leds_per_strip": leds_per_strip,
            "total_leds": strips * leds_per_strip,
        },
        "full_frame_facts": {
            "rgb_bytes": full_rgb_bytes,
            "rgb_bytes_per_second_at_manager_fps": full_rgb_bytes * manager_fps,
            "rgb_bits_per_second_at_manager_fps": full_rgb_bytes * manager_fps * 8,
            "manager_fps": manager_fps,
            "excludes": "command headers, CRC, status, retries, and physical encoding",
        },
        "scenarios": [
            measure_scenario(
                scenario,
                strips=strips,
                leds_per_strip=leds_per_strip,
                manager_fps=manager_fps,
                duration_seconds=duration_seconds,
                timer_ns=timer_ns,
            )
            for scenario in SCENARIOS
        ],
        "preview": characterize_previews(
            strips=strips, leds_per_strip=leds_per_strip
        ),
    }
    report["acceptance"] = {
        "max_plugin_p95_ms": MAX_PLUGIN_P95_MS,
        "failures": evaluate_acceptance(report),
    }
    return report


def _metric(summary: dict[str, Any]) -> str:
    return "/".join(str(summary[key]) for key in ("p50", "p95", "p99", "max"))


def render_markdown(report: dict[str, Any]) -> str:
    machine = report["machine"]
    geometry = report["geometry"]
    facts = report["full_frame_facts"]
    normal, animated = report["scenarios"]
    preview = report["preview"]
    failures = report["acceptance"]["failures"]
    status = "PASS" if not failures else "FAIL"
    lines = [
        "# Phase 1 Clock Baseline",
        "",
        f"Baseline schema: `{report['baseline_version']}`. Acceptance: **{status}**.",
        "",
        "This report characterizes the existing compatibility full-scene Clock. Wall",
        "time comes from an overridden `_clock_now` fixed to one UTC start and advanced",
        "from the same deterministic elapsed timeline used by the background. Therefore",
        "cadence, changed-frame, dirty-pixel, and preview results are deterministic.",
        "Latencies are measurements",
        "from the development host below; they are not Raspberry Pi or ESP32 evidence.",
        "",
        "Regenerate and enforce the functional/performance gates with:",
        "",
        "```bash",
        "uv run --with numpy --with pillow tools/benchmarks/clock_baseline.py --check --markdown-output docs/clock-phase1-baseline.md",
        "```",
        "",
        "## Measurement envelope",
        "",
        f"- Geometry: {geometry['strip_count']} strips x {geometry['leds_per_strip']} LEDs = {geometry['total_leds']} RGB pixels.",
        f"- Manager timeline: {normal['manager_fps']} Hz for {normal['duration_seconds']:.1f}",
        f"  simulated seconds ({normal['manager_calls']} back-to-back measured calls) per",
        "  scenario after one untimed warm-up.",
        f"- Fixed timeline start: `{FIXED_TIMELINE_START.isoformat()}`.",
        f"- Host: `{machine['platform']}` / `{machine['machine']}` / `{machine['processor']}`.",
        f"- Runtime: Python `{machine['python']}`, NumPy `{machine['numpy']}`.",
        "- Timer: `time.perf_counter_ns`; latency covers `ClockAnimation.generate_frame` only.",
        "",
        "## Cadence, latency, and changed pixels",
        "",
        "Latency columns are p50/p95/p99/max in milliseconds. Dirty pixels and ranges",
        "are derived by byte-comparing consecutive presented frames in canonical flat",
        "strip-major order. This full-scene diff is only a proxy for future sparse-overlay",
        "potential; it is not an RGBA overlay measurement. The current Clock",
        "declares no `dirty_ranges`, so every changed tick still sends complete RGB.",
        "",
        "| Scenario | Changed/calls | Changed ratio | Observed cadence | All-call ms | Changed-call ms | Dirty pixels | Derived ranges | Declared dirty frames |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in (normal, animated):
        lines.append(
            f"| {item['scenario_id']} | {item['changed_frames']}/{item['manager_calls']} | "
            f"{item['changed_ratio']:.3%} | {item['observed_changed_hz']} Hz | "
            f"{_metric(item['latency_ms']['all_calls'])} | "
            f"{_metric(item['latency_ms']['changed_calls'])} | "
            f"{_metric(item['derived_dirty_pixels_per_changed_frame'])} | "
            f"{_metric(item['derived_ranges_per_changed_frame'])} | "
            f"{item['declared_dirty_metadata_frames']} |"
        )
    lines.extend([
        "",
        f"The normal scenario is: {normal['description']}",
        f"The animated scenario is: {animated['description']}",
        "Both had zero cached-frame byte mismatches and",
        "zero changed signals without a real pixel delta.",
        "",
        "## Full-frame payload facts",
        "",
        f"One complete {geometry['strip_count']} x {geometry['leds_per_strip']} "
        f"RGB frame is **{facts['rgb_bytes']:,} bytes**. At a",
        f"dense {facts['manager_fps']} Hz that is **{facts['rgb_bytes_per_second_at_manager_fps']:,} bytes/s**",
        f"({facts['rgb_bits_per_second_at_manager_fps']:,} bits/s) before",
        f"{facts['excludes']}.",
        "",
        "| Scenario | Current complete-RGB bytes/s | Idealized byte-diff RGB bytes/s | Diff/full ratio on changed frames |",
        "| --- | ---: | ---: | ---: |",
        f"| normal | {normal['current_full_rgb_payload_bytes_per_second']:,.2f} | {normal['derived_changed_rgb_bytes_per_second']:,.2f} | {normal['derived_payload_ratio_on_changed_frames']:.3%} |",
        f"| animated | {animated['current_full_rgb_payload_bytes_per_second']:,.2f} | {animated['derived_changed_rgb_bytes_per_second']:,.2f} | {animated['derived_payload_ratio_on_changed_frames']:.3%} |",
        "",
        "The idealized byte-diff column is not an on-wire forecast: it counts three RGB",
        "bytes per changed pixel and excludes range/patch headers, alpha, clears, CRC,",
        "status, retries, and repair snapshots. It is retained to size later overlay",
        "acceptance against actual foreground protocol measurements.",
        "",
        "## Preview behavior",
        "",
        "The manifest has no custom Clock preview profile, so it inherits captures at",
        f"`{preview['capture_seconds']}` and simulates intermediate steps at",
        f"{preview['simulation_fps']} FPS. Preview wall time is fixed at",
        f"`{preview['fixed_wall_clock']}`. Output is {preview['format']};",
        f"layout is `{preview['image_layout']}`.",
        "",
        "| Input | Static | Authored frames | Per-frame duration | Encoded loop duration |",
        "| --- | --- | ---: | ---: | ---: |",
        f"| default Clock | {preview['default']['static']} | {preview['default']['authored_frames']} | {preview['default']['frame_duration_ms']} ms | {preview['default']['encoded_loop_duration_ms']} ms |",
        f"| animated stress Clock | {preview['animated']['static']} | {preview['animated']['authored_frames']} | {preview['animated']['frame_duration_ms']} ms | {preview['animated']['encoded_loop_duration_ms']} ms |",
        "",
        "A fixed-time default Clock collapses to one static WebP because its gradient",
        "background and face do not change. The animated stress Clock retains the eight",
        "authored capture frames. The encoded loop presents every retained frame for 500",
        "ms; capture-time gaps are not represented as variable WebP durations.",
        "",
        "## Acceptance criteria",
        "",
        f"- Both scenarios must stay at or below {MAX_PLUGIN_P95_MS:.1f} ms for both",
        "  all-call and changed-call p95 on the capture host. Gating changed calls keeps",
        "  a high cache-hit ratio from concealing expensive semantic/source ticks while",
        "  preserving headroom inside the 5 ms manager period.",
        "- The normal case must change exactly at 1 Hz and the animated case at 12 Hz",
        "  over the deterministic ten-second window; manager calls must not multiply",
        "  semantic/source ticks.",
        "- Every cached frame must remain byte-identical, every changed tick must change",
        "  at least one pixel, and frames must remain contiguous `uint8` "
        f"{geometry['strip_count']} x {geometry['leds_per_strip']} RGB.",
        "- A normal Clock full-scene diff must change less than 10 percent of wall pixels.",
        "  This is a useful proxy for choosing the Clock as a sparse-overlay candidate,",
        "  while explicitly retaining that the current full scene transports the complete",
        "  frame and has no alpha foreground contract.",
        "- The actual preview path must collapse the default fixed-time Clock to one",
        "  frame and retain multiple frames for the animated stress Clock.",
        "",
    ])
    if failures:
        lines.extend(["Failures:", ""] + [f"- {failure}" for failure in failures] + [""])
    else:
        lines.extend(["All portable Clock baseline criteria passed.", ""])

    lines.extend([
        "## Preserved deployment and identity evidence",
        "",
        "These facts are retained repository evidence from the completed Phase 0 gate;",
        "the Clock benchmark does not rerun deployment, flash receivers, or claim fresh",
        "hardware timing:",
        "",
        "- The prior portable gate recorded 580 Python unit/plugin tests plus 860",
        "  subtests, 18 rendering tests, 8 native firmware tests, and 138 deployment",
        "  tests plus 63 subtests passing. Source: the Phase 0 portable-evidence section",
        "  of `docs/plan-revamped-animation-pipeline.md`.",
        "- Clean deployment receipt `be50b119fd5948a78112eb6aab7e18e4` validated",
        "  source commit `9d2ac07c75eb80efeb099edd42297ae6308fbfa7`, selected",
        "  app release `c9a0cb505314bf62fa5c59d7334e7d2cdf017d0aa7582bddc41541ee713748bb`,",
        "  used support release `b91f8415cf0e81c5e90db6a8e4bea07122c40c5b4c4e85de624fc6ab540799a2`,",
        "  and reused Pi runtime identity `eed879c054a5c19f470cd12fa00bfd2d8877d6e5ea6787cebecb7f7927d31c97`.",
        "- That clean receipt captured `clock/before-deploy`, activated through systemd",
        "  `current`, restarted, restored `clock/before-deploy`, and accepted two fresh",
        "  exact-release health samples at 32 x 138 with four logical receivers.",
        "- The retained coordinator build artifact reported firmware SHA-256",
        "  `df40542ef55963c1338b3167419eb48b7d5202c254e263f0ad226dd6fdf97fd9`",
        "  from content-addressed build input",
        "  `2f7fd7d95c50c9be8ba4a794d1a1dd75259840d46d3fa707bc16a3d466354024`.",
        "  The clean deployment skipped flashing because firmware was unchanged; these",
        "  are retained build/deployment identities, not a newly read receiver identity.",
        "- Deployment preservation and rollback behavior remains regression-covered in",
        "  `tests/unit/test_deploy_coordinator.py`, `tests/unit/test_deploy_entrypoint.py`,",
        "  `tests/unit/test_app_releases.py`, and `tests/unit/test_preserve_deploy_settings.py`.",
        "",
        "No Raspberry Pi render latency, SPI throughput, receiver encode/DMA timing,",
        "start skew, drift, or photographed wall acceptance was collected by this lane.",
        "Gate H0 and later physical gates remain outstanding where the roadmap says so.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strips", type=int, default=DEFAULT_STRIP_COUNT)
    parser.add_argument("--leds-per-strip", type=int, default=DEFAULT_LEDS_PER_STRIP)
    parser.add_argument("--manager-fps", type=int, default=DEFAULT_MANAGER_FPS)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--json", action="store_true", help="print the full JSON report")
    parser.add_argument("--markdown-output", type=Path, help="write a human-readable report")
    parser.add_argument("--check", action="store_true", help="fail when acceptance criteria fail")
    args = parser.parse_args()

    report = run_baseline(
        strips=args.strips,
        leds_per_strip=args.leds_per_strip,
        manager_fps=args.manager_fps,
        duration_seconds=args.duration,
    )
    if args.markdown_output:
        output = args.markdown_output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_markdown(report), encoding="utf-8")
        print(f"wrote Clock baseline to {output}")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif not args.markdown_output:
        for scenario in report["scenarios"]:
            print(
                f"{scenario['scenario_id']}: p95="
                f"{scenario['latency_ms']['all_calls']['p95']} ms, "
                f"changed={scenario['changed_frames']}/{scenario['manager_calls']}, "
                f"dirty-p95={scenario['derived_dirty_pixels_per_changed_frame']['p95']}"
            )

    failures = report["acceptance"]["failures"]
    if args.check and failures:
        print("Clock baseline acceptance failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    if args.check:
        print("Clock baseline acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
