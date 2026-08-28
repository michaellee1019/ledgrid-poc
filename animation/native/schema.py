"""Canonical JSON and strict generated-bundle value validation.

Repository component manifests are deliberately *not* validated here.  The
catalog loader owns that contract and the builder consumes its normalized
``component_manifests`` entry.  This module validates only values embedded in
the generated, executable bundle.
"""

from __future__ import annotations

import json
import math
import re
import struct
from collections.abc import Mapping, Sequence
from typing import Any

from animation.component_parameters import SCENE_EXTERNAL_COMPONENT_PARAMETERS

from .constants import (
    ABI_HEADER_PATH,
    ABI_SCHEMA,
    ABI_VERSION,
    BUNDLE_SCHEMA,
    BUNDLE_VERSION,
    COMPONENT_ENTRYPOINT,
    GLOBAL_STRIPS,
    LEDS_PER_STRIP,
    LOCAL_STRIPS,
    MAX_PARAMETERS,
    MAX_PAYLOAD_BYTES,
    MAX_PREVIEW_BYTES,
    PARAMETER_TYPES,
    PAYLOAD_PATH,
    PREVIEW_PATH,
    RECEIVER_VIEWS,
    TARGET,
    TARGET_IDENTITY_FLAGS,
    HOST_IDENTITY_FLAGS,
    HOST_LINK_FLAGS,
    EXPECTED_PLATFORMIO_VERSION,
    TARGET_TOOLCHAIN_PACKAGE,
    EXPECTED_TARGET_TOOLCHAIN_VERSION,
    TARGET_COMPILER_NAME,
    TARGET_DYNCONFIG_NAME,
)
from .errors import NativeManifestError

_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_PARAMETER_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1
_UINT64_MAX = 2**64 - 1
_VIBE_CAPABILITIES = frozenset(("palette_roles", "tempo", "luminance"))
_VIBE_COLOR_POLICIES = frozenset(("semantic", "grade", "preserve"))
_TIMING_ADAPTERS = frozenset(("legacy_speed_param", "scaled_context", "wall_clock"))
_SEMANTIC_ROLES = frozenset((
    "background_low", "background_mid", "background_high", "primary",
    "secondary", "accent", "hud", "warning",
))


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NativeManifestError(f"value is not canonical JSON: {exc}") from exc


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NativeManifestError(f"JSON object contains duplicate field {key!r}")
        result[key] = value
    return result


def parse_canonical_json(data: bytes, *, label: str) -> Any:
    def reject_constant(value: str) -> None:
        raise NativeManifestError(f"{label} contains non-finite value {value}")

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=reject_constant,
        )
    except NativeManifestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeManifestError(f"invalid {label}: {exc}") from exc
    if canonical_json(value) != data:
        raise NativeManifestError(f"{label} is not canonical JSON")
    return value


