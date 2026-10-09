"""Serialized controller commands, request correlation and playlist ownership."""
from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import threading
import time
import uuid
import math
from typing import Any, Mapping
from ipc.scene_contract import DEFAULT_SCENE_PROVIDER_POLICY, SceneProviderPolicy, normalize_scene_payload, normalize_composer_scene

class ControllerCommandConflictError(RuntimeError):
    """A command targets a controller session or ordering revision that changed."""

@dataclass
class ControllerMutationLease:
    resulting_state_revision: int | None = None

class ControllerCommandCoordinator:
    """One process owns ordered writes; failures never restore an earlier Scene."""
    def __init__(self, manager, *, restored_selected_scene=None):
        self.manager = manager
        self.session_id = str(uuid.uuid4())
        self.state_revision = 0
        self._lock = threading.RLock()
        self._selected_scene = deepcopy(restored_selected_scene)

    @property
    def selected_scene(self):
        with self._lock:
            return deepcopy(self._selected_scene)

    def controller_status(self):
        with self._lock:
            return {'controller_session_id': self.session_id,
                'controller_state_revision': self.state_revision,
                'selected_scene': self.selected_scene}

    def note_legacy_mutation(self):
        with self._lock:
            self.state_revision += 1

    @contextmanager
    def legacy_mutation_guard(self, command_guard=None):
        with self._lock:
            if command_guard is not None:
                guard = normalize_controller_command_guard(command_guard, now=time.time())
                if guard is None or guard['expected_controller_session_id'] != self.session_id or guard['expected_controller_state_revision'] != self.state_revision:
                    raise ControllerCommandConflictError('controller session or command revision changed')
            lease = ControllerMutationLease()
            try:
                yield lease
            finally:
                scene = self.manager.get_scene_state() if callable(getattr(self.manager,'get_scene_state',None)) else None
                if scene is not None:
                    self._selected_scene = deepcopy(scene)
                elif not getattr(self.manager,'is_running',False) and getattr(self.manager,'_requested_scene',None) is not None:
                    self._selected_scene = deepcopy(self.manager._requested_scene)
                elif getattr(self.manager,'is_running',False):
                    self._selected_scene = None
                self.state_revision += 1
                lease.resulting_state_revision = self.state_revision

def controller_activation_coordinator(manager, *, restored_selected_scene=None, **_removed):
    """Compatibility factory for the shared serialized command owner."""
    coordinator = getattr(manager, '_controller_command_coordinator', None)
    if coordinator is None:
        coordinator = ControllerCommandCoordinator(manager, restored_selected_scene=restored_selected_scene)
        manager._controller_command_coordinator = coordinator
    return coordinator

def normalize_managed_scene(manager, value):
    if isinstance(value, Mapping) and value.get('schema') == 'ledgrid.scene.v2':
        return normalize_composer_scene({'origin':'composer','scene':value},manager_scene_v2_catalog(manager)).scene
    return normalize_scene_payload(value,catalog=manager_component_catalog(manager),provider_policy=manager_scene_provider_policy(manager))

def start_scene(manager, scene_payload):
    return bool(manager.start_scene(normalize_managed_scene(manager,scene_payload)))

def restore_display_state(manager, state):
    """Apply saved host settings then attempt its Scene; leave failure stopped."""
    if not isinstance(state,dict):
        raise ValueError('saved display state must be an object')
    output = state.get('output',{})
    brightness = output.get('brightness')
    if brightness is None and 'master_brightness' in output:
        brightness = round(float(output['master_brightness'])*255)
    if brightness is not None:
        manager.set_output_brightness(brightness)
    tempo = output.get('animation_speed_scale',output.get('operator_tempo_scale'))
    if tempo is not None:
        manager.set_animation_speed_scale(tempo)
    if output.get('target_fps') is not None:
        manager.set_target_fps(output['target_fps'])
    if state.get('plant_modifiers') is not None:
        manager.set_plant_modifiers(state['plant_modifiers'])
    if state.get('vibe') is not None:
        manager.set_vibe(state['vibe'])
    if not output.get('power',True):
        return manager.stop_animation()
    return start_scene(manager,state.get('scene'))

def normalize_controller_command_guard(
    payload: Mapping[str, Any], *, now: float | None = None
) -> dict[str, Any] | None:
    """Validate the optional compare-and-expire settings-command fields."""
    keys = {
        "expected_controller_session_id",
        "expected_controller_state_revision",
        "expires_at",
    }
    present = keys.intersection(payload)
    if not present:
        return None
    if present != keys:
        raise ValueError(
            "expected controller session, state revision, and expires_at "
            "must be provided together"
        )
    session_id = payload["expected_controller_session_id"]
    revision = payload["expected_controller_state_revision"]
    expires_at = payload["expires_at"]
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("expected_controller_session_id must be a non-empty string")
    if type(revision) is not int or revision < 0:
        raise ValueError(
            "expected_controller_state_revision must be a non-negative integer"
        )
    if (
        isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or not math.isfinite(float(expires_at))
    ):
        raise ValueError("expires_at must be a finite Unix timestamp")
    expiry = float(expires_at)
    if now is not None and expiry <= now:
        raise ValueError("controller command guard has expired")
    return {
        "expected_controller_session_id": session_id,
        "expected_controller_state_revision": revision,
        "expires_at": expiry,
    }


def manager_scene_provider_policy(manager: Any) -> SceneProviderPolicy:
    """Read the manager's immutable provider policy, defaulting safely off."""

    getter = getattr(manager, "scene_provider_policy", None)
    if not callable(getter):
        return DEFAULT_SCENE_PROVIDER_POLICY
    policy = getter()
    if not isinstance(policy, SceneProviderPolicy):
        raise TypeError("manager scene_provider_policy() returned an invalid policy")
    return policy


def manager_scene_v2_catalog(manager: Any) -> Any:
    """Return the controller's closed canonical Scene v2 catalog."""
    getter = getattr(manager, "scene_v2_component_catalog", None)
    if callable(getter):
        return getter()
    from web.composer_final_preview import current_component_catalog
    return current_component_catalog()


def manager_component_catalog(manager: Any) -> list[dict]:
    def scene_v1_catalog(items: Any) -> list[dict]:
        """Adapt Scene v2 product roles at the legacy controller boundary."""

        catalog = []
        for descriptor in list(items or []):
            if not isinstance(descriptor, Mapping):
                catalog.append(descriptor)
                continue
            item = dict(descriptor)
            if (
                item.get("provider", "python") == "python"
                and item.get("plugin_id") == "clock_overlay"
            ):
                # Clock is authored as a Scene v2 widget.  Scene v1 carries
                # composited layers using its older background/overlay roles,
                # so translate only this known transport representation.
                item["role"] = "overlay"
            catalog.append(item)
        return catalog

    getter = getattr(manager, "list_components", None)
    if callable(getter):
        result = getter()
        if isinstance(result, dict):
            result = result.get("components", [])
        return scene_v1_catalog(result)
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
    return scene_v1_catalog(catalog)


def component_params(component: dict) -> dict:
    result = dict(component.get("resolved_parameters") or {})
    result.update(component.get("parameter_overrides") or {})
    return result


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
