#!/usr/bin/env python3
"""Keep operator settings and the selected scene across ordinary restarts.

Scenes are stored as authored, including unsupported components. Loading does
not replace them with another animation. Startup reports unsupported selections
and leaves playback stopped while retaining independent output controls.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read operator settings {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Operator settings must be an object: {path}")
    return value


def load_saved_state(state_path: Path, **_unused: Any) -> dict[str, Any]:
    state_path = Path(state_path)
    if not state_path.exists():
        return {"brightness": 0, "power": False}
    state = read_object(state_path)
    output = state.get("output") or {}
    if not isinstance(output, dict):
        raise RuntimeError("Saved output settings must be an object")
    if "brightness" not in state and "master_brightness" in output:
        level = output["master_brightness"]
        if isinstance(level, bool) or not isinstance(level, (int, float)) or not math.isfinite(level) or not 0 <= level <= 1:
            raise RuntimeError("Invalid saved brightness")
        state["brightness"] = round(level * 255)
    for alias, key in (("animation_speed_scale", "operator_tempo_scale"), ("target_fps", "target_fps"), ("power", "power")):
        if alias not in state and key in output:
            state[alias] = output[key]
    brightness = state.get("brightness", 0)
    if isinstance(brightness, bool) or not isinstance(brightness, (int, float)) or not math.isfinite(brightness) or not 0 <= brightness <= 255:
        raise RuntimeError("Invalid saved brightness")
    state["brightness"] = int(brightness)
    for name in ("animation_speed_scale", "target_fps"):
        if name in state:
            value = state[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise RuntimeError(f"Invalid saved {name}")
    if "power" in state and not isinstance(state["power"], bool):
        raise RuntimeError("Invalid saved power")
    scene = state.get("scene")
    if scene is not None and not isinstance(scene, dict):
        raise RuntimeError("Invalid saved scene")
    background = scene.get("background") if isinstance(scene, dict) else None
    if isinstance(background, dict):
        state["animation"] = background.get("plugin_id", background.get("component_id"))
        state["params"] = dict(background.get("parameters", background.get("resolved_parameters")) or {})
        state["params"].update(background.get("parameter_overrides") or {})
    elif "params" not in state and isinstance(state.get("preset_path"), str):
        # One-time read of the former before-deploy preset, preserved in data/.
        preset_path = Path(state["preset_path"])
        if not preset_path.exists() and isinstance(state.get("animation"), str):
            preset_path = state_path.parent.parent / "presets/animations" / state["animation"] / "before-deploy.json"
        state["params"] = read_object(preset_path).get("params", {})
    state.setdefault("animation", None)
    state.setdefault("params", {})
    if not isinstance(state["params"], dict):
        raise RuntimeError("Invalid saved animation parameters")
    return state


def save_status(status: dict[str, Any], presets_dir: Path, state_path: Path) -> dict[str, Any]:
    del presets_dir  # Authored presets stay independent; restart parameters are inline.
    state = load_saved_state(state_path) if state_path.exists() else {}
    state.update({"schema": "ledgrid.operator-settings", "schema_version": 1})
    for name in ("brightness", "animation_speed_scale", "target_fps", "plant_modifiers", "current_preset"):
        if name in status:
            state[name] = status[name]
    if "is_running" in status:
        state["power"] = bool(status["is_running"])
    if isinstance(status.get("current_animation"), str) and status["current_animation"]:
        state["animation"] = status["current_animation"]
    info = status.get("animation_info")
    if isinstance(info, dict) and isinstance(info.get("current_params"), dict):
        state["params"] = info["current_params"]
    scene = status.get("scene_state") or status.get("selected_scene") or status.get("scene")
    if isinstance(scene, dict) and isinstance(scene.get("background"), dict):
        state["scene"] = scene
    vibe = status.get("vibe")
    if isinstance(vibe, dict):
        state["vibe"] = vibe.get("state", vibe)
    # Avoid stale aggregates overriding freshly written aliases at next startup.
    state["output"] = {
        "master_brightness": state.get("brightness", 0) / 255,
        "operator_tempo_scale": state.get("animation_speed_scale", 1),
        "target_fps": state.get("target_fps", 160),
        "power": state.get("power", False),
    }
    # A small atomic JSON write protects personal data, without deployment rollback.
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".operator-settings-", dir=state_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(state, output, indent=2, allow_nan=False)
            output.write("\n")
        load_saved_state(Path(temporary))
        os.replace(temporary, state_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["save"])
    parser.add_argument("--status", type=Path, default=Path("run_state/status.json"))
    parser.add_argument("--state", type=Path, default=Path("run_state/before_deploy.json"))
    parser.add_argument("--presets", type=Path, default=Path("presets/animations"))
    args = parser.parse_args()
    save_status(read_object(args.status), args.presets, args.state)


if __name__ == "__main__":
    main()
