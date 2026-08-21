"""Build and inspect canonical unsigned native-background bundles."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .archive import deterministic_zip, read_archive_source, read_safe_zip
from .constants import (
    BUNDLE_MEMBERS,
    GLOBAL_STRIPS,
    LEDS_PER_STRIP,
    MANIFEST_PATH,
    MAX_PAYLOAD_BYTES,
    MAX_PREVIEW_BYTES,
    PAYLOAD_PATH,
    PREVIEW_PATH,
)
from .elf import validate_target_elf
from .errors import NativeBundleError, NativeManifestError
from .schema import canonical_json, parse_canonical_json, validate_bundle_manifest


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class VerifiedNativeBundle:
    raw: bytes
    manifest: dict[str, Any]
    members: Mapping[str, bytes]
    bundle_digest: str
    payload_digest: str

    @property
    def payload(self) -> bytes:
        return self.members[PAYLOAD_PATH]

    @property
    def preview(self) -> bytes:
        return self.members[PREVIEW_PATH]


def validate_preview_webp(data: bytes, metadata: Mapping[str, Any]) -> None:
    if not data or len(data) > MAX_PREVIEW_BYTES or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise NativeBundleError("native preview must be a bounded WebP")
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "WEBP" or image.size != (GLOBAL_STRIPS, LEDS_PER_STRIP):
                raise NativeBundleError("native preview must be an animated 32x138 WebP")
            if image.n_frames != metadata["frame_count"] or image.n_frames < 2:
                raise NativeBundleError("native preview frame count does not match its manifest")
            durations: list[int] = []
            for frame_index in range(image.n_frames):
                image.seek(frame_index)
                image.load()
                durations.append(int(image.info.get("duration", 0)))
            if durations != [metadata["duration_ms"]] * image.n_frames:
                raise NativeBundleError("native preview durations do not match its manifest")
            if int(image.info.get("loop", 0)) != 0:
                raise NativeBundleError("native preview must loop indefinitely")
    except NativeBundleError:
        raise
    except (ImportError, OSError, ValueError, EOFError, KeyError) as exc:
        raise NativeBundleError(f"native preview is malformed: {exc}") from exc


def build_bundle(manifest: Mapping[str, Any], payload: bytes, preview: bytes) -> bytes:
    """Create the sole canonical byte representation accepted by inspection."""

    try:
        validated = validate_bundle_manifest(dict(manifest))
    except NativeManifestError as exc:
        raise NativeBundleError(f"invalid native bundle manifest: {exc}") from exc
    if len(payload) != validated["payload"]["size"] or sha256(payload) != validated["payload"]["sha256"]:
        raise NativeBundleError("native payload bytes do not match bundle manifest")
    if len(preview) != validated["preview"]["size"] or sha256(preview) != validated["preview"]["sha256"]:
        raise NativeBundleError("native preview bytes do not match bundle manifest")
    validate_target_elf(payload)
    validate_preview_webp(preview, validated["preview"])
    return deterministic_zip(
        {
            MANIFEST_PATH: canonical_json(validated),
            PAYLOAD_PATH: payload,
            PREVIEW_PATH: preview,
        }
    )


def inspect_bundle(source: str | Path | bytes | bytearray | Any) -> VerifiedNativeBundle:
    """Strictly verify one canonical unsigned bundle without executing payloads."""

    raw = read_archive_source(source)
    members = read_safe_zip(raw)
    if set(members) != BUNDLE_MEMBERS:
        raise NativeBundleError(
            f"native bundle members must be exactly {sorted(BUNDLE_MEMBERS)}, got {sorted(members)}"
        )
    # Stored ZIP plus fixed metadata makes canonical re-encoding portable across
    # zlib implementations and prevents one semantic bundle having many IDs.
    if deterministic_zip(members) != raw:
        raise NativeBundleError("native bundle ZIP representation is not canonical")
    try:
        manifest = validate_bundle_manifest(
            parse_canonical_json(members[MANIFEST_PATH], label="native bundle manifest")
        )
    except NativeManifestError as exc:
        raise NativeBundleError(f"invalid native bundle manifest: {exc}") from exc
    payload = members[PAYLOAD_PATH]
    preview = members[PREVIEW_PATH]
    if not payload or len(payload) > MAX_PAYLOAD_BYTES:
        raise NativeBundleError("native payload is empty or exceeds 512 KiB")
    if len(payload) != manifest["payload"]["size"] or sha256(payload) != manifest["payload"]["sha256"]:
        raise NativeBundleError("native payload hash/size does not match its manifest")
    if not preview or len(preview) > MAX_PREVIEW_BYTES:
        raise NativeBundleError("native preview is empty or exceeds 2 MiB")
    if len(preview) != manifest["preview"]["size"] or sha256(preview) != manifest["preview"]["sha256"]:
        raise NativeBundleError("native preview hash/size does not match its manifest")
    validate_target_elf(payload)
    validate_preview_webp(preview, manifest["preview"])
    return VerifiedNativeBundle(
        raw=raw,
        manifest=manifest,
        members=MappingProxyType(dict(members)),
        bundle_digest=sha256(raw),
        payload_digest=sha256(payload),
    )


__all__ = [
    "MANIFEST_PATH",
    "PAYLOAD_PATH",
    "PREVIEW_PATH",
    "VerifiedNativeBundle",
    "build_bundle",
    "inspect_bundle",
    "sha256",
]
