"""Normalized component descriptors for the host animation catalog.

Manifest discovery deliberately stops at JSON metadata.  Python classes are
only inspected by :func:`bind_python_implementation` after the plugin loader has
already imported a selected implementation.  This keeps catalog discovery
side-effect free while still exposing the parameter schema needed by the live
host once loading is complete.
"""

from __future__ import annotations

import json
import math
import re
import struct
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Mapping, Optional, Type

from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT

from animation.component_parameters import SCENE_EXTERNAL_COMPONENT_PARAMETERS

from .base import AnimationBase, StatefulAnimationBase
from .presentation_contracts import (
    COMPONENT_DESCRIPTOR_SCHEMA,
    COMPONENT_DESCRIPTOR_VERSION,
    NEXT_DEADLINE_SEMANTICS,
    CadenceContract,
    ComponentDescriptor,
    ComponentProvider,
    ComponentRole,
    TimingAdapter,
    VIBE_CAPABILITIES,
    VIBE_COLOR_POLICIES,
    VIBE_PALETTE_ROLES,
)


_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_PYTHON_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PARAMETER_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_EXPLICIT_COMPONENT_FIELDS = frozenset(
    {"provider", "role", "entrypoint", "cadence"}
)
_IMPLEMENTATION_OWNED_FIELDS = frozenset(
    {"parameter_schema", "defaults", "controls"}
)
_NATIVE_MANIFEST_FIELDS = frozenset({
    "manifest_version", "plugin_id", "name", "description", "icon", "gallery",
    "provider", "role", "entrypoint", "cadence", "parameter_schema", "vibe",
    "installation_profile_requirements", "preview", "build", "geometry",
})
_NATIVE_BUILD_FIELDS = frozenset({
    "artifact_kind", "bundle_schema", "bundle_version", "abi_schema",
    "abi_version", "target", "source",
})
_NATIVE_PREVIEW_FIELDS = frozenset({
    "kind", "capture_seconds", "simulation_fps", "framebuffer_readback",
})
_NATIVE_GEOMETRY_FIELDS = frozenset({
    "global_strips", "leds_per_strip", "receiver_views",
})
_NATIVE_RECEIVER_VIEW_FIELDS = frozenset({
    "logical_receiver_id", "global_strip_offset", "local_strips",
    "reverse_local_strip_order",
})
_NATIVE_VIBE_REQUIRED_FIELDS = frozenset(
    {"color_policy", "timing_adapter", "capabilities"}
)
_NATIVE_VIBE_ALLOWED_FIELDS = _NATIVE_VIBE_REQUIRED_FIELDS | {"semantic_roles"}
_NATIVE_ENTRYPOINT = "ledgrid.native-background-abi:2"
_NATIVE_SOURCE = "native/background.cpp"
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1
_UINT64_MAX = 2**64 - 1


