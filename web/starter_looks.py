"""Four immutable, deliberately different Scene v2 Composer built-ins."""

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


def _clock(*, color: list[int], offset: int = 0) -> dict:
    return {
        "id": "clock", "component": {
            "component_id": "clock_overlay", "version": 1,
            "provider": "python", "role": "widget",
            "parameters": {
                "format_24h": True, "show_seconds": True,
                "clock_offset_minutes": offset, "color": color,
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
            widgets=[_clock(color=[255, 224, 128])], palette="ember", pace=.7, brightness=.8,
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
            widgets=[_clock(color=[190, 255, 190], offset=60)], palette="neutral", pace=.85, brightness=.86,
            plant_effects={"version": 1, "active": ["refract"], "strengths": {"refract": .2}},
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
