"""Shared controller-process adapters for versioned scene commands.

This module deliberately depends only on public manager behavior and the IPC
scene contract.  Both the file-backed Pi controller and in-process Mac control
channel use it without importing either application entrypoint.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import threading
import time
import uuid
from typing import Any, Callable, Mapping

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


ACTIVATION_COMMAND_SCHEMA = "ledgrid.scene-activation-command"
ACTIVATION_STATUS_SCHEMA = "ledgrid.scene-activation-status"
ACTIVATION_SCHEMA_VERSION = 1
_TERMINAL_ACTIVATION_PHASES = frozenset(
    {"active", "rolled_back", "failed", "timed_out"}
)
_SHA256_ZERO = "0" * 64


class ControllerActivationError(RuntimeError):
    """A guarded controller activation could not complete safely."""


class ControllerActivationValidationError(ControllerActivationError, ValueError):
    """The activation envelope is malformed or not bound to its desired state."""


class ControllerActivationConflictError(ControllerActivationError):
    """The activation lost compare-and-swap or reused an identity incorrectly."""


class ControllerActivationCancelled(ControllerActivationError):
    """The activation was cancelled before its first mutation."""


class ControllerActivationTimedOut(ControllerActivationError, TimeoutError):
    """Fresh correlated observation did not arrive before the deadline."""


class ControllerActivationPublicationError(ControllerActivationError):
    """A correlated status could not be published durably."""


def _canonical_json_bytes(value: Any) -> bytes:
    """Canonical JSON shared by the fallback controller-only implementation."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _strict_digest(value: Any, label: str, *, allow_none: bool = False) -> str | None:
    if allow_none and value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value.lower() != value
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ControllerActivationValidationError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return value


