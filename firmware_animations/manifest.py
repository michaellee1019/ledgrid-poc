"""Canonical manifests and typed parameter validation."""

from __future__ import annotations

import json
import math
import re
import struct
from collections.abc import Mapping
from typing import Any

from .constants import (
    ANIMATION_ABI,
    ESP32_TARGET,
    FRAME_PARAMETER_NAMES,
    LEDS_PER_STRIP,
    LOCAL_STRIPS,
    PACKAGE_FORMAT,
    RECEIVER_COUNT,
    WALL_STRIPS,
)
from .errors import PackageValidationError

PACKAGE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
PARAMETER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_MANIFEST_PARAMETERS = 31  # ABI v1 reserves one slot for global time_scale.
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PackageValidationError(f"value is not canonical JSON: {exc}") from exc


def parse_canonical_json(data: bytes, *, label: str) -> Any:
    try:
        text = data.decode("utf-8")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageValidationError(f"invalid {label}: {exc}") from exc
    if canonical_json(value) != data:
        raise PackageValidationError(f"{label} is not canonical JSON")
    return value


def _plain_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise PackageValidationError(f"{label} must be a JSON object with string keys")
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PackageValidationError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise PackageValidationError(f"{label} must be finite")
    return number


def _finite_float32(value: Any, label: str) -> float:
    number = _finite_number(value, label)
    try:
        encoded = struct.pack(">f", number)
    except OverflowError as exc:
        raise PackageValidationError(f"{label} must fit in float32") from exc
    decoded = struct.unpack(">f", encoded)[0]
    if not math.isfinite(decoded) or (number != 0.0 and decoded == 0.0):
        raise PackageValidationError(f"{label} must fit in finite nonzero float32")
    return number


