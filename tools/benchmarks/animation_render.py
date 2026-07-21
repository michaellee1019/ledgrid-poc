#!/usr/bin/env python3
"""Headless render benchmark for every active frame-based animation."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import statistics
import sys
import time
import tracemalloc

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animation.core.base import RenderedFrame, StatefulAnimationBase
from animation.core.manager import AnimationManager
from animation.core.plugin_loader import AnimationPluginLoader
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


STRESS_SCENARIOS = {
    "clock-animated": {
        "plugin": "clock",
        "fps": 90.0,
        "config": {
            "background": "aurora", "face": "hourglass", "density": 1.0,
            "glow": 1.0, "motion": 3.0, "speed": 4.0,
        },
    },
    "snake-max-density": {
        "plugin": "snake",
        "fps": 90.0,
        "config": {
            "snake_count": 12, "initial_length": 30, "max_length": 800,
            "food_count": 30, "visual_style": "prism", "background": "aurora",
            "trail_strength": 1.0, "glow": 1.0,
            "moves_per_second": 30.0, "speed": 4.0, "render_fps": 90.0,
        },
    },
    "plant-glow-conway": {
        "plugin": "plant_glow",
        "fps": 100.0,
        "config": {
            "background_source": "conway", "background_style": "arcade",
            "background_strength": 0.7, "background_speed": 3.0,
            "glow_radius": 5, "glow_strength": 2.0, "shimmer": 0.5,
        },
    },
    "plant-glow-pinball": {
        "plugin": "plant_glow",
        "fps": 100.0,
        "config": {
            "background_source": "pinball", "background_strength": 0.7,
            "background_speed": 3.0, "glow_radius": 5,
            "glow_strength": 2.0, "shimmer": 0.5,
        },
    },
}


class BenchmarkController:
    debug = False
    inline_show = True

    def __init__(self, strips: int, leds_per_strip: int):
        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip

    def set_all_pixels(self, _frame):
        pass

    def show(self):
        pass

    def clear(self):
        pass

    def get_hardware_status(self):
        return []


def percentile(samples, ratio):
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return ordered[index]


def benchmark(args):
    controller = BenchmarkController(args.strips, args.leds_per_strip)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        loader = AnimationPluginLoader(allowed_plugins=AnimationManager.ALLOWED_PLUGINS)
        plugins = loader.load_all_plugins()

    work_items = [
        (name, animation_class, "default", {}, args.fps)
        for name, animation_class in sorted(plugins.items())
    ]
    if args.stress:
        for scenario_name, scenario in STRESS_SCENARIOS.items():
            plugin_name = scenario["plugin"]
            work_items.append((
                plugin_name,
                plugins[plugin_name],
                scenario_name,
                scenario["config"],
                scenario["fps"],
            ))

    results = []
    for name, animation_class, scenario_name, config, scenario_fps in work_items:
        if issubclass(animation_class, StatefulAnimationBase):
            results.append({"plugin": name, "scenario": scenario_name, "kind": "stateful"})
            continue

        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                animation = animation_class(controller, config)
                animation.start()
                for frame_count in range(args.warmup):
                    animation.generate_frame(frame_count / scenario_fps, frame_count)

                timings = []
                changed_frames = 0
                rendered = None
                for frame_count in range(args.warmup, args.warmup + args.frames):
                    started = time.perf_counter()
                    rendered = animation.generate_frame(frame_count / scenario_fps, frame_count)
                    timings.append((time.perf_counter() - started) * 1000.0)
                    if not isinstance(rendered, RenderedFrame) or rendered.changed:
                        changed_frames += 1

                # Allocation tracking substantially slows Python-heavy effects,
                # so sample it separately from render latency.
                tracemalloc.start()
                allocation_frames = min(20, args.frames)
                for offset in range(allocation_frames):
                    frame_count = args.warmup + args.frames + offset
                    animation.generate_frame(frame_count / scenario_fps, frame_count)
                _current, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()

            pixels = rendered.pixels if isinstance(rendered, RenderedFrame) else rendered
            if not isinstance(pixels, np.ndarray):
                raise TypeError(f"returned {type(pixels).__name__}, expected ndarray")
            expected_shape = (controller.total_leds, 3)
            if pixels.shape != expected_shape or pixels.dtype != np.uint8:
                raise ValueError(f"returned {pixels.dtype} {pixels.shape}, expected uint8 {expected_shape}")

            results.append({
                "plugin": name,
                "scenario": scenario_name,
                "kind": "frame",
                "mean_ms": round(statistics.mean(timings), 4),
                "p50_ms": round(percentile(timings, 0.50), 4),
                "p95_ms": round(percentile(timings, 0.95), 4),
                "p99_ms": round(percentile(timings, 0.99), 4),
                "max_ms": round(max(timings), 4),
                "peak_kib": round(peak / 1024.0, 2),
                "changed_ratio": round(changed_frames / args.frames, 4),
            })
        except Exception as exc:
            if tracemalloc.is_tracing():
                tracemalloc.stop()
            results.append({
                "plugin": name,
                "scenario": scenario_name,
                "kind": "frame",
                "error": f"{type(exc).__name__}: {exc}",
            })
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strips", type=int, default=DEFAULT_STRIP_COUNT)
    parser.add_argument("--leds-per-strip", type=int, default=DEFAULT_LEDS_PER_STRIP)
    parser.add_argument("--fps", type=float, default=200.0)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--frames", type=int, default=200)
    parser.add_argument(
        "--stress", action="store_true",
        help="also run named animated and maximum-density scenarios",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit nonzero on render errors or p95 latency above the limit",
    )
    parser.add_argument("--max-p95-ms", type=float, default=4.0)
    args = parser.parse_args()

    results = benchmark(args)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("plugin\tscenario\tkind\tmean_ms\tp95_ms\tmax_ms\tpeak_kib\tchanged\terror")
        for result in results:
            print("\t".join(str(result.get(key, "-")) for key in (
                "plugin", "scenario", "kind", "mean_ms", "p95_ms", "max_ms",
                "peak_kib", "changed_ratio", "error"
            )))

    if args.check:
        failures = []
        for result in results:
            if result.get("error"):
                failures.append(
                    f"{result['plugin']}[{result.get('scenario', 'default')}]: {result['error']}"
                )
            elif (
                result.get("kind") == "frame"
                and float(result.get("p95_ms", 0.0)) > args.max_p95_ms
            ):
                failures.append(
                    f"{result['plugin']}[{result.get('scenario', 'default')}]: "
                    f"p95 {result['p95_ms']} ms exceeds "
                    f"{args.max_p95_ms} ms"
                )
        if failures:
            print("animation render acceptance failed:", file=sys.stderr)
            for failure in failures:
                print(f"- {failure}", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
