"""Product-facing validation helpers for the fixed Phase 2C scene editor.

The controller manager owns component construction and lifecycle.  This module
owns the untrusted JSON boundary shared by the web API, file IPC, and deployment
restore.  Keeping it free of plugin imports makes validation deterministic and
prevents a catalog request from executing animation implementation code.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, Iterable, Optional

from animation.component_parameters import SCENE_EXTERNAL_COMPONENT_PARAMETERS
from animation.core.presentation_contracts import component_preset_fingerprint


SCENE_SCHEMA = "ledgrid.scene-state"
SCENE_SCHEMA_VERSION = 1
SCENE_PRESET_SCHEMA = "ledgrid.scene-preset"
SCENE_PRESET_VERSION = 1
DESIRED_DISPLAY_SCHEMA = "ledgrid.desired-display-state"
DESIRED_DISPLAY_VERSION = 1
BROWSER_SCENE_SCHEMA = "ledgrid.browser-scene"
BROWSER_SCENE_VERSION = 1
BROWSER_SCENE_PARAMETER_SCHEMA_VERSION = 1
BROWSER_SCENE_MAX_BYTES = 256 * 1024
BROWSER_SCENE_MAX_DEPTH = 16
BROWSER_SCENE_MAX_VALUES = 4096
BROWSER_SCENE_MAX_STRING_BYTES = 16 * 1024
FIXED_OVERLAY_SLOT = "clock_overlay"
COMPILED_RAINBOW_PLUGIN_ID = "compiled_rainbow"
SUPPORTED_PROVIDERS = frozenset(("python",))
KNOWN_PROVIDERS = frozenset(("python", "receiver_native"))
SUPPORTED_ROLES = frozenset(("background", "overlay", "full_scene"))
GLOBAL_SETTINGS_SCHEMA = "ledgrid.global-settings-state"
GLOBAL_SETTINGS_VERSION = 1
SCENE_ACTIVATION_BASIS_SCHEMA = "ledgrid.scene-activation-basis"
SCENE_ACTIVATION_BASIS_VERSION = 2
SCENE_ACTIVATION_COMMAND_SCHEMA = "ledgrid.scene-activation-command"
SCENE_ACTIVATION_COMMAND_VERSION = 1
SCENE_ACTIVATION_STATUS_SCHEMA = "ledgrid.scene-activation-status"
SCENE_ACTIVATION_STATUS_VERSION = 1
COMPOSER_OPERATIONS_STATUS_SCHEMA = "ledgrid.composer-operations-status"
COMPOSER_OPERATIONS_STATUS_VERSION = 1
SCENE_ACTIVATION_PHASES = frozenset((
    "queued", "preflighting", "applying", "observing", "active",
    "rolling_back", "rolled_back", "failed", "timed_out",
))

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTROLLER_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
_UNSAFE_JSON_KEYS = frozenset(("__proto__", "constructor", "prototype"))
_COMPOSER_INTERACTION_DIRECTIONS = frozenset(
    {"left", "right", "down", "rotate-left", "rotate-right", "drop"}
)


def _browser_interaction_capabilities(
    value: Any,
    *,
    provider: str,
    component_id: str,
    previewable: bool,
) -> dict[str, Any]:
    """Bind declared local preview controls to one exact component identity."""
    raw = value if isinstance(value, Mapping) else {}
    point = raw.get("point") if isinstance(raw.get("point"), Mapping) else {}
    point_enabled = previewable and point.get("kind") == "primary"
    directions = raw.get("directions")
    if not isinstance(directions, (list, tuple)):
        directions = ()
    normalized_directions = sorted({
        direction for direction in directions
        if isinstance(direction, str) and direction in _COMPOSER_INTERACTION_DIRECTIONS
    }) if previewable else []
    return {
        "schema": "ledgrid.composer-interaction-capabilities",
        "schema_version": 1,
        "provider": provider,
        "component_id": component_id,
        "local_preview": {
            "point": {
                "supported": point_enabled,
                "kind": "primary" if point_enabled else None,
                "label": (
                    str(point.get("label") or "Interact with preview")
                    if point_enabled else None
                ),
            },
            "directions": normalized_directions,
        },
        "live_wall": {
            "available": False,
            "reason": "Local preview controls never send commands to the wall.",
        },
    }


class SceneValidationError(ValueError):
    """A stable client-visible validation error."""


@dataclass(frozen=True)
class SceneProviderPolicy:
    """Explicit rollout policy for providers executable by scene clients.

    Receiver-local playback and sparse foreground publication form one product
    slice.  Enabling only one half must not make a receiver-native component
    selectable or valid.  Version 1 deliberately allowlists the statically
    linked compiled rainbow by default. Managed native packages are admitted
    only when the independent module gate is also enabled; their exact bundle
    and payload identities are still bound by the catalog.
    """

    receiver_local_background: bool = False
    receiver_sparse_overlay: bool = False
    receiver_native_modules: bool = False

    def __post_init__(self) -> None:
        for name in (
            "receiver_local_background",
            "receiver_sparse_overlay",
            "receiver_native_modules",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"scene provider policy {name!r} must be boolean")

    @property
    def compiled_rainbow_enabled(self) -> bool:
        return self.receiver_local_background and self.receiver_sparse_overlay

    @property
    def managed_native_enabled(self) -> bool:
        return self.compiled_rainbow_enabled and self.receiver_native_modules

    def allows_receiver_background(self, plugin_id: str) -> bool:
        return self.compiled_rainbow_enabled and (
            plugin_id == COMPILED_RAINBOW_PLUGIN_ID
            or self.receiver_native_modules
        )


DEFAULT_SCENE_PROVIDER_POLICY = SceneProviderPolicy()


def jsonable(value: Any) -> Any:
    """Convert manager/dataclass catalog values into plain JSON values."""
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return jsonable(value.to_dict())
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SceneValidationError(f"{label} must be a JSON object")
    return dict(value)


def _only(payload: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise SceneValidationError(
            f"unsupported {label} fields: {', '.join(unknown)}"
        )


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise SceneValidationError(f"{label} must be a stable identifier")
    return value


def _uint64(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**64:
        raise SceneValidationError(f"{label} must be an unsigned 64-bit integer")
    return value


def _byte(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise SceneValidationError(f"{label} must be an integer from 0 to 255")
    return value


def _signed32(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not -(2**31) <= value < 2**31:
        raise SceneValidationError(f"{label} must be a signed 32-bit integer")
    return value


def _sha256_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise SceneValidationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _opaque_id(value: Any, label: str, *, max_bytes: int = 4096) -> str:
    if not isinstance(value, str) or not value:
        raise SceneValidationError(f"{label} must be a non-empty string")
    if len(value.encode("utf-8")) > max_bytes:
        raise SceneValidationError(f"{label} exceeds the {max_bytes}-byte limit")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise SceneValidationError(f"{label} must not contain control characters")
    return value


def _finite_number(
    value: Any,
    label: str,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SceneValidationError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise SceneValidationError(f"{label} must be finite")
    if minimum is not None and result < minimum:
        raise SceneValidationError(f"{label} must be at least {minimum}")
    if maximum is not None and result > maximum:
        raise SceneValidationError(f"{label} must be at most {maximum}")
    return result


def _operations_timestamp_ms(value: Any) -> Optional[int]:
    """Adapt the controller's epoch-seconds status timestamp without guessing.

    Runtime status has historically used seconds, while controller receipts use
    milliseconds.  A Composer read model has to make that unit conversion at
    one boundary so clients can calculate a bounded evidence age consistently.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        return None
    # Epoch timestamps before 2001 cannot be a useful current observation.
    # Values at or above this threshold are already millisecond timestamps.
    if numeric < 1_000_000_000_000:
        numeric *= 1000
    return int(numeric)


