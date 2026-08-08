"""Signed binary package index."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .constants import (
    ANIMATION_ABI_ID,
    ESP32_TARGET_ID,
    LEDS_PER_STRIP,
    LOCAL_STRIPS,
    PACKAGE_FORMAT,
    RECEIVER_COUNT,
    WALL_STRIPS,
)
from .errors import PackageValidationError

_HEADER = struct.Struct(">4sBBHHBBHH32s")
_MAGIC = b"LGIX"
_KIND = {"native": 1, "frames": 2}
_KIND_BY_VALUE = {value: key for key, value in _KIND.items()}


@dataclass(frozen=True)
class PackageIndex:
    ENCODED_SIZE = _HEADER.size + RECEIVER_COUNT * 32

    kind: str
    manifest_sha256: bytes
    device_payload_sha256: tuple[bytes, bytes, bytes, bytes]

    def encode(self) -> bytes:
        if self.kind not in _KIND or len(self.manifest_sha256) != 32:
            raise PackageValidationError("invalid package index")
        if len(self.device_payload_sha256) != RECEIVER_COUNT or any(len(item) != 32 for item in self.device_payload_sha256):
            raise PackageValidationError("index requires four SHA-256 device digests")
        return _HEADER.pack(
            _MAGIC,
            PACKAGE_FORMAT,
            _KIND[self.kind],
            ANIMATION_ABI_ID,
            ESP32_TARGET_ID,
            RECEIVER_COUNT,
            LOCAL_STRIPS,
            WALL_STRIPS,
            LEDS_PER_STRIP,
            self.manifest_sha256,
        ) + b"".join(self.device_payload_sha256)

    @classmethod
    def decode(cls, data: bytes) -> "PackageIndex":
        expected_size = cls.ENCODED_SIZE
        if len(data) != expected_size:
            raise PackageValidationError("signed index has an invalid size")
        magic, version, kind_value, abi, target, receivers, local_strips, strips, leds, manifest_hash = _HEADER.unpack_from(data)
        if magic != _MAGIC or version != PACKAGE_FORMAT:
            raise PackageValidationError("unsupported signed index")
        if (receivers, local_strips, strips, leds) != (RECEIVER_COUNT, LOCAL_STRIPS, WALL_STRIPS, LEDS_PER_STRIP):
            raise PackageValidationError("signed index geometry is incompatible")
        if abi != ANIMATION_ABI_ID or target != ESP32_TARGET_ID:
            raise PackageValidationError("signed index ABI or target is incompatible")
        kind = _KIND_BY_VALUE.get(kind_value)
        if kind is None:
            raise PackageValidationError("signed index has an unknown package kind")
        start = _HEADER.size
        hashes = tuple(data[start + i * 32:start + (i + 1) * 32] for i in range(RECEIVER_COUNT))
        return cls(kind, manifest_hash, hashes)  # type: ignore[arg-type]
