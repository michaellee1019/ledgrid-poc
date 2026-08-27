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

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UNSAFE_JSON_KEYS = frozenset(("__proto__", "constructor", "prototype"))


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
    saveable = previewable and not provider_collision

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
    elif provider_collision:
        reason = "Provider-qualified storage is unavailable for this component ID collision."
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
        },
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
        resolved = dict(defaults) if isinstance(defaults, Mapping) else {}
        resolved.update(component["parameters"])
        result = {
            "plugin_id": component_id,
            "provider": provider,
            "parameter_overrides": dict(component["parameters"]),
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
