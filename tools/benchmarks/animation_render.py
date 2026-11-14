#!/usr/bin/env python3
"""Benchmark the installed 33x138 Scene v2 host renderer without SPI output."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animation.core.component_catalog import ComponentRole  # noqa: E402
from animation.core.manager import AnimationManager, PreviewLEDController  # noqa: E402
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT  # noqa: E402
from ipc.scene_contract import normalize_composer_scene  # noqa: E402
from web.composer_final_preview import InstalledFinalSceneRuntime, current_component_catalog  # noqa: E402
from web.starter_looks import tetris_player_scene  # noqa: E402


WALL_TIME = datetime(2026, 8, 12, 12, 0, 0, 750_000, tzinfo=timezone.utc)
STRESS_CASES = {
    "gradient-with-clock-and-optics": (
        "gradient", {"direction": "diagonal", "drift": 2.0, "motion": 1.0},
        ("illuminate", "shadow", "hue_shift"), "clock_overlay",
    ),
    "sparkle-dense": (
        "sparkle", {"density": 1.0, "linger": 1.0, "twinkle": 1.0, "night": 0.55},
        ("illuminate", "shadow"), None,
    ),
    "snake-max-density": (
        "snake", {
            "snake_count": 12, "initial_length": 24, "max_length": 320,
            "food_count": 24, "growth_per_food": 12, "visual_style": "prism",
            "background": "aurora", "trails": 1.0, "trail_decay": 0.2,
            "glow": 1.0, "move_cadence": 24.0, "ruleset": "battle", "obstacles": "zigzag",
        }, (), None,
    ),
    "pinball-plant-bumper": (
        "pinball", {"chaos": 1.0, "table_tick_hz": 90.0, "render_fps": 120.0},
        ("bumper",), None,
    ),
    "tetris-player": ("tetris", {}, (), None),
    "tetris-max-density": (
        "tetris", {"tetromino_count": 96, "fall_rate": 4.0, "bot_imperfection": 0.5},
        (), None,
    ),
    "canopy-cup-max-action": (
        "canopy_cup", {"course_difficulty": 1.4, "enemy_density": 1.0,
                       "rivalry": 1.0, "powerup_rate": 1.0, "show_hud": True},
        (), None,
    ),
    "lava-lamp-max-action": (
        "lava_lamp", {"blob_count": 12, "blob_scale": 1.8, "viscosity": 0.0,
                      "heat": 1.0, "turbulence": 1.0, "glow": 1.0,
                      "interaction_radius": 16.0, "interaction_strength": 2.0},
        ("refract", "bumper", "emitter"), None,
    ),
    "conway-dense": (
        "conway_life", {"initial_density": 0.4, "generations_per_second": 20.0,
                        "rule": "B36/S23"},
        (), None,
    ),
    "fluid-max-action": (
        "fluid_tank", {"flow_rate": 2.0, "current": 1.0,
                       "bubble_lift": 1.0, "surface_energy": 1.0},
        ("illuminate", "shadow"), None,
    ),
    "plant-glow-dense": (
        "plant_glow", {"glow_radius": 5, "glow_strength": 1.5, "shimmer": 0.5},
        ("illuminate", "shadow", "hue_shift"), None,
    ),
}


def scene_for(catalog, component_id, parameters, effects=(), widget_id=None):
    descriptors = {item.component_id: item for item in catalog.descriptors}

    def reference(name, values):
        descriptor = descriptors[name]
        return {"component_id": name, "provider": descriptor.provider.value,
                "version": descriptor.version, "role": descriptor.role.value,
                "parameters": values}

    role = descriptors[component_id].role
    background = reference("solid_background", {})
    animation = reference("fireworks", {})
    widgets = []
    if role is ComponentRole.ANIMATION:
        animation = reference(component_id, parameters)
    elif role is ComponentRole.BACKGROUND:
        background = reference(component_id, parameters)
    else:
        widget_id = component_id
        animation = reference("solid", {})
    if widget_id is not None:
        widgets.append({
            "id": widget_id, "component": reference(widget_id, parameters if role is ComponentRole.WIDGET else {}),
            "visible": True, "placement": {"mode": "auto"},
        })
    return normalize_composer_scene({
        "origin": "composer",
        "scene": {
            "schema": "ledgrid.scene.v2",
            "background": background,
            "animation": animation,
            "widgets": widgets,
            "plants": {"effects": {"version": 1, "active": list(effects),
                                   "strengths": {effect: 1.0 for effect in effects}}},
            "look": {"palette_id": "mist", "pace": 1.0, "presentation_brightness": 0.85},
        },
    }, catalog)


def percentile(samples, ratio):
    ordered = sorted(samples)
    return ordered[round((len(ordered) - 1) * ratio)]


def benchmark_case(manager, runtime, canonical, *, fps, warmup, frames, tetris_player=False):
    elapsed = 0.0

    def render(index):
        nonlocal elapsed
        elapsed = index / fps
        wall_time = WALL_TIME + timedelta(seconds=elapsed)
        started = time.perf_counter()
        frame = manager.render_scene_v2_presentation(
            runtime, canonical, monotonic_elapsed=elapsed, wall_time=wall_time,
        )
        return frame, (time.perf_counter() - started) * 1000

    first, first_ms = render(0)
    if tetris_player:
        if not runtime.dispatch_game_input("play"):
            raise ValueError("Tetris player controls are unavailable")
        runtime._runtime._animation.instance._player_time = lambda: elapsed
        first, player_ms = render(0)
        first_ms += player_ms
    if not first.pixels.flags.c_contiguous or not first.changed:
        raise ValueError("first Scene frame must be contiguous and marked changed")
    previous_pixels = first.pixels.copy()
    for index in range(1, warmup + 1):
        frame, _ = render(index)
        np.copyto(previous_pixels, frame.pixels)

    timings = []
    changed_timings = []
    distinct_frames = 0
    for index in range(warmup + 1, warmup + frames + 1):
        frame, duration_ms = render(index)
        timings.append(duration_ms)
        if frame.changed:
            changed_timings.append(duration_ms)
        if not np.array_equal(previous_pixels, frame.pixels):
            if not frame.changed:
                raise ValueError("Scene pixels changed without a changed frame")
            distinct_frames += 1
        np.copyto(previous_pixels, frame.pixels)

    return {
        "first_frame_ms": round(first_ms, 4),
        "mean_ms": round(statistics.mean(timings), 4),
        "p95_ms": round(percentile(timings, 0.95), 4),
        "p99_ms": round(percentile(timings, 0.99), 4),
        "max_ms": round(max(timings), 4),
        "changed_p95_ms": round(percentile(changed_timings, 0.95), 4) if changed_timings else None,
        "changed_ratio": round(len(changed_timings) / frames, 4),
        "distinct_pixel_ratio": round(distinct_frames / frames, 4),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fps", type=float, default=200.0, help="simulated controller call rate")
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--frames", type=int, default=200)
    parser.add_argument("--plugin", help="benchmark only one Composer component ID")
    parser.add_argument("--scenario", choices=STRESS_CASES, help="benchmark only this stress case")
    parser.add_argument("--stress", action="store_true", help="also benchmark named stress cases")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true", help="fail on render errors or p95 above the limit")
    parser.add_argument("--max-p95-ms", type=float, default=4.0)
    args = parser.parse_args()
    if not math.isfinite(args.fps) or args.fps <= 0 or args.frames < 1 or args.warmup < 0:
        parser.error("fps and frames must be positive, and warmup must be nonnegative")
    if not math.isfinite(args.max_p95_ms) or args.max_p95_ms <= 0:
        parser.error("--max-p95-ms must be finite and positive")

    catalog = current_component_catalog()
    cases = []
    if not args.scenario:
        cases.extend(("default", component.component_id, {}, (), None) for component in catalog.descriptors)
    if args.stress or args.scenario:
        cases.extend((name, *case) for name, case in STRESS_CASES.items() if args.scenario in (None, name))
    cases = [case for case in cases if args.plugin in (None, case[1])]
    if not cases:
        parser.error("no matching Composer benchmark cases")

    controller = PreviewLEDController(DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP)
    with redirect_stdout(io.StringIO()):
        manager = AnimationManager(controller, auto_start=False)
    results = []
    for scenario, component_id, parameters, effects, widget in cases:
        row = {"plugin": component_id, "scenario": scenario}
        try:
            tetris_player = scenario == "tetris-player"
            if tetris_player:
                canonical = normalize_composer_scene(
                    {"origin": "composer", "scene": tetris_player_scene()}, catalog,
                )
            else:
                canonical = scene_for(catalog, component_id, parameters, effects, widget)
            runtime = InstalledFinalSceneRuntime(catalog, REPO_ROOT, controller=controller)
            row.update(benchmark_case(manager, runtime, canonical, fps=args.fps, warmup=args.warmup,
                                      frames=args.frames, tetris_player=tetris_player))
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        results.append(row)

    if args.json:
        print(json.dumps({
            "scope": "Scene v2 host render only",
            "machine": platform.machine(), "system": platform.system(),
            "python": platform.python_version(),
            "geometry": [controller.strip_count, controller.leds_per_strip],
            "call_fps": args.fps, "warmup_frames": args.warmup, "sampled_frames": args.frames,
            "results": results,
        }, indent=2))
    else:
        columns = ("plugin", "scenario", "first_frame_ms", "mean_ms", "p95_ms", "p99_ms", "max_ms",
                   "changed_p95_ms", "changed_ratio", "distinct_pixel_ratio", "error")
        print("\t".join(columns))
        for row in results:
            print("\t".join(str(row.get(column, "-")) for column in columns))

    if args.check:
        failures = []
        for row in results:
            label = f"{row['plugin']}[{row['scenario']}]"
            if "error" in row:
                failures.append(f"{label}: {row['error']}")
            elif row["p95_ms"] > args.max_p95_ms:
                failures.append(f"{label}: p95 {row['p95_ms']} ms exceeds {args.max_p95_ms} ms")
        if failures:
            print("Scene v2 host render gate failed:\n- " + "\n- ".join(failures), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