def _finite_float32(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value is not numeric")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("value does not fit a finite float") from exc
    if not math.isfinite(number):
        raise ValueError("value is not finite")
    try:
        decoded = struct.unpack(">f", struct.pack(">f", number))[0]
    except OverflowError as exc:
        raise ValueError("value does not fit float32") from exc
    if not math.isfinite(decoded) or (number != 0.0 and decoded == 0.0):
        raise ValueError("value does not fit finite nonzero float32")
    return number


def _capture_microseconds(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("capture time is not numeric")
    try:
        seconds = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("capture time does not fit a finite float") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("capture time is not finite and non-negative")
    try:
        microseconds = round(seconds * 1_000_000)
    except OverflowError as exc:
        raise ValueError("capture time does not fit uint64 microseconds") from exc
    if not 0 <= microseconds <= _UINT64_MAX:
        raise ValueError("capture time does not fit uint64 microseconds")
    return microseconds


def validate_and_normalize_manifest(
    payload: Dict[str, Any], manifest_path: Path, plugin_id: str
) -> Dict[str, Any]:
    """Validate one provider manifest without importing implementation code.

    Legacy manifests omit every explicit component field and are normalized to
    descriptor version 1.  Once any component field is authored, version 1 and
    the complete provider/role/entrypoint/cadence set are required.
    """
    if not _PLUGIN_ID.fullmatch(plugin_id):
        raise ValueError(f"invalid plugin package ID {plugin_id!r}: {manifest_path}")
    if payload.get("plugin_id") != plugin_id:
        raise ValueError(
            f"manifest plugin_id must match package directory {plugin_id!r}: "
            f"{manifest_path}"
        )

    if payload.get("provider") == ComponentProvider.RECEIVER_NATIVE.value:
        return _validate_and_normalize_native_manifest(
            payload, manifest_path, plugin_id
        )

    class_name = payload.get("class")
    if not isinstance(class_name, str) or not _PYTHON_CLASS.fullmatch(class_name):
        raise ValueError(
            f"manifest class must be a Python class identifier: {manifest_path}"
        )
    icon = payload.get("icon")
    if not isinstance(icon, str) or not icon.strip():
        raise ValueError(f"manifest icon must be a non-empty string: {manifest_path}")
    if payload.get("gallery", "show") not in {"show", "test"}:
        raise ValueError(f"manifest gallery must be 'show' or 'test': {manifest_path}")

    implementation_owned = _IMPLEMENTATION_OWNED_FIELDS.intersection(payload)
    if implementation_owned:
        raise ValueError(
            "manifest component controls must be declared by the Python "
            f"implementation, not {sorted(implementation_owned)}: {manifest_path}"
        )

    authored_fields = _EXPLICIT_COMPONENT_FIELDS.intersection(payload)
    manifest_version = payload.get("manifest_version")
    if authored_fields:
        missing = _EXPLICIT_COMPONENT_FIELDS.difference(payload)
        if missing:
            raise ValueError(
                "explicit component manifest must declare manifest_version and "
                f"provider/role/entrypoint/cadence; missing {sorted(missing)}: "
                f"{manifest_path}"
            )
    elif manifest_version is not None:
        raise ValueError(
            "manifest_version is only valid with explicit "
            f"provider/role/entrypoint/cadence: {manifest_path}"
        )

    if authored_fields:
        if payload["provider"] != ComponentProvider.PYTHON.value:
            raise ValueError(
                "manifest provider must be 'python' in the host component loader: "
                f"{manifest_path}"
            )
        if payload["role"] not in {role.value for role in ComponentRole}:
            raise ValueError(
                "manifest role must be background, overlay, or full_scene: "
                f"{manifest_path}"
            )
        expected_entrypoint = f"animation.plugins.{plugin_id}:{class_name}"
        if payload["entrypoint"] != expected_entrypoint:
            raise ValueError(
                f"manifest entrypoint must be {expected_entrypoint!r}: {manifest_path}"
            )
        payload["cadence"] = normalize_cadence(payload["cadence"], manifest_path)
        if manifest_version != COMPONENT_DESCRIPTOR_VERSION or isinstance(
            manifest_version, bool
        ):
            raise ValueError(
                "explicit component manifest_version must be 1: "
                f"{manifest_path}"
            )

    # Copying here prevents later loader binding from mutating caller-owned JSON.
    normalized = _json_copy(payload, f"manifest {manifest_path}")
    normalized["manifest_version"] = COMPONENT_DESCRIPTOR_VERSION
    normalized["_legacy_component_manifest"] = not bool(authored_fields)
    return normalized


def _validate_and_normalize_native_manifest(
    payload: Dict[str, Any], manifest_path: Path, plugin_id: str
) -> Dict[str, Any]:
    """Validate the strict repository-native source descriptor contract."""
    missing = _NATIVE_MANIFEST_FIELDS.difference(payload)
    unknown = set(payload).difference(_NATIVE_MANIFEST_FIELDS)
    if missing or unknown:
        raise ValueError(
            "receiver-native provider manifest fields "
            f"missing={sorted(missing)} unknown={sorted(unknown)}: {manifest_path}"
        )
    if manifest_path.parent.is_symlink():
        raise ValueError(
            "receiver-native package directory must not be a symlink: "
            f"{manifest_path.parent}"
        )
    init_path = manifest_path.parent / "__init__.py"
    if init_path.exists():
        raise ValueError(
            "receiver-native package must not contain __init__.py: "
            f"{manifest_path.parent}"
        )
    version = payload["manifest_version"]
    if type(version) is not int or version != COMPONENT_DESCRIPTOR_VERSION:
        raise ValueError(
            f"receiver-native manifest_version must be 1: {manifest_path}"
        )
    for name in ("name", "description", "icon"):
        value = payload[name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"receiver-native manifest {name} must be non-empty: {manifest_path}"
            )
    if payload["gallery"] not in {"show", "test"}:
        raise ValueError(
            f"receiver-native manifest gallery must be 'show' or 'test': {manifest_path}"
        )
    if payload["role"] != ComponentRole.BACKGROUND.value:
        raise ValueError(
            f"receiver-native manifest role must be 'background': {manifest_path}"
        )
    if payload["entrypoint"] != _NATIVE_ENTRYPOINT:
        raise ValueError(
            "receiver-native manifest entrypoint must be "
            f"{_NATIVE_ENTRYPOINT!r}: {manifest_path}"
        )

    cadence = normalize_cadence(payload["cadence"], manifest_path)
    if cadence["mode"] != "fixed_fps":
        raise ValueError(
            f"receiver-native manifest cadence must be fixed_fps: {manifest_path}"
        )
    preferred_fps = cadence.get("preferred_fps")
    if preferred_fps is None or not 1 <= preferred_fps <= 200:
        raise ValueError(
            "receiver-native preferred_fps must be from 1 to 200: "
            f"{manifest_path}"
        )

    parameter_schema = _normalize_native_parameter_schema(
        payload["parameter_schema"], manifest_path
    )
    requirements = payload["installation_profile_requirements"]
    if (
        not isinstance(requirements, list)
        or len(requirements) > 16
        or any(
            not isinstance(item, str) or not _PARAMETER_ID.fullmatch(item)
            or len(item) > 48
            for item in requirements
        )
        or len(requirements) != len(set(requirements))
    ):
        raise ValueError(
            "receiver-native installation_profile_requirements must be a unique "
            f"list of at most 16 identifiers no longer than 48 characters: {manifest_path}"
        )
    vibe = _normalize_native_vibe(payload["vibe"], manifest_path)
    preview = _normalize_native_preview(payload["preview"], manifest_path)
    build = _normalize_native_build(payload["build"], manifest_path)
    geometry = _normalize_native_geometry(payload["geometry"], manifest_path)

    normalized = _json_copy(payload, f"manifest {manifest_path}")
    normalized["cadence"] = cadence
    normalized["parameter_schema"] = parameter_schema
    normalized["installation_profile_requirements"] = sorted(requirements)
    normalized["vibe"] = vibe
    normalized["preview"] = preview
    normalized["build"] = build
    normalized["geometry"] = geometry
    normalized["_legacy_component_manifest"] = False
    return normalized


def _normalize_native_geometry(value: Any, manifest_path: Path) -> Dict[str, Any]:
    """Require the exact finalized heterogeneous receiver topology.

    The package owns an explicit geometry binding so a previously built 32-strip
    artifact cannot become selectable after the physical wall changes.  The ABI
    remains width-generic; this is the product/package compatibility gate.
    """
    from animation.native.constants import (
        GLOBAL_STRIPS,
        LEDS_PER_STRIP,
        RECEIVER_VIEWS,
    )

    if not isinstance(value, Mapping) or set(value) != _NATIVE_GEOMETRY_FIELDS:
        actual = set(value) if isinstance(value, Mapping) else set()
        raise ValueError(
            "receiver-native geometry fields "
            f"missing={sorted(_NATIVE_GEOMETRY_FIELDS - actual)} "
            f"unknown={sorted(actual - _NATIVE_GEOMETRY_FIELDS)}: {manifest_path}"
        )
    views = value.get("receiver_views")
    if not isinstance(views, list):
        raise ValueError(
            f"receiver-native geometry.receiver_views must be an array: {manifest_path}"
        )
    normalized_views: list[Dict[str, Any]] = []
    for index, raw in enumerate(views):
        if not isinstance(raw, Mapping) or set(raw) != _NATIVE_RECEIVER_VIEW_FIELDS:
            raise ValueError(
                "receiver-native geometry receiver view fields are invalid at "
                f"index {index}: {manifest_path}"
            )
        logical_id = raw["logical_receiver_id"]
        offset = raw["global_strip_offset"]
        local_strips = raw["local_strips"]
        reverse = raw["reverse_local_strip_order"]
        if (
            type(logical_id) is not int
            or type(offset) is not int
            or type(local_strips) is not int
            or type(reverse) is not bool
            or logical_id < 0
            or offset < 0
            or local_strips < 1
            or offset + local_strips > GLOBAL_STRIPS
        ):
            raise ValueError(
                f"receiver-native geometry receiver view is invalid at index {index}: "
                f"{manifest_path}"
            )
        normalized_views.append({
            "logical_receiver_id": logical_id,
            "global_strip_offset": offset,
            "local_strips": local_strips,
            "reverse_local_strip_order": reverse,
        })
    expected_views = [
        {
            "logical_receiver_id": logical_id,
            "global_strip_offset": offset,
            "local_strips": local_strips,
            "reverse_local_strip_order": reverse,
        }
        for logical_id, offset, local_strips, reverse in RECEIVER_VIEWS
    ]
    normalized = {
        "global_strips": value.get("global_strips"),
        "leds_per_strip": value.get("leds_per_strip"),
        "receiver_views": normalized_views,
    }
    expected = {
        "global_strips": GLOBAL_STRIPS,
        "leds_per_strip": LEDS_PER_STRIP,
        "receiver_views": expected_views,
    }
    if normalized != expected:
        raise ValueError(
            "receiver-native geometry must match the finalized installed topology: "
            f"{manifest_path}"
        )
    return _json_copy(normalized, "receiver-native geometry")


def _normalize_native_vibe(value: Any, manifest_path: Path) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"receiver-native vibe must be an object: {manifest_path}")
    missing = _NATIVE_VIBE_REQUIRED_FIELDS.difference(value)
    unknown = set(value).difference(_NATIVE_VIBE_ALLOWED_FIELDS)
    if missing or unknown:
        raise ValueError(
            "receiver-native vibe fields "
            f"missing={sorted(missing)} unknown={sorted(unknown)}: {manifest_path}"
        )
    color_policy = value["color_policy"]
    timing_adapter = value["timing_adapter"]
    capabilities = value["capabilities"]
    semantic_roles = value.get("semantic_roles", [])
    if color_policy not in VIBE_COLOR_POLICIES:
        raise ValueError(f"receiver-native vibe color_policy is invalid: {manifest_path}")
    if timing_adapter not in {adapter.value for adapter in TimingAdapter}:
        raise ValueError(f"receiver-native vibe timing_adapter is invalid: {manifest_path}")
    if (
        not isinstance(capabilities, list)
        or any(
            not isinstance(item, str) or item not in VIBE_CAPABILITIES
            for item in capabilities
        )
        or len(capabilities) != len(set(capabilities))
    ):
        raise ValueError(f"receiver-native vibe capabilities are invalid: {manifest_path}")
    if (
        not isinstance(semantic_roles, list)
        or any(
            not isinstance(item, str) or item not in VIBE_PALETTE_ROLES
            for item in semantic_roles
        )
        or len(semantic_roles) != len(set(semantic_roles))
    ):
        raise ValueError(f"receiver-native vibe semantic_roles are invalid: {manifest_path}")
    if timing_adapter == TimingAdapter.SCALED_CONTEXT.value and "tempo" not in capabilities:
        raise ValueError(f"receiver-native scaled_context vibe requires tempo: {manifest_path}")
    if timing_adapter == TimingAdapter.WALL_CLOCK.value and "tempo" in capabilities:
        raise ValueError(f"receiver-native wall_clock vibe cannot claim tempo: {manifest_path}")
    if color_policy == "semantic" and (
        "palette_roles" not in capabilities or not semantic_roles
    ):
        raise ValueError(
            f"receiver-native semantic vibe requires palette roles: {manifest_path}"
        )
    if color_policy != "semantic" and semantic_roles:
        raise ValueError(
            f"only receiver-native semantic vibe may declare roles: {manifest_path}"
        )
    if color_policy == "preserve" and "palette_roles" in capabilities:
        raise ValueError(
            f"receiver-native preserve vibe cannot claim palette roles: {manifest_path}"
        )
    normalized = {
        "color_policy": color_policy,
        "timing_adapter": timing_adapter,
        "capabilities": sorted(capabilities),
        "semantic_roles": sorted(semantic_roles),
    }
    return normalized


def _normalize_native_parameter_schema(
    value: Any, manifest_path: Path
) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, Mapping) or len(value) > 31:
        raise ValueError(
            "receiver-native parameter_schema must be an object with at most 31 "
            f"parameters: {manifest_path}"
        )
    normalized: Dict[str, Dict[str, Any]] = {}
    for name, raw_definition in value.items():
        if (
            not isinstance(name, str)
            or len(name) > 48
            or not _PARAMETER_ID.fullmatch(name)
            or name in SCENE_EXTERNAL_COMPONENT_PARAMETERS
        ):
            raise ValueError(
                f"invalid receiver-native parameter name {name!r}: {manifest_path}"
            )
        if not isinstance(raw_definition, Mapping):
            raise ValueError(
                f"receiver-native parameter {name!r} must be an object: {manifest_path}"
            )
        definition = dict(raw_definition)
        kind = definition.get("type")
        allowed = {"type", "default", "description"}
        if kind in {"int", "float"}:
            allowed.update({"min", "max"})
        elif kind == "str":
            allowed.add("options")
        elif kind != "bool":
            raise ValueError(
                f"receiver-native parameter {name!r} has unsupported type {kind!r}: "
                f"{manifest_path}"
            )
        unknown = set(definition).difference(allowed)
        missing = {"type", "default", "description"}.difference(definition)
        if kind in {"int", "float"}:
            missing.update({"min", "max"}.difference(definition))
        elif kind == "str":
            missing.update({"options"}.difference(definition))
        if missing or unknown:
            raise ValueError(
                f"receiver-native parameter {name!r} fields missing={sorted(missing)} "
                f"unknown={sorted(unknown)}: {manifest_path}"
            )
        description = definition["description"]
        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description) > 240
        ):
            raise ValueError(
                f"receiver-native parameter {name!r} description is invalid: "
                f"{manifest_path}"
            )
        _validate_native_parameter_value(
            name, definition, definition["default"], manifest_path=manifest_path
        )
        normalized[name] = _json_copy(
            definition, f"receiver-native parameter_schema.{name}"
        )
    return normalized