def _object(value: Any, *, label: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NativeManifestError(f"{label} must be an object")
    if set(value) != fields:
        raise NativeManifestError(
            f"{label} fields missing={sorted(fields - set(value))} "
            f"unknown={sorted(set(value) - fields, key=repr)}"
        )
    return value


def _text(value: Any, *, label: str, maximum: int = 240) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
    ):
        raise NativeManifestError(f"{label} must be a non-empty bounded string")
    return value


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise NativeManifestError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _finite(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeManifestError(f"{label} must be numeric")
    try:
        number = float(value)
    except OverflowError as exc:
        raise NativeManifestError(f"{label} must be finite") from exc
    if not math.isfinite(number):
        raise NativeManifestError(f"{label} must be finite")
    return number


def _float32(value: Any, *, label: str) -> float:
    number = _finite(value, label=label)
    try:
        decoded = struct.unpack(">f", struct.pack(">f", number))[0]
    except OverflowError as exc:
        raise NativeManifestError(f"{label} must fit float32") from exc
    if not math.isfinite(decoded) or (number != 0.0 and decoded == 0.0):
        raise NativeManifestError(f"{label} must fit finite nonzero float32")
    return number


def _capture_microseconds(value: Any) -> int:
    seconds = _finite(value, label="preview capture time")
    if seconds < 0:
        raise NativeManifestError("preview capture time must be non-negative")
    try:
        microseconds = round(seconds * 1_000_000)
    except OverflowError as exc:
        raise NativeManifestError(
            "preview capture time must fit uint64 microseconds"
        ) from exc
    if not 0 <= microseconds <= _UINT64_MAX:
        raise NativeManifestError(
            "preview capture time must fit uint64 microseconds"
        )
    return microseconds


def validate_parameter_value(name: str, spec: Mapping[str, Any], value: Any) -> Any:
    kind = spec["type"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise NativeManifestError(f"parameter {name!r} must be bool")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise NativeManifestError(f"parameter {name!r} must be int")
        if not spec["min"] <= value <= spec["max"]:
            raise NativeManifestError(f"parameter {name!r} is outside its bounds")
        return value
    if kind == "float":
        number = _float32(value, label=f"parameter {name!r}")
        if not float(spec["min"]) <= number <= float(spec["max"]):
            raise NativeManifestError(f"parameter {name!r} is outside its bounds")
        return number
    if kind == "str":
        if not isinstance(value, str) or value not in spec["options"]:
            raise NativeManifestError(f"parameter {name!r} is not an allowed option")
        return value
    raise NativeManifestError(f"parameter {name!r} has unsupported type {kind!r}")


def validate_parameter_schema(value: Any) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if not isinstance(value, dict) or len(value) > MAX_PARAMETERS:
        raise NativeManifestError(
            f"parameter_schema must be an object with at most {MAX_PARAMETERS} entries"
        )
    for name in value:
        if (
            not isinstance(name, str)
            or _PARAMETER_ID.fullmatch(name) is None
            or name in SCENE_EXTERNAL_COMPONENT_PARAMETERS
        ):
            raise NativeManifestError(f"invalid parameter name {name!r}")
    schema: dict[str, dict[str, Any]] = {}
    defaults: dict[str, Any] = {}
    for name in sorted(value):
        raw = value[name]
        kind = raw.get("type") if isinstance(raw, dict) else None
        if (
            not isinstance(raw, dict)
            or not isinstance(kind, str)
            or kind not in PARAMETER_TYPES
        ):
            raise NativeManifestError(f"parameter {name!r} has invalid schema")
        fields = {"type", "default", "description"}
        if kind in {"int", "float"}:
            fields.update(("min", "max"))
        elif kind == "str":
            fields.add("options")
        if set(raw) != fields:
            raise NativeManifestError(f"parameter {name!r} has unsupported fields")
        _text(raw["description"], label=f"parameter {name!r} description")
        spec = dict(raw)
        if kind == "int":
            if any(isinstance(raw[key], bool) or not isinstance(raw[key], int) for key in ("min", "max")):
                raise NativeManifestError(f"parameter {name!r} requires integer bounds")
            if not _INT32_MIN <= raw["min"] <= raw["max"] <= _INT32_MAX:
                raise NativeManifestError(f"parameter {name!r} bounds must fit int32")
        elif kind == "float":
            if _float32(raw["min"], label=f"{name}.min") > _float32(raw["max"], label=f"{name}.max"):
                raise NativeManifestError(f"parameter {name!r} min exceeds max")
        elif kind == "str":
            options = raw["options"]
            if (
                not isinstance(options, list)
                or not 1 <= len(options) <= 64
                or any(not isinstance(item, str) or not item or len(item.encode("utf-8")) > 63 for item in options)
                or len(options) != len(set(options))
            ):
                raise NativeManifestError(f"parameter {name!r} options are invalid")
        defaults[name] = validate_parameter_value(name, spec, raw["default"])
        schema[name] = spec
    return schema, defaults


def validate_parameters(
    schema: Mapping[str, Mapping[str, Any]], values: Mapping[str, Any] | None
) -> dict[str, Any]:
    if values is not None and not isinstance(values, Mapping):
        raise NativeManifestError("parameters must be an object")
    supplied = dict(values or {})
    unknown = set(supplied).difference(schema)
    if unknown:
        raise NativeManifestError(
            f"unknown parameters: {sorted(unknown, key=repr)}"
        )
    return {
        name: validate_parameter_value(name, spec, supplied.get(name, spec["default"]))
        for name, spec in sorted(schema.items())
    }


def validate_bundle_manifest(value: Any) -> dict[str, Any]:
    fields = {
        "schema", "schema_version", "plugin_id", "component_manifest_sha256",
        "entrypoint", "abi", "target", "geometry", "cadence", "parameter_schema",
        "defaults", "vibe", "installation_profile_requirements", "build", "payload",
        "preview",
    }
    manifest = _object(value, label="native bundle manifest", fields=fields)
    if (
        manifest["schema"] != BUNDLE_SCHEMA
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != BUNDLE_VERSION
    ):
        raise NativeManifestError("unsupported native bundle schema/version")
    if not isinstance(manifest["plugin_id"], str) or _PLUGIN_ID.fullmatch(manifest["plugin_id"]) is None:
        raise NativeManifestError("native bundle plugin_id is invalid")
    _digest(manifest["component_manifest_sha256"], label="component manifest digest")
    if manifest["entrypoint"] != COMPONENT_ENTRYPOINT:
        raise NativeManifestError("native bundle entrypoint is incompatible")
    abi = _object(manifest["abi"], label="bundle ABI", fields={"schema", "version", "header_path", "header_sha256"})
    if (
        abi["schema"] != ABI_SCHEMA
        or type(abi["version"]) is not int
        or abi["version"] != ABI_VERSION
        or abi["header_path"] != ABI_HEADER_PATH
    ):
        raise NativeManifestError("native bundle ABI identity is incompatible")
    _digest(abi["header_sha256"], label="ABI header digest")
    target = _object(manifest["target"], label="bundle target", fields={"name", "elf_class", "endianness", "machine", "type"})
    expected_target = {
        "name": TARGET,
        "elf_class": 32,
        "endianness": "little",
        "machine": "xtensa",
        "type": "shared_object",
    }
    if canonical_json(target) != canonical_json(expected_target):
        raise NativeManifestError("native bundle target is incompatible")
    geometry = _object(manifest["geometry"], label="bundle geometry", fields={"global_strips", "local_strips", "leds_per_strip", "receiver_offsets", "receiver_views"})
    expected_geometry = {
        "global_strips": GLOBAL_STRIPS,
        "local_strips": LOCAL_STRIPS,
        "leds_per_strip": LEDS_PER_STRIP,
        "receiver_offsets": [0, 8, 16, 24, 32],
        "receiver_views": [
            {"logical_receiver_id": logical_id, "global_strip_offset": offset,
             "local_strips": local_strips, "reverse_local_strip_order": reverse}
            for logical_id, offset, local_strips, reverse in RECEIVER_VIEWS
        ],
    }
    if canonical_json(geometry) != canonical_json(expected_geometry):
        raise NativeManifestError("native bundle geometry is incompatible")
    cadence = _object(
        manifest["cadence"],
        label="bundle cadence",
        fields={
            "mode", "preferred_fps", "next_deadline_semantics",
            "abi_next_deadline_semantics",
        },
    )
    if cadence["mode"] != "fixed_fps" or not 1 <= _finite(cadence["preferred_fps"], label="preferred_fps") <= 200:
        raise NativeManifestError("native bundle cadence is incompatible")
    if cadence["next_deadline_semantics"] != "absolute_unscaled_seconds_since_scene_epoch":
        raise NativeManifestError("native bundle deadline semantics are incompatible")
    if cadence["abi_next_deadline_semantics"] != "absolute_unscaled_microseconds_since_scene_epoch":
        raise NativeManifestError("native bundle ABI deadline unit is incompatible")
    schema, defaults = validate_parameter_schema(manifest["parameter_schema"])
    if canonical_json(manifest["defaults"]) != canonical_json(defaults):
        raise NativeManifestError("native bundle defaults do not match parameter schema")
    vibe = _object(
        manifest["vibe"],
        label="bundle vibe",
        fields={"color_policy", "timing_adapter", "capabilities", "semantic_roles"},
    )
    if (
        not isinstance(vibe["color_policy"], str)
        or vibe["color_policy"] not in _VIBE_COLOR_POLICIES
    ):
        raise NativeManifestError("native bundle vibe color policy is invalid")
    if (
        not isinstance(vibe["timing_adapter"], str)
        or vibe["timing_adapter"] not in _TIMING_ADAPTERS
    ):
        raise NativeManifestError("native bundle vibe timing adapter is invalid")
    capabilities = vibe["capabilities"]
    roles = vibe["semantic_roles"]
    if (
        not isinstance(capabilities, list)
        or any(not isinstance(item, str) for item in capabilities)
        or capabilities != sorted(capabilities)
        or len(capabilities) != len(set(capabilities))
        or any(item not in _VIBE_CAPABILITIES for item in capabilities)
        or not isinstance(roles, list)
        or any(not isinstance(item, str) for item in roles)
        or roles != sorted(roles)
        or len(roles) != len(set(roles))
        or any(item not in _SEMANTIC_ROLES for item in roles)
    ):
        raise NativeManifestError("native bundle vibe capabilities or roles are invalid")
    if vibe["timing_adapter"] == "scaled_context" and "tempo" not in capabilities:
        raise NativeManifestError("scaled-context native vibe must claim tempo")
    if vibe["timing_adapter"] == "wall_clock" and "tempo" in capabilities:
        raise NativeManifestError("wall-clock native vibe cannot claim tempo")
    if vibe["color_policy"] == "semantic" and (
        "palette_roles" not in capabilities or not roles
    ):
        raise NativeManifestError("semantic native vibe requires palette roles")
    if vibe["color_policy"] != "semantic" and roles:
        raise NativeManifestError("only semantic native vibe may declare roles")
    if vibe["color_policy"] == "preserve" and "palette_roles" in capabilities:
        raise NativeManifestError("preserve native vibe cannot claim palette roles")
    requirements = manifest["installation_profile_requirements"]
    if (
        not isinstance(requirements, list)
        or len(requirements) > 16
        or any(
            not isinstance(item, str) or _PARAMETER_ID.fullmatch(item) is None
            for item in requirements
        )
        or requirements != sorted(requirements)
        or len(requirements) != len(set(requirements))
    ):
        raise NativeManifestError("native bundle installation requirements are invalid")
    build = _object(
        manifest["build"],
        label="bundle build",
        fields={"source_path", "source_sha256", "source_inputs", "target_flags", "host_flags", "toolchains", "host_artifact_sha256"},
    )
    if build["source_path"] != "native/background.cpp":
        raise NativeManifestError("native bundle source path is incompatible")
    _digest(build["source_sha256"], label="source digest")
    _digest(build["host_artifact_sha256"], label="host artifact digest")
    expected_source_paths = sorted((
        f"animation/plugins/{manifest['plugin_id']}/native/background.cpp",
        ABI_HEADER_PATH,
    ))
    if not isinstance(build["source_inputs"], list) or [item.get("path") if isinstance(item, dict) else None for item in build["source_inputs"]] != expected_source_paths:
        raise NativeManifestError("native bundle source inputs are incomplete, duplicated, or unsafe")
    for index, item in enumerate(build["source_inputs"]):
        entry = _object(item, label=f"source_inputs[{index}]", fields={"path", "sha256"})
        path = _text(entry["path"], label=f"source_inputs[{index}].path")
        if path.startswith("/") or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/")):
            raise NativeManifestError(f"source_inputs[{index}].path is unsafe")
        _digest(entry["sha256"], label=f"source_inputs[{index}].sha256")
    source_by_path = {item["path"]: item["sha256"] for item in build["source_inputs"]}
    if build["source_sha256"] != source_by_path[
        f"animation/plugins/{manifest['plugin_id']}/native/background.cpp"
    ]:
        raise NativeManifestError("source digest contradicts source_inputs")
    if abi["header_sha256"] != source_by_path[ABI_HEADER_PATH]:
        raise NativeManifestError("ABI header digest contradicts source_inputs")
    if build["target_flags"] != list(TARGET_IDENTITY_FLAGS):
        raise NativeManifestError("target build flags do not match the frozen contract")
    allowed_host_flags = [
        list(HOST_IDENTITY_FLAGS + suffix) for suffix in HOST_LINK_FLAGS.values()
    ]
    if build["host_flags"] not in allowed_host_flags:
        raise NativeManifestError("host build flags do not match a supported frozen contract")
    toolchains = _object(build["toolchains"], label="bundle toolchains", fields={"target", "host", "preview_codec"})
    target_toolchain = _object(
        toolchains["target"],
        label="target toolchain",
        fields={"platformio_version", "package", "package_version", "compiler", "compiler_sha256", "compiler_version", "dynconfig", "dynconfig_sha256"},
    )
    if target_toolchain["platformio_version"] != EXPECTED_PLATFORMIO_VERSION or target_toolchain["package"] != TARGET_TOOLCHAIN_PACKAGE or target_toolchain["package_version"] != EXPECTED_TARGET_TOOLCHAIN_VERSION or target_toolchain["compiler"] != TARGET_COMPILER_NAME:
        raise NativeManifestError("target toolchain identity is incompatible")
    _digest(target_toolchain["compiler_sha256"], label="target compiler digest")
    _text(target_toolchain["compiler_version"], label="target compiler version", maximum=500)
    if target_toolchain["dynconfig"] != TARGET_DYNCONFIG_NAME:
        raise NativeManifestError("target dynamic configuration identity is incompatible")
    _digest(target_toolchain["dynconfig_sha256"], label="target dynconfig digest")
    host_toolchain = _object(
        toolchains["host"],
        label="host toolchain",
        fields={"compiler", "compiler_sha256", "compiler_version", "platform", "target", "endianness"},
    )
    _text(host_toolchain["compiler"], label="host compiler", maximum=80)
    _digest(host_toolchain["compiler_sha256"], label="host compiler digest")
    _text(host_toolchain["compiler_version"], label="host compiler version", maximum=500)
    if (
        not isinstance(host_toolchain["platform"], str)
        or host_toolchain["platform"] not in HOST_LINK_FLAGS
    ):
        raise NativeManifestError("host toolchain platform is unsupported")
    if host_toolchain["endianness"] != "little":
        raise NativeManifestError("host toolchain endianness is incompatible")
    if build["host_flags"] != list(
        HOST_IDENTITY_FLAGS + HOST_LINK_FLAGS[host_toolchain["platform"]]
    ):
        raise NativeManifestError("host build flags contradict the host platform")
    _text(host_toolchain["target"], label="host compiler target", maximum=160)
    codec = _object(
        toolchains["preview_codec"],
        label="preview codec",
        fields={"name", "pillow_version", "webp_version"},
    )
    if codec["name"] != "pillow-webp-lossless":
        raise NativeManifestError("preview codec identity is incompatible")
    _text(codec["pillow_version"], label="Pillow version", maximum=80)
    _text(codec["webp_version"], label="WebP version", maximum=80)
    payload = _object(manifest["payload"], label="bundle payload", fields={"path", "size", "sha256"})
    if payload["path"] != PAYLOAD_PATH or isinstance(payload["size"], bool) or not isinstance(payload["size"], int) or not 1 <= payload["size"] <= MAX_PAYLOAD_BYTES:
        raise NativeManifestError("native bundle payload metadata is invalid")
    _digest(payload["sha256"], label="payload digest")
    preview = _object(manifest["preview"], label="bundle preview", fields={"path", "size", "sha256", "width", "height", "frame_count", "duration_ms", "capture_seconds", "simulation_fps"})
    captures = preview["capture_seconds"]
    if not isinstance(captures, list):
        raise NativeManifestError("native bundle preview capture times are invalid")
    encoded_captures = [_capture_microseconds(item) for item in captures]
    if (
        preview["path"] != PREVIEW_PATH
        or isinstance(preview["size"], bool)
        or not isinstance(preview["size"], int)
        or not 1 <= preview["size"] <= MAX_PREVIEW_BYTES
        or type(preview["width"]) is not int
        or preview["width"] != GLOBAL_STRIPS
        or type(preview["height"]) is not int
        or preview["height"] != LEDS_PER_STRIP
        or isinstance(preview["frame_count"], bool)
        or not isinstance(preview["frame_count"], int)
        or preview["frame_count"] != len(captures)
        or isinstance(preview["duration_ms"], bool)
        or not isinstance(preview["duration_ms"], int)
        or not 1 <= preview["duration_ms"] <= 1000
        or isinstance(preview["simulation_fps"], bool)
        or not isinstance(preview["simulation_fps"], int)
        or not 1 <= preview["simulation_fps"] <= 120
    ):
        raise NativeManifestError("native bundle preview metadata is invalid")
    if (
        not 2 <= len(captures) <= 16
        or any(float(left) >= float(right) for left, right in zip(captures, captures[1:]))
        or any(
            left >= right
            for left, right in zip(encoded_captures, encoded_captures[1:])
        )
        or preview["duration_ms"] != max(1, round(1000 / preview["simulation_fps"]))
    ):
        raise NativeManifestError("native bundle preview capture times are invalid")
    _digest(preview["sha256"], label="preview digest")
    normalized = dict(manifest)
    normalized["parameter_schema"] = schema
    normalized["defaults"] = defaults
    return normalized
