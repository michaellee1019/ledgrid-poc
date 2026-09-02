"""Immutable Scene v2 starting scenes shown in the Composer Library."""

from copy import deepcopy

from web.composer_final_preview import NATIVE_AURORA_BUNDLE_DIGEST


def _background(*, gain: float, seed: int, source_fps: int = 30) -> dict:
    return {
        "component_id": "native_aurora", "version": 1,
        "provider": "receiver_native", "role": "background",
        "bundle_digest": NATIVE_AURORA_BUNDLE_DIGEST,
        "parameters": {"gain": gain, "source_fps": source_fps, "seed": seed},
    }


def _animation(component_id: str, parameters: dict) -> dict:
    return {
        "component_id": component_id, "version": 1,
        "provider": "python", "role": "animation", "parameters": parameters,
    }


def _clock(*, offset: int = 0) -> dict:
    return {
        "id": "clock", "component": {
            "component_id": "clock_overlay", "version": 1,
            "provider": "python", "role": "widget",
            "parameters": {
                "format_24h": True, "show_seconds": True,
                "clock_offset_minutes": offset,
            },
        },
        "visible": True,
        "placement": {"mode": "manual", "strip_translation": 0, "led_translation": -8},
    }


def _scene(*, background: dict, animation: dict, widgets: list[dict], palette: str,
           pace: float, brightness: float, plant_effects: dict | None = None) -> dict:
    return {
        "schema": "ledgrid.scene.v2", "background": background, "animation": animation,
        "widgets": widgets,
        "plants": {"effects": plant_effects or {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": palette, "pace": pace, "presentation_brightness": brightness},
    }


# Keep these IDs stable: library favorites, recents, and shared links refer to them.
_STARTERS = (
    (
        "aurora", "Boreal Hush",
        _scene(
            background=_background(gain=.34, seed=4201, source_fps=24),
            animation=_animation("aurora_curtains", {
                "curtain_density": .18, "fold_depth": .18, "glow_intensity": .34,
                "source_fps": 24, "seed": 4201,
            }),
            widgets=[], palette="mist", pace=.48, brightness=.64,
        ),
    ),
    (
        "aurora_clock", "Firefly Timekeeper",
        _scene(
            background=_background(gain=.42, seed=12107),
            animation=_animation("firefly_synchrony", {
                "seed": 12107, "population": 132, "coupling_radius": 10.5,
                "synchrony": 1.15, "wandering": .42, "pulse_softness": .72,
                "meadow_glow": .42,
            }),
            widgets=[_clock()], palette="ember", pace=.7, brightness=.8,
            plant_effects={"version": 1, "active": ["illuminate"], "strengths": {"illuminate": .25}},
        ),
    ),
    (
        "aurora_conway", "Neon Crackle",
        _scene(
            background=_background(gain=.7, seed=808),
            animation=_animation("fireworks", {
                "launch_cadence": 1.7, "shell_population": 46, "burst_size": .38,
                "burst_style": "burst", "gravity": .48, "trails": .46,
                "crackle": .92, "twinkle": .88, "seed": 808,
            }),
            widgets=[], palette="spectrum", pace=1.05, brightness=.94,
        ),
    ),
    (
        "aurora_conway_clock", "Fern Gully Cup",
        _scene(
            background=_background(gain=.58, seed=1107),
            animation=_animation("canopy_cup", {
                "seed": 1107, "world_theme": "emerald_gully", "qualifying_heats": 7,
                "course_difficulty": .86, "enemy_density": .35, "rivalry": .32,
                "powerup_rate": .9, "show_hud": True,
            }),
            widgets=[_clock(offset=60)], palette="neutral", pace=.85, brightness=.86,
            plant_effects={"version": 1, "active": ["refract"], "strengths": {"refract": .2}},
        ),
    ),
    (
        "human_conway_chaos", "Chaos",
        _scene(
            background=_background(gain=.35, seed=1971),
            animation=_animation("conway_life", {
                "seed": 1971, "rule": "B3/S23", "initial_density": .3,
                "generations_per_second": 20.0, "seed_cells": [],
            }),
            widgets=[], palette="spectrum", pace=1.0, brightness=.88,
        ),
    ),
    (
        "human_fancy_coral", "Fancy Coral",
        _scene(
            background=_background(gain=.4, seed=7319),
            animation=_animation("cyclic_reef", {
                "seed": 7319, "species_count": 5, "takeover_threshold": 2,
                "mutation": .002, "grazers": 4, "boundary_glow": .55,
                "topology": "wrap", "pace": 1.0,
            }),
            widgets=[], palette="mist", pace=.9, brightness=.82,
        ),
    ),
    (
        "human_neon_microverse", "Neon Microverse",
        _scene(
            background=_background(gain=.38, seed=616),
            animation=_animation("living_ecosystem", {
                "motion": .55, "density": .62, "background_level": .22,
                "seed": 616, "migration": .55, "predator_pressure": .38,
                "canopy_density": .58, "mutation": .1, "night_life": .42,
            }),
            widgets=[], palette="spectrum", pace=1.0, brightness=.76,
        ),
    ),
    (
        "human_twilight_sparkle", "Twilight Sparkle",
        _scene(
            background=_background(gain=.25, seed=6146),
            animation=_animation("sparkle", {
                "density": .31, "linger": .92, "twinkle": .82,
                "night": .14, "seed": 6146,
            }),
            widgets=[], palette="mist", pace=.7, brightness=.78,
        ),
    ),
    (
        "human_avalanche_factory", "Avalanche Factory",
        _scene(
            background=_background(gain=.2, seed=4201),
            animation=_animation("tetris", {
                "seed": 4201, "tetromino_count": 48,
                "bot_imperfection": .28, "fall_rate": 3.2,
                "smooth_drop": False, "smooth_drop_strength": 0.0,
                "smooth_drop_max_pieces": 16, "render_fps": 100.0,
                "high_density_render_fps": 90.0,
            }),
            widgets=[], palette="ember", pace=1.0, brightness=.9,
        ),
    ),
)


def list_starters() -> list[dict[str, str]]:
    return [{"id": identifier, "name": name} for identifier, name, _ in _STARTERS]


def get_starter(starter_id: str) -> dict:
    for identifier, name, scene in _STARTERS:
        if identifier == starter_id:
            return deepcopy({"id": identifier, "name": name, "scene": scene})
    raise ValueError("That starting point is unavailable.")