def _validate_native_parameter_value(
    name: str,
    definition: Mapping[str, Any],
    value: Any,
    *,
    manifest_path: Optional[Path] = None,
) -> None:
    suffix = f": {manifest_path}" if manifest_path is not None else ""
    kind = definition.get("type")
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"receiver-native parameter {name!r} must be bool{suffix}")
        return
    if kind == "int":
        bounds = (definition.get("min"), definition.get("max"))
        if any(isinstance(item, bool) or not isinstance(item, int) for item in bounds):
            raise ValueError(
                f"receiver-native parameter {name!r} requires integer bounds{suffix}"
            )
        lower, upper = bounds
        if not _INT32_MIN <= lower <= upper <= _INT32_MAX:
            raise ValueError(
                f"receiver-native parameter {name!r} bounds must fit int32{suffix}"
            )
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"receiver-native parameter {name!r} must be int{suffix}")
        if not lower <= value <= upper:
            raise ValueError(
                f"receiver-native parameter {name!r} is outside its bounds{suffix}"
            )
        return
    if kind == "float":
        values = (definition.get("min"), definition.get("max"), value)
        try:
            lower, upper, selected = (
                _finite_float32(item) for item in values
            )
        except ValueError as exc:
            raise ValueError(
                f"receiver-native parameter {name!r} requires finite float32 "
                f"numbers{suffix}"
            ) from exc
        if lower > upper or not lower <= selected <= upper:
            raise ValueError(
                f"receiver-native parameter {name!r} is outside its bounds{suffix}"
            )
        return
    if kind == "str":
        options = definition.get("options")
        if (
            not isinstance(options, list)
            or not 1 <= len(options) <= 64
            or any(
                not isinstance(option, str)
                or not option
                or len(option.encode("utf-8")) > 63
                for option in options
            )
            or len(options) != len(set(options))
        ):
            raise ValueError(
                f"receiver-native parameter {name!r} options are invalid{suffix}"
            )
        if not isinstance(value, str) or value not in options:
            raise ValueError(
                f"receiver-native parameter {name!r} must be one of {options!r}{suffix}"
            )
        return
    raise ValueError(
        f"receiver-native parameter {name!r} has unsupported type {kind!r}{suffix}"
    )


