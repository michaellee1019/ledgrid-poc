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

    results = []
    for name, animation_class in sorted(plugins.items()):
        if issubclass(animation_class, StatefulAnimationBase):
            results.append({"plugin": name, "kind": "stateful"})
            continue

        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                animation = animation_class(controller)
                animation.start()
                for frame_count in range(args.warmup):
                    animation.generate_frame(frame_count / args.fps, frame_count)

                timings = []
                changed_frames = 0
                rendered = None
                for frame_count in range(args.warmup, args.warmup + args.frames):
                    started = time.perf_counter()
                    rendered = animation.generate_frame(frame_count / args.fps, frame_count)
                    timings.append((time.perf_counter() - started) * 1000.0)
                    if not isinstance(rendered, RenderedFrame) or rendered.changed:
                        changed_frames += 1

                # Allocation tracking substantially slows Python-heavy effects,
                # so sample it separately from render latency.
                tracemalloc.start()
                allocation_frames = min(20, args.frames)
                for offset in range(allocation_frames):
                    frame_count = args.warmup + args.frames + offset
                    animation.generate_frame(frame_count / args.fps, frame_count)
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
                "kind": "frame",
                "mean_ms": round(statistics.mean(timings), 4),
                "p50_ms": round(percentile(timings, 0.50), 4),
                "p95_ms": round(percentile(timings, 0.95), 4),
                "p99_ms": round(percentile(timings, 0.99), 4),
                "peak_kib": round(peak / 1024.0, 2),
                "changed_ratio": round(changed_frames / args.frames, 4),
            })
        except Exception as exc:
            if tracemalloc.is_tracing():
                tracemalloc.stop()
            results.append({
                "plugin": name,
                "kind": "frame",
                "error": f"{type(exc).__name__}: {exc}",
            })
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strips", type=int, default=32)
    parser.add_argument("--leds-per-strip", type=int, default=140)
    parser.add_argument("--fps", type=float, default=200.0)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--frames", type=int, default=200)
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
        print("plugin\tkind\tmean_ms\tp95_ms\tpeak_kib\tchanged\terror")
        for result in results:
            print("\t".join(str(result.get(key, "-")) for key in (
                "plugin", "kind", "mean_ms", "p95_ms", "peak_kib", "changed_ratio", "error"
            )))

    if args.check:
        failures = []
        for result in results:
            if result.get("error"):
                failures.append(f"{result['plugin']}: {result['error']}")
            elif (
                result.get("kind") == "frame"
                and float(result.get("p95_ms", 0.0)) > args.max_p95_ms
            ):
                failures.append(
                    f"{result['plugin']}: p95 {result['p95_ms']} ms exceeds "
                    f"{args.max_p95_ms} ms"
                )
        if failures:
            print("animation render acceptance failed:", file=sys.stderr)
            for failure in failures:
                print(f"- {failure}", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
