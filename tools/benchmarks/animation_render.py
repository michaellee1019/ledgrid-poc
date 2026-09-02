#!/usr/bin/env python3
"""Headless render benchmark for every active frame-based animation."""

from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
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
from animation.core.presentation_contracts import OverlayFrame
from animation.core.manager import AnimationManager
from animation.core.plugin_loader import AnimationPluginLoader
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


STRESS_SCENARIOS = {
    "gradient-plant-visuals": {
        "plugin": "gradient", "fps": 200.0,
        "config": {"direction": "diagonal", "drift": 2.0, "motion": 1.0},
        "plant_modifiers": {
            "active": ["illuminate", "shadow", "refract"],
            "strengths": {"illuminate": 1.0, "shadow": 1.0, "refract": 1.0},
        },
    },
    "sparkle-plant-stack": {
        "plugin": "sparkle", "fps": 200.0,
        "config": {"density": 1.0, "linger": 1.0, "twinkle": 1.0, "night": .55},
        "plant_modifiers": {
            "active": ["illuminate", "attractor", "habitat", "emitter"],
            "strengths": {"illuminate": 1.0, "attractor": 1.0, "habitat": 1.0, "emitter": 1.0},
        },
    },
    "snake-plant-obstacle": {
        "plugin": "snake", "fps": 200.0,
        "config": {"snake_count": 3, "food_count": 5, "obstacles": "pillars"},
        "plant_modifiers": {
            "active": ["obstacle"], "strengths": {"obstacle": 1.0},
        },
    },
    "snake-plant-portal": {
        "plugin": "snake", "fps": 200.0,
        "config": {"snake_count": 3, "ruleset": "portal"},
        "plant_modifiers": {
            "active": ["portal"], "strengths": {"portal": 1.0},
        },
    },
    "pinball-plant-bumper": {
        "plugin": "pinball", "fps": 200.0,
        "config": {"chaos": 1.0, "table_tick_hz": 90.0, "render_fps": 120.0},
        "plant_modifiers": {
            "active": ["bumper"], "strengths": {"bumper": 1.0},
        },
    },
    "pinball-plant-portal": {
        "plugin": "pinball", "fps": 200.0,
        "config": {"chaos": 1.0, "table_tick_hz": 90.0, "render_fps": 120.0},
        "plant_modifiers": {
            "active": ["portal"], "strengths": {"portal": 1.0},
        },
    },
    "canopy-cup-max-action": {
        "plugin": "canopy_cup", "fps": 200.0,
        "config": {
            "course_difficulty": 1.4,
            "enemy_density": 1.0, "rivalry": 1.0, "powerup_rate": 1.0,
            "show_hud": True,
        },
        "plant_modifiers": {
                "active": ["illuminate", "obstacle", "emitter"],
                "strengths": {"illuminate": 1.0, "obstacle": 1.0, "emitter": 1.0},
        },
    },
    "lava-lamp-max-action": {
        "plugin": "lava_lamp", "fps": 200.0,
        "config": {
            "blob_count": 12, "blob_scale": 1.8, "viscosity": 0.0,
            "heat": 1.0, "turbulence": 1.0, "glow": 1.0,
            "interaction_radius": 16.0, "interaction_strength": 2.0,
        },
        "plant_modifiers": {
                "active": ["refract", "bumper", "emitter"],
                "strengths": {"refract": 1.0, "bumper": 1.0, "emitter": 1.0},
        },
    },
    "conway-plant-emitter-habitat": {
        "plugin": "conway_life", "fps": 200.0,
        "config": {"initial_density": 0.4, "generations_per_second": 20.0,
                   "rule": "B36/S23"},
        "plant_modifiers": {"active": ["habitat", "emitter"],
                   "strengths": {"habitat": 1.0, "emitter": 1.0}},
    },
    "fluid-plant-stack": {
        "plugin": "fluid_tank", "fps": 200.0,
        "config": {"flow_rate": 2.0, "current": 1.0,
                   "bubble_lift": 1.0, "surface_energy": 1.0},
        "plant_modifiers": {
            "active": ["refract", "slow_zone", "obstacle"],
            "strengths": {"refract": 1.0, "slow_zone": 1.0, "obstacle": 1.0},
        },
    },
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
            "snake_count": 12, "initial_length": 24, "max_length": 320,
            "food_count": 24, "growth_per_food": 12,
            "visual_style": "prism", "background": "aurora",
            "trails": 1.0, "trail_decay": .2, "glow": 1.0,
            "move_cadence": 24.0, "ruleset": "battle", "obstacles": "zigzag",
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

ACCEPTED_SCENE_BACKGROUNDS = ("gradient", "aurora_curtains", "sparkle")


class BenchmarkController:
    debug = False
    inline_show = True

    def __init__(self, strips: int, leds_per_strip: int):
        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip
        self.presentation_calls = 0
        self.full_presentations = 0
        self.partial_presentations = 0
        self.presented_rgb_payload_bytes = 0

    def set_all_pixels(self, frame):
        self.presentation_calls += 1
        self.full_presentations += 1
        self.presented_rgb_payload_bytes += int(np.asarray(frame).shape[0]) * 3

    def set_frame(self, _frame, *, dirty_ranges):
        self.presentation_calls += 1
        self.partial_presentations += 1
        self.presented_rgb_payload_bytes += sum(
            (int(end) - int(start)) * 3 for start, end in dirty_ranges
        )

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
        (name, animation_class, "default", {}, args.fps, None)
        for name, animation_class in sorted(plugins.items())
        if (loader.plugin_manifests.get(name) or {}).get("role") != "overlay"
    ]
    if args.stress:
        for scenario_name, scenario in STRESS_SCENARIOS.items():
            if args.scenario and scenario_name != args.scenario:
                continue
            plugin_name = scenario["plugin"]
            work_items.append((
                plugin_name,
                plugins[plugin_name],
                scenario_name,
                scenario["config"],
                scenario["fps"],
                scenario.get("plant_modifiers"),
            ))
    if args.plugin:
        work_items = [item for item in work_items if item[0] == args.plugin]

    results = []
    for (
        name, animation_class, scenario_name, config, scenario_fps,
        plant_modifiers,
    ) in work_items:
        if issubclass(animation_class, StatefulAnimationBase):
            results.append({"plugin": name, "scenario": scenario_name, "kind": "stateful"})
            continue

        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                animation = animation_class(controller, config)
                if plant_modifiers:
                    animation.set_runtime_plant_modifiers(plant_modifiers)
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
                    if bool(getattr(rendered, "changed", True)):
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

            pixels = getattr(rendered, "pixels", rendered)
            if not isinstance(pixels, np.ndarray):
                raise TypeError(f"returned {type(pixels).__name__}, expected ndarray")
            channels = 4 if isinstance(rendered, OverlayFrame) else 3
            expected_shape = (controller.total_leds, channels)
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