def _uint64(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise ControllerActivationValidationError(
            f"{label} must be an unsigned 64-bit integer"
        )
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ControllerActivationValidationError(
            f"{label} must be a non-empty bounded string"
        )
    return value


def _copy_json(value: Any) -> Any:
    """Detach public records while also rejecting non-JSON state."""

    return json.loads(_canonical_json_bytes(value))


def _vibe_identity(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"vibe_id": value}
    if not isinstance(value, Mapping):
        raise ControllerActivationValidationError("global settings vibe must be an object")
    state = value.get("state", value)
    if not isinstance(state, Mapping):
        raise ControllerActivationValidationError("global settings vibe state must be an object")
    result: dict[str, Any] = {}
    vibe_id = state.get("vibe_id", state.get("id"))
    if not isinstance(vibe_id, str) or not vibe_id:
        raise ControllerActivationValidationError(
            "global settings vibe.vibe_id must be a non-empty string"
        )
    result["vibe_id"] = vibe_id
    if "profile_version" in state:
        result["profile_version"] = _uint64(
            state["profile_version"], "global settings vibe.profile_version"
        )
    if "resolved_profile_digest" in state:
        result["resolved_profile_digest"] = _strict_digest(
            state["resolved_profile_digest"],
            "global settings vibe.resolved_profile_digest",
        )
    return result


def _normalize_global_settings_fallback(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ControllerActivationValidationError("desired global settings must be an object")
    payload = dict(value)
    if payload.get("schema", "ledgrid.global-settings-state") != "ledgrid.global-settings-state":
        raise ControllerActivationValidationError("unsupported global settings schema")
    if payload.get("schema_version", 1) != 1:
        raise ControllerActivationValidationError("unsupported global settings schema_version")
    revision = _uint64(payload.get("revision", 0), "global settings revision")
    output = payload.get("output", payload)
    if not isinstance(output, Mapping):
        raise ControllerActivationValidationError("global settings output must be an object")
    power = output.get("power", True)
    if not isinstance(power, bool):
        raise ControllerActivationValidationError("global settings power must be boolean")
    brightness = output.get("brightness")
    if brightness is None and "master_brightness" in output:
        master = output["master_brightness"]
        if (
            isinstance(master, bool)
            or not isinstance(master, (int, float))
            or not math.isfinite(float(master))
            or not 0 <= float(master) <= 1
        ):
            raise ControllerActivationValidationError(
                "global settings master_brightness must be from 0 to 1"
            )
        brightness = round(float(master) * 255)
    if isinstance(brightness, bool) or not isinstance(brightness, int) or not 0 <= brightness <= 255:
        raise ControllerActivationValidationError(
            "global settings brightness must be an integer from 0 to 255"
        )
    speed = output.get(
        "animation_speed_scale", output.get("operator_tempo_scale")
    )
    if (
        isinstance(speed, bool)
        or not isinstance(speed, (int, float))
        or not math.isfinite(float(speed))
        or float(speed) <= 0
    ):
        raise ControllerActivationValidationError(
            "global settings animation_speed_scale must be positive and finite"
        )
    target_fps = output.get("target_fps")
    if isinstance(target_fps, bool) or not isinstance(target_fps, int) or not 1 <= target_fps <= 200:
        raise ControllerActivationValidationError(
            "global settings target_fps must be an integer from 1 to 200"
        )
    modifiers = PlantModifierState.from_payload(
        payload.get("plant_modifiers", {})
    ).to_dict()
    vibe = _vibe_identity(payload.get("vibe", {"vibe_id": "neutral"}))
    return {
        "schema": "ledgrid.global-settings-state",
        "schema_version": 1,
        "revision": revision,
        "vibe": vibe,
        "plant_modifiers": modifiers,
        "output": {
            "power": power,
            "brightness": brightness,
            "animation_speed_scale": float(speed),
            "target_fps": target_fps,
        },
    }


def _normalize_global_settings(value: Any) -> dict[str, Any]:
    # The canonical contract module is developed independently of this runtime
    # adapter. Resolve its export lazily so either import order remains safe.
    try:
        from ipc.scene_contract import normalize_global_settings_payload
    except ImportError:
        return _normalize_global_settings_fallback(value)
    try:
        return dict(normalize_global_settings_payload(value))
    except (TypeError, ValueError) as exc:
        raise ControllerActivationValidationError(str(exc)) from exc


def _global_settings_digest(value: Mapping[str, Any]) -> str:
    try:
        from ipc.scene_contract import global_settings_digest
    except ImportError:
        return _canonical_json_sha256(value)
    return str(global_settings_digest(value))


def _scene_digest(value: Mapping[str, Any]) -> str:
    return _canonical_json_sha256(value)


def _normalize_activation_command_fallback(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ControllerActivationValidationError("activation command must be an object")
    payload = dict(value)
    if payload.get("schema", ACTIVATION_COMMAND_SCHEMA) != ACTIVATION_COMMAND_SCHEMA:
        raise ControllerActivationValidationError("unsupported activation command schema")
    if payload.get("schema_version", ACTIVATION_SCHEMA_VERSION) != ACTIVATION_SCHEMA_VERSION:
        raise ControllerActivationValidationError(
            "unsupported activation command schema_version"
        )
    activation_id = _identifier(payload.get("activation_id"), "activation_id")
    check_token_digest = _strict_digest(
        payload.get("check_token_digest"), "check_token_digest"
    )
    basis = payload.get("basis")
    desired = payload.get("desired")
    if not isinstance(basis, Mapping) or not isinstance(desired, Mapping):
        raise ControllerActivationValidationError(
            "activation command basis and desired must be objects"
        )
    basis = _copy_json(basis)
    basis_digest = _strict_digest(payload.get("basis_digest"), "basis_digest")
    if _canonical_json_sha256(basis) != basis_digest:
        raise ControllerActivationValidationError(
            "activation command basis_digest does not match basis"
        )
    scene = desired.get("scene")
    globals_payload = desired.get("global_settings")
    profile = _strict_digest(
        desired.get("installation_profile_digest"),
        "desired installation_profile_digest",
    )
    if not isinstance(scene, Mapping):
        raise ControllerActivationValidationError("desired scene must be an object")
    return {
        "schema": ACTIVATION_COMMAND_SCHEMA,
        "schema_version": ACTIVATION_SCHEMA_VERSION,
        "activation_id": activation_id,
        "check_token_digest": check_token_digest,
        "basis": basis,
        "basis_digest": basis_digest,
        "desired": {
            "scene": _copy_json(scene),
            "global_settings": _normalize_global_settings(globals_payload),
            "installation_profile_digest": profile,
        },
    }


def _normalize_activation_command(
    value: Any,
    *,
    catalog: list[dict[str, Any]] | None = None,
    provider_policy: SceneProviderPolicy = DEFAULT_SCENE_PROVIDER_POLICY,
    now: int | None = None,
) -> dict[str, Any]:
    try:
        from ipc.scene_contract import normalize_scene_activation_command
    except ImportError:
        return _normalize_activation_command_fallback(value)
    try:
        return dict(normalize_scene_activation_command(
            value,
            catalog=catalog,
            provider_policy=provider_policy,
            now=now,
        ))
    except (TypeError, ValueError) as exc:
        raise ControllerActivationValidationError(str(exc)) from exc


def _activation_identity_from_basis(basis: Mapping[str, Any]) -> dict[str, Any]:
    try:
        from ipc.scene_contract import activation_identity_from_basis
    except ImportError:
        global_settings = basis.get("global_settings") or {}
        browser_scene = basis.get("browser_scene") or {}
        host_scene = basis.get("host_scene") or browser_scene
        return {
            "scene_identity": {
                "revision": host_scene.get("revision", 0),
                "digest": host_scene.get("digest"),
            },
            "component_identities": _copy_json(basis.get("components", [])),
            "global_settings_identity": {
                "revision": global_settings.get("revision", 0),
                "digest": global_settings.get("digest"),
            },
            "installation_profile_digest": basis.get(
                "installation_profile_digest"
            ),
        }
    return dict(activation_identity_from_basis(basis))


def _status_activation_identity_or_none(value: Any) -> dict[str, Any] | None:
    """Return a contract identity when prior state was itself an activation."""

    try:
        from ipc.scene_contract import normalize_activation_identity

        return dict(normalize_activation_identity(value))
    except (TypeError, ValueError):
        # Idle, painter, and legacy animation states have a controller identity
        # for CAS, but are not scene-activation identities.
        return None


@dataclass(frozen=True)
class ControllerStateSnapshot:
    """Complete controller-owned state required for verified compensation."""

    snapshot_id: str
    state_revision: int
    active_identity: Any
    scene: dict[str, Any] | None
    global_settings: dict[str, Any]
    installation_profile_digest: str
    receiver_profile_noop: bool = False
    receiver_profile_wall: Any = None
    receiver_profile_snapshots: tuple[Any, ...] = ()


@dataclass
class _ActivationRecord:
    command: dict[str, Any]
    status: dict[str, Any]
    snapshot: ControllerStateSnapshot | None = None
    cancel_requested: bool = False
    pending_publications: list[dict[str, Any]] = field(default_factory=list)
    publication_error: str | None = None
    historical: bool = False
    receiver_evidence_before: dict[str, Any] | None = None


class ControllerActivationCoordinator:
    """Serialize, CAS, apply, observe, and compensate scene activations.

    The coordinator intentionally targets the manager's public control surface.
    It can therefore protect the hardware process and the in-process local
    dashboard without adding a second scene, profile, or global-settings
    authority.  Receiver profile installation is used when the controller and
    managed library expose the existing transaction surface.
    """

    def __init__(
        self,
        manager: Any,
        *,
        session_id: str | None = None,
        max_records: int = 64,
        observation_timeout: float = 1.0,
        observation_interval: float = 0.01,
        clock: Callable[[], float] | None = None,
        wall_clock_ms: Callable[[], int] | None = None,
        sleeper: Callable[[float], None] | None = None,
        observer: Callable[[dict[str, Any]], Mapping[str, Any] | None] | None = None,
        fault_injector: Callable[[str, str, str], None] | None = None,
        status_sink: Callable[[dict[str, Any]], None] | None = None,
        restored_selected_scene: Mapping[str, Any] | None = None,
        cancel_probe: Callable[[str], bool] | None = None,
        commit_callback: Callable[[], None] | None = None,
    ) -> None:
        if type(max_records) is not int or max_records < 1:
            raise ValueError("max_records must be a positive integer")
        if observation_timeout < 0 or observation_interval < 0:
            raise ValueError("observation timing must be non-negative")
        self.manager = manager
        self.session_id = session_id or uuid.uuid4().hex
        if re.fullmatch(r"[0-9a-f]{32}", self.session_id) is None:
            raise ValueError("session_id must be a lowercase 128-bit hexadecimal ID")
        self.max_records = max_records
        self.observation_timeout = float(observation_timeout)
        self.observation_interval = float(observation_interval)
        self._clock = clock or time.monotonic
        self._wall_clock_ms = wall_clock_ms or (lambda: int(time.time() * 1000))
        self._sleep = sleeper or time.sleep
        self._observer = observer
        self._fault_injector = fault_injector
        self._status_sink = status_sink
        self._cancel_probe = cancel_probe
        self._commit_callback = commit_callback
        self._lock = threading.RLock()
        self._execution_lock = threading.Lock()
        self._records: OrderedDict[str, _ActivationRecord] = OrderedDict()
        self._state_revision = 0
        self._global_settings_revision = 0
        manager_status = self._manager_status()
        self._selected_scene = self._live_scene(manager_status)
        if self._selected_scene is None and restored_selected_scene is not None:
            self._selected_scene = normalize_scene_payload(
                restored_selected_scene,
                catalog=manager_component_catalog(self.manager) or None,
                provider_policy=manager_scene_provider_policy(self.manager),
            )
        self._active_identity = self._derive_active_identity()

    @property
    def state_revision(self) -> int:
        with self._lock:
            return self._state_revision

    @property
    def current_identity_digest(self) -> str:
        with self._lock:
            return _canonical_json_sha256(self._active_identity)

    def set_status_sink(
        self, status_sink: Callable[[dict[str, Any]], None] | None
    ) -> None:
        with self._lock:
            self._status_sink = status_sink

    def set_commit_callback(
        self, commit_callback: Callable[[], None] | None
    ) -> None:
        """Set the durable restart-state barrier used before publishing Active."""

        with self._lock:
            self._commit_callback = commit_callback

    def _manager_status(self) -> dict[str, Any]:
        getter = getattr(self.manager, "get_current_status", None)
        if callable(getter):
            result = getter()
            if isinstance(result, Mapping):
                return dict(result)
        return {
            "is_running": bool(getattr(self.manager, "is_running", False)),
            "painter_active": bool(getattr(self.manager, "painter_active", False)),
            "brightness": getattr(self.manager, "output_brightness", 255),
            "animation_speed_scale": getattr(
                self.manager, "animation_speed_scale", 1.0
            ),
            "target_fps": getattr(self.manager, "target_fps", 200),
            "plant_modifiers": getattr(
                getattr(self.manager, "plant_modifier_state", None),
                "to_dict",
                lambda: {"version": 1, "active": [], "strengths": {}},
            )(),
            "vibe": {"vibe_id": "neutral"},
            "installation_profile_digest": getattr(
                getattr(self.manager, "_installation_profile_selection", None),
                "selected_digest",
                EMPTY_INSTALLATION_PROFILE_DIGEST,
            ),
        }

    def _live_scene(
        self, status: Mapping[str, Any] | None = None
    ) -> dict[str, Any] | None:
        getter = getattr(self.manager, "get_scene_state", None)
        scene = getter() if callable(getter) else None
        if scene is None and isinstance(status, Mapping):
            scene = status.get("scene_state")
        if scene is None:
            return None
        if not isinstance(scene, Mapping):
            raise ControllerActivationError("manager returned a non-object scene state")
        return normalize_scene_payload(
            scene,
            catalog=manager_component_catalog(self.manager) or None,
            provider_policy=manager_scene_provider_policy(self.manager),
        )

    def _current_scene(
        self, status: Mapping[str, Any] | None = None
    ) -> dict[str, Any] | None:
        scene = self._live_scene(status)
        if scene is not None:
            return scene
        selected = getattr(self, "_selected_scene", None)
        return None if selected is None else _copy_json(selected)

    def _current_global_settings(
        self,
        status: Mapping[str, Any],
        *,
        revision: int | None = None,
    ) -> dict[str, Any]:
        brightness = status.get("brightness")
        if brightness is None:
            brightness = getattr(self.manager, "output_brightness", None)
        if brightness is None:
            brightness = 255
        vibe = _vibe_identity(status.get("vibe", {"vibe_id": "neutral"}))
        if (
            "profile_version" not in vibe
            or "resolved_profile_digest" not in vibe
        ):
            from animation.core.presentation_contracts import resolve_vibe

            resolved = resolve_vibe(vibe["vibe_id"]).state.to_dict()
            vibe = {
                "vibe_id": resolved["vibe_id"],
                "profile_version": resolved["profile_version"],
                "resolved_profile_digest": resolved["resolved_profile_digest"],
            }
        modifiers = status.get(
            "plant_modifiers",
            {"version": 1, "active": [], "strengths": {}},
        )
        return _normalize_global_settings({
            "schema": "ledgrid.global-settings-state",
            "schema_version": 1,
            "revision": (
                self._global_settings_revision if revision is None else revision
            ),
            "vibe": vibe,
            "plant_modifiers": modifiers,
            "output": {
                "power": bool(
                    status.get("is_running", False)
                    or status.get("painter_active", False)
                ),
                "brightness": brightness,
                "animation_speed_scale": status.get(
                    "animation_speed_scale", 1.0
                ),
                "target_fps": status.get("target_fps", 200),
            },
        })

    def _component_identities(
        self, scene: Mapping[str, Any] | None
    ) -> list[dict[str, Any]]:
        if scene is None:
            return []
        items: list[tuple[str, Mapping[str, Any]]] = [
            ("background", scene["background"]),
            ("known_python_fallback", scene["known_python_fallback"]),
        ]
        items.extend(
            (str(overlay["slot_id"]), overlay["component"])
            for overlay in scene.get("overlays", [])
        )
        catalog = {
            (item.get("provider", "python"), item.get("plugin_id")): item
            for item in manager_component_catalog(self.manager)
            if isinstance(item, Mapping)
        }
        result = []
        for slot_id, component in items:
            provider = component.get("provider", "python")
            component_id = component.get("plugin_id")
            descriptor = catalog.get((provider, component_id), {})
            component_digest = _canonical_json_sha256(descriptor or component)
            controller_runtime_digest = _canonical_json_sha256({
                "provider": provider,
                "component_id": component_id,
                "descriptor": descriptor,
            })
            identity = {
                "slot_id": slot_id,
                "provider": provider,
                "component_id": component_id,
                "component_digest": component_digest,
                "browser_runtime_digest": component_digest,
                "controller_runtime_digest": controller_runtime_digest,
                "parameter_schema_version": descriptor.get(
                    "parameter_schema_version", 1
                ),
            }
            if provider == "receiver_native":
                build = descriptor.get("build") or {}
                identity["bundle_digest"] = component.get(
                    "bundle_digest", build.get("bundle_digest")
                )
                identity["expected_payload_digest"] = component.get(
                    "expected_payload_digest",
                    build.get("expected_payload_digest"),
                )
            result.append(identity)
        return result

    def _derive_active_identity(self) -> dict[str, Any]:
        status = self._manager_status()
        scene = self._current_scene(status)
        globals_state = self._current_global_settings(status)
        profile = self._current_profile_digest(
            status, boundary="active identity derivation"
        )
        return {
            "scene_identity": (
                None
                if scene is None
                else {"revision": scene["revision"], "digest": _scene_digest(scene)}
            ),
            "component_identities": self._component_identities(scene),
            "global_settings_identity": {
                "revision": globals_state["revision"],
                "digest": _global_settings_digest(globals_state),
            },
            "installation_profile_digest": profile,
        }

    def active_identity(self) -> dict[str, Any]:
        with self._lock:
            return _copy_json(self._active_identity)

    def controller_status(self) -> dict[str, Any]:
        with self._lock:
            records = [
                _copy_json(record.status) for record in self._records.values()
            ]
            return {
                "controller_session_id": self.session_id,
                "controller_state_revision": self._state_revision,
                "active_identity": _copy_json(self._active_identity),
                "current_identity_digest": _canonical_json_sha256(
                    self._active_identity
                ),
                "scene_state": (
                    None
                    if self._selected_scene is None
                    else _copy_json(self._selected_scene)
                ),
                "latest_activation": records[-1] if records else None,
                "activations": records,
            }

    def _flush_publications(
        self, record: _ActivationRecord, *, required: bool
    ) -> bool:
        """Publish staged receipts in order, retaining them across I/O failure.

        Status durability is a transaction precondition before the first
        mutation, but it must never be allowed to prevent compensation after a
        mutation has started.  Keeping the complete phase sequence also lets a
        recovered file sink validate every transition instead of skipping from
        an old durable phase directly to a terminal receipt.
        """

        sink = self._status_sink
        if sink is None:
            record.pending_publications.clear()
            record.publication_error = None
            return True
        while record.pending_publications:
            pending = _copy_json(record.pending_publications[0])
            try:
                sink(pending)
            except Exception as exc:
                record.publication_error = str(exc)
                if required:
                    raise ControllerActivationPublicationError(
                        "activation status publication failed during "
                        f"{pending['phase']}: {exc}"
                    ) from exc
                return False
            del record.pending_publications[0]
        record.publication_error = None
        return True

    def _publish(
        self, record: _ActivationRecord, *, required: bool = True
    ) -> dict[str, Any]:
        public = _copy_json(record.status)
        if (
            not record.pending_publications
            or record.pending_publications[-1] != public
        ):
            record.pending_publications.append(public)
        self._flush_publications(record, required=required)
        return public

    def _set_phase(
        self,
        record: _ActivationRecord,
        phase: str,
        *,
        error: str | None = None,
        publication_required: bool = True,
    ) -> dict[str, Any]:
        record.status["phase"] = phase
        if error is not None:
            record.status["error"] = error
        return self._publish(record, required=publication_required)

    def _trim_records(self) -> None:
        """Bound retained receipts without discarding the live rollback owner."""

        while len(self._records) > self.max_records:
            removable_id = next((
                activation_id
                for activation_id, record in self._records.items()
                if record.historical
                or record.status["phase"] in {
                    "rolled_back", "failed", "timed_out",
                }
            ), None)
            if removable_id is None:
                # A current active receipt and an in-flight replacement may
                # briefly coexist because the former owns the only safe
                # compensation snapshot.  Completion makes one removable.
                break
            del self._records[removable_id]

    def _invalidate_active_rollbacks(
        self, *, reason: str, except_activation_id: str | None = None
    ) -> None:
        """Make every superseded active receipt historical and non-restorable."""

        with self._lock:
            for activation_id, record in tuple(self._records.items()):
                if (
                    activation_id == except_activation_id
                    or record.status["phase"] != "active"
                    or record.historical
                ):
                    continue
                record.snapshot = None
                record.historical = True
                # Keep the immutable snapshot ID in the historical receipt: the
                # status transition contract forbids changing it once issued.
                # `available` is the rollback authority bit.
                record.status["rollback"].update(
                    available=False,
                    result=None,
                    error=reason,
                )
                self._publish(record, required=False)
            self._trim_records()

    def _fault(self, phase: str, boundary: str, activation_id: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(phase, boundary, activation_id)

    def _new_status(self, command: Mapping[str, Any]) -> dict[str, Any]:
        identity = _activation_identity_from_basis(command["basis"])
        return {
            "schema": ACTIVATION_STATUS_SCHEMA,
            "schema_version": ACTIVATION_SCHEMA_VERSION,
            "activation_id": command["activation_id"],
            "basis_digest": command["basis_digest"],
            "command_id": command["activation_id"],
            "phase": "queued",
            "requested_identity": _copy_json(identity),
            "normalized_identity": _copy_json(identity),
            "observed_identity": None,
            "controller": {
                "session_id": self.session_id,
                "state_revision_before": self._state_revision,
                "state_revision_after": None,
            },
            "telemetry": {
                "complete": False,
                "fresh": False,
                "observed_at": None,
            },
            "rollback": {
                "available": False,
                "snapshot_id": None,
                "result": None,
                "error": None,
            },
            "camera_observation": None,
            "error": None,
        }

    def queue(self, command_payload: Any) -> dict[str, Any]:
        command = _normalize_activation_command(
            command_payload,
            catalog=manager_component_catalog(self.manager) or None,
            provider_policy=manager_scene_provider_policy(self.manager),
        )
        activation_id = command["activation_id"]
        with self._lock:
            existing = self._records.get(activation_id)
            if existing is not None:
                if existing.command["basis_digest"] != command["basis_digest"]:
                    raise ControllerActivationConflictError(
                        "activation_id is already bound to another basis"
                    )
                return _copy_json(existing.status)
            record = _ActivationRecord(
                command=_copy_json(command), status=self._new_status(command)
            )
            self._records[activation_id] = record
            self._trim_records()
            return self._publish(record)

    def queue_durable_handoff(
        self, command_payload: Any, durable_status_payload: Any
    ) -> dict[str, Any]:
        """Adopt only the web process's exact, unmutated queued receipt."""

        from ipc.scene_contract import normalize_scene_activation_status

        command = _normalize_activation_command(
            command_payload,
            catalog=manager_component_catalog(self.manager) or None,
            provider_policy=manager_scene_provider_policy(self.manager),
        )
        durable = normalize_scene_activation_status(durable_status_payload)
        with self._lock:
            basis_controller = command["basis"]["controller"]
            current_basis = {
                "session_id": self.session_id,
                "state_revision": self._state_revision,
                "current_identity_digest": _canonical_json_sha256(
                    self._active_identity
                ),
            }
            if basis_controller != current_basis:
                raise ControllerActivationConflictError(
                    "durable queued activation no longer matches current "
                    "controller state"
                )
            expected = self._new_status(command)
            if durable != expected:
                raise ControllerActivationConflictError(
                    "durable queued activation contains non-handoff evidence"
                )
            return self.queue(command)

    def reject_durable_queued(
        self,
        command_payload: Any,
        durable_status_payload: Any,
        *,
        error: str,
    ) -> dict[str, Any]:
        """Close a valid but non-adoptable queued receipt without mutation."""

        from ipc.scene_contract import (
            normalize_scene_activation_status,
            validate_scene_activation_status_transition,
        )

        command = _normalize_activation_command(
            command_payload,
            catalog=manager_component_catalog(self.manager) or None,
            provider_policy=manager_scene_provider_policy(self.manager),
        )
        durable = normalize_scene_activation_status(durable_status_payload)
        if durable["activation_id"] != command["activation_id"]:
            raise ControllerActivationConflictError(
                "durable queued receipt activation_id does not match its command"
            )
        if durable["phase"] != "queued":
            raise ControllerActivationConflictError(
                "only a queued durable receipt can use queued-handoff rejection"
            )
        terminal = _copy_json(durable)
        terminal["phase"] = "failed"
        terminal["error"] = error
        terminal["rollback"].update(
            available=False,
            result=None,
            error="no rollback authority was acquired before rejection",
        )
        terminal = validate_scene_activation_status_transition(durable, terminal)
        record = _ActivationRecord(
            command=_copy_json(command),
            status=terminal,
            snapshot=None,
            historical=True,
        )
        with self._lock:
            self._records[command["activation_id"]] = record
            self._trim_records()
        return self._publish(record, required=False)

    def get(self, activation_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(activation_id)
            if record is None:
                return None
            # The controller loop calls get() for every retained activation.
            # Use that natural retry point to drain any phase receipts retained
            # during a transient filesystem failure.
            self._flush_publications(record, required=False)
            return _copy_json(record.status)

    def reconcile_durable_active(
        self, command_payload: Any, durable_status_payload: Any
    ) -> dict[str, Any]:
        """Reconcile a prior-session active receipt against restored live state.

        Restart loses the in-memory compensation snapshot. A receipt is renewed
        under the new controller session only when the persisted display state,
        globals, profile, and runtime identities all still match exactly.
        """

        from ipc.scene_contract import normalize_scene_activation_status

        command = _normalize_activation_command(
            command_payload,
            catalog=manager_component_catalog(self.manager) or None,
            provider_policy=manager_scene_provider_policy(self.manager),
        )
        durable = normalize_scene_activation_status(durable_status_payload)
        if durable["phase"] != "active":
            return durable
        if (
            durable["activation_id"] != command["activation_id"]
            or durable["basis_digest"] != command["basis_digest"]
        ):
            raise ControllerActivationConflictError(
                "durable activation receipt does not match its command"
            )
        with self._execution_lock:
            existing = self.get(command["activation_id"])
            if existing is not None:
                return existing
            record = _ActivationRecord(
                command=_copy_json(command),
                status=self._new_status(command),
            )
            desired = command["desired"]
            current_status = self._manager_status()
            current_scene = self._current_scene(current_status)
            current_globals = self._current_global_settings(
                current_status,
                revision=desired["global_settings"]["revision"],
            )
            current_profile = self._current_profile_digest(
                current_status, boundary="active restart reconciliation"
            )
            runtimes = manager_controller_runtime_digests(self.manager)
            runtimes_match = all(
                runtimes.get(
                    f"{component['provider']}:{component['component_id']}"
                ) == component["controller_runtime_digest"]
                for component in command["basis"]["components"]
            )
            receiver_evidence = (
                self._receiver_activation_evidence(
                    current_status,
                    desired["scene"],
                    desired["installation_profile_digest"],
                )
                if self._uses_receiver_runtime(desired["scene"])
                else None
            )
            exact = (
                current_scene == desired["scene"]
                and current_globals == desired["global_settings"]
                and current_profile == desired["installation_profile_digest"]
                and runtimes_match
                and (
                    not self._uses_receiver_runtime(desired["scene"])
                    or receiver_evidence is not None
                )
            )
            self._records[command["activation_id"]] = record
            if not exact:
                return self._set_phase(
                    record,
                    "failed",
                    error=(
                        "persisted display state does not match the prior active "
                        "activation after controller restart"
                    ),
                )
            identity = _activation_identity_from_basis(command["basis"])
            self._state_revision += 1
            self._global_settings_revision = desired["global_settings"]["revision"]
            self._selected_scene = _copy_json(desired["scene"])
            self._active_identity = _copy_json(identity)
            record.status["observed_identity"] = _copy_json(identity)
            record.status["controller"]["state_revision_after"] = self._state_revision
            record.status["telemetry"].update(
                complete=True,
                fresh=True,
                observed_at=int(time.time() * 1000),
            )
            # The old process's rollback snapshot cannot be recovered. Never
            # advertise compensation authority after restart reconciliation.
            record.status["rollback"].update(
                available=False,
                snapshot_id=None,
                result=None,
                error=None,
            )
            return self._set_phase(record, "active")

    def reconcile_durable_nonterminal(
        self, command_payload: Any, durable_status_payload: Any
    ) -> dict[str, Any]:
        """Close an interrupted prior-session receipt without replaying mutation."""

        from ipc.scene_contract import (
            normalize_scene_activation_status,
            validate_scene_activation_status_transition,
        )

        command = _normalize_activation_command(
            command_payload,
            catalog=manager_component_catalog(self.manager) or None,
            provider_policy=manager_scene_provider_policy(self.manager),
        )
        durable = normalize_scene_activation_status(durable_status_payload)
        if durable["phase"] in _TERMINAL_ACTIVATION_PHASES:
            return durable
        if (
            durable["activation_id"] != command["activation_id"]
            or durable["basis_digest"] != command["basis_digest"]
        ):
            raise ControllerActivationConflictError(
                "durable activation receipt does not match its command"
            )

        desired = command["desired"]
        current_status = self._manager_status()
        current_scene = self._current_scene(current_status)
        current_globals = self._current_global_settings(
            current_status, revision=desired["global_settings"]["revision"]
        )
        current_profile = self._current_profile_digest(
            current_status, boundary="nonterminal restart reconciliation"
        )
        runtimes = manager_controller_runtime_digests(self.manager)
        runtimes_match = all(
            runtimes.get(f"{item['provider']}:{item['component_id']}")
            == item["controller_runtime_digest"]
            for item in command["basis"]["components"]
        )
        receiver_proven = bool(
            not self._uses_receiver_runtime(desired["scene"])
            or self._receiver_activation_evidence(
                current_status,
                desired["scene"],
                desired["installation_profile_digest"],
            ) is not None
        )
        desired_is_current = bool(
            current_scene == desired["scene"]
            and current_globals == desired["global_settings"]
            and current_profile == desired["installation_profile_digest"]
            and runtimes_match
            and receiver_proven
        )
        terminal = _copy_json(durable)
        terminal["phase"] = "failed"
        terminal["error"] = (
            "controller restarted during activation; restored state matches the "
            "requested identity but the prior observation and rollback snapshot "
            "cannot be renewed"
            if desired_is_current
            else "controller restarted during activation; requested identity is not "
            "the exact restored state"
        )
        terminal["rollback"].update(
            available=False,
            result=None,
            error="rollback snapshot authority was lost on controller restart",
        )
        terminal = validate_scene_activation_status_transition(durable, terminal)
        record = _ActivationRecord(
            command=_copy_json(command),
            status=terminal,
            snapshot=None,
            historical=True,
        )
        with self._lock:
            self._records[command["activation_id"]] = record
            self._trim_records()
        return self._publish(record, required=False)

    def cancel(self, activation_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._records.get(activation_id)
            if record is None:
                raise KeyError(activation_id)
            phase = record.status["phase"]
            if phase in _TERMINAL_ACTIVATION_PHASES:
                return _copy_json(record.status)
            if phase not in {"queued", "preflighting"}:
                raise ControllerActivationConflictError(
                    "activation can be cancelled only while queued or preflighting"
                )
            record.cancel_requested = True
            if phase == "queued":
                return self._set_phase(
                    record, "failed", error="activation cancelled before mutation"
                )
            return self._publish(record)

    def _check_cancelled(self, record: _ActivationRecord) -> None:
        if self._cancel_probe is not None and self._cancel_probe(
            record.command["activation_id"]
        ):
            record.cancel_requested = True
        if record.cancel_requested:
            raise ControllerActivationCancelled(
                "activation cancelled before mutation"
            )

    def _empty_receiver_profile_noop_allowed(
        self,
        *,
        current_scene: Mapping[str, Any] | None,
        desired_scene: Mapping[str, Any] | None,
        current_profile: str,
        desired_profile: str,
    ) -> bool:
        """Return the one physical-controller case requiring no profile I/O."""

        controller = getattr(self.manager, "controller", None)
        return bool(
            current_profile == EMPTY_INSTALLATION_PROFILE_DIGEST
            and desired_profile == EMPTY_INSTALLATION_PROFILE_DIGEST
            and not self._uses_receiver_runtime(current_scene)
            and not self._uses_receiver_runtime(desired_scene)
            and getattr(
                controller, "_receiver_geometry_profile_enabled", None
            ) is False
            and hasattr(controller, "_installation_profile_wall")
            and getattr(controller, "_installation_profile_wall") is None
        )

    def _receiver_profile_authority_is_host_only(self) -> bool:
        controller = getattr(self.manager, "controller", None)
        return bool(
            not hasattr(controller, "installation_profile_wall")
            and not hasattr(controller, "install_installation_profile")
            and not hasattr(controller, "_receiver_geometry_profile_enabled")
            and not hasattr(controller, "_installation_profile_wall")
        )

    def _current_profile_digest(
        self, status: Mapping[str, Any], *, boundary: str
    ) -> str:
        if "installation_profile_digest" not in status:
            if self._receiver_profile_authority_is_host_only():
                return EMPTY_INSTALLATION_PROFILE_DIGEST
            raise ControllerActivationValidationError(
                f"current installation_profile_digest is missing during {boundary}"
            )
        profile = _strict_digest(
            status["installation_profile_digest"],
            f"current installation_profile_digest during {boundary}",
        )
        assert isinstance(profile, str)
        return profile

    def _capture_receiver_profile_snapshot(
        self, *, receiver_profile_noop: bool = False
    ) -> tuple[Any, tuple[Any, ...]]:
        if receiver_profile_noop:
            return None, ()
        controller = getattr(self.manager, "controller", None)
        getter = getattr(controller, "installation_profile_wall", None)
        if not callable(getter):
            return None, ()
        try:
            wall = getter()
            snapshots = tuple(
                receiver.transaction_snapshot() for receiver in wall.receivers
            )
        except (RuntimeError, OSError) as exc:
            raise ControllerActivationError(
                f"could not capture exact receiver profile snapshot: {exc}"
            ) from exc
        return wall, snapshots

    def _preflight_empty_receiver_profile(self) -> None:
        """Reject receiver deactivation until an exact clear transaction exists."""

        controller = getattr(self.manager, "controller", None)
        getter = getattr(controller, "installation_profile_wall", None)
        if not callable(getter):
            return
        try:
            wall = getter()
            snapshots = tuple(
                receiver.transaction_snapshot() for receiver in wall.receivers
            )
        except (RuntimeError, OSError) as exc:
            raise ControllerActivationValidationError(
                f"cannot verify an empty receiver profile transition: {exc}"
            ) from exc
        if any(getattr(snapshot, "active_binding", None) is not None for snapshot in snapshots):
            raise ControllerActivationValidationError(
                "receiver profile deactivation has no exact clear transaction"
            )

    def _receiver_profile_context(self, profile_digest: str):
        """Return exact receiver authority, or None for a host-only controller."""

        controller = getattr(self.manager, "controller", None)
        getter = getattr(controller, "installation_profile_wall", None)
        installer = getattr(controller, "install_installation_profile", None)
        if not callable(getter) and not callable(installer):
            return None
        if not callable(getter) or not callable(installer):
            raise ControllerActivationValidationError(
                "receiver profile capability lacks exact transaction authority"
            )
        try:
            wall = getter()
        except (RuntimeError, OSError) as exc:
            raise ControllerActivationValidationError(
                f"receiver profile authority is unavailable: {exc}"
            ) from exc
        resolver = getattr(
            self.manager, "resolve_installation_profile_candidate", None
        )
        if callable(resolver):
            candidate = resolver(profile_digest)
        else:
            library = getattr(self.manager, "_installation_profile_library", None)
            topology = getattr(self.manager, "_installation_profile_topology", None)
            if library is None or topology is None:
                raise ControllerActivationValidationError(
                    "receiver profile capability lacks managed library authority"
                )
            from animation.core.installation_profile_transaction import (
                candidate_from_resolved,
            )

            candidate = candidate_from_resolved(
                library.resolve(profile_digest, topology)
            )
        return wall, installer, candidate

    @staticmethod
    def _receiver_profile_is_exact(wall: Any, candidate: Any) -> bool:
        try:
            wall_status = wall.status()
            if (
                getattr(wall_status, "healthy", False) is not True
                or getattr(wall_status, "active_profile_id", None)
                != candidate.profile_id
            ):
                return False
            return all(
                receiver.transaction_snapshot().active_binding
                == candidate.binding_for(receiver_id)
                for receiver_id, receiver in enumerate(wall.receivers)
            )
        except (AttributeError, RuntimeError, OSError):
            return False

    def _receiver_profile_matches(self, profile_digest: str) -> bool:
        if profile_digest == EMPTY_INSTALLATION_PROFILE_DIGEST:
            controller = getattr(self.manager, "controller", None)
            getter = getattr(controller, "installation_profile_wall", None)
            if not callable(getter):
                return not callable(
                    getattr(controller, "install_installation_profile", None)
                )
            try:
                wall = getter()
                status = wall.status()
                return (
                    getattr(status, "active_profile_id", None) is None
                    and all(
                        receiver.transaction_snapshot().active_binding is None
                        for receiver in wall.receivers
                    )
                )
            except (AttributeError, RuntimeError, OSError):
                return False
        context = self._receiver_profile_context(profile_digest)
        if context is None:
            return True
        wall, _installer, candidate = context
        return self._receiver_profile_is_exact(wall, candidate)

    @staticmethod
    def _uses_receiver_runtime(scene: Mapping[str, Any] | None) -> bool:
        return bool(
            isinstance(scene, Mapping)
            and isinstance(scene.get("background"), Mapping)
            and scene["background"].get("provider") == "receiver_native"
        )

    @staticmethod
    def _receiver_publication_evidence(
        status: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Return one fresh-capable publisher sample without assuming identity."""

        receiver = status.get("receiver_hybrid")
        if not isinstance(receiver, Mapping):
            return None
        publisher = receiver.get("publisher")
        if not isinstance(publisher, Mapping):
            return None
        binding = publisher.get("binding")
        if not isinstance(binding, Mapping):
            return None
        context_revision = receiver.get("context_revision")
        context_digest = receiver.get("context_digest")
        session_id = publisher.get("controller_session_id")
        generation = publisher.get("generation")
        last_success_at = publisher.get("last_success_at")
        if not (
            receiver.get("healthy") is True
            and receiver.get("telemetry_complete") is True
            and type(context_revision) is int
            and context_revision >= 0
            and isinstance(context_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", context_digest) is not None
            and publisher.get("healthy") is True
            and publisher.get("active") is True
            and publisher.get("authority_known") is True
            and publisher.get("repair_required") is False
            and isinstance(session_id, str)
            and re.fullmatch(r"[0-9a-f]{32}", session_id) is not None
            and type(generation) is int
            and generation > 0
            and isinstance(last_success_at, (int, float))
            and not isinstance(last_success_at, bool)
            and math.isfinite(float(last_success_at))
        ):
            return None
        return {
            "source_scene_revision": receiver.get("source_scene_revision"),
            "binding_scene_revision": binding.get("scene_revision"),
            "context_revision": context_revision,
            "context_digest": context_digest,
            "publisher_session_id": session_id,
            "publisher_generation": generation,
            "publisher_last_success_at": float(last_success_at),
        }

    @staticmethod
    def _receiver_activation_evidence(
        status: Mapping[str, Any],
        scene: Mapping[str, Any],
        installation_profile_digest: str,
    ) -> dict[str, Any] | None:
        """Return exact fresh receiver/runtime evidence for one desired scene."""

        sample = ControllerActivationCoordinator._receiver_publication_evidence(
            status
        )
        receiver = status.get("receiver_hybrid")
        background = scene.get("background")
        if (
            sample is None
            or not isinstance(receiver, Mapping)
            or not isinstance(background, Mapping)
            or background.get("provider") != "receiver_native"
        ):
            return None
        revision = scene.get("revision")
        if (
            sample["source_scene_revision"] != revision
            or sample["binding_scene_revision"] != revision
        ):
            return None
        driver = receiver.get("driver")
        resolved_parameters = background.get("resolved_parameters")
        overrides = background.get("parameter_overrides")
        if (
            not isinstance(driver, Mapping)
            or not isinstance(resolved_parameters, Mapping)
            or not isinstance(overrides, Mapping)
        ):
            return None
        effective_parameters = dict(resolved_parameters)
        effective_parameters.update(overrides)
        bundle_digest = background.get("bundle_digest")
        payload_digest = background.get("expected_payload_digest")
        parameter_digest = driver.get("parameter_digest")
        if not (
            driver.get("state") == "active"
            and isinstance(bundle_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", bundle_digest) is not None
            and driver.get("bundle_digest") == bundle_digest
            and isinstance(payload_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", payload_digest) is not None
            and driver.get("payload_digest") == payload_digest
            and driver.get("effective_parameters") == effective_parameters
            and isinstance(parameter_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", parameter_digest) is not None
            and driver.get("context_digest") == sample["context_digest"]
            and driver.get("installation_profile_digest")
            == installation_profile_digest
        ):
            return None
        return {
            **sample,
            "driver_bundle_digest": bundle_digest,
            "driver_payload_digest": payload_digest,
            "driver_parameter_digest": parameter_digest,
            "driver_effective_parameters": _copy_json(effective_parameters),
            "driver_installation_profile_digest": installation_profile_digest,
        }

    @staticmethod
    def _receiver_evidence_advanced(
        before: Mapping[str, Any] | None, after: Mapping[str, Any]
    ) -> bool:
        if before is None:
            return True
        if after["context_revision"] != before.get("context_revision"):
            return True
        if after["publisher_session_id"] != before.get("publisher_session_id"):
            return True
        if after["publisher_generation"] > before.get("publisher_generation", -1):
            return True
        return (
            after["publisher_last_success_at"]
            > before.get("publisher_last_success_at", float("-inf"))
        )

    def _snapshot(
        self, *, receiver_profile_noop: bool = False
    ) -> ControllerStateSnapshot:
        status = self._manager_status()
        scene = self._current_scene(status)
        globals_state = self._current_global_settings(status)
        if globals_state["output"]["power"] and scene is None:
            raise ControllerActivationValidationError(
                "current painter or legacy animation state cannot be restored exactly"
            )
        profile = self._current_profile_digest(
            status, boundary="snapshot"
        )
        if receiver_profile_noop and not self._empty_receiver_profile_noop_allowed(
            current_scene=scene,
            desired_scene=scene,
            current_profile=profile,
            desired_profile=profile,
        ):
            raise ControllerActivationConflictError(
                "empty receiver profile no-op authority changed before snapshot"
            )
        wall, receiver_snapshots = self._capture_receiver_profile_snapshot(
            receiver_profile_noop=receiver_profile_noop
        )
        body = {
            "state_revision": self._state_revision,
            "active_identity": self._active_identity,
            "scene": scene,
            "global_settings": globals_state,
            "installation_profile_digest": profile,
            "receiver_profile_noop": receiver_profile_noop,
        }
        return ControllerStateSnapshot(
            snapshot_id=_canonical_json_sha256(body),
            state_revision=self._state_revision,
            active_identity=_copy_json(self._active_identity),
            scene=None if scene is None else _copy_json(scene),
            global_settings=_copy_json(globals_state),
            installation_profile_digest=profile,
            receiver_profile_noop=receiver_profile_noop,
            receiver_profile_wall=wall,
            receiver_profile_snapshots=receiver_snapshots,
        )

    def _preflight(
        self, record: _ActivationRecord
    ) -> tuple[dict[str, Any], dict[str, Any], str, bool]:
        command = record.command
        activation_id = command["activation_id"]
        basis = command["basis"]
        desired = command["desired"]
        if basis["qualification"]["expires_at"] <= self._wall_clock_ms():
            raise ControllerActivationTimedOut(
                "activation Check expired before controller preflight"
            )
        controller_basis = basis.get("controller")
        if not isinstance(controller_basis, Mapping):
            raise ControllerActivationValidationError(
                "activation basis.controller must be an object"
            )
        if controller_basis.get("session_id") != self.session_id:
            raise ControllerActivationConflictError(
                "controller session changed after Check"
            )
        if controller_basis.get("state_revision") != self._state_revision:
            raise ControllerActivationConflictError(
                "controller state revision changed after Check"
            )
        expected_current = controller_basis.get("current_identity_digest")
        if expected_current != _canonical_json_sha256(self._active_identity):
            raise ControllerActivationConflictError(
                "controller active identity changed after Check"
            )
        current_runtimes = manager_controller_runtime_digests(self.manager)
        for component in basis.get("components", ()):
            qualified_id = (
                f"{component['provider']}:{component['component_id']}"
            )
            if (
                current_runtimes.get(qualified_id)
                != component.get("controller_runtime_digest")
            ):
                raise ControllerActivationConflictError(
                    f"controller runtime changed after Check for {qualified_id}"
                )
        self._fault("preflighting", "after_cas", activation_id)
        self._check_cancelled(record)

        scene = normalize_scene_payload(
            desired["scene"],
            catalog=manager_component_catalog(self.manager) or None,
            provider_policy=manager_scene_provider_policy(self.manager),
        )
        self._fault("preflighting", "scene", activation_id)
        scene_preflight = getattr(self.manager, "preflight_scene", None)
        if callable(scene_preflight):
            scene_preflight(scene)
        self._check_cancelled(record)

        globals_state = _normalize_global_settings(desired["global_settings"])
        output = globals_state["output"]
        validator = getattr(self.manager, "validate_output_brightness", None)
        if callable(validator):
            validator(output["brightness"])
        tempo_validator = getattr(self.manager, "_validate_tempo_scale", None)
        if callable(tempo_validator):
            tempo_validator(output["animation_speed_scale"])
        self._fault("preflighting", "global_settings", activation_id)
        self._check_cancelled(record)

        profile = desired["installation_profile_digest"]
        current_status = self._manager_status()
        current_profile = self._current_profile_digest(
            current_status, boundary="preflight"
        )
        receiver_profile_noop = self._empty_receiver_profile_noop_allowed(
            current_scene=self._current_scene(current_status),
            desired_scene=scene,
            current_profile=current_profile,
            desired_profile=profile,
        )
        profile_preflight = getattr(
            self.manager, "preflight_installation_profile", None
        )
        if callable(profile_preflight):
            profile_preflight(profile)
        elif profile != EMPTY_INSTALLATION_PROFILE_DIGEST:
            raise ControllerActivationValidationError(
                "manager cannot preflight the desired installation profile"
            )
        if profile == EMPTY_INSTALLATION_PROFILE_DIGEST:
            if not receiver_profile_noop:
                self._preflight_empty_receiver_profile()
        else:
            # Resolve all receiver transaction authority before snapshot/mutation.
            self._receiver_profile_context(profile)
        self._fault("preflighting", "installation_profile", activation_id)
        self._check_cancelled(record)

        expected_identity = _activation_identity_from_basis(basis)
        if expected_identity["scene_identity"]["digest"] != _scene_digest(scene):
            raise ControllerActivationValidationError(
                "desired scene does not match the checked scene identity"
            )
        if (
            expected_identity["global_settings_identity"]["digest"]
            != _global_settings_digest(globals_state)
        ):
            raise ControllerActivationValidationError(
                "desired global settings do not match the checked identity"
            )
        if expected_identity["installation_profile_digest"] != profile:
            raise ControllerActivationValidationError(
                "desired installation profile does not match the checked identity"
            )
        self._fault("preflighting", "complete", activation_id)
        self._check_cancelled(record)
        return scene, globals_state, profile, receiver_profile_noop

    def _install_receiver_profile(self, profile_digest: str) -> None:
        if profile_digest == EMPTY_INSTALLATION_PROFILE_DIGEST:
            return
        context = self._receiver_profile_context(profile_digest)
        if context is None:
            return
        wall, installer, candidate = context
        result = installer(candidate)
        if getattr(result, "success", False) is not True:
            raise ControllerActivationError(
                getattr(result, "error", None)
                or "receiver installation-profile transaction failed"
            )
        if (
            getattr(result, "profile_id", None) != candidate.profile_id
            or not self._receiver_profile_is_exact(wall, candidate)
        ):
            raise ControllerActivationError(
                "receiver installation-profile identity proof is stale or incomplete"
            )

    def _apply_profile(
        self,
        profile_digest: str,
        *,
        desired_scene: Mapping[str, Any] | None,
        receiver_profile_noop: bool = False,
    ) -> None:
        current_status = self._manager_status()
        current = self._current_profile_digest(
            current_status, boundary="apply"
        )
        if receiver_profile_noop:
            if not self._empty_receiver_profile_noop_allowed(
                current_scene=self._current_scene(current_status),
                desired_scene=desired_scene,
                current_profile=current,
                desired_profile=profile_digest,
            ):
                raise ControllerActivationConflictError(
                    "empty receiver profile no-op authority changed after preflight"
                )
            return
        # Receiver-capable controllers must prove/rebind the exact generation
        # even when the host selection already names the desired digest.
        self._install_receiver_profile(profile_digest)
        if current == profile_digest:
            if not self._receiver_profile_matches(profile_digest):
                raise ControllerActivationError(
                    "receiver installation profile does not match host selection"
                )
            return
        selector = getattr(self.manager, "select_installation_profile", None)
        if not callable(selector):
            if profile_digest != EMPTY_INSTALLATION_PROFILE_DIGEST:
                raise ControllerActivationError(
                    "manager cannot select the desired installation profile"
                )
            return
        selected = selector(profile_digest)
        if isinstance(selected, Mapping):
            observed = selected.get(
                "selected_digest", selected.get("profile_digest")
            )
            if observed is not None and observed != profile_digest:
                raise ControllerActivationError(
                    "manager selected a different installation profile"
                )

    def _apply_state(
        self,
        scene: Mapping[str, Any] | None,
        globals_state: Mapping[str, Any],
        profile_digest: str,
        *,
        activation_id: str,
        inject_faults: bool,
        receiver_profile_noop: bool = False,
    ) -> None:
        def boundary(name: str) -> None:
            if inject_faults:
                self._fault("applying", name, activation_id)

        self._apply_profile(
            profile_digest,
            desired_scene=scene,
            receiver_profile_noop=receiver_profile_noop,
        )
        boundary("installation_profile")
        self.manager.set_plant_modifiers(globals_state["plant_modifiers"])
        boundary("plant_modifiers")
        self.manager.set_vibe(globals_state["vibe"])
        boundary("vibe")
        output = globals_state["output"]
        self.manager.set_animation_speed_scale(output["animation_speed_scale"])
        boundary("animation_speed_scale")
        self.manager.set_target_fps(output["target_fps"])
        boundary("target_fps")
        self.manager.set_output_brightness(output["brightness"])
        boundary("brightness")
        if output["power"]:
            if scene is None:
                raise ControllerActivationError(
                    "powered desired state requires a complete scene"
                )
            if not start_scene(self.manager, dict(scene)):
                raise ControllerActivationError("manager rejected desired scene")
            self._selected_scene = _copy_json(scene)
        else:
            self._selected_scene = None if scene is None else _copy_json(scene)
            self.manager.stop_animation()
        boundary("scene")
        boundary("complete")

    def _restore_receiver_profile_snapshot(
        self, snapshot: ControllerStateSnapshot
    ) -> None:
        if snapshot.receiver_profile_wall is None:
            return
        controller = getattr(self.manager, "controller", None)
        guard = getattr(controller, "_controller_lock", None)
        context = guard() if callable(guard) else nullcontext()
        with context:
            errors = []
            for receiver_id, (receiver, prior) in enumerate(zip(
                snapshot.receiver_profile_wall.receivers,
                snapshot.receiver_profile_snapshots,
            )):
                try:
                    receiver.compensate_profile(prior)
                except (RuntimeError, OSError) as exc:
                    errors.append(f"receiver {receiver_id}: {exc}")
            for receiver_id, (receiver, prior) in enumerate(zip(
                snapshot.receiver_profile_wall.receivers,
                snapshot.receiver_profile_snapshots,
            )):
                try:
                    if receiver.transaction_snapshot() != prior:
                        errors.append(
                            f"receiver {receiver_id}: restored binding snapshot differs"
                        )
                except (RuntimeError, OSError) as exc:
                    errors.append(f"receiver {receiver_id}: verify failed: {exc}")
            if errors:
                raise ControllerActivationError("; ".join(errors))

    def _observed_identity(
        self,
        record: _ActivationRecord,
        scene: Mapping[str, Any],
        globals_state: Mapping[str, Any],
        profile_digest: str,
        *,
        receiver_profile_noop: bool = False,
    ) -> tuple[dict[str, Any], bool, bool]:
        status = self._manager_status()
        observed_scene = self._current_scene(status)
        observed_profile = self._current_profile_digest(
            status, boundary="observation"
        )
        if receiver_profile_noop and not self._empty_receiver_profile_noop_allowed(
            current_scene=observed_scene,
            desired_scene=scene,
            current_profile=observed_profile,
            desired_profile=profile_digest,
        ):
            return self._derive_active_identity(), False, True
        if self._observer is not None:
            raw = self._observer(record.command)
            if raw is None:
                raise ControllerActivationTimedOut("activation observation is unavailable")
            if not isinstance(raw, Mapping):
                raise ControllerActivationError("activation observer returned invalid data")
            identity = raw.get("identity", raw.get("observed_identity"))
            if not isinstance(identity, Mapping):
                raise ControllerActivationError(
                    "activation observer omitted observed identity"
                )
            return (
                dict(identity),
                bool(raw.get("telemetry_complete", raw.get("complete", False))),
                bool(raw.get("telemetry_fresh", raw.get("fresh", False))),
            )

        observed_globals = self._current_global_settings(
            status, revision=globals_state["revision"]
        )
        if (
            observed_scene != scene
            or observed_globals != globals_state
            or observed_profile != profile_digest
            or (
                not receiver_profile_noop
                and not self._receiver_profile_matches(profile_digest)
            )
        ):
            return self._derive_active_identity(), False, True
        if self._uses_receiver_runtime(scene):
            evidence = self._receiver_activation_evidence(
                status, scene, profile_digest
            )
            complete = evidence is not None
            fresh = bool(
                evidence is not None
                and self._receiver_evidence_advanced(
                    record.receiver_evidence_before, evidence
                )
            )
        else:
            # A host Python scene is observed synchronously by the controller.
            complete = True
            fresh = True
        return _activation_identity_from_basis(record.command["basis"]), complete, fresh

    def _observe(
        self,
        record: _ActivationRecord,
        scene: Mapping[str, Any],
        globals_state: Mapping[str, Any],
        profile_digest: str,
        *,
        receiver_profile_noop: bool = False,
    ) -> dict[str, Any]:
        deadline = self._clock() + self.observation_timeout
        expected = record.status["normalized_identity"]
        while True:
            self._fault("observing", "poll", record.command["activation_id"])
            observed, complete, fresh = self._observed_identity(
                record,
                scene,
                globals_state,
                profile_digest,
                receiver_profile_noop=receiver_profile_noop,
            )
            if observed == expected and complete and fresh:
                self._fault("observing", "matched", record.command["activation_id"])
                return observed
            if self._clock() >= deadline:
                raise ControllerActivationTimedOut(
                    "desired activation was not freshly observed before timeout"
                )
            self._sleep(self.observation_interval)

    def _rollback(
        self, record: _ActivationRecord, snapshot: ControllerStateSnapshot
    ) -> bool:
        record.status["rollback"].update(
            available=True, snapshot_id=snapshot.snapshot_id
        )
        # Publication is still staged and retried, but compensation must run
        # even when the status filesystem is unavailable.
        self._set_phase(
            record, "rolling_back", publication_required=False
        )
        try:
            self._fault("rolling_back", "before_restore", record.command["activation_id"])
            self._apply_state(
                snapshot.scene,
                snapshot.global_settings,
                snapshot.installation_profile_digest,
                activation_id=record.command["activation_id"],
                inject_faults=False,
                receiver_profile_noop=snapshot.receiver_profile_noop,
            )
            self._restore_receiver_profile_snapshot(snapshot)
            prior_global_revision = self._global_settings_revision
            self._global_settings_revision = snapshot.global_settings["revision"]
            current = self._snapshot(
                receiver_profile_noop=snapshot.receiver_profile_noop
            )
            if (
                current.scene != snapshot.scene
                or current.global_settings != snapshot.global_settings
                or current.installation_profile_digest
                != snapshot.installation_profile_digest
            ):
                raise ControllerActivationError(
                    "post-rollback controller snapshot differs from prior state"
                )
            self._active_identity = _copy_json(snapshot.active_identity)
            self._global_settings_revision = snapshot.global_settings["revision"]
            self._state_revision += 1
            record.status["observed_identity"] = (
                _status_activation_identity_or_none(snapshot.active_identity)
            )
            record.status["controller"]["state_revision_after"] = (
                self._state_revision
            )
            record.status["telemetry"].update(
                complete=True,
                fresh=True,
                observed_at=int(time.time() * 1000),
            )
            record.status["rollback"].update(result="succeeded", error=None)
        except Exception as exc:
            if "prior_global_revision" in locals():
                self._global_settings_revision = prior_global_revision
            record.status["rollback"].update(result="failed", error=str(exc))
            # A failed rollback invalidates every outstanding Check even though
            # the desired activation never became active.
            self._state_revision += 1
            self._global_settings_revision = self._state_revision
            try:
                self._active_identity = self._derive_active_identity()
            except Exception as identity_exc:
                # Authority loss must not escape before the terminal receipt.
                # Publish a unique, non-activation CAS identity so every prior
                # Check is invalid and every new activation still encounters
                # the explicit current-profile validation before mutation.
                self._active_identity = {
                    "authority": "unavailable",
                    "state_revision": self._state_revision,
                    "error_digest": _canonical_json_sha256({
                        "rollback_error": str(exc),
                        "identity_error": str(identity_exc),
                    }),
                }
            return False
        if self._commit_callback is not None:
            try:
                # Compensation is not crash-safe until the restored controller
                # state replaces any desired state persisted before Active.
                # Keep this barrier ahead of every terminal rollback receipt.
                self._commit_callback()
            except Exception as exc:
                record.status["rollback"].update(result="failed", error=str(exc))
                return False
        return True

    def execute(self, activation_id: str) -> dict[str, Any]:
        with self._execution_lock:
            with self._lock:
                record = self._records.get(activation_id)
                if record is None:
                    raise KeyError(activation_id)
                if record.status["phase"] in _TERMINAL_ACTIVATION_PHASES:
                    return _copy_json(record.status)
                if record.status["phase"] != "queued":
                    return _copy_json(record.status)
            mutation_started = False
            try:
                self._set_phase(record, "preflighting")
                (
                    scene,
                    globals_state,
                    profile,
                    receiver_profile_noop,
                ) = self._preflight(record)
                snapshot = self._snapshot(
                    receiver_profile_noop=receiver_profile_noop
                )
                record.snapshot = snapshot
                if self._uses_receiver_runtime(scene):
                    record.receiver_evidence_before = (
                        self._receiver_publication_evidence(
                            self._manager_status()
                        )
                    )
                record.status["rollback"].update(
                    available=True, snapshot_id=snapshot.snapshot_id
                )
                self._check_cancelled(record)
                self._set_phase(record, "applying")
                mutation_started = True
                self._apply_state(
                    scene,
                    globals_state,
                    profile,
                    activation_id=activation_id,
                    inject_faults=True,
                    receiver_profile_noop=receiver_profile_noop,
                )
                self._set_phase(record, "observing")
                observed = self._observe(
                    record,
                    scene,
                    globals_state,
                    profile,
                    receiver_profile_noop=receiver_profile_noop,
                )
                self._state_revision += 1
                self._global_settings_revision = globals_state["revision"]
                self._active_identity = _copy_json(observed)
                record.status["observed_identity"] = _copy_json(observed)
                record.status["controller"]["state_revision_after"] = (
                    self._state_revision
                )
                record.status["telemetry"].update(
                    complete=True,
                    fresh=True,
                    observed_at=int(time.time() * 1000),
                )
                record.status["rollback"].update(
                    available=True, result=None, error=None
                )
                if self._commit_callback is not None:
                    # A web client must never observe durable Active before the
                    # controller can restore that exact state after restart.
                    self._commit_callback()
                active = self._set_phase(record, "active")
                self._invalidate_active_rollbacks(
                    reason="superseded by a later guarded activation",
                    except_activation_id=activation_id,
                )
                return active
            except ControllerActivationCancelled as exc:
                return self._set_phase(
                    record,
                    "failed",
                    error=str(exc),
                    publication_required=False,
                )
            except Exception as exc:
                timed_out = isinstance(exc, (ControllerActivationTimedOut, TimeoutError))
                if mutation_started and record.snapshot is not None:
                    rolled_back = self._rollback(record, record.snapshot)
                    terminal = "timed_out" if timed_out else (
                        "rolled_back" if rolled_back else "failed"
                    )
                else:
                    terminal = "timed_out" if timed_out else "failed"
                # A terminal receipt is retained for later ordered retry.  Once
                # mutation has started, a second publication failure must not
                # escape before or after compensation.
                return self._set_phase(
                    record,
                    terminal,
                    error=str(exc),
                    publication_required=False,
                )

    def rollback(
        self,
        activation_id: str,
        *,
        snapshot_id: str,
        expected_session_id: str,
        expected_state_revision: int,
    ) -> dict[str, Any]:
        """Restore one retained activation snapshot as a guarded transaction."""

        with self._execution_lock:
            with self._lock:
                record = self._records.get(activation_id)
                if record is None:
                    raise KeyError(activation_id)
                if record.status["phase"] == "rolled_back":
                    return _copy_json(record.status)
                if record.status["phase"] != "active":
                    raise ControllerActivationConflictError(
                        "only an active activation with a retained snapshot can roll back"
                    )
                if expected_session_id != self.session_id:
                    raise ControllerActivationConflictError(
                        "controller session changed before rollback"
                    )
                if expected_state_revision != self._state_revision:
                    raise ControllerActivationConflictError(
                        "controller state revision changed before rollback"
                    )
                if record.snapshot is None:
                    raise ControllerActivationConflictError(
                        "active activation rollback snapshot is no longer retained"
                    )
                if snapshot_id != record.snapshot.snapshot_id:
                    raise ControllerActivationConflictError(
                        "rollback snapshot identity does not match"
                    )
                snapshot = record.snapshot
            restored = self._rollback(record, snapshot)
            if not restored:
                return self._set_phase(
                    record,
                    "failed",
                    error="exact activation rollback failed",
                    publication_required=False,
                )
            record.status["error"] = None
            return self._set_phase(
                record, "rolled_back", publication_required=False
            )

    def activate(self, command_payload: Any) -> dict[str, Any]:
        status = self.queue(command_payload)
        if status["phase"] != "queued":
            return status
        return self.execute(status["activation_id"])

    def note_legacy_mutation(self) -> None:
        """Invalidate checked bases after a successful non-activation mutation."""

        with self._lock:
            self._state_revision += 1
            self._global_settings_revision = self._state_revision
            self._active_identity = self._derive_active_identity()
            self._invalidate_active_rollbacks(
                reason="superseded by a later controller mutation"
            )

    @contextmanager
    def legacy_mutation_guard(self):
        """Serialize legacy writes and invalidate checked bases on any change."""

        with self._execution_lock:
            before_status = self._manager_status()
            before_power = bool(
                before_status.get("is_running", False)
                or before_status.get("painter_active", False)
            )
            before = self._derive_active_identity()
            completed = False
            try:
                yield
                completed = True
            finally:
                after_status = self._manager_status()
                after_live_scene = self._live_scene(after_status)
                after_power = bool(
                    after_status.get("is_running", False)
                    or after_status.get("painter_active", False)
                )
                if after_live_scene is not None:
                    self._selected_scene = after_live_scene
                elif after_power or before_power != after_power:
                    self._selected_scene = None
                after = self._derive_active_identity()
                if completed or after != before:
                    self.note_legacy_mutation()


def controller_activation_coordinator(
    manager: Any,
    *,
    status_sink: Callable[[dict[str, Any]], None] | None = None,
    restored_selected_scene: Mapping[str, Any] | None = None,
    cancel_probe: Callable[[str], bool] | None = None,
    commit_callback: Callable[[], None] | None = None,
) -> ControllerActivationCoordinator:
    """Return the one process-local coordinator attached to a manager."""

    coordinator = getattr(manager, "_controller_activation_coordinator", None)
    if not isinstance(coordinator, ControllerActivationCoordinator):
        coordinator = ControllerActivationCoordinator(
            manager,
            status_sink=status_sink,
            restored_selected_scene=restored_selected_scene,
            cancel_probe=cancel_probe,
            commit_callback=commit_callback,
        )
        setattr(manager, "_controller_activation_coordinator", coordinator)
    elif status_sink is not None:
        coordinator.set_status_sink(status_sink)
    if commit_callback is not None:
        coordinator.set_commit_callback(commit_callback)
    return coordinator


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


def manager_controller_runtime_digests(manager: Any) -> dict[str, str]:
    """Resolve the runtime identities using the same managed-catalog priority."""

    from ipc.scene_contract import component_contract_digest

    result: dict[str, str] = {}
    for component in manager_component_catalog(manager):
        if not isinstance(component, Mapping):
            continue
        provider = component.get("provider", "python")
        component_id = component.get("plugin_id")
        if not all(isinstance(value, str) and value for value in (
            provider, component_id
        )):
            continue
        build = component.get("build")
        build = build if isinstance(build, Mapping) else {}
        capabilities = component.get("browser_capabilities")
        capabilities = capabilities if isinstance(capabilities, Mapping) else {}
        identity = capabilities.get("managed_identity")
        identity = identity if isinstance(identity, Mapping) else {}
        candidates = (
            component.get("controller_runtime_digest"),
            build.get("expected_payload_digest"),
            build.get("bundle_digest"),
            build.get("contract_digest"),
            component.get("component_digest"),
            identity.get("component_digest"),
        )
        digest = next((
            value for value in candidates
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
        ), None)
        if digest is None:
            try:
                digest = component_contract_digest(component)
            except (TypeError, ValueError):
                continue
        result[f"{provider}:{component_id}"] = digest
    return result


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
