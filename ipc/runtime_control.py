"""Shared controller-process adapters for versioned scene commands.

This module deliberately depends only on public manager behavior and the IPC
scene contract.  Both the file-backed Pi controller and in-process Mac control
channel use it without importing either application entrypoint.
"""

from __future__ import annotations

from typing import Any

from animation.core.installation_profile_runtime import (
    EMPTY_INSTALLATION_PROFILE_DIGEST,
)
from animation.core.plant_awareness import PlantModifierState
from ipc.scene_contract import (
    DEFAULT_SCENE_PROVIDER_POLICY,
    SceneProviderPolicy,
    SceneValidationError,
    normalize_scene_payload,
)


def manager_scene_provider_policy(manager: Any) -> SceneProviderPolicy:
    """Read the manager's immutable provider policy, defaulting safely off."""

    getter = getattr(manager, "scene_provider_policy", None)
    if not callable(getter):
        return DEFAULT_SCENE_PROVIDER_POLICY
    policy = getter()
    if not isinstance(policy, SceneProviderPolicy):
        raise TypeError("manager scene_provider_policy() returned an invalid policy")
    return policy


def manager_component_catalog(manager: Any) -> list[dict]:
    getter = getattr(manager, "list_components", None)
    if callable(getter):
        result = getter()
        if isinstance(result, dict):
            result = result.get("components", [])
        return list(result or [])
    loader = getattr(manager, "plugin_loader", None)
    if loader is None:
        return []
    catalog = []
    for plugin_id in loader.list_plugins():
        manifest = dict(loader.plugin_manifests.get(plugin_id) or {})
        info = loader.get_plugin_info(plugin_id) or {}
        catalog.append({
            **info,
            "plugin_id": plugin_id,
            "provider": manifest.get("provider", "python"),
            "role": manager._plugin_role(plugin_id),
        })
    return catalog


def component_params(component: dict) -> dict:
    result = dict(component.get("resolved_parameters") or {})
    result.update(component.get("parameter_overrides") or {})
    return result


def start_scene(manager: Any, scene_payload: dict) -> bool:
    scene = normalize_scene_payload(
        scene_payload,
        catalog=manager_component_catalog(manager) or None,
        provider_policy=manager_scene_provider_policy(manager),
    )
    starter = getattr(manager, "start_scene", None)
    if callable(starter):
        return bool(starter(scene))
    background = scene["background"]
    overlays = scene["overlays"]
    if not overlays:
        return bool(manager.start_animation(
            background["plugin_id"], component_params(background)
        ))
    overlay = overlays[0]
    placement = overlay["placement"]
    return bool(manager.start_composed_scene(
        background["plugin_id"], component_params(background),
        overlay["component"]["plugin_id"], component_params(overlay["component"]),
        overlay["opacity"], placement["strip_translation"],
        placement["led_translation"],
    ))


def update_scene_component(manager: Any, target: str, update: dict) -> bool:
    updater = getattr(manager, "update_scene_component", None)
    if callable(updater):
        try:
            return bool(updater(target, update))
        except TypeError:
            return bool(updater(target, **update))
    if target == "background":
        if update.get("component") is not None:
            raise ValueError("replace a background by applying a complete scene")
        params = update.get("params", update.get("parameter_overrides", {}))
        return bool(manager.update_animation_parameters(params))
    if target != "clock_overlay":
        raise ValueError("scene component target must be background or clock_overlay")
    if update.get("remove"):
        return bool(manager.remove_overlay())
    changed = bool(manager.set_overlay_enabled(update["enabled"])) if "enabled" in update else True
    placement = update.get("placement") or {}
    return bool(manager.update_overlay(
        update.get("params", update.get("parameter_overrides")),
        opacity=update.get("opacity"),
        strip_offset=placement.get("strip_translation"),
        led_offset=placement.get("led_translation"),
    )) and changed


def _python_fallback_scene(scene: Any) -> dict:
    """Build the conservative background-only scene recorded for recovery."""

    if not isinstance(scene, dict):
        raise ValueError("desired display state has no scene fallback")
    fallback = scene.get("known_python_fallback")
    if not isinstance(fallback, dict) or fallback.get("provider", "python") != "python":
        raise ValueError("desired display state has no recorded Python fallback")
    return {
        "schema": "ledgrid.scene-state",
        "schema_version": 1,
        "revision": scene.get("revision", 0),
        "background": dict(fallback),
        "overlays": [],
        "known_python_fallback": dict(fallback),
    }