def _normalize_native_preview(value: Any, manifest_path: Path) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _NATIVE_PREVIEW_FIELDS:
        actual = set(value) if isinstance(value, Mapping) else set()
        raise ValueError(
            "receiver-native preview fields "
            f"missing={sorted(_NATIVE_PREVIEW_FIELDS - actual)} "
            f"unknown={sorted(actual - _NATIVE_PREVIEW_FIELDS)}: {manifest_path}"
        )
    if value["kind"] != "native_host_build":
        raise ValueError(
            f"receiver-native preview kind must be native_host_build: {manifest_path}"
        )
    if value["framebuffer_readback"] is not False:
        raise ValueError(
            f"receiver-native preview framebuffer_readback must be false: {manifest_path}"
        )
    captures = value["capture_seconds"]
    try:
        capture_microseconds = [
            _capture_microseconds(item) for item in captures
        ] if isinstance(captures, list) else []
    except ValueError as exc:
        raise ValueError(
            "receiver-native preview capture_seconds must fit uint64 "
            f"microseconds: {manifest_path}"
        ) from exc
    if (
        not isinstance(captures, list)
        or not 2 <= len(captures) <= 16
        or any(
            float(left) >= float(right)
            for left, right in zip(captures, captures[1:])
        )
        or any(
            left >= right
            for left, right in zip(
                capture_microseconds, capture_microseconds[1:]
            )
        )
    ):
        raise ValueError(
            "receiver-native preview capture_seconds must be 2-16 strictly "
            "increasing uint64-microsecond-representable non-negative numbers: "
            f"{manifest_path}"
        )
    simulation_fps = value["simulation_fps"]
    if (
        isinstance(simulation_fps, bool)
        or not isinstance(simulation_fps, int)
        or not 1 <= simulation_fps <= 120
    ):
        raise ValueError(
            f"receiver-native preview simulation_fps must be 1-120: {manifest_path}"
        )
    return _json_copy(dict(value), "receiver-native preview")


