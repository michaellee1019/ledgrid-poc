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
from animation.core.defaults import DEFAULT_PLANT_AWARE
from animation.core.plant_awareness import PlantModifierState


PRESET_ID = "before-deploy"
STATE_VERSION = 4


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


def _brightness_level(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 255 else None


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

    # Phase 2A keeps authored parameters immutable. Operator tempo and vibe tempo
    # are presentation state, so current_params.speed is already the exact value
    # that must survive a restart.
    params = dict(params)
    params.pop("plant_aware", None)
    params.pop("plant_modifiers", None)
    return params


def _vibe_state(status: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the canonical independent vibe state from manager status."""
    vibe = status.get("vibe")
    if not isinstance(vibe, dict):
        return None
    state = vibe.get("state", vibe)
    return dict(state) if isinstance(state, dict) and state else None


def _current_preset(
    payload: Any, animation: str, *, label: str = "current preset"
) -> dict[str, Any] | None:
    """Return the canonical preset identity shape used by manager status."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} is invalid")
    preset_id = payload.get("preset_id")
    name = payload.get("name")
    preset_animation = payload.get("animation", animation)
    if not all(
        isinstance(value, str) and value
        for value in (preset_id, name, preset_animation)
    ) or preset_animation != animation:
        raise RuntimeError(f"{label} is invalid")
    return {
        "preset_id": preset_id,
        "name": name,
        "animation": animation,
        "is_dirty": bool(payload.get("is_dirty", False)),
    }


def _apply_independent_status(
    status: dict[str, Any], state: dict[str, Any]
) -> None:
    """Update top-level controls without requiring an active animation."""
    speed_scale = _positive_finite_number(status.get("animation_speed_scale"))
    if speed_scale is not None:
        state["animation_speed_scale"] = speed_scale
    target_fps = _positive_int(status.get("target_fps"))
    if target_fps is not None:
        state["target_fps"] = target_fps
    brightness = _brightness_level(status.get("brightness"))
    if brightness is not None:
        state["brightness"] = brightness
    try:
        if "plant_modifiers" in status:
            modifiers = PlantModifierState.from_payload(status["plant_modifiers"])
        else:
            modifiers = PlantModifierState.from_legacy(
                status.get("plant_aware", DEFAULT_PLANT_AWARE)
            )
    except ValueError as exc:
        raise RuntimeError(
            f"Controller status has invalid plant modifiers: {exc}"
        ) from exc
    state["plant_modifiers"] = modifiers.to_dict()
    vibe = _vibe_state(status)
    if vibe is not None:
        state["vibe"] = vibe


def save_status(
    status: dict[str, Any], presets_dir: Path, state_path: Path
) -> dict[str, Any]:
    """Persist a controller status snapshot as the restart default."""
    animation = _safe_animation_name(status.get("current_animation"))
    if not status.get("is_running") or not animation:
        # Global presentation controls remain independently writable while the
        # wall is stopped. Retain the last valid playable snapshot and update
        # only those controls so the next restart does not discard the choice.
        try:
            state = _read_object(state_path)
            animation = _safe_animation_name(state.get("animation"))
            preset_path_value = state.get("preset_path")
            if not animation or not isinstance(preset_path_value, str):
                raise RuntimeError("saved state is incomplete")
            preset = _read_object(Path(preset_path_value))
            if (
                preset.get("animation") != animation
                or not isinstance(preset.get("params"), dict)
            ):
                raise RuntimeError("saved preset is invalid")
        except RuntimeError as exc:
            raise RuntimeError(
                "No running animation is available to preserve"
            ) from exc
        state["version"] = STATE_VERSION
        state["saved_at"] = time.time()
        _apply_independent_status(status, state)
        _atomic_write(state_path, state)
        return preset

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
    current_preset = _current_preset(status.get("current_preset"), animation)
    if current_preset is not None:
        state["current_preset"] = current_preset
    _apply_independent_status(status, state)
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
    if "current_preset" in state:
        result["current_preset"] = _current_preset(
            state["current_preset"], animation, label="Saved current preset"
        )
    speed_scale = _positive_finite_number(state.get("animation_speed_scale"))
    target_fps = _positive_int(state.get("target_fps"))
    brightness = _brightness_level(state.get("brightness"))
    if "animation_speed_scale" in state and speed_scale is None:
        raise RuntimeError("Saved deployment state has an invalid animation speed scale")
    if "target_fps" in state and target_fps is None:
        raise RuntimeError("Saved deployment state has an invalid target FPS")
    if "brightness" in state and brightness is None:
        raise RuntimeError("Saved deployment state has an invalid brightness")
    if speed_scale is not None:
        result["animation_speed_scale"] = speed_scale
    if target_fps is not None:
        result["target_fps"] = target_fps
    if brightness is not None:
        result["brightness"] = brightness
    try:
        if "plant_modifiers" in state:
            modifiers = PlantModifierState.from_payload(state["plant_modifiers"])
        else:
            modifiers = PlantModifierState.from_legacy(
                state.get("plant_aware", DEFAULT_PLANT_AWARE)
            )
    except ValueError as exc:
        raise RuntimeError(f"Saved deployment state has invalid plant modifiers: {exc}") from exc
    result["plant_modifiers"] = modifiers.to_dict()
    result.pop("plant_aware", None)
    # Vibe validation and fallback belong to the manager's central registry. An
    # unknown persisted profile version is intentionally passed through so the
    # manager can select neutral and expose its observable fallback diagnostic.
    if "vibe" in state:
        if not isinstance(state["vibe"], dict) or not state["vibe"]:
            raise RuntimeError("Saved deployment state has an invalid vibe")
        result["vibe"] = dict(state["vibe"])
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
    if "vibe" in state:
        expected_vibe, expect_diagnostic = _expected_restored_vibe(state["vibe"])
        vibe_command = channel.send_command("set_vibe", vibe=state["vibe"])
        _wait_for_vibe(
            channel, vibe_command["command_id"], expected_vibe,
            expect_diagnostic, timeout,
        )
    else:
        expected_vibe = None
        expect_diagnostic = False
    start_payload: dict[str, Any] = {
        "animation": animation,
        "config": state["params"],
    }
    if state.get("current_preset") is not None:
        start_payload["preset"] = state["current_preset"]
    command = channel.send_command("start", **start_payload)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = channel.read_status() or {}
        if (
            status.get("last_command_id") == command["command_id"]
            and status.get("current_animation") == animation
            and status.get("is_running")
            and (
                state.get("current_preset") is None
                or status.get("current_preset") == state["current_preset"]
            )
            and (
                expected_vibe is None
                or _status_has_expected_vibe(
                    status, expected_vibe, expect_diagnostic=expect_diagnostic
                )
            )
        ):
            restored = {
                "animation": animation,
                "params": state["params"],
            }
            if "vibe" in state:
                restored["vibe"] = expected_vibe
                restored["vibe_fallback"] = expect_diagnostic
            if state.get("current_preset") is not None:
                restored["current_preset"] = state["current_preset"]
            return restored
        time.sleep(0.1)
    raise RuntimeError(f"Controller did not restore {animation!r} before timeout")


def _expected_restored_vibe(
    persisted: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Resolve known state or the specified visible neutral fallback."""
    from animation.core.presentation_contracts import VibeState, resolve_vibe

    try:
        state = VibeState.from_payload(persisted)
        resolved = resolve_vibe(
            state.vibe_id,
            revision=state.revision,
            profile_version=state.profile_version,
        )
        if (
            resolved.state.resolved_profile_digest
            != state.resolved_profile_digest
        ):
            raise ValueError("persisted vibe profile digest does not match registry")
        return resolved.state.to_dict(), False
    except (TypeError, ValueError):
        revision = persisted.get("revision", 0)
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or not 0 <= revision <= 2**64 - 1
        ):
            revision = 0
        return resolve_vibe("neutral", revision=revision).state.to_dict(), True


def _vibe_identity(payload: Any) -> tuple[Any, Any, Any, Any]:
    state = payload.get("state", payload) if isinstance(payload, dict) else {}
    return (
        state.get("id", state.get("vibe_id")),
        state.get("profile_version"),
        state.get("resolved_profile_digest"),
        state.get("revision"),
    )


def _status_has_expected_vibe(
    status: dict[str, Any], expected: dict[str, Any], *, expect_diagnostic: bool
) -> bool:
    vibe = status.get("vibe")
    if not isinstance(vibe, dict) or _vibe_identity(vibe) != _vibe_identity(expected):
        return False
    return bool(vibe.get("diagnostic")) if expect_diagnostic else True


def _wait_for_vibe(
    channel: FileControlChannel,
    command_id: Any,
    expected: dict[str, Any],
    expect_diagnostic: bool,
    timeout: float,
) -> None:
    """Require both command acknowledgement and resolved presentation state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = channel.read_status() or {}
        if (
            status.get("last_command_id") == command_id
            and _status_has_expected_vibe(
                status, expected, expect_diagnostic=expect_diagnostic
            )
        ):
            return
        time.sleep(0.1)
    raise RuntimeError("Controller did not restore expected vibe before timeout")


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
