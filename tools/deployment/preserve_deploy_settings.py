#!/usr/bin/env python3
"""Preserve the active animation preset across a fast service restart."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ipc.control_channel import FileControlChannel


PRESET_ID = "before-deploy"
STATE_VERSION = 2


def _positive_finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def _positive_int(value: Any) -> int | None:
    number = _positive_finite_number(value)
    if number is None or not number.is_integer():
        return None
    integer = int(number)
    return integer if integer > 0 else None


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a JSON object in {path}")
    return payload


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _safe_animation_name(name: Any) -> str:
    if not isinstance(name, str):
        return ""
    safe_name = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")
    return safe_name if safe_name == name else ""


def _preset_params(status: dict[str, Any]) -> dict[str, Any]:
    animation_info = status.get("animation_info")
    params = animation_info.get("current_params") if isinstance(animation_info, dict) else None
    if not isinstance(params, dict):
        raise RuntimeError("Controller status does not contain current animation parameters")

    # The manager applies its global speed scale when an animation starts. Status
    # contains the already-scaled value, so store the corresponding input value
    # to avoid scaling it a second time on restore.
    params = dict(params)
    speed_scale = status.get("animation_speed_scale")
    speed = params.get("speed")
    valid_speed = _positive_finite_number(speed)
    valid_speed_scale = _positive_finite_number(speed_scale)
    if valid_speed is not None and valid_speed_scale is not None:
        params["speed"] = valid_speed / valid_speed_scale
    return params


def save_status(
    status: dict[str, Any], presets_dir: Path, state_path: Path
) -> dict[str, Any]:
    """Persist a controller status snapshot as the restart default."""
    animation = _safe_animation_name(status.get("current_animation"))
    if not status.get("is_running") or not animation:
        raise RuntimeError("No running animation is available to preserve")

    params = _preset_params(status)
    preset_path = presets_dir / animation / f"{PRESET_ID}.json"
    now = time.time()
    try:
        existing = _read_object(preset_path)
    except RuntimeError:
        existing = {}
    preset = {
        "version": 1,
        "preset_id": PRESET_ID,
        "name": PRESET_ID,
        "animation": animation,
        "params": params,
        "created_at": existing.get("created_at", now),
        "updated_at": now,
    }
    _atomic_write(preset_path, preset)
    state = {
        "version": STATE_VERSION,
        "animation": animation,
        "preset_path": str(preset_path),
        "saved_at": now,
    }
    speed_scale = _positive_finite_number(status.get("animation_speed_scale"))
    if speed_scale is not None:
        state["animation_speed_scale"] = speed_scale
    target_fps = _positive_int(status.get("target_fps"))
    if target_fps is not None:
        state["target_fps"] = target_fps
    plant_aware = status.get("plant_aware")
    if isinstance(plant_aware, bool):
        state["plant_aware"] = plant_aware
    _atomic_write(state_path, state)
    return preset


def save(status_path: Path, presets_dir: Path, state_path: Path) -> dict[str, Any]:
    return save_status(_read_object(status_path), presets_dir, state_path)


def load_saved_state(state_path: Path) -> dict[str, Any]:
    """Load and validate the animation and parameters used for restart."""
    state = _read_object(state_path)
    animation = _safe_animation_name(state.get("animation"))
    if not animation:
        raise RuntimeError("Saved deployment state has an invalid animation name")

    preset_path = state.get("preset_path")
    if not isinstance(preset_path, str) or not preset_path:
        raise RuntimeError("Saved deployment state does not contain a preset path")
    preset = _read_object(Path(preset_path))
    if preset.get("animation") != animation or not isinstance(preset.get("params"), dict):
        raise RuntimeError("before-deploy preset is invalid")

    result = dict(state)
    result["animation"] = animation
    result["params"] = dict(preset["params"])
    speed_scale = _positive_finite_number(state.get("animation_speed_scale"))
    target_fps = _positive_int(state.get("target_fps"))
    plant_aware = state.get("plant_aware")
    if "animation_speed_scale" in state and speed_scale is None:
        raise RuntimeError("Saved deployment state has an invalid animation speed scale")
    if "target_fps" in state and target_fps is None:
        raise RuntimeError("Saved deployment state has an invalid target FPS")
    if "plant_aware" in state and not isinstance(plant_aware, bool):
        raise RuntimeError("Saved deployment state has an invalid plant-aware state")
    if speed_scale is not None:
        result["animation_speed_scale"] = speed_scale
    if target_fps is not None:
        result["target_fps"] = target_fps
    if isinstance(plant_aware, bool):
        result["plant_aware"] = plant_aware
    return result


def _wait_for_fresh_controller(channel: FileControlChannel, started_at: float, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = channel.read_status() or {}
        if status.get("updated_at", 0) >= started_at:
            return
        time.sleep(0.1)
    raise RuntimeError("Controller did not publish fresh status after restart")


def restore(status_path: Path, control_path: Path, state_path: Path, timeout: float) -> dict[str, Any]:
    restore_started_at = time.time()
    state = load_saved_state(state_path)
    animation = state["animation"]

    channel = FileControlChannel(str(control_path), str(status_path))
    _wait_for_fresh_controller(channel, restore_started_at, timeout)
    command = channel.send_command("start", animation=animation, config=state["params"])

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = channel.read_status() or {}
        if (
            status.get("last_command_id") == command["command_id"]
            and status.get("current_animation") == animation
            and status.get("is_running")
        ):
            return {
                "animation": animation,
                "params": state["params"],
            }
        time.sleep(0.1)
    raise RuntimeError(f"Controller did not restore {animation!r} before timeout")


def record_deploy(deployment_path: Path, timestamp: float | None = None) -> float:
    deploy_timestamp = time.time() if timestamp is None else timestamp
    _atomic_write(
        deployment_path,
        {
            "version": STATE_VERSION,
            "deploy_timestamp": deploy_timestamp,
        },
    )
    return deploy_timestamp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("save", "restore", "record-deploy"))
    parser.add_argument("--status", type=Path, default=Path("run_state/status.json"))
    parser.add_argument("--control", type=Path, default=Path("run_state/control.json"))
    parser.add_argument("--presets", type=Path, default=Path("presets/animations"))
    parser.add_argument("--state", type=Path, default=Path("run_state/before_deploy.json"))
    parser.add_argument("--deployment", type=Path, default=Path("run_state/deployment.json"))
    parser.add_argument("--wait", type=float, default=10.0)
    args = parser.parse_args()

    if args.action == "save":
        preset = save(args.status, args.presets, args.state)
        print(f"Saved {preset['animation']}/{PRESET_ID}")
    elif args.action == "restore":
        preset = restore(args.status, args.control, args.state, args.wait)
        print(f"Restored {preset['animation']}/{PRESET_ID}")
    else:
        deploy_timestamp = record_deploy(args.deployment)
        print(f"Recorded deployment timestamp {deploy_timestamp}")


if __name__ == "__main__":
    main()