def benchmark_scenes(args):
    """Measure accepted Phase 2B scenes at the real manager call cadence."""
    controller = BenchmarkController(args.strips, args.leds_per_strip)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        manager = AnimationManager(controller, auto_start=False)
    manager._launch_animation_loop = lambda: None
    results = []
    for background_name in ACCEPTED_SCENE_BACKGROUNDS:
        if args.plugin and args.plugin != background_name:
            continue
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                scene_wall_start = datetime(
                    2026, 8, 12, 12, 0, 0, 750_000, tzinfo=timezone.utc
                )
                simulated_wall_time = [scene_wall_start]
                manager._wall_time = lambda: simulated_wall_time[0].timestamp()
                if not manager.start_composed_scene(
                    background_name,
                    overlay_name="clock_overlay",
                    overlay_config={"show_seconds": True},
                ):
                    raise RuntimeError("scene start failed")
                background = manager._scene_background
                overlay = manager._scene_overlay
                base_time = max(
                    float(background["started_at"]), float(overlay["started_at"])
                )
                for index in range(args.warmup):
                    simulated_wall_time[0] = scene_wall_start + timedelta(
                        seconds=index / args.fps
                    )
                    manager.render_composed_scene_frame(
                        now=base_time + index / args.fps
                    )

                timings = []
                changed_calls = 0
                overlay_changed_before = overlay["changed_calls"]
                overlay_renders_before = overlay["render_count"]
                overlay_dirty_pixels = 0
                overlay_dirty_ranges = 0
                presentation_calls_before = controller.presentation_calls
                full_presentations_before = controller.full_presentations
                partial_presentations_before = controller.partial_presentations
                payload_bytes_before = controller.presented_rgb_payload_bytes
                for index in range(args.frames):
                    now = base_time + (args.warmup + index) / args.fps
                    simulated_wall_time[0] = scene_wall_start + timedelta(
                        seconds=(args.warmup + index) / args.fps
                    )
                    started = time.perf_counter()
                    frame = manager.render_composed_scene_frame(now=now)
                    timings.append((time.perf_counter() - started) * 1000.0)
                    changed_calls += int(frame.changed)
                    presented_count = (
                        controller.presentation_calls - presentation_calls_before
                    )
                    if frame.changed or presented_count == 0:
                        use_partial = bool(
                            frame.dirty_ranges and presented_count > 0
                        )
                        manager._present_frame(
                            frame.pixels,
                            frame.dirty_ranges,
                            use_partial,
                            controller.inline_show,
                        )
                    dirty = overlay.get("last_dirty_ranges")
                    if dirty:
                        overlay_dirty_pixels += sum(end - start for start, end in dirty)
                        overlay_dirty_ranges += len(dirty)

                overlay_changed = overlay["changed_calls"] - overlay_changed_before
                overlay_renders = overlay["render_count"] - overlay_renders_before
                results.append({
                    "plugin": background_name,
                    "scenario": "clock-overlay-scene",
                    "kind": "scene",
                    "mean_ms": round(statistics.mean(timings), 4),
                    "p50_ms": round(percentile(timings, 0.50), 4),
                    "p95_ms": round(percentile(timings, 0.95), 4),
                    "p99_ms": round(percentile(timings, 0.99), 4),
                    "max_ms": round(max(timings), 4),
                    "manager_changed_ratio": round(changed_calls / args.frames, 4),
                    "overlay_changed_ratio": round(overlay_changed / args.frames, 4),
                    "overlay_render_ratio": round(overlay_renders / args.frames, 4),
                    "overlay_dirty_pixels": overlay_dirty_pixels,
                    "overlay_dirty_ranges": overlay_dirty_ranges,
                    "presentation_calls": (
                        controller.presentation_calls - presentation_calls_before
                    ),
                    "full_presentations": (
                        controller.full_presentations - full_presentations_before
                    ),
                    "partial_presentations": (
                        controller.partial_presentations - partial_presentations_before
                    ),
                    "presented_rgb_payload_bytes": (
                        controller.presented_rgb_payload_bytes - payload_bytes_before
                    ),
                })
        except Exception as exc:
            results.append({
                "plugin": background_name,
                "scenario": "clock-overlay-scene",
                "kind": "scene",
                "error": f"{type(exc).__name__}: {exc}",
            })
        finally:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                manager.stop_animation(clear_leds=False)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strips", type=int, default=DEFAULT_STRIP_COUNT)
    parser.add_argument("--leds-per-strip", type=int, default=DEFAULT_LEDS_PER_STRIP)
    parser.add_argument("--fps", type=float, default=200.0)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--frames", type=int, default=200)
    parser.add_argument("--plugin", help="benchmark only one plugin ID")
    parser.add_argument("--scenario", help="benchmark only one named stress scenario")
    parser.add_argument(
        "--stress", action="store_true",
        help="also run named animated and maximum-density scenarios",
    )
    parser.add_argument(
        "--scenes", action="store_true",
        help="also benchmark the three accepted background + clock scenes",
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
    if args.scenes:
        results.extend(benchmark_scenes(args))
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
                result.get("kind") in {"frame", "scene"}
                and float(result.get("p95_ms", 0.0)) > args.max_p95_ms
            ):
                failures.append(
                    f"{result['plugin']}[{result.get('scenario', 'default')}]: "
                    f"p95 {result['p95_ms']} ms exceeds "
                    f"{args.max_p95_ms} ms"
                )
            elif (
                result.get("kind") == "scene"
                and float(result.get("overlay_render_ratio", 0.0)) <= 0.0
            ):
                failures.append(
                    f"{result['plugin']}[{result.get('scenario', 'default')}]: "
                    "clock overlay did not render across a wall-clock rollover"
                )
        if failures:
            print("animation render acceptance failed:", file=sys.stderr)
            for failure in failures:
                print(f"- {failure}", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
