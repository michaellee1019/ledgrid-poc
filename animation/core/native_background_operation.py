"""Managed native-background identities and host-side wire preparation.

This module has no SPI or filesystem mutation.  It accepts only a revalidated
managed-library resolve, binds its canonical bundle/payload identities to the
exact configured receiver roster, and encodes the ABI-v2 typed parameters used
by both activation and live updates.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import struct
from typing import Any, Mapping, Sequence

from animation.core.native_background_library import ResolvedNativeBackground
from animation.native.constants import ABI_VERSION, TARGET
from animation.native.schema import (
    canonical_json,
    validate_parameters,
)


TARGET_ESP32_S3_WIRE_ID = 1
TYPED_PARAMETER_VERSION = 1
MAX_TYPED_PARAMETERS = 31


class NativeBackgroundOperationError(RuntimeError):
    """A managed native operation is invalid or cannot prove safe state."""


def _digest(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        raise NativeBackgroundOperationError(
            f"{field} must be a lowercase SHA-256 digest"
        )
    try:
        decoded = bytes.fromhex(value)
    except ValueError as exc:
        raise NativeBackgroundOperationError(
            f"{field} must be a lowercase SHA-256 digest"
        ) from exc
    if decoded.hex() != value:
        raise NativeBackgroundOperationError(
            f"{field} must be a lowercase SHA-256 digest"
        )
    return value


@dataclass(frozen=True)
class NativeBackgroundBinding:
    bundle_digest: str
    payload_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "bundle_digest",
            _digest(self.bundle_digest, field="bundle_digest"),
        )
        object.__setattr__(
            self,
            "payload_digest",
            _digest(self.payload_digest, field="payload_digest"),
        )


@dataclass(frozen=True)
class NativeReceiverTopology:
    logical_receiver_id: int
    global_strip_offset: int
    local_strips: int
    reverse_local_strip_order: bool

    def __post_init__(self) -> None:
        if (
            type(self.logical_receiver_id) is not int
            or not 0 <= self.logical_receiver_id <= 0xFE
        ):
            raise ValueError("logical_receiver_id must be an integer from 0 through 254")
        if (
            type(self.global_strip_offset) is not int
            or not 0 <= self.global_strip_offset <= 0xFFFF
        ):
            raise ValueError("global_strip_offset must fit uint16")
        if type(self.local_strips) is not int or not 1 <= self.local_strips <= 8:
            raise ValueError("local_strips must be an integer from 1 through 8")
        if type(self.reverse_local_strip_order) is not bool:
            raise TypeError("reverse_local_strip_order must be a boolean")


@dataclass(frozen=True)
class EncodedNativeParameters:
    values: Mapping[str, Any]
    schema_revision: int
    blob: bytes
    digest: str


@dataclass(frozen=True)
class ManagedNativeBackground:
    resolved: ResolvedNativeBackground
    binding: NativeBackgroundBinding
    receiver_topology: tuple[NativeReceiverTopology, ...]
    global_strips: int
    leds_per_strip: int
    cadence_hz: int
    abi_version: int
    target: int
    parameter_schema_revision: int

    @property
    def payload(self) -> bytes:
        return self.resolved.payload

    @property
    def manifest(self) -> Mapping[str, Any]:
        return self.resolved.verified.manifest

    def descriptor_for(self, logical_receiver_id: int) -> dict[str, Any]:
        views = {
            view.logical_receiver_id: view for view in self.receiver_topology
        }
        try:
            view = views[logical_receiver_id]
        except KeyError as exc:
            raise NativeBackgroundOperationError(
                f"native bundle has no receiver view for logical ID {logical_receiver_id}"
            ) from exc
        return {
            "bundle_digest": self.binding.bundle_digest,
            "payload_digest": self.binding.payload_digest,
            "payload_size": len(self.payload),
            "abi_version": self.abi_version,
            "target": self.target,
            "global_strips": self.global_strips,
            "local_strips": view.local_strips,
            "leds_per_strip": self.leds_per_strip,
            "global_strip_offset": view.global_strip_offset,
            "cadence_hz": self.cadence_hz,
            "parameter_schema_revision": self.parameter_schema_revision,
            "flags": 0,
        }

    def encode_parameters(
        self, values: Mapping[str, Any] | None
    ) -> EncodedNativeParameters:
        return encode_native_parameters(self.manifest["parameter_schema"], values)


def encode_native_parameters(
    schema: Mapping[str, Mapping[str, Any]],
    values: Mapping[str, Any] | None,
) -> EncodedNativeParameters:
    """Validate and encode the canonical ABI-v2 typed parameter blob."""

    validated = validate_parameters(schema, values)
    canonical_schema = canonical_json(schema)
    schema_revision = int.from_bytes(
        hashlib.sha256(canonical_schema).digest()[:4], "big"
    )
    # Zero is reserved as "no schema identity" in firmware status. A SHA-256
    # prefix collision with zero is valid content but cannot be represented by
    # the v1 wire contract, so fail closed rather than silently substituting.
    if schema_revision == 0:
        raise NativeBackgroundOperationError(
            "parameter schema revision collides with the reserved zero value"
        )
    if len(validated) > MAX_TYPED_PARAMETERS:
        raise NativeBackgroundOperationError(
            f"native parameters exceed the {MAX_TYPED_PARAMETERS}-entry ABI bound"
        )

    blob = bytearray((TYPED_PARAMETER_VERSION, len(validated)))
    for parameter_id, name in enumerate(sorted(validated)):
        spec = schema[name]
        value = validated[name]
        kind = spec["type"]
        blob.extend(struct.pack(">HBB", parameter_id, {
            "int": 1,
            "float": 2,
            "bool": 3,
            "str": 4,
        }[kind], 0))
        if kind == "int":
            blob.extend(struct.pack(">i", value))
        elif kind == "float":
            encoded = struct.pack(">f", value)
            if not math.isfinite(struct.unpack(">f", encoded)[0]):
                raise NativeBackgroundOperationError(
                    f"parameter {name!r} is not a finite float32"
                )
            blob.extend(encoded)
        elif kind == "bool":
            blob.append(int(value))
        else:
            blob.extend(struct.pack(">H", spec["options"].index(value)))
    encoded = bytes(blob)
    return EncodedNativeParameters(
        values=validated,
        schema_revision=schema_revision,
        blob=encoded,
        digest=hashlib.sha256(encoded).hexdigest(),
    )


def managed_native_background(
    resolved: ResolvedNativeBackground,
    configured_topology: Sequence[NativeReceiverTopology],
) -> ManagedNativeBackground:
    """Bind a managed resolve to one exact configured receiver topology."""

    if not isinstance(resolved, ResolvedNativeBackground):
        raise TypeError("resolved must be a managed ResolvedNativeBackground")
    if not configured_topology:
        raise ValueError("configured_topology must contain at least one receiver")
    topology = tuple(configured_topology)
    if any(not isinstance(view, NativeReceiverTopology) for view in topology):
        raise TypeError("configured_topology contains an invalid receiver view")
    logical_ids = tuple(view.logical_receiver_id for view in topology)
    if len(set(logical_ids)) != len(logical_ids):
        raise ValueError("configured_topology contains duplicate logical receiver IDs")

    manifest = resolved.verified.manifest
    if not isinstance(manifest, Mapping):
        raise NativeBackgroundOperationError("managed native manifest is unavailable")
    geometry = manifest.get("geometry")
    if not isinstance(geometry, Mapping):
        raise NativeBackgroundOperationError("managed native geometry is unavailable")
    raw_views = geometry.get("receiver_views")
    if not isinstance(raw_views, list):
        raise NativeBackgroundOperationError("native receiver views are unavailable")
    try:
        manifest_views = tuple(
            NativeReceiverTopology(
                logical_receiver_id=view["logical_receiver_id"],
                global_strip_offset=view["global_strip_offset"],
                local_strips=view["local_strips"],
                reverse_local_strip_order=view["reverse_local_strip_order"],
            )
            for view in raw_views
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise NativeBackgroundOperationError(
            "native receiver views are malformed"
        ) from exc
    by_id = {view.logical_receiver_id: view for view in manifest_views}
    if set(by_id) != set(logical_ids) or any(
        by_id[view.logical_receiver_id] != view for view in topology
    ):
        raise NativeBackgroundOperationError(
            "native bundle receiver views do not match the configured wall topology"
        )

    try:
        global_strips = geometry["global_strips"]
        leds_per_strip = geometry["leds_per_strip"]
        cadence = manifest["cadence"]["preferred_fps"]
        abi_version = manifest["abi"]["version"]
        target_name = manifest["target"]["name"]
    except (KeyError, TypeError) as exc:
        raise NativeBackgroundOperationError(
            "managed native descriptor fields are unavailable"
        ) from exc
    if type(global_strips) is not int or type(leds_per_strip) is not int:
        raise NativeBackgroundOperationError("native geometry fields must be integers")
    if isinstance(cadence, bool) or not isinstance(cadence, (int, float)):
        raise NativeBackgroundOperationError("native cadence must be numeric")
    cadence_hz = int(cadence)
    if cadence_hz != cadence or not 1 <= cadence_hz <= 0xFFFF:
        raise NativeBackgroundOperationError(
            "native cadence must be an exact positive uint16 frequency"
        )
    if abi_version != ABI_VERSION or target_name != TARGET:
        raise NativeBackgroundOperationError("native ABI or target identity drifted")
    expected_global = max(
        view.global_strip_offset + view.local_strips for view in topology
    )
    occupied = {
        strip
        for view in topology
        for strip in range(
            view.global_strip_offset,
            view.global_strip_offset + view.local_strips,
        )
    }
    if (
        global_strips != expected_global
        or occupied != set(range(global_strips))
    ):
        raise NativeBackgroundOperationError(
            "native receiver views do not exactly partition the global wall"
        )
    if len(resolved.payload) != resolved.receipt.payload_size:
        raise NativeBackgroundOperationError("managed native payload size drifted")
    if hashlib.sha256(resolved.payload).hexdigest() != resolved.payload_digest:
        raise NativeBackgroundOperationError("managed native payload digest drifted")

    encoded_defaults = encode_native_parameters(
        manifest["parameter_schema"], manifest["defaults"]
    )
    return ManagedNativeBackground(
        resolved=resolved,
        binding=NativeBackgroundBinding(
            resolved.bundle_digest, resolved.payload_digest
        ),
        receiver_topology=topology,
        global_strips=global_strips,
        leds_per_strip=leds_per_strip,
        cadence_hz=cadence_hz,
        abi_version=abi_version,
        target=TARGET_ESP32_S3_WIRE_ID,
        parameter_schema_revision=encoded_defaults.schema_revision,
    )


__all__ = [
    "EncodedNativeParameters",
    "ManagedNativeBackground",
    "NativeBackgroundBinding",
    "NativeBackgroundOperationError",
    "NativeReceiverTopology",
    "encode_native_parameters",
    "managed_native_background",
]