def _operations_uint(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _operations_finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else None


def build_composer_operations_status(
    status: Any,
    *,
    now_ms: int,
    raw_evidence_url: str = "/api/v1/composer/settings/observed",
    stale_after_ms: int = 10_000,
) -> dict[str, Any]:
    """Project controller status into Composer's bounded operations read model.

    This is intentionally a projection, not a second source of truth.  It
    carries a controller session/revision with every observation, summarizes
    health without exposing raw controller internals, and names the bounded
    Composer settings observation as the owner of drill-down evidence.
    """
    if not isinstance(now_ms, int) or isinstance(now_ms, bool) or now_ms < 0:
        raise SceneValidationError("composer operations now_ms must be a non-negative integer")
    if not isinstance(stale_after_ms, int) or isinstance(stale_after_ms, bool) or stale_after_ms < 1:
        raise SceneValidationError("composer operations stale_after_ms must be positive")
    if not isinstance(raw_evidence_url, str) or not raw_evidence_url.startswith("/"):
        raise SceneValidationError("composer operations raw_evidence_url must be an absolute path")

    raw = dict(status) if isinstance(status, Mapping) else {}
    observed_at = _operations_timestamp_ms(raw.get("updated_at", raw.get("timestamp")))
    age_ms = None if observed_at is None else max(0, now_ms - observed_at)
    freshness = (
        "unknown" if observed_at is None
        else "fresh" if age_ms is not None and age_ms <= stale_after_ms
        else "stale"
    )
    session_id = raw.get("controller_session_id")
    session_id = session_id if isinstance(session_id, str) and _CONTROLLER_SESSION_ID.fullmatch(session_id) else None
    state_revision = _operations_uint(raw.get("controller_state_revision"))
    identity = raw.get("active_identity")
    identity = jsonable(identity) if isinstance(identity, Mapping) else None
    current_identity_digest = raw.get("current_identity_digest")
    current_identity_digest = (
        current_identity_digest
        if isinstance(current_identity_digest, str) and _SHA256.fullmatch(current_identity_digest)
        else None
    )
    output_state = (
        "running" if raw.get("is_running") is True
        else "idle" if raw.get("is_running") is False
        else "unknown"
    )

    global_settings = raw.get("global_settings")
    global_settings = (
        dict(global_settings) if isinstance(global_settings, Mapping) else {}
    )
    global_output = global_settings.get("output")
    global_output = dict(global_output) if isinstance(global_output, Mapping) else {}
    observed_power = global_output.get("power")
    if not isinstance(observed_power, bool):
        observed_power = raw.get("is_running")
        if not isinstance(observed_power, bool):
            observed_power = None

    latest = raw.get("latest_activation")
    latest = dict(latest) if isinstance(latest, Mapping) else None
    desired = latest.get("normalized_identity") if latest else None
    desired = jsonable(desired) if isinstance(desired, Mapping) else None
    receipt_session = (
        latest.get("controller", {}).get("session_id")
        if isinstance(latest and latest.get("controller"), Mapping) else None
    )
    phase = latest.get("phase") if latest else None
    if freshness == "stale":
        reconciliation_state = "stale"
        reconciliation_reason = "The latest controller observation exceeded its freshness window."
    elif session_id is None or state_revision is None:
        reconciliation_state = "unavailable"
        reconciliation_reason = "The controller did not supply a revision-qualified observation."
    elif desired is None:
        reconciliation_state = "unknown"
        reconciliation_reason = "No activation receipt owns a desired output identity."
    elif isinstance(receipt_session, str) and receipt_session != session_id:
        reconciliation_state = "reconnected"
        reconciliation_reason = "The controller session changed after the activation receipt."
    elif identity != desired:
        reconciliation_state = "diverged"
        reconciliation_reason = "The observed output identity does not match the activation receipt."
    elif phase == "active":
        reconciliation_state = "current"
        reconciliation_reason = "The latest activation receipt matches the fresh observed output."
    else:
        reconciliation_state = "pending"
        reconciliation_reason = "An activation receipt exists but has not reached an active observation."

    # Power is an activation global, never an independent legacy command.  A
    # controller that publishes the complete globals supplies its own observed
    # bit; older controller status remains safely readable through is_running.
    # The state is deliberately bounded to the same provider/revision tuple as
    # every other Composer observation.
    if freshness != "fresh" or session_id is None or state_revision is None:
        power_state = "stale"
        power_reason = "Output power needs a fresh revision-qualified controller observation."
    elif phase in {"failed", "timed_out", "rolled_back"}:
        power_state = "failed"
        power_reason = "The latest guarded activation did not leave a current output-power observation."
    elif phase in {"queued", "preflighting", "applying", "observing"}:
        power_state = "pending"
        power_reason = "A guarded activation is waiting for the controller to acknowledge output power."
    elif observed_power is True:
        power_state = "on"
        power_reason = "Output is on in the fresh controller observation."
    elif observed_power is False:
        power_state = "off"
        power_reason = "Output is off in the fresh controller observation."
    else:
        power_state = "unknown"
        power_reason = "The controller did not publish an output-power observation."

    receiver = raw.get("receiver_hybrid")
    if not isinstance(receiver, Mapping):
        scene = raw.get("scene")
        receiver = scene.get("receiver") if isinstance(scene, Mapping) else None
    receiver = dict(receiver) if isinstance(receiver, Mapping) else {}
    driver = raw.get("driver_stats")
    devices = driver.get("devices") if isinstance(driver, Mapping) else None
    device_count = len(devices) if isinstance(devices, list) else 0
    expected_count = _operations_uint(raw.get("receiver_count"))
    expected_count = expected_count if expected_count is not None else device_count
    expected = list(range(expected_count))
    expected_ids = set(expected)
    readable = {
        value for value in receiver.get("readable_devices", ())
        if _operations_uint(value) is not None and value in expected_ids
    }
    unverified = {
        value for value in receiver.get("unverified_devices", ())
        if _operations_uint(value) is not None and value in expected_ids
    }
    driver_connected: set[int] | None = None
    if isinstance(driver, Mapping):
        aggregate = driver.get("aggregate")
        device_map = aggregate.get("device_map") if isinstance(aggregate, Mapping) else None
        if isinstance(devices, list) and isinstance(device_map, list) and len(devices) == len(device_map):
            mapped_ids = [
                entry.get("logical_device") if isinstance(entry, Mapping) else None
                for entry in device_map
            ]
            if (
                all(isinstance(entry, Mapping) for entry in devices)
                and all(_operations_uint(value) is not None and value in expected_ids for value in mapped_ids)
                and len(set(mapped_ids)) == len(mapped_ids)
                and set(mapped_ids) == expected_ids
            ):
                driver_connected = set(mapped_ids)
        if driver_connected is None:
            driver_connected = set()
    connected = (driver_connected if driver_connected is not None else readable) - unverified
    if freshness != "fresh":
        connected = set()
    connected = sorted(connected)
    unverified = sorted(unverified)
    missing = sorted(expected_ids - set(connected))
    if not receiver:
        receiver_state = "unknown"
    elif receiver.get("healthy") is True and not missing:
        receiver_state = "healthy"
    elif receiver.get("operational") is False or receiver.get("degraded") is True or missing:
        receiver_state = "degraded"
    else:
        receiver_state = "unknown"

    target_fps = _operations_finite(raw.get("target_fps"))
    actual_fps = _operations_finite(raw.get("actual_fps"))
    if output_state != "running":
        performance_state = "idle"
    elif target_fps is None or target_fps <= 0 or actual_fps is None:
        performance_state = "unknown"
    elif actual_fps >= target_fps * 0.8:
        performance_state = "healthy"
    else:
        performance_state = "degraded"

    health_states = {receiver_state, performance_state}
    overall = (
        "unavailable" if freshness != "fresh"
        else "degraded" if "degraded" in health_states
        else "healthy" if health_states <= {"healthy", "idle"}
        else "unknown"
    )
    return {
        "schema": COMPOSER_OPERATIONS_STATUS_SCHEMA,
        "schema_version": COMPOSER_OPERATIONS_STATUS_VERSION,
        "observation": {
            "state": output_state,
            "freshness": freshness,
            "observed_at": observed_at,
            "age_ms": age_ms,
            "revision": {
                "session_id": session_id,
                "state_revision": state_revision,
                "identity_digest": current_identity_digest,
            },
            "identity": identity,
        },
        "reconciliation": {
            "state": reconciliation_state,
            "reason": reconciliation_reason,
            "desired_identity": desired,
            "receipt_phase": phase if isinstance(phase, str) else None,
        },
        "output_power": {
            "state": power_state,
            "observed": observed_power,
            "revision": {
                "session_id": session_id,
                "state_revision": state_revision,
            },
            "reason": power_reason,
        },
        "health": {
            "state": overall,
            "receivers": {
                "state": receiver_state,
                "expected": expected,
                "connected": connected,
                "missing": missing,
                "unverified": unverified,
                "telemetry_complete": receiver.get("telemetry_complete") if isinstance(receiver.get("telemetry_complete"), bool) else None,
                "error": receiver.get("error") if isinstance(receiver.get("error"), str) else None,
            },
            "performance": {
                "state": performance_state,
                "target_fps": target_fps,
                "actual_fps": actual_fps,
            },
        },
        "raw_evidence": {
            "owner": "controller_status",
            "url": raw_evidence_url,
            "observed_at": observed_at,
        },
    }


def canonical_json_bytes(value: Any) -> bytes:
    """Return the stable UTF-8 representation used by activation identities.

    Unlike ``json.dumps(..., default=str)``, this helper is deliberately strict:
    mappings require string keys, numbers must be finite, and non-JSON objects
    are rejected rather than acquiring an environment-dependent identity.
    """

    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            for key, item in current.items():
                if not isinstance(key, str):
                    raise SceneValidationError(
                        "canonical JSON object keys must be strings"
                    )
                stack.append(item)
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
        elif isinstance(current, float):
            if not math.isfinite(current):
                raise SceneValidationError("canonical JSON numbers must be finite")
        elif current is not None and not isinstance(
            current, (str, int, bool)
        ):
            raise SceneValidationError(
                f"canonical JSON does not support {type(current).__name__}"
            )
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise SceneValidationError("value is not canonical JSON") from exc


def canonical_json_sha256(value: Any) -> str:
    """Return the lowercase SHA-256 identity of ``canonical_json_bytes``."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def validate_bounded_browser_json(
    value: Any,
    *,
    label: str = "browser scene document",
    encoded_size: Optional[int] = None,
) -> None:
    """Reject resource-exhaustion and JavaScript-object hazards at import edges.

    Flask has already decoded request JSON when this helper is normally called,
    so the walk is deliberately iterative.  It provides deterministic limits
    without relying on Python's recursion ceiling, and it is also suitable for
    file-import validation used outside the web process.
    """
    if encoded_size is not None:
        if isinstance(encoded_size, bool) or not isinstance(encoded_size, int):
            raise TypeError("encoded_size must be an integer")
        if encoded_size < 0 or encoded_size > BROWSER_SCENE_MAX_BYTES:
            raise SceneValidationError(
                f"{label} exceeds the {BROWSER_SCENE_MAX_BYTES}-byte limit"
            )
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise SceneValidationError(
            f"{label} must contain only finite JSON values"
        ) from exc
    if len(encoded) > BROWSER_SCENE_MAX_BYTES:
        raise SceneValidationError(
            f"{label} exceeds the {BROWSER_SCENE_MAX_BYTES}-byte limit"
        )

    values_seen = 0
    stack: list[tuple[Any, int, str]] = [(value, 0, label)]
    while stack:
        current, depth, path = stack.pop()
        values_seen += 1
        if values_seen > BROWSER_SCENE_MAX_VALUES:
            raise SceneValidationError(
                f"{label} exceeds the {BROWSER_SCENE_MAX_VALUES}-value limit"
            )
        if depth > BROWSER_SCENE_MAX_DEPTH:
            raise SceneValidationError(
                f"{path} exceeds the maximum nesting depth of "
                f"{BROWSER_SCENE_MAX_DEPTH}"
            )
        if isinstance(current, Mapping):
            for key, item in current.items():
                if not isinstance(key, str):
                    raise SceneValidationError(f"{path} keys must be strings")
                if key in _UNSAFE_JSON_KEYS:
                    raise SceneValidationError(
                        f"{path}.{key} is not allowed in imported JSON"
                    )
                stack.append((item, depth + 1, f"{path}.{key}"))
        elif isinstance(current, (list, tuple)):
            stack.extend(
                (item, depth + 1, f"{path}[{index}]")
                for index, item in enumerate(current)
            )
        elif isinstance(current, str):
            if len(current.encode("utf-8")) > BROWSER_SCENE_MAX_STRING_BYTES:
                raise SceneValidationError(
                    f"{path} exceeds the {BROWSER_SCENE_MAX_STRING_BYTES}-byte string limit"
                )
        elif isinstance(current, float) and not math.isfinite(current):
            raise SceneValidationError(f"{path} must be finite")
        elif current is not None and not isinstance(
            current, (str, int, float, bool)
        ):
            raise SceneValidationError(f"{path} is not a JSON value")


def component_contract_digest(component: Mapping[str, Any]) -> str:
    """Stable digest of the authoring-facing portion of one catalog record."""
    item = jsonable(component)
    if not isinstance(item, dict):
        raise TypeError("component must be a mapping")
    component_id, provider, role = _catalog_identity(item)
    if component_id is None or provider is None or role is None:
        raise SceneValidationError(
            "catalog component requires provider, plugin_id, and role"
        )
    schema_version = item.get(
        "parameter_schema_version", BROWSER_SCENE_PARAMETER_SCHEMA_VERSION
    )
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 1
    ):
        raise SceneValidationError(
            f"catalog component {provider}:{component_id} "
            "parameter_schema_version must be a positive integer"
        )
    identity = {
        "provider": provider,
        "component_id": component_id,
        "role": role,
        "entrypoint": item.get("entrypoint"),
        "parameter_schema_version": schema_version,
        "parameter_schema": item.get("parameter_schema", {}),
        "defaults": item.get("defaults", {}),
        "build": item.get("build", {}),
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _catalog_identity(item: Mapping[str, Any]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    component_id = item.get("plugin_id", item.get("component_id", item.get("plugin_name")))
    provider = item.get("provider", "python")
    role = item.get("role", "background")
    if isinstance(provider, Enum):
        provider = provider.value
    if isinstance(role, Enum):
        role = role.value
    return (
        component_id if isinstance(component_id, str) else None,
        provider if isinstance(provider, str) else None,
        role if isinstance(role, str) else None,
    )


def catalog_index(catalog: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in catalog:
        item = jsonable(raw)
        if not isinstance(item, dict):
            continue
        component_id, provider, _role = _catalog_identity(item)
        if component_id and provider:
            result[(component_id, provider)] = item
    return result


def decorate_catalog(
    catalog: Iterable[Mapping[str, Any]],
    *,
    provider_policy: SceneProviderPolicy = DEFAULT_SCENE_PROVIDER_POLICY,
) -> list[dict[str, Any]]:
    """Expose explicit fixed-editor compatibility for every catalog item."""
    if not isinstance(provider_policy, SceneProviderPolicy):
        raise TypeError("provider_policy must be a SceneProviderPolicy")
    decorated = []
    for raw in catalog:
        item = jsonable(raw)
        if not isinstance(item, dict):
            continue
        component_id, provider, role = _catalog_identity(item)
        if component_id is not None:
            item.setdefault("plugin_id", component_id)
        item.setdefault("provider", provider)
        item.setdefault("role", role)
        slots: list[str] = []
        diagnostic = None
        compatibility = item.get("compatibility")
        explicitly_noncomposable = (
            isinstance(compatibility, dict)
            and compatibility.get("composable") is False
        )
        if provider == "receiver_native":
            if not provider_policy.compiled_rainbow_enabled:
                # Preserve the Phase 2C feature-off product response exactly.
                diagnostic = "This provider is catalog-visible but not executable in host scenes."
            elif (
                component_id != COMPILED_RAINBOW_PLUGIN_ID
                and not provider_policy.managed_native_enabled
            ):
                diagnostic = (
                    "Only the compiled_rainbow receiver-native background is "
                    "enabled by the version 1 scene policy."
                )
            elif role != "background":
                diagnostic = "The compiled_rainbow component must declare the background role."
            elif explicitly_noncomposable:
                diagnostic = str(
                    compatibility.get("diagnostic")
                    or "This component is not compatible with composed scenes."
                )
            else:
                slots = ["background"]
        elif provider not in SUPPORTED_PROVIDERS:
            diagnostic = "This provider is catalog-visible but not executable in host scenes."
        elif explicitly_noncomposable:
            diagnostic = str(
                compatibility.get("diagnostic")
                or "This component is not compatible with composed host scenes."
            )
        elif role == "background":
            slots = ["background"]
        elif role == "overlay" and component_id == FIXED_OVERLAY_SLOT:
            slots = [FIXED_OVERLAY_SLOT]
        elif role == "overlay":
            diagnostic = "Phase 2C supports only the fixed clock overlay slot."
        elif role == "full_scene":
            diagnostic = "Compatibility full scenes remain available through the animation controls."
        else:
            diagnostic = "The component declares an unsupported role."
        item["scene_compatibility"] = {
            "selectable": bool(slots),
            "slots": slots,
            "diagnostic": diagnostic,
        }
        decorated.append(item)
    return decorated


def filter_catalog(
    catalog: Iterable[Mapping[str, Any]],
    *,
    provider: Optional[str] = None,
    role: Optional[str] = None,
    provider_policy: SceneProviderPolicy = DEFAULT_SCENE_PROVIDER_POLICY,
) -> list[dict[str, Any]]:
    if provider is not None:
        if provider not in KNOWN_PROVIDERS:
            raise SceneValidationError(
                f"provider filter must be one of {', '.join(sorted(KNOWN_PROVIDERS))}"
            )
    if role is not None and role not in SUPPORTED_ROLES:
        raise SceneValidationError(
            f"role filter must be one of {', '.join(sorted(SUPPORTED_ROLES))}"
        )
    result = []
    for item in decorate_catalog(catalog, provider_policy=provider_policy):
        _component_id, item_provider, item_role = _catalog_identity(item)
        if provider is not None and item_provider != provider:
            continue
        if role is not None and item_role != role:
            continue
        result.append(item)
    return sorted(
        result,
        key=lambda item: str(item.get("name") or item.get("plugin_id") or "").casefold(),
    )


def decorate_browser_component(
    component: Mapping[str, Any],
    *,
    browser_runtime: Mapping[str, Any],
    provider_collision: bool = False,
) -> dict[str, Any]:
    """Attach the immutable identities and explicit composer capabilities.

    The browser runtime is supplied by the web asset catalog rather than
    discovered here, keeping this module portable and free of filesystem or
    implementation imports.
    """
    item = jsonable(component)
    runtime = jsonable(browser_runtime)
    if not isinstance(item, dict) or not isinstance(runtime, dict):
        raise TypeError("component and browser_runtime must be mappings")
    component_id, provider, role = _catalog_identity(item)
    if component_id is None or provider is None or role is None:
        raise SceneValidationError(
            "catalog component requires provider, plugin_id, and role"
        )
    schema_version = item.get(
        "parameter_schema_version", BROWSER_SCENE_PARAMETER_SCHEMA_VERSION
    )
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 1
    ):
        raise SceneValidationError(
            f"catalog component {provider}:{component_id} "
            "parameter_schema_version must be a positive integer"
        )
    component_digest = component_contract_digest(item)
    runtime_digest = runtime.get("digest")
    runtime_supported = runtime.get("supported") is True
    runtime_identity_ready = (
        isinstance(runtime_digest, str)
        and _SHA256.fullmatch(runtime_digest) is not None
    )
    previewable = runtime_supported and runtime_identity_ready
    interactions = _browser_interaction_capabilities(
        item.get("interaction_capabilities"),
        provider=provider,
        component_id=component_id,
        previewable=previewable,
    )

    compatibility = item.get("scene_compatibility")
    compatibility = compatibility if isinstance(compatibility, dict) else {}
    composable = compatibility.get("selectable") is True
    availability = item.get("availability")
    availability = availability if isinstance(availability, dict) else {}
    available = availability.get("state", "ready") == "ready"
    implementation = item.get("compatibility")
    implementation = implementation if isinstance(implementation, dict) else {}
    implementation_ready = implementation.get("implementation_loaded", True) is not False
    # A preview-only renderer can still be saved as a private draft.  Scene
    # composability is deliberately an activation concern, not a persistence
    # gate.
    # Presets are keyed by provider/component/preset. A duplicate plugin ID is
    # therefore only a legacy-file migration concern, never a saveability gate.
    saveable = previewable

    managed_identity: dict[str, Any] = {
        "provider": provider,
        "component_id": component_id,
        "component_digest": component_digest,
        "runtime_digest": runtime_digest,
        "parameter_schema_version": schema_version,
    }
    native_identity_ready = True
    if provider == "receiver_native":
        build = item.get("build")
        build = build if isinstance(build, dict) else {}
        bundle_digest = build.get("bundle_digest", build.get("contract_digest"))
        payload_digest = build.get("expected_payload_digest")
        managed_identity.update(
            bundle_digest=bundle_digest,
            expected_payload_digest=payload_digest,
        )
        native_identity_ready = all(
            isinstance(digest, str) and _SHA256.fullmatch(digest) is not None
            for digest in (bundle_digest, payload_digest)
        )
    activation_ready = (
        saveable
        and composable
        and available
        and implementation_ready
        and native_identity_ready
    )

    reason: Optional[str] = None
    if not runtime_supported:
        reason = str(runtime.get("reason") or "Browser preview runtime is unavailable.")
    elif not runtime_identity_ready:
        reason = "Browser preview runtime has no verified content digest."
    elif not composable:
        reason = str(
            compatibility.get("diagnostic")
            or "This component is not compatible with version 1 browser scenes."
        )
    elif not available:
        reason = str(
            availability.get("reason")
            or availability.get("diagnostic")
            or "The managed component is unavailable."
        )
    elif not implementation_ready:
        reason = "The managed host implementation is not loaded."
    elif not native_identity_ready:
        reason = "The managed native bundle identity is incomplete."

    runtime["digest"] = runtime_digest
    item.update({
        "component_digest": component_digest,
        "parameter_schema_version": schema_version,
        "browser_runtime": runtime,
        "browser_capabilities": {
            "previewable": previewable,
            "saveable": saveable,
            "activation_ready": activation_ready,
            "reason": reason,
            "managed_identity": managed_identity,
            "interactions": interactions,
        },
        "interaction_capabilities": interactions,
    })
    return item


def _validate_browser_parameters(
    value: Any,
    descriptor: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    params = _object(value, label)
    schema = descriptor.get("parameter_schema")
    if not isinstance(schema, Mapping):
        raise SceneValidationError(f"{label} catalog schema is unavailable")
    for name, parameter in params.items():
        definition = schema.get(name)
        if not isinstance(definition, Mapping):
            raise SceneValidationError(f"{label}.{name} is not a supported parameter")
        type_name = definition.get("type")
        if type_name == "bool":
            valid_type = isinstance(parameter, bool)
        elif type_name == "int":
            valid_type = isinstance(parameter, int) and not isinstance(parameter, bool)
        elif type_name == "float":
            valid_type = isinstance(parameter, (int, float)) and not isinstance(parameter, bool)
        elif type_name == "str":
            valid_type = isinstance(parameter, str)
        elif type_name == "cells":
            valid_type = isinstance(parameter, list)
            if valid_type:
                max_items = definition.get("max_items", 4554)
                if (
                    isinstance(max_items, bool)
                    or not isinstance(max_items, int)
                    or not 0 <= max_items <= 4554
                ):
                    raise SceneValidationError(
                        f"{label}.{name} catalog max_items is invalid"
                    )
                if len(parameter) > max_items:
                    raise SceneValidationError(
                        f"{label}.{name} must contain at most {max_items} cells"
                    )
                strip_min = int(definition.get("strip_min", 0))
                strip_max = int(definition.get("strip_max", 32))
                led_min = int(definition.get("led_min", 0))
                led_max = int(definition.get("led_max", 137))
                for index, cell in enumerate(parameter):
                    if (
                        not isinstance(cell, (list, tuple))
                        or len(cell) != 2
                        or any(
                            isinstance(coordinate, bool)
                            or not isinstance(coordinate, int)
                            for coordinate in cell
                        )
                    ):
                        raise SceneValidationError(
                            f"{label}.{name}[{index}] must be [strip, led] integers"
                        )
                    strip, led = cell
                    if not strip_min <= strip <= strip_max:
                        raise SceneValidationError(
                            f"{label}.{name}[{index}] strip must be from "
                            f"{strip_min} to {strip_max}"
                        )
                    if not led_min <= led <= led_max:
                        raise SceneValidationError(
                            f"{label}.{name}[{index}] led must be from "
                            f"{led_min} to {led_max}"
                        )
        else:
            raise SceneValidationError(
                f"{label}.{name} uses unsupported catalog type {type_name!r}"
            )
        if not valid_type:
            raise SceneValidationError(f"{label}.{name} must be {type_name}")
        if isinstance(parameter, (int, float)) and not isinstance(parameter, bool):
            try:
                finite = math.isfinite(float(parameter))
            except (OverflowError, ValueError):
                finite = False
            if not finite:
                raise SceneValidationError(f"{label}.{name} must be finite")
            if "min" in definition and parameter < definition["min"]:
                raise SceneValidationError(
                    f"{label}.{name} must be at least {definition['min']}"
                )
            if "max" in definition and parameter > definition["max"]:
                raise SceneValidationError(
                    f"{label}.{name} must be at most {definition['max']}"
                )
        if "options" in definition and parameter not in definition["options"]:
            raise SceneValidationError(
                f"{label}.{name} must be one of {definition['options']}"
            )
        if isinstance(parameter, str) and name.endswith(("_path", "_file")):
            normalized_path = parameter.replace("\\", "/")
            path_parts = normalized_path.split("/")
            if (
                not normalized_path
                or normalized_path.startswith("/")
                or ":" in path_parts[0]
                or ".." in path_parts
            ):
                raise SceneValidationError(
                    f"{label}.{name} must be a catalog-managed relative path"
                )
            allowed_paths = set()
            options = definition.get("options")
            if isinstance(options, (list, tuple)):
                allowed_paths.update(item for item in options if isinstance(item, str))
            if isinstance(definition.get("default"), str):
                allowed_paths.add(definition["default"])
            defaults = descriptor.get("defaults")
            if isinstance(defaults, Mapping) and isinstance(defaults.get(name), str):
                allowed_paths.add(defaults[name])
            if parameter not in allowed_paths:
                raise SceneValidationError(
                    f"{label}.{name} is not a catalog-managed asset"
                )
    return params


def _browser_catalog_index(
    catalog: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in catalog:
        item = jsonable(raw)
        if not isinstance(item, dict):
            continue
        component_id, provider, _role = _catalog_identity(item)
        if component_id and provider:
            result[(provider, component_id)] = item
    return result


def _browser_component_ref(
    value: Any,
    label: str,
    *,
    catalog: Mapping[tuple[str, str], Mapping[str, Any]],
    role: str,
    purpose: str,
) -> dict[str, Any]:
    payload = _object(value, label)
    _only(
        payload,
        {
            "provider", "component_id", "component_digest", "runtime_digest",
            "parameter_schema_version", "parameters", "preset_id",
            "preset_fingerprint",
        },
        label,
    )
    provider = _identifier(payload.get("provider"), f"{label}.provider")
    component_id = _identifier(
        payload.get("component_id"), f"{label}.component_id"
    )
    if provider not in {catalog_provider for catalog_provider, _ in catalog}:
        raise SceneValidationError(
            f"{label}.provider {provider!r} is not present in the catalog"
        )
    descriptor = catalog.get((provider, component_id))
    if descriptor is None:
        raise SceneValidationError(
            f"{label}.component_id identifies unknown component "
            f"{provider}:{component_id}"
        )
    _component_id, _provider, catalog_role = _catalog_identity(descriptor)
    if catalog_role != role:
        raise SceneValidationError(
            f"{label}.component_id requires catalog role {role!r}; "
            f"{provider}:{component_id} declares {catalog_role!r}"
        )

    capabilities = descriptor.get("browser_capabilities")
    if not isinstance(capabilities, Mapping):
        raise SceneValidationError(
            f"{label}.component_id has no browser capability record"
        )
    required_capability = {
        "preview": "previewable",
        "import": "previewable",
        "save": "saveable",
        "activation": "activation_ready",
    }[purpose]
    if capabilities.get(required_capability) is not True:
        reason = capabilities.get("reason") or "managed capability is unavailable"
        raise SceneValidationError(
            f"{label}.component_id is not {required_capability}: {reason}"
        )
    managed_identity = capabilities.get("managed_identity")
    if not isinstance(managed_identity, Mapping):
        raise SceneValidationError(
            f"{label}.component_id has no required managed identity"
        )
    component_digest = _sha256_digest(
        payload.get("component_digest"), f"{label}.component_digest"
    )
    if component_digest != managed_identity.get("component_digest"):
        raise SceneValidationError(
            f"{label}.component_digest does not match the catalog binding"
        )
    runtime_digest = _sha256_digest(
        payload.get("runtime_digest"), f"{label}.runtime_digest"
    )
    if runtime_digest != managed_identity.get("runtime_digest"):
        raise SceneValidationError(
            f"{label}.runtime_digest does not match the catalog binding"
        )
    schema_version = payload.get("parameter_schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 1
    ):
        raise SceneValidationError(
            f"{label}.parameter_schema_version must be a positive integer"
        )
    if schema_version != managed_identity.get("parameter_schema_version"):
        raise SceneValidationError(
            f"{label}.parameter_schema_version does not match the catalog binding"
        )
    parameters = _validate_browser_parameters(
        payload.get("parameters"), descriptor, f"{label}.parameters"
    )

    preset_id = payload.get("preset_id")
    preset_fingerprint = payload.get("preset_fingerprint")
    if preset_id is None:
        if preset_fingerprint is not None:
            raise SceneValidationError(
                f"{label}.preset_fingerprint requires preset_id"
            )
    else:
        preset_id = _identifier(preset_id, f"{label}.preset_id")
        preset_fingerprint = _sha256_digest(
            preset_fingerprint, f"{label}.preset_fingerprint"
        )

    result = {
        "provider": provider,
        "component_id": component_id,
        "component_digest": component_digest,
        "runtime_digest": runtime_digest,
        "parameter_schema_version": schema_version,
        "parameters": parameters,
    }
    if preset_id is not None:
        result.update(
            preset_id=preset_id,
            preset_fingerprint=preset_fingerprint,
        )
    return result


def normalize_browser_scene_document(
    value: Any,
    *,
    catalog: Iterable[Mapping[str, Any]],
    purpose: str = "preview",
) -> dict[str, Any]:
    """Validate the browser's single-background plus fixed-Clock document."""
    if purpose not in {"preview", "import", "save", "activation"}:
        raise ValueError("purpose must be preview, import, save, or activation")
    validate_bounded_browser_json(value)
    payload = _object(value, "browser scene")
    _only(
        payload,
        {
            "schema", "schema_version", "revision", "background", "layers",
            "installation_profile", "fallback",
        },
        "browser scene",
    )
    if payload.get("schema") != BROWSER_SCENE_SCHEMA:
        raise SceneValidationError(
            f"browser scene.schema must be {BROWSER_SCENE_SCHEMA!r}"
        )
    if payload.get("schema_version") != BROWSER_SCENE_VERSION:
        raise SceneValidationError(
            f"unsupported browser scene.schema_version "
            f"{payload.get('schema_version')!r}"
        )
    revision = _uint64(payload.get("revision"), "browser scene.revision")
    indexed_catalog = _browser_catalog_index(catalog)
    background = _browser_component_ref(
        payload.get("background"),
        "browser scene.background",
        catalog=indexed_catalog,
        role="background",
        purpose=purpose,
    )

    raw_layers = payload.get("layers")
    if not isinstance(raw_layers, list):
        raise SceneValidationError("browser scene.layers must be an array")
    if len(raw_layers) > 1:
        raise SceneValidationError(
            "browser scene.layers supports at most one clock layer"
        )
    layers = []
    if raw_layers:
        layer = _object(raw_layers[0], "browser scene.layers[0]")
        _only(
            layer,
            {"role", "component", "enabled", "opacity", "blend_mode"},
            "browser scene.layers[0]",
        )
        if layer.get("role") != "clock":
            raise SceneValidationError(
                "browser scene.layers[0].role must be 'clock'"
            )
        component = _browser_component_ref(
            layer.get("component"),
            "browser scene.layers[0].component",
            catalog=indexed_catalog,
            role="overlay",
            purpose=purpose,
        )
        if component["component_id"] != FIXED_OVERLAY_SLOT:
            raise SceneValidationError(
                "browser scene.layers[0].component.component_id must be "
                f"{FIXED_OVERLAY_SLOT!r}"
            )
        enabled = layer.get("enabled")
        if not isinstance(enabled, bool):
            raise SceneValidationError(
                "browser scene.layers[0].enabled must be boolean"
            )
        opacity = _byte(
            layer.get("opacity"), "browser scene.layers[0].opacity"
        )
        if layer.get("blend_mode") != "source_over":
            raise SceneValidationError(
                "browser scene.layers[0].blend_mode must be 'source_over'"
            )
        layers.append({
            "role": "clock",
            "component": component,
            "enabled": enabled,
            "opacity": opacity,
            "blend_mode": "source_over",
        })

    installation = _object(
        payload.get("installation_profile"),
        "browser scene.installation_profile",
    )
    _only(installation, {"digest"}, "browser scene.installation_profile")
    profile_digest = _sha256_digest(
        installation.get("digest"),
        "browser scene.installation_profile.digest",
    )
    fallback = _browser_component_ref(
        payload.get("fallback"),
        "browser scene.fallback",
        catalog=indexed_catalog,
        role="background",
        purpose=purpose,
    )
    if fallback["provider"] != "python":
        raise SceneValidationError("browser scene.fallback.provider must be 'python'")
    if background["provider"] == "python" and (
        fallback["component_id"] != background["component_id"]
        or fallback["parameters"] != background["parameters"]
    ):
        raise SceneValidationError(
            "browser scene.fallback must match a Python background exactly"
        )
    return {
        "schema": BROWSER_SCENE_SCHEMA,
        "schema_version": BROWSER_SCENE_VERSION,
        "revision": revision,
        "background": background,
        "layers": layers,
        "installation_profile": {"digest": profile_digest},
        "fallback": fallback,
    }


def browser_scene_to_host_scene(
    document: Mapping[str, Any],
    *,
    catalog: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Adapt an already-normalized browser document to the v1 host boundary."""
    indexed_catalog = _browser_catalog_index(catalog)

    def host_ref(component: Mapping[str, Any]) -> dict[str, Any]:
        provider = component["provider"]
        component_id = component["component_id"]
        descriptor = indexed_catalog[(provider, component_id)]
        defaults = descriptor.get("defaults")
        resolved = {
            name: value
            for name, value in (
                defaults.items() if isinstance(defaults, Mapping) else ()
            )
            if name not in SCENE_EXTERNAL_COMPONENT_PARAMETERS
        }
        overrides = {
            name: value
            for name, value in component["parameters"].items()
            if name not in SCENE_EXTERNAL_COMPONENT_PARAMETERS
        }
        resolved.update(overrides)
        result = {
            "plugin_id": component_id,
            "provider": provider,
            "parameter_overrides": overrides,
            "resolved_parameters": resolved,
        }
        if "preset_id" in component:
            result.update(
                preset_id=component["preset_id"],
                preset_fingerprint=component["preset_fingerprint"],
            )
        if provider == "receiver_native":
            capabilities = descriptor["browser_capabilities"]
            identity = capabilities["managed_identity"]
            result.update(
                bundle_digest=identity["bundle_digest"],
                expected_payload_digest=identity["expected_payload_digest"],
            )
        return result

    overlays = []
    if document["layers"]:
        layer = document["layers"][0]
        overlays.append({
            "slot_id": FIXED_OVERLAY_SLOT,
            "component": host_ref(layer["component"]),
            "enabled": layer["enabled"],
            "opacity": layer["opacity"],
            "placement": {
                "strip_translation": 0,
                "led_translation": 0,
                "clip_policy": "clip_to_wall",
            },
            "stale_policy": {"policy": "hold"},
        })
    return {
        "schema": SCENE_SCHEMA,
        "schema_version": SCENE_SCHEMA_VERSION,
        "revision": document["revision"],
        "background": host_ref(document["background"]),
        "overlays": overlays,
        "known_python_fallback": host_ref(document["fallback"]),
    }


def _component_ref(
    value: Any,
    label: str,
    *,
    expected_roles: set[str],
    catalog: Optional[Iterable[Mapping[str, Any]]],
    provider_policy: SceneProviderPolicy,
    allow_receiver_background: bool = False,
) -> dict[str, Any]:
    payload = _object(value, label)
    _only(
        payload,
        {
            "plugin_id", "provider", "preset_id", "preset_fingerprint",
            "parameter_overrides", "resolved_parameters", "bundle_digest",
            "expected_payload_digest",
        },
        label,
    )
    plugin_id = _identifier(payload.get("plugin_id"), f"{label}.plugin_id")
    provider = payload.get("provider", "python")
    if provider == "receiver_native":
        if (
            not allow_receiver_background
            or not provider_policy.allows_receiver_background(plugin_id)
        ):
            if not provider_policy.compiled_rainbow_enabled:
                # Preserve the feature-off Phase 2C validation response.
                raise SceneValidationError(
                    f"{label}.provider {provider!r} is unsupported; "
                    "Phase 2C supports python"
                )
            if label == "scene.known_python_fallback":
                raise SceneValidationError(
                    "scene.known_python_fallback must use the python provider"
                )
            if not provider_policy.managed_native_enabled:
                raise SceneValidationError(
                    "receiver_native scene backgrounds are limited to "
                    f"{COMPILED_RAINBOW_PLUGIN_ID!r} by the version 1 policy"
                )
            raise SceneValidationError(
                "receiver_native scene background is disabled by the active policy"
            )
        for field in ("bundle_digest", "expected_payload_digest"):
            digest = payload.get(field)
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                raise SceneValidationError(
                    f"receiver-native {label}.{field} must be a lowercase SHA-256 digest"
                )
    elif provider not in SUPPORTED_PROVIDERS:
        raise SceneValidationError(
            f"{label}.provider {provider!r} is unsupported; Phase 2C supports python"
        )
    else:
        for forbidden in ("bundle_digest", "expected_payload_digest"):
            if payload.get(forbidden) is not None:
                raise SceneValidationError(f"python {label} must not declare {forbidden}")
    preset_id = payload.get("preset_id")
    fingerprint = payload.get("preset_fingerprint")
    if preset_id is None:
        if fingerprint is not None:
            raise SceneValidationError(f"{label}.preset_fingerprint requires preset_id")
    else:
        _identifier(preset_id, f"{label}.preset_id")
        if not isinstance(fingerprint, str) or _SHA256.fullmatch(fingerprint) is None:
            raise SceneValidationError(
                f"{label}.preset_fingerprint must be a lowercase SHA-256 digest"
            )
    overrides = _object(payload.get("parameter_overrides", {}), f"{label}.parameter_overrides")
    resolved = _object(payload.get("resolved_parameters", {}), f"{label}.resolved_parameters")
    leaked = sorted(
        SCENE_EXTERNAL_COMPONENT_PARAMETERS
        & (set(overrides) | set(resolved))
    )
    if leaked:
        raise SceneValidationError(
            f"{label} must not capture scene-external state: "
            + ", ".join(leaked)
        )

    if catalog is not None:
        descriptor = catalog_index(catalog).get((plugin_id, provider))
        if descriptor is None:
            raise SceneValidationError(f"unknown {label} component {plugin_id!r}")
        _component_id, _provider, role = _catalog_identity(descriptor)
        if role not in expected_roles:
            raise SceneValidationError(
                f"{label} requires role {', '.join(sorted(expected_roles))}; "
                f"{plugin_id!r} declares {role!r}"
            )
        compatibility = descriptor.get("compatibility")
        if (
            isinstance(compatibility, dict)
            and compatibility.get("composable") is False
        ):
            raise SceneValidationError(
                f"{label} component {plugin_id!r} is not composable: "
                f"{compatibility.get('diagnostic', 'compatibility contract rejected it')}"
            )
        if provider == "receiver_native":
            build = descriptor.get("build")
            if (
                isinstance(build, Mapping)
                and build.get("identity_authority") != "managed_library"
            ):
                expected_bindings = {
                    "bundle_digest": build.get("contract_digest"),
                    "expected_payload_digest": build.get(
                        "expected_payload_digest"
                    ),
                }
                for field, expected_digest in expected_bindings.items():
                    if (
                        isinstance(expected_digest, str)
                        and payload[field] != expected_digest
                    ):
                        raise SceneValidationError(
                            f"{label}.{field} does not match the catalog binding"
                        )

    result: dict[str, Any] = {
        "plugin_id": plugin_id,
        "provider": provider,
        "parameter_overrides": overrides,
        "resolved_parameters": resolved,
    }
    if provider == "receiver_native":
        result.update(
            bundle_digest=payload["bundle_digest"],
            expected_payload_digest=payload["expected_payload_digest"],
        )
    if preset_id is not None:
        result.update(preset_id=preset_id, preset_fingerprint=fingerprint)
    return result


def normalize_scene_payload(
    value: Any,
    *,
    catalog: Optional[Iterable[Mapping[str, Any]]] = None,
    provider_policy: SceneProviderPolicy = DEFAULT_SCENE_PROVIDER_POLICY,
) -> dict[str, Any]:
    """Validate and canonicalize the fixed background + clock-overlay scene."""
    if not isinstance(provider_policy, SceneProviderPolicy):
        raise TypeError("provider_policy must be a SceneProviderPolicy")
    payload = _object(value, "scene")
    if "scene" in payload and not {"background", "overlays"}.intersection(payload):
        _only(payload, {"scene"}, "scene envelope")
        payload = _object(payload["scene"], "scene")
    _only(
        payload,
        {
            "schema", "schema_version", "revision", "background", "overlays",
            "known_python_fallback",
        },
        "scene",
    )
    if payload.get("schema", SCENE_SCHEMA) != SCENE_SCHEMA:
        raise SceneValidationError(f"unsupported scene schema {payload.get('schema')!r}")
    if payload.get("schema_version", SCENE_SCHEMA_VERSION) != SCENE_SCHEMA_VERSION:
        raise SceneValidationError(
            f"unsupported scene schema_version {payload.get('schema_version')!r}"
        )
    revision = _uint64(payload.get("revision", 0), "scene.revision")
    background = _component_ref(
        payload.get("background"), "scene.background",
        expected_roles={"background"}, catalog=catalog,
        provider_policy=provider_policy,
        allow_receiver_background=True,
    )
    fallback = _component_ref(
        payload.get("known_python_fallback", payload.get("background")),
        "scene.known_python_fallback",
        expected_roles={"background"}, catalog=catalog,
        provider_policy=provider_policy,
    )
    raw_overlays = payload.get("overlays", [])
    if not isinstance(raw_overlays, list) or len(raw_overlays) > 1:
        raise SceneValidationError("scene.overlays must contain at most the clock_overlay slot")

    overlays = []
    if raw_overlays:
        overlay = _object(raw_overlays[0], "scene.overlays[0]")
        _only(
            overlay,
            {"slot_id", "component", "enabled", "opacity", "placement", "stale_policy"},
            "scene overlay",
        )
        if overlay.get("slot_id") != FIXED_OVERLAY_SLOT:
            raise SceneValidationError(
                f"scene overlay slot_id must be {FIXED_OVERLAY_SLOT!r}"
            )
        component = _component_ref(
            overlay.get("component"), "scene overlay component",
            expected_roles={"overlay"}, catalog=catalog,
            provider_policy=provider_policy,
        )
        if component["plugin_id"] != FIXED_OVERLAY_SLOT:
            raise SceneValidationError(
                f"the {FIXED_OVERLAY_SLOT} slot requires that component"
            )
        enabled = overlay.get("enabled", True)
        if not isinstance(enabled, bool):
            raise SceneValidationError("scene overlay enabled must be boolean")
        opacity = _byte(overlay.get("opacity", 255), "scene overlay opacity")
        placement = _object(overlay.get("placement", {}), "scene overlay placement")
        _only(
            placement,
            {"strip_translation", "led_translation", "clip_policy"},
            "scene overlay placement",
        )
        clip_policy = placement.get("clip_policy", "clip_to_wall")
        if clip_policy != "clip_to_wall":
            raise SceneValidationError("scene overlay clip_policy must be 'clip_to_wall'")
        stale = _object(overlay.get("stale_policy", {"policy": "hold"}), "scene overlay stale_policy")
        _only(stale, {"policy", "lease_ms"}, "scene overlay stale_policy")
        policy = stale.get("policy", "hold")
        if policy not in {"hold", "clear_after_lease"}:
            raise SceneValidationError(
                "scene overlay stale policy must be 'hold' or 'clear_after_lease'"
            )
        lease_ms = stale.get("lease_ms")
        if policy == "clear_after_lease":
            if isinstance(lease_ms, bool) or not isinstance(lease_ms, int) or not 1 <= lease_ms < 2**32:
                raise SceneValidationError(
                    "clear_after_lease stale policy requires lease_ms from 1 to 4294967295"
                )
        elif lease_ms is not None:
            raise SceneValidationError("hold stale policy must not declare lease_ms")
        stale_result: dict[str, Any] = {"policy": policy}
        if lease_ms is not None:
            stale_result["lease_ms"] = lease_ms
        overlays.append({
            "slot_id": FIXED_OVERLAY_SLOT,
            "component": component,
            "enabled": enabled,
            "opacity": opacity,
            "placement": {
                "strip_translation": _signed32(
                    placement.get("strip_translation", 0),
                    "scene overlay strip_translation",
                ),
                "led_translation": _signed32(
                    placement.get("led_translation", 0),
                    "scene overlay led_translation",
                ),
                "clip_policy": clip_policy,
            },
            "stale_policy": stale_result,
        })

    return {
        "schema": SCENE_SCHEMA,
        "schema_version": SCENE_SCHEMA_VERSION,
        "revision": revision,
        "background": background,
        "overlays": overlays,
        "known_python_fallback": fallback,
    }


def background_only_scene(
    animation: str,
    params: Mapping[str, Any],
    *,
    preset_id: Optional[str] = None,
    preset_fingerprint: Optional[str] = None,
    revision: int = 0,
) -> dict[str, Any]:
    component: dict[str, Any] = {
        "plugin_id": _identifier(animation, "animation"),
        "provider": "python",
        "parameter_overrides": dict(params),
        "resolved_parameters": dict(params),
    }
    if preset_id is not None:
        component["preset_id"] = _identifier(preset_id, "preset_id")
        component["preset_fingerprint"] = preset_fingerprint or component_preset_fingerprint(
            animation, preset_id, params
        )
    return {
        "schema": SCENE_SCHEMA,
        "schema_version": SCENE_SCHEMA_VERSION,
        "revision": _uint64(revision, "revision"),
        "background": dict(component),
        "overlays": [],
        "known_python_fallback": dict(component),
    }


def scene_preview_identity(
    scene: Mapping[str, Any], vibe: Mapping[str, Any], plant_modifiers: Mapping[str, Any],
    *,
    elapsed: float = 0.0,
    provider_policy: SceneProviderPolicy = DEFAULT_SCENE_PROVIDER_POLICY,
) -> str:
    """Content identity for every visual preview input; never includes live objects."""
    canonical = {
        "scene": normalize_scene_payload(
            scene, provider_policy=provider_policy
        ),
        "vibe": jsonable(vibe),
        "plant_modifiers": jsonable(plant_modifiers),
        # Elapsed is the requested source/cadence point.  Quantized component
        # clocks may map multiple values to one frame, but they must never reuse
        # an identity produced for another requested preview point.
        "elapsed": elapsed,
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def normalize_global_settings_payload(value: Any) -> dict[str, Any]:
    """Canonicalize the controller-native global settings checked for activation."""

    payload = _object(value, "global settings")
    _only(
        payload,
        {
            "schema", "schema_version", "revision", "vibe",
            "plant_modifiers", "output",
        },
        "global settings",
    )
    if payload.get("schema") != GLOBAL_SETTINGS_SCHEMA:
        raise SceneValidationError(
            f"global settings.schema must be {GLOBAL_SETTINGS_SCHEMA!r}"
        )
    if payload.get("schema_version") != GLOBAL_SETTINGS_VERSION:
        raise SceneValidationError(
            "global settings.schema_version must be "
            f"{GLOBAL_SETTINGS_VERSION}"
        )
    revision = _uint64(payload.get("revision"), "global settings.revision")

    vibe = _object(payload.get("vibe"), "global settings.vibe")
    _only(
        vibe,
        {"vibe_id", "profile_version", "resolved_profile_digest"},
        "global settings.vibe",
    )
    vibe_id = _identifier(vibe.get("vibe_id"), "global settings.vibe.vibe_id")
    profile_version = vibe.get("profile_version")
    if (
        isinstance(profile_version, bool)
        or not isinstance(profile_version, int)
        or not 1 <= profile_version < 2**31
    ):
        raise SceneValidationError(
            "global settings.vibe.profile_version must be a positive "
            "signed 32-bit integer"
        )
    resolved_profile_digest = _sha256_digest(
        vibe.get("resolved_profile_digest"),
        "global settings.vibe.resolved_profile_digest",
    )

    raw_modifiers = _object(
        payload.get("plant_modifiers"), "global settings.plant_modifiers"
    )
    _only(
        raw_modifiers,
        {"version", "active", "strengths"},
        "global settings.plant_modifiers",
    )
    # Import lazily so the JSON contract remains cheap for catalog-only callers.
    from animation.core.plant_awareness import PlantModifierState

    try:
        plant_modifiers = PlantModifierState.from_payload(raw_modifiers).to_dict()
    except (TypeError, ValueError) as exc:
        raise SceneValidationError(
            f"invalid global settings.plant_modifiers: {exc}"
        ) from exc

    output = _object(payload.get("output"), "global settings.output")
    _only(
        output,
        {"power", "brightness", "animation_speed_scale", "target_fps"},
        "global settings.output",
    )
    power = output.get("power")
    if not isinstance(power, bool):
        raise SceneValidationError("global settings.output.power must be boolean")
    brightness = _byte(
        output.get("brightness"), "global settings.output.brightness"
    )
    animation_speed_scale = _finite_number(
        output.get("animation_speed_scale"),
        "global settings.output.animation_speed_scale",
        minimum=0.01,
        maximum=100.0,
    )
    target_fps = output.get("target_fps")
    if (
        isinstance(target_fps, bool)
        or not isinstance(target_fps, int)
        or not 1 <= target_fps <= 200
    ):
        raise SceneValidationError(
            "global settings.output.target_fps must be an integer from 1 to 200"
        )

    return {
        "schema": GLOBAL_SETTINGS_SCHEMA,
        "schema_version": GLOBAL_SETTINGS_VERSION,
        "revision": revision,
        "vibe": {
            "vibe_id": vibe_id,
            "profile_version": profile_version,
            "resolved_profile_digest": resolved_profile_digest,
        },
        "plant_modifiers": plant_modifiers,
        "output": {
            "power": power,
            "brightness": brightness,
            "animation_speed_scale": animation_speed_scale,
            "target_fps": target_fps,
        },
    }


def global_settings_digest(value: Any) -> str:
    """Return the identity of one fully normalized global-settings state."""

    return canonical_json_sha256(normalize_global_settings_payload(value))


_ACTIVATION_COMPONENT_SLOTS = (
    "background", FIXED_OVERLAY_SLOT, "known_python_fallback",
)


def normalize_activation_component_identity(value: Any) -> dict[str, Any]:
    """Normalize one complete authoring/controller runtime component binding."""

    payload = _object(value, "activation component identity")
    _only(
        payload,
        {
            "slot_id", "provider", "component_id", "component_digest",
            "browser_runtime_digest", "controller_runtime_digest",
            "parameter_schema_version", "bundle_digest",
            "expected_payload_digest",
        },
        "activation component identity",
    )
    slot_id = payload.get("slot_id")
    if slot_id not in _ACTIVATION_COMPONENT_SLOTS:
        raise SceneValidationError(
            "activation component identity.slot_id must be background, "
            f"{FIXED_OVERLAY_SLOT}, or known_python_fallback"
        )
    provider = _identifier(
        payload.get("provider"), "activation component identity.provider"
    )
    if provider not in KNOWN_PROVIDERS:
        raise SceneValidationError(
            "activation component identity.provider must be python or "
            "receiver_native"
        )
    component_id = _identifier(
        payload.get("component_id"),
        "activation component identity.component_id",
    )
    component_digest = _sha256_digest(
        payload.get("component_digest"),
        "activation component identity.component_digest",
    )
    browser_runtime_digest = _sha256_digest(
        payload.get("browser_runtime_digest"),
        "activation component identity.browser_runtime_digest",
    )
    controller_runtime_digest = _sha256_digest(
        payload.get("controller_runtime_digest"),
        "activation component identity.controller_runtime_digest",
    )
    schema_version = payload.get("parameter_schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or not 1 <= schema_version < 2**31
    ):
        raise SceneValidationError(
            "activation component identity.parameter_schema_version must be "
            "a positive signed 32-bit integer"
        )
    result = {
        "slot_id": slot_id,
        "provider": provider,
        "component_id": component_id,
        "component_digest": component_digest,
        "browser_runtime_digest": browser_runtime_digest,
        "controller_runtime_digest": controller_runtime_digest,
        "parameter_schema_version": schema_version,
    }
    native_digests = (
        payload.get("bundle_digest"), payload.get("expected_payload_digest")
    )
    if provider == "receiver_native":
        result.update(
            bundle_digest=_sha256_digest(
                native_digests[0],
                "activation component identity.bundle_digest",
            ),
            expected_payload_digest=_sha256_digest(
                native_digests[1],
                "activation component identity.expected_payload_digest",
            ),
        )
    elif any(item is not None for item in native_digests):
        raise SceneValidationError(
            "python activation component identity must not declare native digests"
        )
    return result


def _normalize_component_identities(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise SceneValidationError("activation components must be an array")
    components = [normalize_activation_component_identity(item) for item in value]
    slots = [item["slot_id"] for item in components]
    if len(slots) != len(set(slots)):
        raise SceneValidationError("activation components contain duplicate slots")
    required = {"background", "known_python_fallback"}
    if not required.issubset(slots):
        raise SceneValidationError(
            "activation components require background and known_python_fallback"
        )
    order = {slot: index for index, slot in enumerate(_ACTIVATION_COMPONENT_SLOTS)}
    return sorted(components, key=lambda item: order[item["slot_id"]])


def _normalize_digest_revision_identity(value: Any, label: str) -> dict[str, Any]:
    payload = _object(value, label)
    _only(payload, {"revision", "digest"}, label)
    return {
        "revision": _uint64(payload.get("revision"), f"{label}.revision"),
        "digest": _sha256_digest(payload.get("digest"), f"{label}.digest"),
    }


def normalize_activation_controller_identity(value: Any) -> dict[str, Any]:
    """Normalize the controller compare-and-swap identity bound by Check."""

    controller = _object(value, "activation controller identity")
    _only(
        controller,
        {"session_id", "state_revision", "current_identity_digest"},
        "activation controller identity",
    )
    session_id = controller.get("session_id")
    if (
        not isinstance(session_id, str)
        or _CONTROLLER_SESSION_ID.fullmatch(session_id) is None
    ):
        raise SceneValidationError(
            "activation controller identity.session_id must be a lowercase "
            "128-bit hexadecimal ID"
        )
    state_revision = _uint64(
        controller.get("state_revision"),
        "activation controller identity.state_revision",
    )
    current_identity_digest = controller.get("current_identity_digest")
    if current_identity_digest is not None:
        current_identity_digest = _sha256_digest(
            current_identity_digest,
            "activation controller identity.current_identity_digest",
        )
    return {
        "session_id": session_id,
        "state_revision": state_revision,
        "current_identity_digest": current_identity_digest,
    }


def normalize_activation_qualification(value: Any) -> dict[str, Any]:
    """Normalize the retained qualification identity and absolute expiry."""

    qualification = _object(value, "activation qualification")
    _only(
        qualification,
        {"version", "record_digest", "expires_at"},
        "activation qualification",
    )
    version = _identifier(
        qualification.get("version"), "activation qualification.version"
    )
    expires_at = _uint64(
        qualification.get("expires_at"), "activation qualification.expires_at"
    )
    if expires_at == 0:
        raise SceneValidationError(
            "activation qualification.expires_at must be positive"
        )
    record_digest = _sha256_digest(
        qualification.get("record_digest"),
        "activation qualification.record_digest",
    )
    return {
        "version": version,
        "record_digest": record_digest,
        "expires_at": expires_at,
    }


def normalize_scene_activation_basis(value: Any) -> dict[str, Any]:
    """Normalize the exact state a short-lived server Check authorizes."""

    payload = _object(value, "scene activation basis")
    _only(
        payload,
        {
            "schema", "schema_version", "browser_scene", "host_scene",
            "components", "installation_profile_digest", "global_settings",
            "controller", "qualification",
        },
        "scene activation basis",
    )
    if payload.get("schema") != SCENE_ACTIVATION_BASIS_SCHEMA:
        raise SceneValidationError(
            f"scene activation basis.schema must be "
            f"{SCENE_ACTIVATION_BASIS_SCHEMA!r}"
        )
    if payload.get("schema_version") != SCENE_ACTIVATION_BASIS_VERSION:
        raise SceneValidationError(
            "scene activation basis.schema_version must be "
            f"{SCENE_ACTIVATION_BASIS_VERSION}"
        )
    browser_scene = _normalize_digest_revision_identity(
        payload.get("browser_scene"), "scene activation basis.browser_scene"
    )
    host_scene = _normalize_digest_revision_identity(
        payload.get("host_scene"), "scene activation basis.host_scene"
    )
    if browser_scene["revision"] != host_scene["revision"]:
        raise SceneValidationError(
            "scene activation basis browser and host scene revisions must match"
        )
    components = _normalize_component_identities(payload.get("components"))
    installation_profile_digest = _sha256_digest(
        payload.get("installation_profile_digest"),
        "scene activation basis.installation_profile_digest",
    )
    global_settings = _normalize_digest_revision_identity(
        payload.get("global_settings"),
        "scene activation basis.global_settings",
    )

    controller = normalize_activation_controller_identity(
        payload.get("controller")
    )
    qualification = normalize_activation_qualification(
        payload.get("qualification")
    )

    return {
        "schema": SCENE_ACTIVATION_BASIS_SCHEMA,
        "schema_version": SCENE_ACTIVATION_BASIS_VERSION,
        "browser_scene": browser_scene,
        "host_scene": host_scene,
        "components": components,
        "installation_profile_digest": installation_profile_digest,
        "global_settings": global_settings,
        "controller": controller,
        "qualification": qualification,
    }


def scene_activation_basis_digest(value: Any) -> str:
    """Return the stable identity bound into a server-owned Check token."""

    return canonical_json_sha256(normalize_scene_activation_basis(value))


def _controller_runtime_digest(
    bindings: Mapping[str, Any], slot_id: str, component: Mapping[str, Any]
) -> str:
    qualified_id = f"{component['provider']}:{component['component_id']}"
    value = bindings.get(slot_id, bindings.get(qualified_id))
    return _sha256_digest(
        value,
        f"controller runtime digest for {slot_id}",
    )


def build_scene_activation_basis(
    *,
    browser_scene: Mapping[str, Any],
    catalog: Iterable[Mapping[str, Any]],
    global_settings: Mapping[str, Any],
    controller_runtime_digests: Mapping[str, Any],
    controller_session_id: str,
    controller_state_revision: int,
    current_identity_digest: Optional[str],
    qualification_version: str,
    qualification_record_digest: str,
    expires_at: int,
    host_scene: Optional[Mapping[str, Any]] = None,
    provider_policy: SceneProviderPolicy = DEFAULT_SCENE_PROVIDER_POLICY,
) -> dict[str, Any]:
    """Build a Check basis from the authoritative browser/catalog boundary.

    The host scene and component list are derived, not trusted caller summaries.
    Supplying ``host_scene`` asks the helper to verify it is byte-identical after
    normalization to the browser-derived host payload.
    """

    catalog_items = list(catalog)
    document = normalize_browser_scene_document(
        browser_scene, catalog=catalog_items, purpose="activation"
    )
    derived_host = normalize_scene_payload(
        browser_scene_to_host_scene(document, catalog=catalog_items),
        catalog=catalog_items or None,
        provider_policy=provider_policy,
    )
    if host_scene is not None:
        supplied_host = normalize_scene_payload(
            host_scene,
            catalog=catalog_items or None,
            provider_policy=provider_policy,
        )
        if supplied_host != derived_host:
            raise SceneValidationError(
                "supplied host scene does not match the normalized browser scene"
            )
    settings = normalize_global_settings_payload(global_settings)
    if not isinstance(controller_runtime_digests, Mapping):
        raise SceneValidationError("controller_runtime_digests must be an object")

    component_sources: list[tuple[str, Mapping[str, Any], Mapping[str, Any]]] = [
        ("background", document["background"], derived_host["background"]),
    ]
    if document["layers"]:
        component_sources.append((
            FIXED_OVERLAY_SLOT,
            document["layers"][0]["component"],
            derived_host["overlays"][0]["component"],
        ))
    component_sources.append((
        "known_python_fallback",
        document["fallback"],
        derived_host["known_python_fallback"],
    ))
    components = []
    for slot_id, browser_component, host_component in component_sources:
        identity = {
            "slot_id": slot_id,
            "provider": browser_component["provider"],
            "component_id": browser_component["component_id"],
            "component_digest": browser_component["component_digest"],
            "browser_runtime_digest": browser_component["runtime_digest"],
            "controller_runtime_digest": _controller_runtime_digest(
                controller_runtime_digests, slot_id, browser_component
            ),
            "parameter_schema_version": browser_component[
                "parameter_schema_version"
            ],
        }
        if browser_component["provider"] == "receiver_native":
            identity.update(
                bundle_digest=host_component["bundle_digest"],
                expected_payload_digest=host_component[
                    "expected_payload_digest"
                ],
            )
        components.append(identity)

    return normalize_scene_activation_basis({
        "schema": SCENE_ACTIVATION_BASIS_SCHEMA,
        "schema_version": SCENE_ACTIVATION_BASIS_VERSION,
        "browser_scene": {
            "revision": document["revision"],
            "digest": canonical_json_sha256(document),
        },
        "host_scene": {
            "revision": derived_host["revision"],
            "digest": canonical_json_sha256(derived_host),
        },
        "components": components,
        "installation_profile_digest": document[
            "installation_profile"
        ]["digest"],
        "global_settings": {
            "revision": settings["revision"],
            "digest": canonical_json_sha256(settings),
        },
        "controller": {
            "session_id": controller_session_id,
            "state_revision": controller_state_revision,
            "current_identity_digest": current_identity_digest,
        },
        "qualification": {
            "version": qualification_version,
            "record_digest": qualification_record_digest,
            "expires_at": expires_at,
        },
    })


def normalize_activation_identity(value: Any) -> dict[str, Any]:
    """Normalize one complete active or intentionally inactive wall identity.

    An inactive controller still has exact globals and profile identities.  It
    is represented by a null scene and an empty component list so rollback can
    prove restoration without inventing a placeholder scene or losing state.
    """

    payload = _object(value, "activation identity")
    _only(
        payload,
        {
            "scene_identity", "component_identities",
            "global_settings_identity", "installation_profile_digest",
        },
        "activation identity",
    )
    raw_scene = payload.get("scene_identity")
    raw_components = payload.get("component_identities")
    if raw_scene is None:
        if raw_components != []:
            raise SceneValidationError(
                "inactive activation identity requires an empty component list"
            )
        scene_identity = None
        component_identities: list[dict[str, Any]] = []
    else:
        scene_identity = _normalize_digest_revision_identity(
            raw_scene, "activation identity.scene_identity"
        )
        component_identities = _normalize_component_identities(raw_components)
    return {
        "scene_identity": scene_identity,
        "component_identities": component_identities,
        "global_settings_identity": _normalize_digest_revision_identity(
            payload.get("global_settings_identity"),
            "activation identity.global_settings_identity",
        ),
        "installation_profile_digest": _sha256_digest(
            payload.get("installation_profile_digest"),
            "activation identity.installation_profile_digest",
        ),
    }


def activation_identity_from_basis(value: Any) -> dict[str, Any]:
    """Project the exact desired/observed identity from a normalized basis."""

    basis = normalize_scene_activation_basis(value)
    return normalize_activation_identity({
        "scene_identity": basis["host_scene"],
        "component_identities": basis["components"],
        "global_settings_identity": basis["global_settings"],
        "installation_profile_digest": basis["installation_profile_digest"],
    })


def _host_scene_component_map(scene: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {
        "background": scene["background"],
        "known_python_fallback": scene["known_python_fallback"],
    }
    if scene["overlays"]:
        result[FIXED_OVERLAY_SLOT] = scene["overlays"][0]["component"]
    return result


def normalize_scene_activation_command(
    value: Any,
    *,
    catalog: Optional[Iterable[Mapping[str, Any]]] = None,
    provider_policy: SceneProviderPolicy = DEFAULT_SCENE_PROVIDER_POLICY,
    now: Optional[int] = None,
) -> dict[str, Any]:
    """Validate the one guarded command allowed to mutate complete wall state."""

    payload = _object(value, "scene activation command")
    _only(
        payload,
        {
            "schema", "schema_version", "activation_id", "check_token_digest",
            "basis", "basis_digest", "desired",
        },
        "scene activation command",
    )
    if payload.get("schema") != SCENE_ACTIVATION_COMMAND_SCHEMA:
        raise SceneValidationError(
            f"scene activation command.schema must be "
            f"{SCENE_ACTIVATION_COMMAND_SCHEMA!r}"
        )
    if payload.get("schema_version") != SCENE_ACTIVATION_COMMAND_VERSION:
        raise SceneValidationError(
            "scene activation command.schema_version must be "
            f"{SCENE_ACTIVATION_COMMAND_VERSION}"
        )
    activation_id = _opaque_id(
        payload.get("activation_id"), "scene activation command.activation_id",
        max_bytes=256,
    )
    check_token_digest = _sha256_digest(
        payload.get("check_token_digest"),
        "scene activation command.check_token_digest",
    )
    basis = normalize_scene_activation_basis(payload.get("basis"))
    basis_digest = _sha256_digest(
        payload.get("basis_digest"), "scene activation command.basis_digest"
    )
    if scene_activation_basis_digest(basis) != basis_digest:
        raise SceneValidationError(
            "scene activation command.basis_digest does not match basis"
        )
    if now is not None:
        now_value = _uint64(now, "scene activation command current time")
        if basis["qualification"]["expires_at"] <= now_value:
            raise SceneValidationError("scene activation command Check has expired")

    desired = _object(payload.get("desired"), "scene activation command.desired")
    _only(
        desired,
        {"scene", "global_settings", "installation_profile_digest"},
        "scene activation command.desired",
    )
    catalog_items = list(catalog) if catalog is not None else None
    scene = normalize_scene_payload(
        desired.get("scene"),
        catalog=catalog_items,
        provider_policy=provider_policy,
    )
    settings = normalize_global_settings_payload(desired.get("global_settings"))
    profile_digest = _sha256_digest(
        desired.get("installation_profile_digest"),
        "scene activation command.desired.installation_profile_digest",
    )
    if canonical_json_sha256(scene) != basis["host_scene"]["digest"]:
        raise SceneValidationError(
            "scene activation command desired scene does not match checked basis"
        )
    if scene["revision"] != basis["host_scene"]["revision"]:
        raise SceneValidationError(
            "scene activation command desired scene revision does not match checked basis"
        )
    if canonical_json_sha256(settings) != basis["global_settings"]["digest"]:
        raise SceneValidationError(
            "scene activation command desired global settings do not match checked basis"
        )
    if settings["revision"] != basis["global_settings"]["revision"]:
        raise SceneValidationError(
            "scene activation command desired global settings revision does not "
            "match checked basis"
        )
    if profile_digest != basis["installation_profile_digest"]:
        raise SceneValidationError(
            "scene activation command desired installation profile does not "
            "match checked basis"
        )

    scene_components = _host_scene_component_map(scene)
    if set(scene_components) != {
        component["slot_id"] for component in basis["components"]
    }:
        raise SceneValidationError(
            "scene activation command desired component slots do not match checked basis"
        )
    for identity in basis["components"]:
        component = scene_components[identity["slot_id"]]
        if (
            component["provider"] != identity["provider"]
            or component["plugin_id"] != identity["component_id"]
        ):
            raise SceneValidationError(
                f"scene activation command desired {identity['slot_id']} identity "
                "does not match checked basis"
            )
        if identity["provider"] == "receiver_native" and (
            component.get("bundle_digest") != identity["bundle_digest"]
            or component.get("expected_payload_digest")
            != identity["expected_payload_digest"]
        ):
            raise SceneValidationError(
                f"scene activation command desired {identity['slot_id']} native "
                "identity does not match checked basis"
            )

    return {
        "schema": SCENE_ACTIVATION_COMMAND_SCHEMA,
        "schema_version": SCENE_ACTIVATION_COMMAND_VERSION,
        "activation_id": activation_id,
        "check_token_digest": check_token_digest,
        "basis": basis,
        "basis_digest": basis_digest,
        "desired": {
            "scene": scene,
            "global_settings": settings,
            "installation_profile_digest": profile_digest,
        },
    }


def _optional_text(value: Any, label: str, *, max_bytes: int = 4096) -> Optional[str]:
    if value is None:
        return None
    return _opaque_id(value, label, max_bytes=max_bytes)


def _normalize_camera_observation(value: Any) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    payload = _object(value, "scene activation status.camera_observation")
    _only(
        payload,
        {"observed_at", "evidence_digest", "source"},
        "scene activation status.camera_observation",
    )
    return {
        "observed_at": _uint64(
            payload.get("observed_at"),
            "scene activation status.camera_observation.observed_at",
        ),
        "evidence_digest": _sha256_digest(
            payload.get("evidence_digest"),
            "scene activation status.camera_observation.evidence_digest",
        ),
        "source": _identifier(
            payload.get("source"),
            "scene activation status.camera_observation.source",
        ),
    }


def normalize_scene_activation_status(value: Any) -> dict[str, Any]:
    """Normalize one correlated activation status and enforce phase invariants."""

    payload = _object(value, "scene activation status")
    _only(
        payload,
        {
            "schema", "schema_version", "activation_id", "basis_digest",
            "command_id", "phase", "requested_identity", "normalized_identity",
            "observed_identity", "controller", "telemetry", "rollback",
            "camera_observation", "error",
        },
        "scene activation status",
    )
    if payload.get("schema") != SCENE_ACTIVATION_STATUS_SCHEMA:
        raise SceneValidationError(
            f"scene activation status.schema must be "
            f"{SCENE_ACTIVATION_STATUS_SCHEMA!r}"
        )
    if payload.get("schema_version") != SCENE_ACTIVATION_STATUS_VERSION:
        raise SceneValidationError(
            "scene activation status.schema_version must be "
            f"{SCENE_ACTIVATION_STATUS_VERSION}"
        )
    activation_id = _opaque_id(
        payload.get("activation_id"), "scene activation status.activation_id",
        max_bytes=256,
    )
    basis_digest = _sha256_digest(
        payload.get("basis_digest"), "scene activation status.basis_digest"
    )
    command_id = _optional_text(
        payload.get("command_id"), "scene activation status.command_id",
        max_bytes=256,
    )
    phase = payload.get("phase")
    if phase not in SCENE_ACTIVATION_PHASES:
        raise SceneValidationError(
            "scene activation status.phase is not a legal activation phase"
        )
    error = _optional_text(
        payload.get("error"), "scene activation status.error"
    )
    requested_identity = normalize_activation_identity(
        payload.get("requested_identity")
    )
    normalized_identity = normalize_activation_identity(
        payload.get("normalized_identity")
    )
    if (
        requested_identity["scene_identity"] is None
        or normalized_identity["scene_identity"] is None
    ):
        raise SceneValidationError(
            "scene activation requested and normalized identities must be active"
        )
    observed_raw = payload.get("observed_identity")
    observed_identity = (
        None if observed_raw is None else normalize_activation_identity(observed_raw)
    )

    controller = _object(
        payload.get("controller"), "scene activation status.controller"
    )
    _only(
        controller,
        {"session_id", "state_revision_before", "state_revision_after"},
        "scene activation status.controller",
    )
    session_id = controller.get("session_id")
    if (
        not isinstance(session_id, str)
        or _CONTROLLER_SESSION_ID.fullmatch(session_id) is None
    ):
        raise SceneValidationError(
            "scene activation status.controller.session_id must be a lowercase "
            "128-bit hexadecimal ID"
        )
    state_revision_before = _uint64(
        controller.get("state_revision_before"),
        "scene activation status.controller.state_revision_before",
    )
    state_revision_after = controller.get("state_revision_after")
    if state_revision_after is not None:
        state_revision_after = _uint64(
            state_revision_after,
            "scene activation status.controller.state_revision_after",
        )

    telemetry = _object(
        payload.get("telemetry"), "scene activation status.telemetry"
    )
    _only(
        telemetry,
        {"complete", "fresh", "observed_at"},
        "scene activation status.telemetry",
    )
    complete = telemetry.get("complete")
    fresh = telemetry.get("fresh")
    if not isinstance(complete, bool) or not isinstance(fresh, bool):
        raise SceneValidationError(
            "scene activation status telemetry complete and fresh must be boolean"
        )
    observed_at = telemetry.get("observed_at")
    if observed_at is not None:
        observed_at = _uint64(
            observed_at, "scene activation status.telemetry.observed_at"
        )
    if fresh and observed_at is None:
        raise SceneValidationError(
            "fresh scene activation telemetry requires observed_at"
        )

    rollback = _object(
        payload.get("rollback"), "scene activation status.rollback"
    )
    _only(
        rollback,
        {"available", "snapshot_id", "result", "error"},
        "scene activation status.rollback",
    )
    rollback_available = rollback.get("available")
    if not isinstance(rollback_available, bool):
        raise SceneValidationError(
            "scene activation status.rollback.available must be boolean"
        )
    snapshot_id = _optional_text(
        rollback.get("snapshot_id"),
        "scene activation status.rollback.snapshot_id",
        max_bytes=256,
    )
    if rollback_available and snapshot_id is None:
        raise SceneValidationError(
            "available scene activation rollback requires snapshot_id"
        )
    rollback_result = rollback.get("result")
    if rollback_result not in {None, "succeeded", "failed"}:
        raise SceneValidationError(
            "scene activation status.rollback.result must be succeeded, failed, or null"
        )
    rollback_error = _optional_text(
        rollback.get("error"), "scene activation status.rollback.error"
    )
    if rollback_result == "succeeded" and rollback_error is not None:
        raise SceneValidationError(
            "successful scene activation rollback must not include an error"
        )
    if rollback_result == "failed" and rollback_error is None:
        raise SceneValidationError(
            "failed scene activation rollback requires an error"
        )

    if phase == "active":
        if observed_identity != normalized_identity:
            raise SceneValidationError(
                "active scene activation status requires exact observed identity"
            )
        if not complete or not fresh:
            raise SceneValidationError(
                "active scene activation status requires complete fresh telemetry"
            )
        if (
            state_revision_after is None
            or state_revision_after <= state_revision_before
        ):
            raise SceneValidationError(
                "active scene activation status requires an advanced controller "
                "state revision"
            )
    if phase == "rolled_back" and rollback_result != "succeeded":
        raise SceneValidationError(
            "rolled_back scene activation status requires successful rollback"
        )
    if phase in {"failed", "timed_out"} and error is None:
        raise SceneValidationError(
            f"{phase} scene activation status requires an error"
        )
    exact_restoration_required = (
        phase in {"rolled_back", "failed", "timed_out"}
        and rollback_available
        and rollback_result == "succeeded"
    )
    if exact_restoration_required:
        if observed_identity is None or not complete or not fresh:
            raise SceneValidationError(
                "successful scene activation rollback requires an exact fresh "
                "observed identity"
            )
        if (
            state_revision_after is None
            or state_revision_after <= state_revision_before
        ):
            raise SceneValidationError(
                "successful scene activation rollback requires an advanced "
                "controller state revision"
            )

    return {
        "schema": SCENE_ACTIVATION_STATUS_SCHEMA,
        "schema_version": SCENE_ACTIVATION_STATUS_VERSION,
        "activation_id": activation_id,
        "basis_digest": basis_digest,
        "command_id": command_id,
        "phase": phase,
        "error": error,
        "requested_identity": requested_identity,
        "normalized_identity": normalized_identity,
        "observed_identity": observed_identity,
        "controller": {
            "session_id": session_id,
            "state_revision_before": state_revision_before,
            "state_revision_after": state_revision_after,
        },
        "telemetry": {
            "complete": complete,
            "fresh": fresh,
            "observed_at": observed_at,
        },
        "rollback": {
            "available": rollback_available,
            "snapshot_id": snapshot_id,
            "result": rollback_result,
            "error": rollback_error,
        },
        "camera_observation": _normalize_camera_observation(
            payload.get("camera_observation")
        ),
    }


_ACTIVATION_PHASE_TRANSITIONS = {
    "queued": frozenset(("queued", "preflighting", "failed", "timed_out")),
    "preflighting": frozenset((
        "preflighting", "applying", "failed", "timed_out",
    )),
    "applying": frozenset((
        "applying", "observing", "rolling_back", "failed", "timed_out",
    )),
    "observing": frozenset((
        "observing", "active", "rolling_back", "failed", "timed_out",
    )),
    "active": frozenset(("active", "rolling_back")),
    "rolling_back": frozenset((
        "rolling_back", "rolled_back", "failed", "timed_out",
    )),
    "rolled_back": frozenset(("rolled_back",)),
    "failed": frozenset(("failed",)),
    "timed_out": frozenset(("timed_out",)),
}


def validate_scene_activation_status_transition(
    previous: Any, current: Any
) -> dict[str, Any]:
    """Reject illegal or identity-changing updates to one activation receipt."""

    before = normalize_scene_activation_status(previous)
    after = normalize_scene_activation_status(current)
    restart_reconciliation = (
        before["phase"] == "active"
        and before["controller"]["session_id"]
        != after["controller"]["session_id"]
        and after["phase"] in {"active", "failed"}
        and after["rollback"]["available"] is False
        and after["rollback"]["snapshot_id"] is None
        and after["rollback"]["result"] is None
    )
    if (
        after["phase"] not in _ACTIVATION_PHASE_TRANSITIONS[before["phase"]]
        and not restart_reconciliation
    ):
        raise SceneValidationError(
            f"illegal scene activation phase transition "
            f"{before['phase']} -> {after['phase']}"
        )
    completed_rollback = (
        before["phase"] == "rolling_back"
        and after["phase"] == "rolled_back"
        and after["rollback"]["result"] == "succeeded"
    )
    for field in (
        "activation_id", "basis_digest", "requested_identity",
        "normalized_identity",
    ):
        if before[field] != after[field]:
            raise SceneValidationError(
                f"scene activation status {field} must not change"
            )
    if (
        before["controller"]["session_id"] != after["controller"]["session_id"]
        and not restart_reconciliation
    ):
        raise SceneValidationError(
            "scene activation status controller session must not change"
        )
    if (
        before["controller"]["state_revision_before"]
        != after["controller"]["state_revision_before"]
        and not restart_reconciliation
    ):
        raise SceneValidationError(
            "scene activation status initial controller revision must not change"
        )
    if before["command_id"] is not None and before["command_id"] != after["command_id"]:
        raise SceneValidationError(
            "scene activation status command_id must not change once assigned"
        )
    if (
        before["observed_identity"] is not None
        and before["observed_identity"] != after["observed_identity"]
        and not (completed_rollback or restart_reconciliation)
    ):
        raise SceneValidationError(
            "scene activation status observed identity must not change outside "
            "a completed rollback"
        )
    prior_after = before["controller"]["state_revision_after"]
    next_after = after["controller"]["state_revision_after"]
    if (
        prior_after is not None
        and prior_after != next_after
        and not (
            completed_rollback
            and next_after is not None
            and next_after > prior_after
        )
        and not restart_reconciliation
    ):
        raise SceneValidationError(
            "scene activation status resulting controller revision must not "
            "change outside a completed rollback"
        )
    for field in ("complete", "fresh"):
        if (
            before["telemetry"][field]
            and not after["telemetry"][field]
            and not restart_reconciliation
        ):
            raise SceneValidationError(
                f"scene activation status telemetry {field} must not regress"
            )
    if (
        before["telemetry"]["observed_at"] is not None
        and before["telemetry"]["observed_at"]
        != after["telemetry"]["observed_at"]
        and not (completed_rollback or restart_reconciliation)
    ):
        raise SceneValidationError(
            "scene activation status telemetry observed_at must not change "
            "outside a completed rollback"
        )
    if (
        before["rollback"]["snapshot_id"] is not None
        and before["rollback"]["snapshot_id"]
        != after["rollback"]["snapshot_id"]
        and not restart_reconciliation
    ):
        raise SceneValidationError(
            "scene activation status rollback snapshot must not change"
        )
    if (
        before["rollback"]["result"] is not None
        and before["rollback"]["result"] != after["rollback"]["result"]
    ):
        raise SceneValidationError(
            "scene activation status rollback result must not change"
        )
    return after
