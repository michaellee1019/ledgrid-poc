"""Build and strictly verify signed ``.lga`` packages."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .archive import deterministic_zip, read_safe_zip
from .constants import (
    ANIMATION_ABI,
    DEFAULT_IMPORT_ALLOWLIST,
    ESP32_TARGET,
    FRAME_PARAMETER_NAMES,
    INDEX_PATH,
    LEDS_PER_STRIP,
    LOCAL_STRIPS,
    MANIFEST_PATH,
    MAX_NATIVE_BYTES,
    MAX_PREVIEW_BYTES,
    MAX_TRACK_BYTES,
    NATIVE_PAYLOAD_PATH,
    PACKAGE_FORMAT,
    PREVIEW_PATH,
    RECEIVER_COUNT,
    SIGNATURE_PATH,
    WALL_STRIPS,
    track_path,
)
from .errors import PackageValidationError
from .index import PackageIndex
from .manifest import canonical_json, parse_canonical_json, validate_manifest
from .signing import load_signing_key, public_key_id, sign, verify
from .tracks import DecodedTrack, decode_track, encode_image_tracks, generate_preview_webp


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class VerifiedPackage:
    manifest: dict[str, Any]
    digest: str
    members: Mapping[str, bytes]
    tracks: tuple[DecodedTrack, ...]
    raw: bytes

    def payload_for_device(self, device_index: int) -> bytes:
        if not 0 <= device_index < RECEIVER_COUNT:
            raise ValueError("device index must be in [0, 3]")
        if self.manifest["kind"] == "native":
            return self.members[NATIVE_PAYLOAD_PATH]
        return self.members[track_path(device_index)]

    def verification_envelope(self, device_index: int) -> "ReceiverVerificationEnvelope":
        if not 0 <= device_index < RECEIVER_COUNT:
            raise ValueError("device index must be in [0, 3]")
        index = PackageIndex.decode(self.members[INDEX_PATH])
        return ReceiverVerificationEnvelope(
            package_id=self.manifest["id"], package_digest=self.digest,
            key_id=self.manifest["signing_key_id"], kind=self.manifest["kind"],
            device_index=device_index, payload_size=len(self.payload_for_device(device_index)),
            payload_digest=index.device_payload_sha256[device_index],
            signed_index=self.members[INDEX_PATH], signature=self.members[SIGNATURE_PATH],
        )


@dataclass(frozen=True)
class ReceiverVerificationEnvelope:
    """Exact bounded trust inputs for one receiver's ASSET_BEGIN.

    ``signed_index`` is the canonical fixed-size LGIX binary (176 bytes).
    ``signature`` is raw low-S P-256 ``r || s`` (64 bytes). ``key_id`` is the
    canonical 20-byte fingerprint and ``payload_digest`` is 32 bytes.

    :meth:`asset_begin_command` emits the receiver's exact 313-byte, big-endian
    ASSET_BEGIN v1 command body. With the SPI CRC it occupies 315 bytes, safely
    below both the receiver's 1024-byte begin bound and 4096-byte transaction.
    """

    MAX_SIGNED_INDEX_BYTES = PackageIndex.ENCODED_SIZE
    MAX_SIGNATURE_BYTES = 64
    KEY_ID_BYTES = 20
    ASSET_BEGIN_COMMAND_BYTES = 313
    ASSET_BEGIN_WITH_CRC_BYTES = 315

    package_id: str
    package_digest: str
    key_id: str
    kind: str
    device_index: int
    payload_size: int
    payload_digest: bytes
    signed_index: bytes
    signature: bytes

    def __post_init__(self) -> None:
        key_bytes = self.key_id.encode("ascii")
        if len(key_bytes) != self.KEY_ID_BYTES:
            raise PackageValidationError("receiver envelope key id must be 20 ASCII bytes")
        if not 1 <= self.payload_size <= 0xFFFFFFFF:
            raise PackageValidationError("receiver envelope payload size is invalid")
        if len(self.payload_digest) != 32 or len(self.signed_index) != self.MAX_SIGNED_INDEX_BYTES or len(self.signature) != self.MAX_SIGNATURE_BYTES:
            raise PackageValidationError("receiver verification envelope has invalid fixed-size fields")
        index = PackageIndex.decode(self.signed_index)
        if self.kind != index.kind or not 0 <= self.device_index < RECEIVER_COUNT or index.device_payload_sha256[self.device_index] != self.payload_digest:
            raise PackageValidationError("receiver envelope is not bound to its signed device payload")

    def asset_begin_command(self) -> bytes:
        import struct

        key_id = self.key_id.encode("ascii")
        kind_id = 1 if self.kind == "native" else 2
        body = (
            struct.pack(">I", self.payload_size)
            + self.payload_digest
            + struct.pack(">BHHBHB", kind_id, 1, 1, LOCAL_STRIPS, LEDS_PER_STRIP, self.device_index)
            + bytes([len(key_id)]) + key_id
            + struct.pack(">H", len(self.signed_index)) + self.signed_index
            + bytes([len(self.signature)]) + self.signature
        )
        command = bytes([0x22, 1]) + struct.pack(">H", len(body)) + body
        if len(command) != self.ASSET_BEGIN_COMMAND_BYTES:
            raise PackageValidationError("receiver ASSET_BEGIN envelope is not canonical size")
        return command


def _validate_preview_webp(data: bytes) -> None:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise PackageValidationError("package preview is not WebP")
    try:
        from PIL import Image
    except ImportError as exc:
        raise PackageValidationError("preview validation requires Pillow>=10.0.0") from exc
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "WEBP" or image.size != (WALL_STRIPS, LEDS_PER_STRIP) or getattr(image, "n_frames", 1) < 2:
                raise PackageValidationError("preview must be an animated 32x138 WebP")
            for frame_index in range(image.n_frames):
                image.seek(frame_index)
                image.load()
    except PackageValidationError:
        raise
    except (OSError, ValueError, EOFError) as exc:
        raise PackageValidationError(f"package preview is malformed: {exc}") from exc


def _validate_native_elf(data: bytes) -> None:
    # elf_loader 1.3.2 consumes little-endian ELF32 ET_DYN images for Xtensa.
    # Parsing just the invariant ELF header here keeps verification dependency
    # free; the trusted packer separately obtains undefined imports with nm.
    if len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01":
        raise PackageValidationError("native payload is not a little-endian ELF32 image")
    if int.from_bytes(data[16:18], "little") != 3:
        raise PackageValidationError("native payload must be an ELF shared object (ET_DYN)")
    if int.from_bytes(data[18:20], "little") != 94:
        raise PackageValidationError("native payload is not compiled for Xtensa ESP32-S3")


def _base_manifest(metadata: Mapping[str, Any], *, kind: str, key_id: str) -> dict[str, Any]:
    manifest = dict(metadata)
    manifest.update(
        {
            "format_version": PACKAGE_FORMAT,
            "kind": kind,
            "abi": ANIMATION_ABI,
            "target": ESP32_TARGET,
            "geometry": {
                "strips": WALL_STRIPS,
                "leds_per_strip": LEDS_PER_STRIP,
                "receiver_count": RECEIVER_COUNT,
                "strips_per_receiver": LOCAL_STRIPS,
            },
            "signing_key_id": key_id,
        }
    )
    return manifest


def _finish_package(
    manifest: dict[str, Any],
    payload_members: dict[str, bytes],
    device_hashes: tuple[bytes, bytes, bytes, bytes],
    private_key: bytes | str | Path,
) -> bytes:
    manifest["payload_hashes"] = {path: sha256(payload) for path, payload in sorted(payload_members.items())}
    validated = validate_manifest(manifest)
    manifest_bytes = canonical_json(validated)
    index = PackageIndex(validated["kind"], hashlib.sha256(manifest_bytes).digest(), device_hashes).encode()
    signature = sign(index, private_key)
    members = dict(payload_members)
    members.update({MANIFEST_PATH: manifest_bytes, INDEX_PATH: index, SIGNATURE_PATH: signature})
    return deterministic_zip(members)


_FRAME_CONTROLS: dict[str, dict[str, Any]] = {
    "asset_brightness": {"type": "float", "min": 0.0, "max": 1.0, "default": 1.0, "description": "Asset brightness multiplier"},
    "loop": {"type": "bool", "default": True, "description": "Repeat after the source loop count"},
    "pause": {"type": "bool", "default": False, "description": "Pause on the current frame"},
    "playback_speed": {"type": "float", "min": 0.1, "max": 4.0, "default": 1.0, "description": "Playback time multiplier"},
}


def build_frame_package(
    source: str | Path | bytes,
    metadata: Mapping[str, Any],
    private_key: bytes | str | Path,
    *,
    keyframe_interval: int = 30,
) -> bytes:
    key = load_signing_key(private_key)
    key_id = public_key_id(key.verifying_key.to_pem())
    image, tracks = encode_image_tracks(source, keyframe_interval=keyframe_interval)
    preview = generate_preview_webp(image)
    if len(preview) > MAX_PREVIEW_BYTES:
        raise PackageValidationError("generated preview exceeds the 2 MiB limit")
    _validate_preview_webp(preview)
    manifest = _base_manifest(metadata, kind="frames", key_id=key_id)
    supplied_schema = manifest.get("parameter_schema", {})
    if supplied_schema != {}:
        raise PackageValidationError(
            "frame packages cannot declare custom parameters"
        )
    schema: dict[str, dict[str, Any]] = {}
    for name, spec in _FRAME_CONTROLS.items():
        resolved_spec = dict(spec)
        if name == "loop":
            resolved_spec["default"] = image.loop_count == 0
        if name in schema and schema[name] != resolved_spec:
            raise PackageValidationError(f"frame control {name!r} cannot be redefined")
        schema[name] = resolved_spec
    if set(schema) != FRAME_PARAMETER_NAMES:
        raise AssertionError("receiver frame-control schema is incomplete")
    manifest["parameter_schema"] = schema
    manifest.pop("imports", None)
    payload_members = {track_path(index): tracks[index] for index in range(RECEIVER_COUNT)}
    payload_members[PREVIEW_PATH] = preview
    device_hashes = tuple(hashlib.sha256(track).digest() for track in tracks)
    return _finish_package(manifest, payload_members, device_hashes, private_key)


def build_native_package(
    module: str | Path | bytes,
    preview_webp: str | Path | bytes,
    metadata: Mapping[str, Any],
    private_key: bytes | str | Path,
    *,
    imports: list[str],
) -> bytes:
    module_bytes = module if isinstance(module, bytes) else Path(module).read_bytes()
    preview_bytes = preview_webp if isinstance(preview_webp, bytes) else Path(preview_webp).read_bytes()
    if not module_bytes or len(module_bytes) > MAX_NATIVE_BYTES:
        raise PackageValidationError("native payload must be non-empty and at most 512 KiB")
    _validate_native_elf(module_bytes)
    if len(preview_bytes) > MAX_PREVIEW_BYTES:
        raise PackageValidationError("native preview must be no larger than 2 MiB")
    _validate_preview_webp(preview_bytes)
    key = load_signing_key(private_key)
    manifest = _base_manifest(metadata, kind="native", key_id=public_key_id(key.verifying_key.to_pem()))
    manifest["imports"] = imports
    payload_members = {NATIVE_PAYLOAD_PATH: module_bytes, PREVIEW_PATH: preview_bytes}
    digest = hashlib.sha256(module_bytes).digest()
    return _finish_package(manifest, payload_members, (digest, digest, digest, digest), private_key)


def inspect_package(
    source: str | Path | bytes,
    trusted_keys: Mapping[str, bytes | str | Path],
    *,
    expected_abi: str = ANIMATION_ABI,
    expected_target: str = ESP32_TARGET,
    import_allowlist: set[str] | frozenset[str] = DEFAULT_IMPORT_ALLOWLIST,
) -> VerifiedPackage:
    raw, members = read_safe_zip(source)
    for required in (MANIFEST_PATH, INDEX_PATH, SIGNATURE_PATH):
        if required not in members:
            raise PackageValidationError(f"package is missing {required}")
    manifest_value = parse_canonical_json(members[MANIFEST_PATH], label="manifest")
    manifest = validate_manifest(manifest_value, expected_abi=expected_abi, expected_target=expected_target)
    key_id = manifest["signing_key_id"]
    public_key = trusted_keys.get(key_id)
    if public_key is None:
        raise PackageValidationError(f"unknown signing key: {key_id}")
    if public_key_id(public_key) != key_id:
        raise PackageValidationError("trusted key fingerprint does not match signing_key_id")
    index_bytes = members[INDEX_PATH]
    verify(index_bytes, members[SIGNATURE_PATH], public_key)
    index = PackageIndex.decode(index_bytes)
    if index.kind != manifest["kind"] or index.manifest_sha256 != hashlib.sha256(members[MANIFEST_PATH]).digest():
        raise PackageValidationError("signed index does not match the manifest")
    expected_payload_paths = {PREVIEW_PATH}
    if manifest["kind"] == "native":
        expected_payload_paths.add(NATIVE_PAYLOAD_PATH)
    else:
        expected_payload_paths.update(track_path(index) for index in range(RECEIVER_COUNT))
    allowed_members = expected_payload_paths | {MANIFEST_PATH, INDEX_PATH, SIGNATURE_PATH}
    if set(members) != allowed_members:
        raise PackageValidationError(f"package contains missing or unexpected members: {sorted(set(members) ^ allowed_members)}")
    if set(manifest["payload_hashes"]) != expected_payload_paths:
        raise PackageValidationError("manifest payload_hashes do not match required package members")
    for path in expected_payload_paths:
        if sha256(members[path]) != manifest["payload_hashes"][path]:
            raise PackageValidationError(f"payload hash mismatch: {path}")
    preview = members[PREVIEW_PATH]
    if len(preview) > MAX_PREVIEW_BYTES:
        raise PackageValidationError("package preview exceeds the 2 MiB limit")
    _validate_preview_webp(preview)
    tracks: tuple[DecodedTrack, ...] = ()
    if manifest["kind"] == "native":
        payload = members[NATIVE_PAYLOAD_PATH]
        if not payload or len(payload) > MAX_NATIVE_BYTES:
            raise PackageValidationError("native payload exceeds its 512 KiB limit")
        _validate_native_elf(payload)
        imports = set(manifest.get("imports", []))
        forbidden = imports - set(import_allowlist)
        if forbidden:
            raise PackageValidationError(f"native payload imports forbidden symbols: {sorted(forbidden)}")
        digest = hashlib.sha256(payload).digest()
        if index.device_payload_sha256 != (digest, digest, digest, digest):
            raise PackageValidationError("signed index native hashes do not match the payload")
    else:
        decoded = []
        device_hashes = []
        for device_index in range(RECEIVER_COUNT):
            payload = members[track_path(device_index)]
            if len(payload) > MAX_TRACK_BYTES:
                raise PackageValidationError("frame payload exceeds its 2.5 MiB limit")
            decoded.append(decode_track(payload, expected_device_index=device_index))
            device_hashes.append(hashlib.sha256(payload).digest())
        first = decoded[0]
        if any(track.durations_ms != first.durations_ms or track.loop_count != first.loop_count or len(track.frames) != len(first.frames) for track in decoded[1:]):
            raise PackageValidationError("frame tracks disagree on timing or looping")
        tracks = tuple(decoded)
        if index.device_payload_sha256 != tuple(device_hashes):
            raise PackageValidationError("signed index frame hashes do not match the tracks")
    return VerifiedPackage(manifest, sha256(raw), members, tracks, raw)