def _normalize_native_build(value: Any, manifest_path: Path) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _NATIVE_BUILD_FIELDS:
        actual = set(value) if isinstance(value, Mapping) else set()
        raise ValueError(
            "receiver-native build fields "
            f"missing={sorted(_NATIVE_BUILD_FIELDS - actual)} "
            f"unknown={sorted(actual - _NATIVE_BUILD_FIELDS)}: {manifest_path}"
        )
    if (
        type(value["bundle_version"]) is not int
        or type(value["abi_version"]) is not int
    ):
        raise ValueError(
            f"receiver-native build versions must be integers: {manifest_path}"
        )
    source_value = value.get("source")
    if isinstance(source_value, str):
        source = PurePosixPath(source_value)
        if source.is_absolute() or ".." in source.parts:
            raise ValueError(
                f"receiver-native source path escapes package: {manifest_path}"
            )
    expected = {
        "artifact_kind": "receiver_native_module",
        "bundle_schema": "ledgrid.native-background-bundle",
        "bundle_version": 1,
        "abi_schema": "ledgrid.native-background-abi",
        "abi_version": 2,
        "target": "esp32-s3",
        "source": _NATIVE_SOURCE,
    }
    if dict(value) != expected:
        raise ValueError(
            "receiver-native build contract must exactly match the v1 ABI-v2 "
            f"source contract: {manifest_path}"
        )
    return _json_copy(dict(value), "receiver-native build")