def restore_display_state(manager: Any, state: dict) -> bool:
    """Validate the complete desired state before applying any mutation."""
    if not isinstance(state, dict):
        raise ValueError("desired display state must be an object")
    raw_scene = state.get("scene")
    catalog = manager_component_catalog(manager) or None
    provider_policy = manager_scene_provider_policy(manager)
    try:
        scene = normalize_scene_payload(
            raw_scene,
            catalog=catalog,
            provider_policy=provider_policy,
        )
    except SceneValidationError:
        # A receiver scene saved by a canary-capable release remains useful
        # data when ordinary production (all gates off) starts later. Resolve
        # the recorded Python component before any scene or hardware mutation.
        native_background = (
            raw_scene.get("background")
            if isinstance(raw_scene, dict) else None
        )
        if (
            not isinstance(native_background, dict)
            or native_background.get("provider") != "receiver_native"
            or provider_policy.allows_receiver_background(
                str(native_background.get("plugin_id", ""))
            )
        ):
            raise
        scene = normalize_scene_payload(
            _python_fallback_scene(raw_scene),
            catalog=catalog,
            provider_policy=provider_policy,
        )
    output = state.get("output", {})
    if not isinstance(output, dict):
        raise ValueError("desired display output must be an object")
    unknown = sorted(set(output) - {
        "power", "master_brightness", "operator_tempo_scale", "target_fps",
        "brightness", "animation_speed_scale",
    })
    if unknown:
        raise ValueError(
            f"desired display output has unsupported fields: {', '.join(unknown)}"
        )
    power = output.get("power", True)
    if not isinstance(power, bool):
        raise ValueError("desired display power must be boolean")
    brightness = output.get("brightness")
    if brightness is None and "master_brightness" in output:
        master = output.get("master_brightness")
        if (
            isinstance(master, bool) or not isinstance(master, (int, float))
            or not 0 <= float(master) <= 1
        ):
            raise ValueError("desired display master_brightness must be from 0 to 1")
        brightness = round(float(master) * 255)
    if brightness is not None:
        brightness = manager.validate_output_brightness(brightness)
    tempo = output.get("animation_speed_scale", output.get("operator_tempo_scale"))
    if tempo is not None:
        tempo = manager._validate_tempo_scale(tempo)
    target_fps = output.get("target_fps")
    if target_fps is not None:
        if isinstance(target_fps, bool) or not isinstance(target_fps, int):
            raise ValueError("desired display target_fps must be an integer")
        if not 1 <= target_fps <= 200:
            raise ValueError("desired display target_fps must be between 1 and 200")
    modifiers = PlantModifierState.from_payload(state.get("plant_modifiers", {})).to_dict()
    vibe = state.get("vibe")
    if vibe is not None and not isinstance(vibe, dict):
        raise ValueError("desired display vibe must be a versioned object")

    installation_profile_digest = state.get(
        "installation_profile_digest", EMPTY_INSTALLATION_PROFILE_DIGEST
    )
    profile_preflight = getattr(
        manager, "preflight_installation_profile", None
    )
    profile_selector = getattr(manager, "select_installation_profile", None)
    if callable(profile_preflight):
        # Resolve the immutable managed artifact together with every other
        # aggregate validation.  This method is explicitly read-only: no
        # profile, scene, controller, or receiver state has changed yet.
        profile_preflight(installation_profile_digest)
    elif installation_profile_digest != EMPTY_INSTALLATION_PROFILE_DIGEST:
        raise ValueError(
            "manager cannot preflight a nonempty installation profile"
        )

    prior_profile_digest = EMPTY_INSTALLATION_PROFILE_DIGEST
    current_status = getattr(manager, "get_current_status", None)
    if callable(current_status):
        status = current_status()
        if isinstance(status, dict):
            prior_profile_digest = status.get(
                "installation_profile_digest", prior_profile_digest
            )

    # Validation is complete.  Profile authority changes before scene start so
    # the first frame receives the resolved view; scene rejection restores the
    # prior profile rather than leaving a partial aggregate restore.
    if callable(profile_selector):
        profile_selector(installation_profile_digest)
    try:
        if power:
            background = scene.get("background", {})
            adopter = getattr(manager, "adopt_scene", None)
            if (
                background.get("provider") == "receiver_native"
                and background.get("plugin_id") != "compiled_rainbow"
                and callable(adopter)
            ):
                started = bool(adopter(scene))
            else:
                started = start_scene(manager, scene)
            if not started:
                if callable(profile_selector):
                    profile_selector(prior_profile_digest)
                return False
    except Exception:
        if callable(profile_selector):
            profile_selector(prior_profile_digest)
        raise
    if not power:
        manager.stop_animation()
    manager.set_plant_modifiers(modifiers)
    if vibe is not None:
        manager.set_vibe(vibe)
    if tempo is not None:
        manager.set_animation_speed_scale(tempo)
    if target_fps is not None:
        manager.set_target_fps(target_fps)
    if brightness is not None:
        manager.set_output_brightness(brightness)
    return True
