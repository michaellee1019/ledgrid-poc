"""The four immutable current Composer starting points."""

from copy import deepcopy


def _overlay(slot_id, component_id, parameters, opacity, led=0):
    return {"slot_id": slot_id, "component": {"component_id": component_id, "version": 1, "provider": "python", "role": "overlay", "parameters": parameters}, "enabled": True, "opacity": opacity, "placement": {"strip_translation": 0, "led_translation": led, "clip_policy": "clip_to_wall"}, "stale_policy": {"policy": "hold"}}


_BACKGROUND = {"slot_id": "background", "component_id": "aurora_curtains", "version": 1, "provider": "python", "role": "background"}
_STARTERS = (
    ("aurora", "Aurora only", {**_BACKGROUND, "parameters": {"seed": 101, "curtain_density": .34, "fold_depth": .25, "glow_intensity": .45, "source_fps": 30}}, []),
    ("aurora_clock", "Aurora + Clock", {**_BACKGROUND, "parameters": {"seed": 202, "curtain_density": .62, "fold_depth": .72, "glow_intensity": .65, "source_fps": 30}}, [_overlay("clock_upper", "clock_overlay", {"show_seconds": True, "color": [255, 224, 128]}, 230, -8)]),
    ("aurora_conway", "Aurora + Conway", {**_BACKGROUND, "parameters": {"seed": 303, "curtain_density": .48, "fold_depth": .42, "glow_intensity": .78, "source_fps": 30}}, [_overlay("conway_lower", "conway_life", {"seed": 303, "rule": "B36/S23", "initial_density": .18, "generations_per_second": 4.0}, 190)]),
    ("aurora_conway_clock", "Aurora + Conway + Clock", {**_BACKGROUND, "parameters": {"seed": 404, "curtain_density": .7, "fold_depth": .6, "glow_intensity": .8, "source_fps": 30}}, [_overlay("conway_lower", "conway_life", {"seed": 404, "rule": "B3/S23", "initial_density": .12, "generations_per_second": 6.0}, 185), _overlay("clock_upper", "clock_overlay", {"format_24h": True, "show_seconds": False, "color": [80, 220, 255]}, 220, -8)]),
)


def list_starters(): return [{"id": item[0], "name": item[1]} for item in _STARTERS]
def get_starter(starter_id):
    for item in _STARTERS:
        if item[0] == starter_id: return deepcopy({"id": item[0], "name": item[1], "background": item[2], "overlays": item[3]})
    raise ValueError("That starting point is unavailable.")