def normalize_cadence(value: Any, manifest_path: Path) -> Dict[str, Any]:
    """Validate the bounded v1 fixed-FPS or event-driven cadence shape."""
    if not isinstance(value, dict):
        raise ValueError(f"manifest cadence must be an object: {manifest_path}")
    allowed = {"mode", "preferred_fps", "next_deadline_semantics"}
    unknown = set(value).difference(allowed)
    if unknown:
        raise ValueError(
            f"manifest cadence has unsupported keys {sorted(unknown)}: {manifest_path}"
        )
    mode = value.get("mode")
    preferred_fps = value.get("preferred_fps")
    deadline = value.get("next_deadline_semantics", NEXT_DEADLINE_SEMANTICS)
    try:
        contract = CadenceContract(
            mode=mode,
            preferred_fps=preferred_fps,
            next_deadline_semantics=deadline,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid manifest cadence: {manifest_path}: {exc}") from exc
    normalized = {
        "mode": contract.mode.value,
        "next_deadline_semantics": contract.next_deadline_semantics,
    }
    if contract.preferred_fps is not None:
        normalized["preferred_fps"] = contract.preferred_fps
    return normalized


def scanned_descriptor(
    plugin_id: str,
    manifest: Optional[Mapping[str, Any]],
    *,
    flat_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create conservative safe metadata for a scanned package or flat plugin."""
    payload = dict(manifest or {})
    legacy = True if manifest is None else bool(payload.get("_legacy_component_manifest"))
    class_name = payload.get("class")
    explicit = not legacy
    role = payload.get("role", "full_scene" if plugin_id == "clock" else "background")
    entrypoint = payload.get("entrypoint")
    if not entrypoint:
        if class_name:
            entrypoint = f"animation.plugins.{plugin_id}:{class_name}"
        else:
            entrypoint = f"{plugin_id}:<implementation-unloaded>"
    cadence_payload = payload.get("cadence") or {
        "mode": "event_driven",
        "next_deadline_semantics": NEXT_DEADLINE_SEMANTICS,
    }
    vibe = payload.get("vibe") if isinstance(payload.get("vibe"), dict) else {}

    provider = payload.get("provider", "python")
    if provider == ComponentProvider.RECEIVER_NATIVE.value:
        classification = "receiver_native_source"
        diagnostic = (
            "Trusted repository native source metadata is available for build "
            "and host preview; receiver activation remains disabled until Phase 4."
        )
    elif flat_file is not None:
        classification = "external_flat_plugin"
        diagnostic = "External Python implementation has not been classified."
    elif plugin_id == "clock":
        classification = "legacy_clock"
        diagnostic = "Compatibility Clock owns the complete scene; use clock_overlay for composition."
    elif explicit:
        classification = "declared_component"
        diagnostic = "Declared Python component is awaiting implementation binding."
    else:
        classification = "legacy_animation"
        diagnostic = "Legacy Python component is awaiting implementation classification."

    descriptor = _descriptor_dict(
        plugin_id=plugin_id,
        name=payload.get("name", _display_name(plugin_id)),
        description=payload.get("description", "Python animation component"),
        icon=payload.get("icon", "✨"),
        gallery=payload.get("gallery", "show"),
        provider=provider,
        role=role,
        entrypoint=entrypoint,
        parameter_schema=payload.get("parameter_schema", {}),
        defaults={
            name: definition["default"]
            for name, definition in payload.get("parameter_schema", {}).items()
        },
        cadence=cadence_payload,
        timing_adapter=vibe.get("timing_adapter", TimingAdapter.LEGACY_SPEED_PARAM.value),
        vibe_color_policy=vibe.get("color_policy", "preserve"),
        vibe_capabilities=tuple(vibe.get("capabilities", ())),
        preview=payload.get("preview", {}),
        installation_profile_requirements=tuple(
            payload.get("installation_profile_requirements", ())
        ),
        build=payload.get("build", {}),
    )
    if provider == ComponentProvider.RECEIVER_NATIVE.value:
        descriptor["geometry"] = _json_copy(
            payload.get("geometry", {}), f"native geometry {plugin_id}"
        )
    descriptor["compatibility"] = {
        "legacy_manifest": legacy,
        "classification": classification,
        # Native source metadata is complete without loading a Python class. Its
        # role is composable even though provider policy keeps it non-executable.
        "composable": provider == ComponentProvider.RECEIVER_NATIVE.value,
        "implementation_loaded": False,
        "parameter_metadata": (
            "manifest"
            if provider == ComponentProvider.RECEIVER_NATIVE.value
            else "implementation_not_loaded"
        ),
        "diagnostic": diagnostic,
    }
    return descriptor


def bind_python_implementation(
    descriptor: Mapping[str, Any], animation_class: Type[AnimationBase]
) -> Dict[str, Any]:
    """Enrich one scanned descriptor after its Python class has been loaded."""
    if not isinstance(animation_class, type) or not issubclass(
        animation_class, AnimationBase
    ):
        raise TypeError("component implementation must inherit AnimationBase")

    class _CatalogController:
        strip_count = DEFAULT_STRIP_COUNT
        leds_per_strip = DEFAULT_LEDS_PER_STRIP
        total_leds = strip_count * leds_per_strip
        debug = False

    instance = animation_class(_CatalogController())
    schema = _normalize_parameter_schema(instance.get_parameter_schema())
    authored_defaults = instance.authored_params_snapshot()
    # Existing plugins occasionally refined their constructor defaults without
    # updating the descriptive schema inherited from AnimationBase.  The loaded
    # descriptor is an execution contract, so its defaults must reproduce actual
    # no-config construction rather than silently changing behavior in a scene.
    for name, definition in schema.items():
        if name in authored_defaults:
            definition["default"] = _json_copy(
                authored_defaults[name], f"component default {name}"
            )
    defaults = {name: definition["default"] for name, definition in schema.items()}
    plugin_id = str(descriptor["plugin_id"])
    declared_role = str(descriptor["role"])
    is_stateful = issubclass(animation_class, StatefulAnimationBase)
    role = "full_scene" if is_stateful or plugin_id == "clock" else declared_role
    composable = role in {"background", "overlay"} and not is_stateful

    if is_stateful:
        classification = "stateful_animation"
        diagnostic = (
            "StatefulAnimationBase controls complete-output timing and cannot be "
            "used in a composed host scene."
        )
    elif role == "full_scene":
        classification = "full_scene_component"
        diagnostic = "Full-scene compatibility component cannot be composed."
    elif descriptor["compatibility"]["legacy_manifest"]:
        classification = "animation_base"
        diagnostic = "Legacy AnimationBase component classified through the Python adapter."
    else:
        classification = "declared_component"
        diagnostic = "Versioned Python component validated and bound."

    rebound = _json_copy(descriptor, f"descriptor {plugin_id}")
    rebound.update(
        {
            "name": str(getattr(animation_class, "ANIMATION_NAME", animation_class.__name__)),
            "description": str(
                getattr(animation_class, "ANIMATION_DESCRIPTION", "No description")
            ),
            "entrypoint": f"{animation_class.__module__}:{animation_class.__name__}",
            "role": role,
            "parameter_schema": schema,
            "defaults": defaults,
        }
    )
    rebound["compatibility"].update(
        {
            "classification": classification,
            "composable": composable,
            "implementation_loaded": True,
            "parameter_metadata": "loaded",
            "diagnostic": diagnostic,
        }
    )
    return rebound


def painter_descriptor() -> Dict[str, Any]:
    """Return the catalog-only descriptor for the existing painter output mode."""
    descriptor = _descriptor_dict(
        plugin_id="painter",
        name="Painter",
        description="Direct manually-authored complete-frame output",
        icon="🎨",
        gallery="test",
        provider="python",
        role="full_scene",
        entrypoint="compatibility:painter",
        parameter_schema={},
        defaults={},
        cadence={
            "mode": "event_driven",
            "next_deadline_semantics": NEXT_DEADLINE_SEMANTICS,
        },
        timing_adapter=TimingAdapter.WALL_CLOCK.value,
        vibe_color_policy="preserve",
        vibe_capabilities=(),
        preview={},
    )
    descriptor["compatibility"] = {
        "legacy_manifest": True,
        "classification": "painter",
        "composable": False,
        "implementation_loaded": True,
        "parameter_metadata": "not_applicable",
        "diagnostic": "Painter owns complete output and is isolated from composed scenes.",
    }
    return descriptor


def filter_catalog(
    descriptors: Iterable[Mapping[str, Any]],
    *,
    provider: Optional[str] = None,
    role: Optional[str] = None,
) -> list[Dict[str, Any]]:
    """Return deterministic JSON copies matching optional provider and role."""
    if provider is not None:
        try:
            provider = ComponentProvider(provider).value
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported component provider {provider!r}") from exc
    if role is not None:
        try:
            role = ComponentRole(role).value
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported component role {role!r}") from exc
    return [
        _json_copy(item, f"descriptor {item.get('plugin_id', '?')}")
        for item in sorted(descriptors, key=lambda value: str(value["plugin_id"]))
        if (provider is None or item["provider"] == provider)
        and (role is None or item["role"] == role)
    ]


def color_policy_inventory(
    descriptors: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Generate a deterministic policy audit from a unified component catalog."""
    components = []
    identities = set()
    counts = {policy: 0 for policy in sorted(VIBE_COLOR_POLICIES)}
    for descriptor in sorted(
        descriptors,
        key=lambda value: (
            str(value.get("provider", "")),
            str(value.get("plugin_id", "")),
        ),
    ):
        identity = (descriptor.get("provider"), descriptor.get("plugin_id"))
        if identity in identities:
            raise ValueError(f"duplicate component descriptor identity {identity!r}")
        identities.add(identity)
        policy = descriptor.get("vibe_color_policy")
        if policy not in VIBE_COLOR_POLICIES:
            raise ValueError(
                f"component {identity!r} lacks an explicit canonical color policy"
            )
        capabilities = tuple(sorted(descriptor.get("vibe_capabilities", ())))
        unsupported = set(capabilities).difference(VIBE_CAPABILITIES)
        if unsupported:
            raise ValueError(
                f"component {identity!r} has unsupported vibe capabilities "
                f"{sorted(unsupported)}"
            )
        if policy == "semantic" and "palette_roles" not in capabilities:
            raise ValueError(
                f"semantic component {identity!r} must claim palette_roles"
            )
        if policy == "preserve" and "palette_roles" in capabilities:
            raise ValueError(
                f"preserve component {identity!r} cannot claim palette_roles"
            )
        counts[policy] += 1
        components.append({
            "plugin_id": identity[1],
            "provider": identity[0],
            "role": descriptor.get("role"),
            "color_policy": policy,
            "vibe_capabilities": list(capabilities),
            "vibe_enabled": bool(capabilities),
        })
    return {
        "schema": "ledgrid.component-color-policy-inventory",
        "component_count": len(components),
        "counts": counts,
        "components": components,
    }


def validate_parameter_overrides(
    descriptor: Mapping[str, Any], values: Mapping[str, Any]
) -> Dict[str, Any]:
    """Reject component controls that its loaded implementation did not declare."""
    if not isinstance(values, Mapping):
        raise TypeError("component controls must be an object")
    compatibility = descriptor.get("compatibility", {})
    metadata = compatibility.get("parameter_metadata")
    if metadata not in {"loaded", "manifest"}:
        raise ValueError(
            f"component {descriptor.get('plugin_id')!r} has no loaded parameter schema"
        )
    schema = descriptor.get("parameter_schema", {})
    unknown = set(values).difference(schema)
    if unknown:
        raise ValueError(
            f"component controls contain undeclared parameters {sorted(unknown)}"
        )
    if descriptor.get("provider") == ComponentProvider.RECEIVER_NATIVE.value:
        for name, value in values.items():
            _validate_native_parameter_value(name, schema[name], value)
    return _json_copy(dict(values), "component controls")


def _descriptor_dict(
    *,
    plugin_id: str,
    name: str,
    description: str,
    icon: str,
    gallery: str,
    provider: str,
    role: str,
    entrypoint: str,
    parameter_schema: Mapping[str, Any],
    defaults: Mapping[str, Any],
    cadence: Mapping[str, Any],
    timing_adapter: str,
    vibe_color_policy: str,
    vibe_capabilities: tuple[str, ...],
    preview: Mapping[str, Any],
    installation_profile_requirements: tuple[str, ...] = (),
    build: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    build = {} if build is None else build
    cadence_contract = CadenceContract(
        cadence["mode"],
        preferred_fps=cadence.get("preferred_fps"),
        next_deadline_semantics=cadence.get(
            "next_deadline_semantics", NEXT_DEADLINE_SEMANTICS
        ),
    )
    validated = ComponentDescriptor(
        manifest_version=COMPONENT_DESCRIPTOR_VERSION,
        plugin_id=plugin_id,
        name=name,
        description=description,
        icon=icon,
        gallery=gallery,
        provider=provider,
        role=role,
        entrypoint=entrypoint,
        parameter_schema=parameter_schema,
        defaults=defaults,
        cadence=cadence_contract,
        timing_adapter=timing_adapter,
        vibe_color_policy=vibe_color_policy,
        vibe_capabilities=vibe_capabilities,
        installation_profile_requirements=installation_profile_requirements,
        preview=preview,
        build=build,
    )
    return {
        "schema": COMPONENT_DESCRIPTOR_SCHEMA,
        "manifest_version": validated.manifest_version,
        "plugin_id": validated.plugin_id,
        "name": validated.name,
        "description": validated.description,
        "icon": validated.icon,
        "gallery": validated.gallery,
        "provider": validated.provider.value,
        "role": validated.role.value,
        "entrypoint": validated.entrypoint,
        "parameter_schema": _json_copy(parameter_schema, "parameter_schema"),
        "defaults": _json_copy(defaults, "defaults"),
        "cadence": {
            "mode": cadence_contract.mode.value,
            **(
                {"preferred_fps": cadence_contract.preferred_fps}
                if cadence_contract.preferred_fps is not None
                else {}
            ),
            "next_deadline_semantics": cadence_contract.next_deadline_semantics,
        },
        "timing_adapter": validated.timing_adapter.value,
        "vibe_color_policy": validated.vibe_color_policy,
        "vibe_capabilities": list(validated.vibe_capabilities),
        "installation_profile_requirements": list(
            validated.installation_profile_requirements
        ),
        "preview": _json_copy(preview, "preview"),
        "build": _json_copy(build, "build"),
    }


def _normalize_parameter_schema(value: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise TypeError("component parameter schema must be an object")
    normalized: Dict[str, Dict[str, Any]] = {}
    for name, definition in value.items():
        if not isinstance(name, str) or not _PARAMETER_ID.fullmatch(name):
            raise ValueError(f"component parameter name must be an identifier: {name!r}")
        if not isinstance(definition, Mapping):
            raise TypeError(f"component parameter {name!r} schema must be an object")
        if "default" not in definition:
            raise ValueError(f"component parameter {name!r} must declare a default")
        normalized[name] = _json_copy(dict(definition), f"parameter_schema.{name}")
    return normalized


def _json_copy(value: Any, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain finite JSON-compatible values") from exc


def _display_name(plugin_id: str) -> str:
    return " ".join(part.capitalize() for part in plugin_id.split("_"))
