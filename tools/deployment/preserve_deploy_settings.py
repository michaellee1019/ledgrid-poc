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

from ipc.control_channel import FileControlChannel  # noqa: E402
from animation.core.defaults import DEFAULT_PLANT_AWARE  # noqa: E402
from animation.core.plant_awareness import PlantModifierState  # noqa: E402
from animation.core.presentation_contracts import (  # noqa: E402
    VibeState, component_preset_fingerprint, resolve_vibe,
)
from ipc.scene_contract import (  # noqa: E402
    DEFAULT_SCENE_PROVIDER_POLICY,
    DESIRED_DISPLAY_SCHEMA,
    DESIRED_DISPLAY_VERSION,
    SceneProviderPolicy,
    SceneValidationError,
    background_only_scene,
    normalize_scene_payload,
)
from tools.deployment.receiver_hybrid_config import (  # noqa: E402
    ReceiverHybridConfig,
    resolve_receiver_hybrid_config,
)


PRESET_ID = "before-deploy"
STATE_VERSION = 5
UNKNOWN_INSTALLATION_PROFILE_DIGEST = "0" * 64
FINALIZED_NATIVE_TOPOLOGY = {
    0: (8, 0, False),
    1: (8, 8, False),
    2: (8, 24, True),
    3: (8, 16, True),
    4: (1, 32, False),
}
RECEIVER_HYBRID_CANARY_ENV = "LEDGRID_RECEIVER_HYBRID_CANARY"
RECEIVER_NATIVE_MODULES_CANARY_ENV = "LEDGRID_RECEIVER_NATIVE_MODULES_CANARY"


def receiver_hybrid_canary_enabled(value: Any = None) -> bool:
    """Resolve the deliberately named receiver-hybrid canary switch."""

    if value is None:
        value = os.environ.get(RECEIVER_HYBRID_CANARY_ENV, "0")
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise TypeError("receiver hybrid canary must be a boolean or string")
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(
        f"{RECEIVER_HYBRID_CANARY_ENV} must be a boolean switch"
    )


def receiver_native_modules_canary_enabled(value: Any = None) -> bool:
    """Resolve the independent managed-native rollout switch, defaulting off."""

    if value is None:
        value = os.environ.get(RECEIVER_NATIVE_MODULES_CANARY_ENV, "0")
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise TypeError("receiver native modules canary must be a boolean or string")
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(
        f"{RECEIVER_NATIVE_MODULES_CANARY_ENV} must be a boolean switch"
    )


def receiver_hybrid_provider_policy(
    enabled: bool, *, receiver_native_modules: bool = False
) -> SceneProviderPolicy:
    if type(enabled) is not bool:
        raise TypeError("receiver hybrid canary state must be boolean")
    return SceneProviderPolicy(
        receiver_local_background=enabled,
        receiver_sparse_overlay=enabled,
        receiver_native_modules=receiver_native_modules,
    )


def _status_provider_policy(status: dict[str, Any]) -> SceneProviderPolicy:
    flags = status.get("feature_flags")
    if not isinstance(flags, dict):
        return DEFAULT_SCENE_PROVIDER_POLICY
    return SceneProviderPolicy(
        receiver_local_background=(
            flags.get("receiver_local_background") is True
        ),
        receiver_sparse_overlay=(
            flags.get("receiver_sparse_overlay") is True
        ),
        receiver_native_modules=(
            flags.get("receiver_native_modules") is True
        ),
    )


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


def _preset_fingerprint(animation: str, preset_id: str, params: dict[str, Any]) -> str:
    return component_preset_fingerprint(animation, preset_id, params)


def _scene_from_status(
    status: dict[str, Any], animation: str, params: dict[str, Any],
    *, provider_policy: SceneProviderPolicy = DEFAULT_SCENE_PROVIDER_POLICY,
) -> dict[str, Any]:
    raw_scene = status.get("scene_state")
    if not isinstance(raw_scene, dict):
        raw_scene = status.get("scene")
    if isinstance(raw_scene, dict) and raw_scene.get("schema"):
        try:
            return normalize_scene_payload(
                raw_scene, provider_policy=provider_policy
            )
        except SceneValidationError as exc:
            raise RuntimeError(f"Controller status has an invalid scene: {exc}") from exc
    selected = _current_preset(status.get("current_preset"), animation)
    preset_id = selected.get("preset_id") if selected else None
    fingerprint = (
        _preset_fingerprint(animation, preset_id, params)
        if preset_id is not None else None
    )
    return background_only_scene(
        animation, params,
        preset_id=preset_id,
        preset_fingerprint=fingerprint,
    )


