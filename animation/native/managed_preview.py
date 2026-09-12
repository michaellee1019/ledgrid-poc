"""Verified execution seam for trusted repository-built native host peers."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
from typing import Any, Mapping, Sequence

import numpy as np

from animation.core.compositing import BaseFrame
from animation.core.plugin_loader import AnimationPluginLoader

from .bundle import inspect_bundle, sha256
from .constants import GLOBAL_STRIPS, LEDS_PER_STRIP
from .errors import NativePreviewError
from .host_peer import HOST_PEER_RECEIPT_SCHEMA, HOST_PEER_RECEIPT_VERSION, validate_host_peer
from .preview_worker import run as run_host_preview
from .schema import canonical_json, validate_parameters


_BUILD_RECEIPT_SCHEMA = "ledgrid.native-background-build-receipt"
_BUILD_FILES = frozenset(
    ("bundle.zip", "host-preview.so", "module.so", "preview.webp", "receipt.json")
)
_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise NativePreviewError(f"managed native {label} must be a regular non-symlink file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise NativePreviewError(f"managed native {label} is unreadable: {exc}") from exc


def _current_component_manifest(root: Path, plugin_id: str) -> dict[str, Any]:
    loader = AnimationPluginLoader(str(root / "animation" / "plugins"))
    try:
        loader.scan_components()
        manifest = loader.component_manifests[plugin_id]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise NativePreviewError(
            f"managed native source manifest is unavailable for {plugin_id!r}: {exc}"
        ) from exc
    return {key: value for key, value in manifest.items() if not key.startswith("_")}


class ManagedNativeHostPreview:
    """Execute the verified host peer built from one exact native bundle source."""

    def __init__(self, project_root: str | Path, plugin_id: str, bundle_digest: str) -> None:
        if not _PLUGIN_ID.fullmatch(plugin_id) or not _SHA256.fullmatch(bundle_digest):
            raise NativePreviewError("managed native build selector is invalid")
        root = Path(project_root).resolve(strict=True)
        runtime = root / "run_state"
        if runtime.is_symlink():
            expected = root.parent.parent / "run_state"
            if (
                root.parent.name != "releases"
                or re.fullmatch(r"[0-9a-f]{64}", root.name) is None
                or Path(os.readlink(runtime)).is_absolute()
                or runtime.resolve(strict=False) != expected.resolve(strict=False)
            ):
                raise NativePreviewError("managed native runtime link is outside deployment policy")
            runtime = expected
        elif runtime.exists() and runtime.resolve(strict=False) != runtime:
            raise NativePreviewError("managed native runtime path must not contain symlinks")
        build = runtime / "native_background_builds" / plugin_id / bundle_digest
        try:
            resolved_build = build.resolve(strict=True)
        except OSError as exc:
            raise NativePreviewError(
                f"managed native build is unavailable for {plugin_id}@{bundle_digest}"
            ) from exc
        if resolved_build != build.absolute():
            raise NativePreviewError("managed native build path must not contain symlinks")
        if build.is_symlink() or not build.is_dir():
            raise NativePreviewError(
                f"managed native build is unavailable for {plugin_id}@{bundle_digest}"
            )
        try:
            names = frozenset(path.name for path in build.iterdir())
        except OSError as exc:
            raise NativePreviewError(f"managed native build is unreadable: {exc}") from exc
        if names != _BUILD_FILES:
            raise NativePreviewError("managed native build contains missing or unexpected files")

        bundle_raw = _regular_file(build / "bundle.zip", "bundle")
        module_raw = _regular_file(build / "module.so", "payload")
        host_raw = _regular_file(build / "host-preview.so", "host preview")
        preview_raw = _regular_file(build / "preview.webp", "preview")
        receipt_raw = _regular_file(build / "receipt.json", "build receipt")
        try:
            verified = inspect_bundle(bundle_raw)
            receipt = json.loads(receipt_raw.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError) as exc:
            raise NativePreviewError(f"managed native build verification failed: {exc}") from exc

        if verified.bundle_digest != bundle_digest or verified.manifest["plugin_id"] != plugin_id:
            raise NativePreviewError("managed native bundle identity does not match its selector")
        if module_raw != verified.payload or preview_raw != verified.preview:
            raise NativePreviewError("managed native materialized artifacts disagree with the bundle")
        if canonical_json(receipt) != receipt_raw or not isinstance(receipt, dict):
            raise NativePreviewError("managed native build receipt is not canonical JSON")

        current = _current_component_manifest(root, plugin_id)
        if sha256(canonical_json(current)) != verified.manifest["component_manifest_sha256"]:
            raise NativePreviewError("managed native component manifest is stale")
        platform_peer = (
            receipt.get("schema") == HOST_PEER_RECEIPT_SCHEMA
            and receipt.get("schema_version") == HOST_PEER_RECEIPT_VERSION
        )
        if platform_peer:
            validate_host_peer(
                build,
                plugin_id=plugin_id,
                bundle_digest=bundle_digest,
                component_manifest=current,
            )
            host_digest = receipt["host_artifact_sha256"]
        else:
            if sha256(host_raw) != verified.manifest["build"]["host_artifact_sha256"]:
                raise NativePreviewError("managed native host preview digest does not match the bundle")
            if (
                receipt.get("schema") != _BUILD_RECEIPT_SCHEMA
                or receipt.get("schema_version") != 1
                or receipt.get("plugin_id") != plugin_id
                or receipt.get("bundle_digest") != bundle_digest
                or receipt.get("payload_digest") != verified.payload_digest
            ):
                raise NativePreviewError("managed native build receipt identity does not match its bytes")
            for source in verified.manifest["build"]["source_inputs"]:
                path = root / source["path"]
                raw = _regular_file(path, f"source input {source['path']}")
                if hashlib.sha256(raw).hexdigest() != source["sha256"]:
                    raise NativePreviewError(f"managed native source input is stale: {source['path']}")
            host_digest = verified.manifest["build"]["host_artifact_sha256"]

        self.plugin_id = plugin_id
        self.bundle_digest = bundle_digest
        self.payload_digest = verified.payload_digest
        self.host_artifact_digest = host_digest
        self.manifest = verified.manifest
        self.host_library_path = build / "host-preview.so"
        self._pixels = np.zeros((GLOBAL_STRIPS * LEDS_PER_STRIP, 3), dtype=np.uint8)
        self._key: tuple[Any, ...] | None = None

    def render(
        self,
        *,
        parameters: Mapping[str, Any],
        palette: Sequence[Sequence[int]],
        scaled_scene_time: float,
        unscaled_scene_time: float,
        frame_index: int,
    ) -> BaseFrame:
        schema = self.manifest["parameter_schema"]
        resolved = validate_parameters(schema, parameters)
        source_fps = float(resolved["source_fps"])
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
            for value in (scaled_scene_time, unscaled_scene_time)
        ):
            raise NativePreviewError("managed native preview time must be finite and non-negative")
        palette_value = tuple(tuple(int(channel) for channel in color) for color in palette)
        if (
            len(palette_value) != 8
            or any(len(color) != 3 or any(not 0 <= channel <= 255 for channel in color) for color in palette_value)
        ):
            raise NativePreviewError("managed native preview requires eight semantic RGB roles")
        scaled_us = round(float(scaled_scene_time) * 1_000_000)
        source_fps_f32 = struct.unpack("<f", struct.pack("<f", source_fps))[0]
        source_fps_q8 = int(source_fps_f32 * 256.0)
        source_tick = scaled_us * source_fps_q8 // 256_000_000
        key = (
            tuple(sorted(resolved.items())), palette_value, source_tick,
        )
        if key == self._key:
            return BaseFrame(self._pixels, changed=False, dirty_ranges=())

        unscaled_us = round(float(unscaled_scene_time) * 1_000_000)
        request = {
            "host_library": str(self.host_library_path),
            "manifest": self.manifest,
            "parameters": resolved,
            "frame_count": 1,
            "scene_times_us": [unscaled_us],
            "scaled_scene_times_us": [scaled_us],
            "cadence_period_us": math.ceil(1_000_000 / source_fps),
            "render_budget_ms": 1000 / source_fps,
            "vibe": {"luminance_q8_8": 256, "palette": palette_value},
        }
        try:
            evidence, raw = run_host_preview(request)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise NativePreviewError(f"managed native host preview failed closed: {exc}") from exc
        if evidence["frame_count"] != 1 or evidence["changed_frames"] != 1:
            raise NativePreviewError("managed native host preview did not produce one complete frame")
        pixels = np.frombuffer(raw, dtype=np.uint8)
        if pixels.size != self._pixels.size:
            raise NativePreviewError("managed native host preview returned incompatible geometry")
        self._pixels[:] = pixels.reshape(self._pixels.shape)
        self._key = key
        return BaseFrame(self._pixels, changed=True)


__all__ = ["ManagedNativeHostPreview"]