def _wire_enum_option(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    encoded = value.encode("utf-8")
    return (
        len(encoded) <= 63
        and all(ord(character) >= 0x21 and ord(character) != 0x7F for character in value)
    )


def validate_parameter_schema(schema: Any) -> dict[str, dict[str, Any]]:
    obj = _plain_object(schema, "parameter_schema")
    if len(obj) > MAX_MANIFEST_PARAMETERS:
        raise PackageValidationError(
            f"parameter_schema has more than {MAX_MANIFEST_PARAMETERS} entries"
        )
    validated: dict[str, dict[str, Any]] = {}
    for name, raw_spec in obj.items():
        if not PARAMETER_ID_RE.fullmatch(name):
            raise PackageValidationError(f"invalid parameter name: {name!r}")
        spec = _plain_object(raw_spec, f"parameter {name!r}")
        kind = spec.get("type")
        allowed = {"type", "default", "description"}
        if kind in {"int", "float"}:
            allowed |= {"min", "max"}
        elif kind == "enum":
            allowed |= {"options"}
        elif kind not in {"bool", "color"}:
            raise PackageValidationError(f"parameter {name!r} has unsupported type {kind!r}")
        unknown = set(spec) - allowed
        if unknown:
            raise PackageValidationError(f"parameter {name!r} has unknown fields: {sorted(unknown)}")
        if "default" not in spec:
            raise PackageValidationError(f"parameter {name!r} is missing a default")
        description = spec.get("description", "")
        if not isinstance(description, str) or len(description) > 240:
            raise PackageValidationError(f"parameter {name!r} has an invalid description")
        if kind in {"int", "float"}:
            if "min" not in spec or "max" not in spec:
                raise PackageValidationError(f"numeric parameter {name!r} requires min and max")
            number_validator = _finite_number if kind == "int" else _finite_float32
            lower = number_validator(spec["min"], f"{name}.min")
            upper = number_validator(spec["max"], f"{name}.max")
            if lower > upper:
                raise PackageValidationError(f"parameter {name!r} has min greater than max")
            if kind == "int" and (not isinstance(spec["min"], int) or isinstance(spec["min"], bool) or not isinstance(spec["max"], int) or isinstance(spec["max"], bool)):
                raise PackageValidationError(f"integer parameter {name!r} requires integer bounds")
            if kind == "int" and not (
                _INT32_MIN <= spec["min"] <= spec["max"] <= _INT32_MAX
            ):
                raise PackageValidationError(
                    f"integer parameter {name!r} bounds must fit in int32"
                )
        elif kind == "enum":
            options = spec.get("options")
            if not isinstance(options, list) or not 1 <= len(options) <= 64:
                raise PackageValidationError(f"enum parameter {name!r} requires 1..64 options")
            if any(not _wire_enum_option(option) for option in options):
                raise PackageValidationError(f"enum parameter {name!r} has an invalid option")
            if len(set(options)) != len(options):
                raise PackageValidationError(f"enum parameter {name!r} has duplicate options")
        validated[name] = dict(spec)
    validate_parameters(validated, {}, require_all=False)
    return validated


def _validated_parameter_value(name: str, spec: Mapping[str, Any], value: Any) -> Any:
    kind = spec["type"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise PackageValidationError(f"parameter {name!r} must be bool")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise PackageValidationError(f"parameter {name!r} must be int")
        if value < spec["min"] or value > spec["max"]:
            raise PackageValidationError(f"parameter {name!r} is outside its bounds")
        return value
    if kind == "float":
        number = _finite_float32(value, f"parameter {name!r}")
        if number < float(spec["min"]) or number > float(spec["max"]):
            raise PackageValidationError(f"parameter {name!r} is outside its bounds")
        return number
    if kind == "enum":
        if not isinstance(value, str) or value not in spec["options"]:
            raise PackageValidationError(f"parameter {name!r} is not an allowed enum option")
        return value
    if kind == "color":
        if not isinstance(value, str) or not COLOR_RE.fullmatch(value):
            raise PackageValidationError(f"parameter {name!r} must be #RRGGBB")
        return value.upper()
    raise PackageValidationError(f"parameter {name!r} has unsupported type {kind!r}")


def validate_parameters(
    manifest_or_schema: Mapping[str, Any],
    parameters: Mapping[str, Any] | None,
    *,
    require_all: bool = False,
) -> dict[str, Any]:
    schema_raw = manifest_or_schema.get("parameter_schema", manifest_or_schema)
    if not isinstance(schema_raw, Mapping):
        raise PackageValidationError("parameter schema must be an object")
    supplied = _plain_object(dict(parameters or {}), "parameters")
    unknown = set(supplied) - set(schema_raw)
    if unknown:
        raise PackageValidationError(f"unknown parameters: {sorted(unknown)}")
    result: dict[str, Any] = {}
    for name, raw_spec in schema_raw.items():
        if not isinstance(raw_spec, Mapping):
            raise PackageValidationError(f"parameter {name!r} schema must be an object")
        if name in supplied:
            value = supplied[name]
        elif "default" in raw_spec:
            value = raw_spec["default"]
        elif require_all:
            raise PackageValidationError(f"missing parameter: {name}")
        else:
            continue
        result[name] = _validated_parameter_value(name, raw_spec, value)
    return result


def validate_manifest(
    value: Any,
    *,
    expected_abi: str = ANIMATION_ABI,
    expected_target: str = ESP32_TARGET,
) -> dict[str, Any]:
    manifest = _plain_object(value, "manifest")
    required = {
        "format_version", "id", "name", "version", "description", "kind",
        "abi", "target", "geometry", "preferred_fps", "parameter_schema",
        "payload_hashes", "provenance", "signing_key_id",
    }
    unknown = set(manifest) - required - {"imports"}
    missing = required - set(manifest)
    if missing or unknown:
        raise PackageValidationError(f"manifest fields missing={sorted(missing)} unknown={sorted(unknown)}")
    if manifest["format_version"] != PACKAGE_FORMAT:
        raise PackageValidationError("unsupported package format")
    package_id = manifest["id"]
    if not isinstance(package_id, str) or not PACKAGE_ID_RE.fullmatch(package_id) or len(package_id) > 80:
        raise PackageValidationError("invalid package id")
    if not isinstance(manifest["name"], str) or not 1 <= len(manifest["name"]) <= 100:
        raise PackageValidationError("invalid package name")
    if not isinstance(manifest["version"], str) or not SEMVER_RE.fullmatch(manifest["version"]):
        raise PackageValidationError("version must be semantic versioning")
    if not isinstance(manifest["description"], str) or not 1 <= len(manifest["description"]) <= 500:
        raise PackageValidationError("invalid description")
    if manifest["kind"] not in {"native", "frames"}:
        raise PackageValidationError("kind must be native or frames")
    if manifest["abi"] != expected_abi:
        raise PackageValidationError(f"unsupported ABI: {manifest['abi']!r}")
    if manifest["target"] != expected_target:
        raise PackageValidationError(f"unsupported target: {manifest['target']!r}")
    geometry = _plain_object(manifest["geometry"], "geometry")
    expected_geometry = {
        "strips": WALL_STRIPS,
        "leds_per_strip": LEDS_PER_STRIP,
        "receiver_count": RECEIVER_COUNT,
        "strips_per_receiver": LOCAL_STRIPS,
    }
    if geometry != expected_geometry:
        raise PackageValidationError(f"unsupported geometry: {geometry!r}")
    fps = _finite_number(manifest["preferred_fps"], "preferred_fps")
    if not 1 <= fps <= 200:
        raise PackageValidationError("preferred_fps must be in [1, 200]")
    schema = validate_parameter_schema(manifest["parameter_schema"])
    if manifest["kind"] == "frames" and set(schema) != FRAME_PARAMETER_NAMES:
        raise PackageValidationError(
            "frame parameter_schema must contain only the receiver frame controls"
        )
    validate_parameters(schema, {}, require_all=True)
    hashes = _plain_object(manifest["payload_hashes"], "payload_hashes")
    if not hashes or len(hashes) > 8:
        raise PackageValidationError("payload_hashes must contain 1..8 members")
    for path, digest in hashes.items():
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise PackageValidationError(f"invalid SHA-256 for {path!r}")
    provenance = _plain_object(manifest["provenance"], "provenance")
    if set(provenance) != {"author", "license", "source", "generated_by"}:
        raise PackageValidationError("provenance requires author, license, source, and generated_by")
    if any(not isinstance(v, str) or not v or len(v) > 240 for v in provenance.values()):
        raise PackageValidationError("invalid provenance value")
    key_id = manifest["signing_key_id"]
    if not isinstance(key_id, str) or not re.fullmatch(r"key-[0-9a-f]{16}", key_id):
        raise PackageValidationError("invalid signing_key_id")
    imports = manifest.get("imports", [])
    if manifest["kind"] == "native":
        if not isinstance(imports, list) or len(imports) > 64 or any(not isinstance(item, str) or not PARAMETER_ID_RE.fullmatch(item) for item in imports):
            raise PackageValidationError("native imports must be a list of safe symbols")
        if len(set(imports)) != len(imports):
            raise PackageValidationError("native imports contain duplicates")
    elif "imports" in manifest:
        raise PackageValidationError("frame packages cannot declare imports")
    return dict(manifest)