def _canonical_persisted_vibe(status: dict[str, Any]) -> dict[str, Any]:
    payload = _vibe_state(status)
    if payload is None:
        return resolve_vibe("neutral").state.to_dict()
    try:
        state = VibeState.from_payload(payload)
        resolved = resolve_vibe(
            state.vibe_id, revision=state.revision,
            profile_version=state.profile_version,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Controller status has an invalid vibe: {exc}") from exc
    if resolved.state.resolved_profile_digest != state.resolved_profile_digest:
        raise RuntimeError("Controller status vibe digest does not match registry")
    return state.to_dict()


def _desired_display_state(
    status: dict[str, Any], scene: dict[str, Any], *, previous: Any = None
) -> dict[str, Any]:
    prior = previous if isinstance(previous, dict) else {}
    if "plant_modifiers" in status:
        modifier_payload = status["plant_modifiers"]
    elif "plant_aware" in status:
        modifier_payload = PlantModifierState.from_legacy(status["plant_aware"]).to_dict()
    else:
        modifier_payload = prior.get(
            "plant_modifiers", {"version": 1, "active": [], "strengths": {}}
        )
    modifiers = PlantModifierState.from_payload(modifier_payload).to_dict()
    prior_output = prior.get("output") if isinstance(prior.get("output"), dict) else {}
    brightness = _brightness_level(status.get("brightness"))
    if brightness is None:
        previous_level = prior_output.get("master_brightness")
        brightness = round(previous_level * 255) if isinstance(previous_level, (int, float)) else 255
    tempo = (
        _positive_finite_number(status.get("animation_speed_scale"))
        or _positive_finite_number(prior_output.get("operator_tempo_scale"))
        or 1.0
    )
    target_fps = (
        _positive_int(status.get("target_fps"))
        or _positive_int(prior_output.get("target_fps"))
        or 200
    )
    prior_digest = (
        previous.get("installation_profile_digest")
        if isinstance(previous, dict) else None
    )
    digest = status.get("installation_profile_digest", prior_digest)
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        digest = UNKNOWN_INSTALLATION_PROFILE_DIGEST
    result = {
        "schema": DESIRED_DISPLAY_SCHEMA,
        "schema_version": DESIRED_DISPLAY_VERSION,
        "revision": time.time_ns() & (2**64 - 1),
        "scene": scene,
        "vibe": (
            _canonical_persisted_vibe(status)
            if _vibe_state(status) is not None
            else dict(prior.get("vibe") or resolve_vibe("neutral").state.to_dict())
        ),
        "plant_modifiers": modifiers,
        "installation_profile_digest": digest,
        "output": {
            "master_brightness": brightness / 255.0,
            "operator_tempo_scale": tempo,
            "power": bool(status.get("is_running")),
            # Preserve the existing operational cadence even though it is not
            # presentation state in the frozen OutputState dataclass.
            "target_fps": target_fps,
        },
    }
    background = scene.get("background") if isinstance(scene, dict) else None
    managed_native = bool(
        isinstance(background, dict)
        and background.get("provider") == "receiver_native"
        and background.get("plugin_id") != "compiled_rainbow"
    )
    if managed_native:
        # A powered-off guarded activation selects an exact scene without
        # starting receiver playback.  Persist that selection and power state,
        # but do not invent or reuse playback evidence for a bundle that is not
        # active.  The next powered activation must establish fresh authority.
        if status.get("is_running"):
            receiver_status = status.get("receiver_hybrid")
            driver = (
                receiver_status.get("driver")
                if isinstance(receiver_status, dict) else None
            )
            if not isinstance(driver, dict):
                raise RuntimeError(
                    "Controller status has no managed-native restoration authority"
                )
            parameter_digest = driver.get("parameter_digest")
            if (
                driver.get("bundle_digest") != background.get("bundle_digest")
                or driver.get("payload_digest")
                != background.get("expected_payload_digest")
                or not isinstance(parameter_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", parameter_digest) is None
            ):
                raise RuntimeError(
                    "Controller status has no exact managed-native parameter binding"
                )
            result["native_expectation"] = {
                "bundle_digest": background["bundle_digest"],
                "payload_digest": background["expected_payload_digest"],
                "parameter_digest": parameter_digest,
            }
    elif isinstance(prior.get("native_expectation"), dict):
        # Retain the exact authority across idle global-control updates. It is
        # ignored when provider policy deliberately restores the Python fallback.
        result["native_expectation"] = dict(prior["native_expectation"])
    return result


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
    provider_policy = _status_provider_policy(status)
    animation = _safe_animation_name(status.get("current_animation"))
    raw_scene = status.get("scene_state")
    if not isinstance(raw_scene, dict):
        raw_scene = status.get("scene")
    scene = None
    if isinstance(raw_scene, dict) and raw_scene.get("schema"):
        scene = _scene_from_status(
            status, animation, {}, provider_policy=provider_policy
        )

    if (not status.get("is_running") or not animation) and scene is None:
        # Global presentation controls remain independently writable while the
        # wall is stopped. Retain the last valid playable snapshot and update
        # only those controls so the next restart does not discard the choice.
        try:
            previous = load_saved_state(
                state_path, provider_policy=provider_policy
            )
            animation = previous["animation"]
            preset_path_value = previous.get("preset_path")
            if isinstance(preset_path_value, str):
                preset = _read_object(Path(preset_path_value))
            else:
                preset = {
                    "animation": animation,
                    "params": dict(previous.get("params") or {}),
                }
        except RuntimeError as exc:
            raise RuntimeError(
                "No running animation is available to preserve"
            ) from exc
        scene = previous["scene"]
        state = _desired_display_state(status, scene, previous=previous)
        state.update({
            "version": STATE_VERSION,
            "animation": animation,
            "saved_at": time.time(),
        })
        if isinstance(preset_path_value, str):
            state["preset_path"] = preset_path_value
        _atomic_write(state_path, state)
        return preset

    stopped_selected_scene = not bool(status.get("is_running"))
    if not animation and scene is not None:
        animation = _safe_animation_name(scene["background"].get("plugin_id"))
        if not animation:
            raise RuntimeError(
                "Controller selected scene has an invalid background animation"
            )
    native_background = bool(
        scene is not None
        and scene["background"].get("provider") == "receiver_native"
    )
    if native_background:
        background = scene["background"]
        params = dict(background.get("resolved_parameters") or {})
        params.update(background.get("parameter_overrides") or {})
    elif stopped_selected_scene and scene is not None:
        # Guarded power-off keeps an exact selected scene even though no plugin
        # is currently running and animation_info is therefore absent.
        background = scene["background"]
        params = dict(background.get("resolved_parameters") or {})
        params.update(background.get("parameter_overrides") or {})
    else:
        params = _preset_params(status)
        if scene is None:
            scene = _scene_from_status(
                status, animation, params, provider_policy=provider_policy
            )

    preset_path = presets_dir / animation / f"{PRESET_ID}.json"
    now = time.time()
    preset = {
        "version": 1,
        "preset_id": PRESET_ID,
        "name": PRESET_ID,
        "animation": animation,
        "params": params,
        "created_at": now,
        "updated_at": now,
    }
    if native_background:
        # Receiver-native components have no Python plugin preset file. Their
        # exact desired scene and recorded Python fallback are the durable
        # restart contract; writing a fake preset would make deployment tools
        # imply that the Python loader can instantiate the native component.
        state = _desired_display_state(status, scene)
        state.update({
            "version": STATE_VERSION,
            "animation": animation,
            "saved_at": now,
        })
        _atomic_write(state_path, state)
        return preset

    try:
        existing = _read_object(preset_path)
    except RuntimeError:
        existing = {}
    preset["created_at"] = existing.get("created_at", now)
    _atomic_write(preset_path, preset)
    state = _desired_display_state(status, scene)
    state.update({
        # ``version`` and the legacy aliases keep older deploy coordinators able
        # to identify the capture while the schema marks the new desired state.
        "version": STATE_VERSION,
        "animation": animation,
        "preset_path": str(preset_path),
        "saved_at": now,
    })
    current_preset = _current_preset(status.get("current_preset"), animation)
    if current_preset is not None:
        state["current_preset"] = current_preset
    _atomic_write(state_path, state)
    return preset


def save(status_path: Path, presets_dir: Path, state_path: Path) -> dict[str, Any]:
    return save_status(_read_object(status_path), presets_dir, state_path)


def load_saved_state(
    state_path: Path,
    *,
    provider_policy: SceneProviderPolicy = DEFAULT_SCENE_PROVIDER_POLICY,
) -> dict[str, Any]:
    """Load and validate the animation and parameters used for restart."""
    state = _read_object(state_path)
    if "scene" in state or state.get("schema") == DESIRED_DISPLAY_SCHEMA:
        return _load_desired_display_state(
            state, provider_policy=provider_policy
        )

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


def _known_python_fallback_scene(raw_scene: Any) -> dict[str, Any]:
    if not isinstance(raw_scene, dict):
        raise RuntimeError("Saved desired display state has no scene fallback")
    fallback = raw_scene.get("known_python_fallback")
    try:
        return normalize_scene_payload({
            "schema": "ledgrid.scene-state",
            "schema_version": 1,
            "revision": 0,
            "background": fallback,
            "overlays": [],
            "known_python_fallback": fallback,
        })
    except (SceneValidationError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Saved desired display state has no valid Python fallback: {exc}"
        ) from exc


def _load_desired_display_state(
    state: dict[str, Any],
    *,
    provider_policy: SceneProviderPolicy = DEFAULT_SCENE_PROVIDER_POLICY,
) -> dict[str, Any]:
    """Validate the aggregate before exposing any values to controller startup."""
    raw_scene = state.get("scene")
    fallback_scene = _known_python_fallback_scene(raw_scene)
    for alias, validator, message in (
        ("animation_speed_scale", _positive_finite_number, "animation speed scale"),
        ("target_fps", _positive_int, "target FPS"),
        ("brightness", _brightness_level, "brightness"),
    ):
        if alias in state and validator(state[alias]) is None:
            raise RuntimeError(f"Saved deployment state has an invalid {message}")
    fallback_reason = None
    if (
        state.get("schema") != DESIRED_DISPLAY_SCHEMA
        or state.get("schema_version") != DESIRED_DISPLAY_VERSION
    ):
        scene = fallback_scene
        fallback_reason = (
            f"unsupported desired display schema/version: "
            f"{state.get('schema')!r}/{state.get('schema_version')!r}"
        )
    else:
        try:
            scene = normalize_scene_payload(
                raw_scene, provider_policy=provider_policy
            )
        except (SceneValidationError, TypeError, ValueError) as exc:
            scene = fallback_scene
            fallback_reason = f"unsupported saved scene: {exc}"

    revision = state.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or not 0 <= revision < 2**64:
        raise RuntimeError("Saved desired display state has an invalid revision")
    digest = state.get("installation_profile_digest")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise RuntimeError(
            "Saved desired display state has an invalid installation profile digest"
        )
    output = state.get("output")
    if not isinstance(output, dict):
        raise RuntimeError("Saved desired display state has invalid output controls")
    allowed_output = {
        "master_brightness", "operator_tempo_scale", "power", "target_fps"
    }
    unknown_output = sorted(set(output) - allowed_output)
    if unknown_output:
        raise RuntimeError(
            f"Saved desired display output has unsupported fields: {', '.join(unknown_output)}"
        )
    master = output.get("master_brightness")
    if (
        isinstance(master, bool) or not isinstance(master, (int, float))
        or not math.isfinite(float(master)) or not 0 <= float(master) <= 1
    ):
        raise RuntimeError("Saved desired display state has invalid master brightness")
    tempo = _positive_finite_number(output.get("operator_tempo_scale"))
    if tempo is None:
        raise RuntimeError("Saved desired display state has invalid operator tempo")
    power = output.get("power")
    if not isinstance(power, bool):
        raise RuntimeError("Saved desired display state has invalid power state")
    target_fps = _positive_int(output.get("target_fps", 200))
    if target_fps is None or target_fps > 200:
        raise RuntimeError("Saved desired display state has invalid target FPS")
    try:
        modifiers = PlantModifierState.from_payload(state.get("plant_modifiers"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Saved desired display state has invalid plant modifiers: {exc}"
        ) from exc
    vibe = state.get("vibe")
    if not isinstance(vibe, dict) or not vibe:
        raise RuntimeError("Saved desired display state has invalid vibe")
    resolved_vibe, vibe_fallback = _expected_restored_vibe(vibe)

    background = scene["background"]
    params = dict(background.get("resolved_parameters") or {})
    params.update(background.get("parameter_overrides") or {})
    result = dict(state)
    result.update({
        "scene": scene,
        "fallback_scene": fallback_scene,
        "animation": background["plugin_id"],
        "params": params,
        "animation_speed_scale": tempo,
        "target_fps": target_fps,
        "brightness": round(float(master) * 255),
        "power": power,
        "plant_modifiers": modifiers.to_dict(),
        # Preserve the original persisted state for the manager's observable
        # neutral-fallback diagnostic.  ``resolved_vibe`` is used only by the
        # restore acknowledgement logic.
        "vibe": dict(vibe),
        "expected_vibe": resolved_vibe,
    })
    if fallback_reason is not None:
        result["scene_fallback_reason"] = fallback_reason
    if vibe_fallback:
        result["vibe_fallback_reason"] = "unsupported saved vibe; using neutral"
    if "current_preset" in state:
        result["current_preset"] = _current_preset(
            state["current_preset"], result["animation"], label="Saved current preset"
        )
    return result


def _wait_for_fresh_controller(channel: FileControlChannel, started_at: float, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = channel.read_status() or {}
        if status.get("updated_at", 0) >= started_at:
            return
        time.sleep(0.1)
    raise RuntimeError("Controller did not publish fresh status after restart")


def restore(
    status_path: Path,
    control_path: Path,
    state_path: Path,
    timeout: float,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    restore_started_at = time.time()
    hybrid_config = resolve_receiver_hybrid_config(
        Path.cwd() if root is None else root
    )
    state = load_saved_state(
        state_path,
        provider_policy=receiver_hybrid_provider_policy(
            hybrid_config.enabled,
            receiver_native_modules=hybrid_config.native_modules_enabled,
        ),
    )
    animation = state["animation"]

    channel = FileControlChannel(str(control_path), str(status_path))
    _wait_for_fresh_controller(channel, restore_started_at, timeout)
    if "scene" in state:
        expected_vibe, expect_diagnostic = _expected_restored_vibe(state["vibe"])
        command = channel.send_command("restore_display_state", state={
            "schema": DESIRED_DISPLAY_SCHEMA,
            "schema_version": DESIRED_DISPLAY_VERSION,
            "revision": state["revision"],
            "scene": state["scene"],
            "vibe": state["vibe"],
            "plant_modifiers": state["plant_modifiers"],
            "installation_profile_digest": state["installation_profile_digest"],
            "output": state["output"],
        })
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = channel.read_status() or {}
            scene_status = status.get("scene_state")
            scene_background = (
                scene_status.get("background", {}).get("plugin_id")
                if isinstance(scene_status, dict) else status.get("current_animation")
            )
            power_matches = (
                bool(status.get("is_running")) == bool(state.get("power", True))
            )
            if (
                status.get("last_command_id") == command["command_id"]
                and power_matches
                and status.get("installation_profile_digest")
                == state["installation_profile_digest"]
                and (
                    not state.get("power", True)
                    or (
                        scene_background == animation
                        and _restored_scene_proof(
                            status,
                            state["scene"],
                            hybrid_config,
                            native_expectation=state.get("native_expectation"),
                        )
                    )
                )
                and _status_has_expected_vibe(
                    status, expected_vibe, expect_diagnostic=expect_diagnostic
                )
            ):
                return {
                    "animation": animation,
                    "scene": state["scene"],
                    "params": state["params"],
                    "vibe": expected_vibe,
                    "vibe_fallback": expect_diagnostic,
                    "scene_fallback": state.get("scene_fallback_reason"),
                    "installation_profile_digest": state[
                        "installation_profile_digest"
                    ],
                }
            time.sleep(0.1)
        raise RuntimeError(
            f"Controller did not restore desired display {animation!r} before timeout"
        )
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


def _restored_scene_proof(
    status: dict[str, Any],
    expected_scene: dict[str, Any],
    hybrid_config: ReceiverHybridConfig,
    *,
    native_expectation: Any = None,
) -> bool:
    """Require exact desired state and, for native scenes, operational proof."""

    background = expected_scene.get("background")
    if not isinstance(background, dict) or background.get("provider") != "receiver_native":
        return True
    if status.get("scene_state") != expected_scene:
        return False
    if not hybrid_config.enabled:
        return False
    scene_status = status.get("scene")
    receiver = status.get("receiver_hybrid")
    if not isinstance(scene_status, dict) or not isinstance(receiver, dict):
        return False
    expected_ids = sorted(FINALIZED_NATIVE_TOPOLOGY)
    if sorted(int(value) for value in hybrid_config.physical_lane_order) != expected_ids:
        return False
    common = bool(
        receiver.get("operational") is True
        and receiver.get("fallback_active") is False
        and receiver.get("error") is None
    )
    if not common:
        return False
    if background.get("plugin_id") == "compiled_rainbow":
        return bool(
            scene_status.get("provider_mode") == "receiver_hybrid"
            and receiver.get("healthy") is True
            and receiver.get("telemetry_complete") is True
            and receiver.get("transport_policy") == "strict_all_readable_v1"
            and sorted(receiver.get("readable_devices", [])) == expected_ids
            and receiver.get("unverified_devices") == []
        )

    if not getattr(hybrid_config, "native_modules_enabled", False):
        return False
    if not isinstance(native_expectation, dict):
        return False
    driver = receiver.get("driver")
    if not isinstance(driver, dict):
        return False
    capability_report = driver.get("capability_report")
    devices = (
        capability_report.get("devices")
        if isinstance(capability_report, dict) else None
    )
    if not isinstance(devices, list):
        return False
    agreement = driver.get("agreement")
    if not isinstance(agreement, dict):
        return False
    required_capabilities = capability_report.get("required_capabilities")
    if (
        type(required_capabilities) is not int
        or required_capabilities <= 0
        or agreement.get("exact_roster") is not True
        or agreement.get("verified_receiver_ids") != expected_ids
    ):
        return False
    observed_topology = {
        item.get("logical_device"): (
            item.get("local_strip_count"), item.get("global_strip_offset"),
            item.get("reverse_native_strip_order"),
        )
        for item in devices if isinstance(item, dict)
    }
    capability_masks = {
        item.get("logical_device"): item.get("capabilities")
        for item in devices if isinstance(item, dict)
    }
    driver_stats = status.get("driver_stats")
    device_statuses = (
        driver_stats.get("devices") if isinstance(driver_stats, dict) else None
    )
    if not isinstance(device_statuses, list) or len(device_statuses) != 5:
        return False
    by_id = {
        item.get("receiver_logical_device"): item
        for item in device_statuses if isinstance(item, dict)
    }
    if set(by_id) != set(expected_ids):
        return False
    parameter_digest = native_expectation.get("parameter_digest")
    context_digest = driver.get("context_digest")
    profile_digest = status.get("installation_profile_digest")
    if not all(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
        for value in (parameter_digest, context_digest, profile_digest)
    ):
        return False
    unanimous_fields = (
        "receiver_vibe_revision",
        "receiver_vibe_digest",
        "receiver_plant_modifier_revision",
        "receiver_plant_modifier_digest",
    )
    unanimous_values = {
        field: by_id[0].get(field) for field in unanimous_fields
    }
    if any(value in (None, "") for value in unanimous_values.values()):
        return False
    for receiver_id in expected_ids:
        device = by_id[receiver_id]
        capabilities = capability_masks.get(receiver_id)
        if (
            type(capabilities) is not int
            or capabilities & required_capabilities != required_capabilities
            or device.get("receiver_status_seen") is not True
            or int(device.get("receiver_status_version", 0) or 0) < 6
            or device.get("receiver_native_executing") is not True
            or device.get("receiver_native_cache_integrity_ok") is not True
            or device.get("receiver_native_active_bundle_digest")
            != background.get("bundle_digest")
            or device.get("receiver_native_active_payload_digest")
            != background.get("expected_payload_digest")
            or device.get("receiver_native_active_parameter_digest")
            != parameter_digest
            or device.get("receiver_active_context_digest") != context_digest
            or device.get("receiver_profile_active_global_digest") != profile_digest
            or any(
                device.get(field) != value
                for field, value in unanimous_values.items()
            )
        ):
            return False
    return bool(
        scene_status.get("provider_mode") == "receiver_native"
        and receiver.get("healthy") is True
        and driver.get("state") == "active"
        and driver.get("bundle_digest") == background.get("bundle_digest")
        and driver.get("payload_digest")
            == background.get("expected_payload_digest")
        and driver.get("bundle_digest") == native_expectation.get("bundle_digest")
        and driver.get("payload_digest") == native_expectation.get("payload_digest")
        and driver.get("parameter_digest") == parameter_digest
        and driver.get("installation_profile_digest") == profile_digest
        and sorted(observed_topology) == expected_ids
        and observed_topology == FINALIZED_NATIVE_TOPOLOGY
    )


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
        preset = restore(
            args.status,
            args.control,
            args.state,
            args.wait,
        )
        print(f"Restored {preset['animation']}/{PRESET_ID}")
    else:
        deploy_timestamp = record_deploy(args.deployment)
        print(f"Recorded deployment timestamp {deploy_timestamp}")


if __name__ == "__main__":
    main()
