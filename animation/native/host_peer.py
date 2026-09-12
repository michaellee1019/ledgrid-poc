"""Build and verify a platform-local host peer for one exact native bundle."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Any, Mapping

from animation.core.plugin_loader import AnimationPluginLoader

from .bundle import inspect_bundle, sha256
from .constants import HOST_IDENTITY_FLAGS, HOST_LINK_FLAGS, PLUGIN_ROOT
from .errors import NativeBuildError, NativePreviewError
from .schema import canonical_json


HOST_PEER_RECEIPT_SCHEMA = "ledgrid.native-background-build-receipt"
HOST_PEER_RECEIPT_VERSION = 2
HOST_PEER_FILES = frozenset(
    ("bundle.zip", "host-preview.so", "module.so", "preview.webp", "receipt.json")
)
HOST_PEER_COMPILER = Path("/usr/bin/c++")


def _host_platform() -> str:
    value = (
        "darwin"
        if sys.platform == "darwin"
        else "linux"
        if sys.platform.startswith("linux")
        else ""
    )
    if value not in HOST_LINK_FLAGS:
        raise NativeBuildError(f"native host preview does not support {sys.platform!r}")
    return value


def _machine() -> str:
    value = platform.machine().lower()
    aliases = {"amd64": "x86_64", "arm64": "aarch64"}
    return aliases.get(value, value)


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _platform_compiler() -> Path:
    if not HOST_PEER_COMPILER.is_file():
        raise NativeBuildError(
            f"platform host C++ compiler is unavailable: {HOST_PEER_COMPILER}"
        )
    return HOST_PEER_COMPILER.resolve(strict=True)


def _regular(path: Path, *, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise NativeBuildError(f"{label} is unavailable: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise NativeBuildError(f"{label} must be a regular non-symlink file")
    return path.read_bytes()


def _current_component_manifest(root: Path, plugin_id: str) -> dict[str, Any]:
    loader = AnimationPluginLoader(os.fspath(root / PLUGIN_ROOT))
    try:
        loader.scan_components()
        manifest = loader.component_manifests[plugin_id]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise NativeBuildError(
            f"native component manifest is unavailable: {exc}"
        ) from exc
    return {key: value for key, value in manifest.items() if not key.startswith("_")}


def _binary_identity(raw: bytes) -> dict[str, Any]:
    machine = _machine()
    pointer_bits = ctypes.sizeof(ctypes.c_void_p) * 8
    if raw[:4] == b"\x7fELF":
        if len(raw) < 20 or raw[5] != 1:
            raise NativePreviewError("platform host peer must be a little-endian ELF")
        elf_bits = {1: 32, 2: 64}.get(raw[4])
        expected_machine = {
            "aarch64": 183,
            "armv7l": 40,
            "armv6l": 40,
            "x86_64": 62,
        }.get(machine)
        if elf_bits != pointer_bits or expected_machine is None:
            raise NativePreviewError("platform host peer ELF class is incompatible")
        elf_type, elf_machine = struct.unpack_from("<HH", raw, 16)
        if elf_type != 3 or elf_machine != expected_machine:
            raise NativePreviewError(
                "platform host peer ELF machine/type is incompatible"
            )
        return {"format": "elf", "bits": elf_bits, "machine_id": elf_machine}
    if raw[:4] == b"\xcf\xfa\xed\xfe":
        if len(raw) < 16 or pointer_bits != 64:
            raise NativePreviewError("platform host peer Mach-O class is incompatible")
        cpu_type, _cpu_subtype, file_type = struct.unpack_from("<III", raw, 4)
        expected_cpu = {"aarch64": 0x0100000C, "x86_64": 0x01000007}.get(machine)
        if expected_cpu is None or cpu_type != expected_cpu or file_type != 6:
            raise NativePreviewError(
                "platform host peer Mach-O machine/type is incompatible"
            )
        return {"format": "mach-o", "bits": 64, "machine_id": cpu_type}
    raise NativePreviewError("platform host peer is not a supported shared library")


def _parse_receipt(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NativePreviewError(
            f"platform host peer receipt is malformed: {exc}"
        ) from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise NativePreviewError("platform host peer receipt is not canonical JSON")
    return value


def validate_host_peer(
    directory: Path,
    *,
    plugin_id: str,
    bundle_digest: str,
    component_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate an immutable platform peer before its isolated worker loads it."""

    if directory.is_symlink() or not directory.is_dir():
        raise NativePreviewError("platform host peer directory is unsafe")
    if directory.stat().st_mode & 0o222:
        raise NativePreviewError("platform host peer directory must be immutable")
    if frozenset(path.name for path in directory.iterdir()) != HOST_PEER_FILES:
        raise NativePreviewError(
            "platform host peer contains missing or unexpected files"
        )
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o222:
            raise NativePreviewError(
                "platform host peer files must be immutable regular files"
            )

    bundle_raw = (directory / "bundle.zip").read_bytes()
    module_raw = (directory / "module.so").read_bytes()
    preview_raw = (directory / "preview.webp").read_bytes()
    host_raw = (directory / "host-preview.so").read_bytes()
    receipt = _parse_receipt((directory / "receipt.json").read_bytes())
    verified = inspect_bundle(bundle_raw)
    expected_fields = {
        "schema",
        "schema_version",
        "plugin_id",
        "bundle_digest",
        "payload_digest",
        "component_manifest_sha256",
        "source_inputs",
        "host_artifact_sha256",
        "host",
    }
    if (
        set(receipt) != expected_fields
        or receipt.get("schema") != HOST_PEER_RECEIPT_SCHEMA
        or receipt.get("schema_version") != HOST_PEER_RECEIPT_VERSION
    ):
        raise NativePreviewError("platform host peer receipt contract is invalid")
    if (
        receipt.get("plugin_id") != plugin_id
        or receipt.get("bundle_digest") != bundle_digest
        or receipt.get("payload_digest") != verified.payload_digest
        or verified.bundle_digest != bundle_digest
        or verified.manifest.get("plugin_id") != plugin_id
        or module_raw != verified.payload
        or preview_raw != verified.preview
        or receipt.get("component_manifest_sha256")
        != verified.manifest.get("component_manifest_sha256")
        or sha256(canonical_json(dict(component_manifest)))
        != verified.manifest.get("component_manifest_sha256")
        or receipt.get("source_inputs")
        != verified.manifest.get("build", {}).get("source_inputs")
        or receipt.get("host_artifact_sha256") != sha256(host_raw)
    ):
        raise NativePreviewError(
            "platform host peer identity does not match its exact bundle/source"
        )
    host = receipt.get("host")
    expected_host_fields = {
        "platform",
        "machine",
        "endianness",
        "pointer_bits",
        "compiler",
        "compiler_sha256",
        "compiler_version",
        "target",
        "flags",
        "binary",
    }
    if not isinstance(host, dict) or set(host) != expected_host_fields:
        raise NativePreviewError("platform host peer toolchain identity is invalid")
    string_fields = ("compiler", "compiler_version", "target")
    compiler_digest = host.get("compiler_sha256")
    if (
        any(
            not isinstance(host.get(field), str) or not host[field]
            for field in string_fields
        )
        or not isinstance(compiler_digest, str)
        or len(compiler_digest) != 64
        or any(character not in "0123456789abcdef" for character in compiler_digest)
    ):
        raise NativePreviewError("platform host peer toolchain identity is invalid")
    expected_platform = (
        "darwin"
        if sys.platform == "darwin"
        else "linux"
        if sys.platform.startswith("linux")
        else None
    )
    expected_flags = list(
        HOST_IDENTITY_FLAGS + HOST_LINK_FLAGS.get(expected_platform or "", ())
    )
    if (
        host.get("platform") != expected_platform
        or host.get("machine") != _machine()
        or host.get("endianness") != sys.byteorder
        or host.get("pointer_bits") != ctypes.sizeof(ctypes.c_void_p) * 8
        or host.get("flags") != expected_flags
        or host.get("binary") != _binary_identity(host_raw)
    ):
        raise NativePreviewError("platform host peer is incompatible with this host")
    return receipt


def build_host_peer(
    source_root: Path,
    bundle_path: Path,
    output_root: Path,
    *,
    plugin_id: str,
    bundle_digest: str,
) -> dict[str, Any]:
    """Compile one host peer from verified snapshot bytes without rebuilding the bundle."""

    source_input = source_root.absolute()
    try:
        source_root = source_input.resolve(strict=True)
    except OSError as exc:
        raise NativeBuildError(
            f"native host preview source root is unavailable: {exc}"
        ) from exc
    if source_root != source_input or not source_root.is_dir():
        raise NativeBuildError("native host preview source root is unsafe")
    output_root = output_root.absolute()
    existing_output_parent = output_root
    while not existing_output_parent.exists():
        existing_output_parent = existing_output_parent.parent
    if (
        existing_output_parent.is_symlink()
        or existing_output_parent.resolve(strict=True) != existing_output_parent
    ):
        raise NativeBuildError("platform host peer output root is unsafe")
    verified = inspect_bundle(_regular(bundle_path, label="managed native bundle"))
    if (
        verified.bundle_digest != bundle_digest
        or verified.manifest.get("plugin_id") != plugin_id
    ):
        raise NativeBuildError(
            "managed native bundle does not match the requested host peer"
        )
    component = _current_component_manifest(source_root, plugin_id)
    if sha256(canonical_json(component)) != verified.manifest.get(
        "component_manifest_sha256"
    ):
        raise NativeBuildError(
            "snapshot component manifest does not match the managed bundle"
        )
    source_inputs = verified.manifest.get("build", {}).get("source_inputs")
    if not isinstance(source_inputs, list):
        raise NativeBuildError("managed native bundle has no source input identity")
    for item in source_inputs:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise NativeBuildError(
                "managed native bundle source input identity is malformed"
            )
        path = source_root / str(item["path"])
        try:
            resolved_path = path.resolve(strict=True)
        except OSError as exc:
            raise NativeBuildError(
                f"snapshot native source input is unavailable: {item.get('path')}"
            ) from exc
        if (
            resolved_path != path.absolute()
            or sha256(_regular(path, label=f"source input {item['path']}"))
            != item["sha256"]
        ):
            raise NativeBuildError(
                f"snapshot native source input is stale: {item.get('path')}"
            )

    destination = output_root / plugin_id / bundle_digest
    if destination.exists() or destination.is_symlink():
        validate_host_peer(
            destination,
            plugin_id=plugin_id,
            bundle_digest=bundle_digest,
            component_manifest=component,
        )
        return {"path": os.fspath(destination), "reused": True}

    compiler = _platform_compiler()
    host_platform = _host_platform()
    flags = HOST_IDENTITY_FLAGS + HOST_LINK_FLAGS[host_platform]
    source_relative = (
        f"{PLUGIN_ROOT}/{plugin_id}/{verified.manifest['build']['source_path']}"
    )
    if source_relative not in {item["path"] for item in source_inputs}:
        raise NativeBuildError(
            "managed native bundle source path is outside its input identity"
        )
    expected_dependencies = {item["path"] for item in source_inputs}
    environment = {
        **os.environ,
        "LC_ALL": "C",
        "LANG": "C",
        "SOURCE_DATE_EPOCH": "0",
        "ZERO_AR_DATE": "1",
    }
    output_root.mkdir(parents=True, exist_ok=True)
    if output_root.is_symlink() or output_root.resolve(strict=True) != output_root:
        raise NativeBuildError("platform host peer output root is unsafe")
    plugin_output = output_root / plugin_id
    plugin_output.mkdir(parents=True, exist_ok=True)
    if (
        plugin_output.is_symlink()
        or plugin_output.resolve(strict=True) != plugin_output
    ):
        raise NativeBuildError("platform host peer root is unsafe")
    with tempfile.TemporaryDirectory(
        prefix=f".{bundle_digest}.", dir=plugin_output
    ) as name:
        temporary = Path(name)
        host_path = temporary / "host-preview.so"
        depfile = temporary / "host.d"
        command = (
            os.fspath(compiler),
            *flags,
            "-MMD",
            "-MF",
            os.fspath(depfile),
            "-o",
            os.fspath(host_path),
            source_relative,
        )
        completed = subprocess.run(
            command,
            cwd=source_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            raise NativeBuildError(
                f"platform host peer compile failed: {(completed.stderr or completed.stdout).strip()[-4000:]}"
            )
        try:
            dependency_text = (
                depfile.read_text(encoding="utf-8")
                .replace("\\\n", " ")
                .split(":", 1)[1]
            )
            dependencies = set()
            import shlex

            for value in shlex.split(dependency_text):
                absolute = (
                    (source_root / value).resolve()
                    if not Path(value).is_absolute()
                    else Path(value).resolve()
                )
                dependencies.add(absolute.relative_to(source_root.resolve()).as_posix())
        except (OSError, ValueError, IndexError) as exc:
            raise NativeBuildError(
                f"platform host peer dependency evidence is malformed: {exc}"
            ) from exc
        if dependencies != expected_dependencies:
            raise NativeBuildError(
                f"platform host peer consumed inputs outside the allowlist: {sorted(dependencies)}"
            )
        host_raw = host_path.read_bytes()
        binary = _binary_identity(host_raw)
        compiler_version = subprocess.run(
            (os.fspath(compiler), "--version"),
            cwd=source_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        target = subprocess.run(
            (os.fspath(compiler), "-dumpmachine"),
            cwd=source_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        receipt = {
            "schema": HOST_PEER_RECEIPT_SCHEMA,
            "schema_version": HOST_PEER_RECEIPT_VERSION,
            "plugin_id": plugin_id,
            "bundle_digest": bundle_digest,
            "payload_digest": verified.payload_digest,
            "component_manifest_sha256": verified.manifest["component_manifest_sha256"],
            "source_inputs": source_inputs,
            "host_artifact_sha256": sha256(host_raw),
            "host": {
                "platform": host_platform,
                "machine": _machine(),
                "endianness": sys.byteorder,
                "pointer_bits": ctypes.sizeof(ctypes.c_void_p) * 8,
                "compiler": compiler.name,
                "compiler_sha256": _digest_file(compiler),
                "compiler_version": compiler_version,
                "target": target,
                "flags": list(flags),
                "binary": binary,
            },
        }
        files = {
            "bundle.zip": verified.raw,
            "module.so": verified.payload,
            "preview.webp": verified.preview,
            "host-preview.so": host_raw,
            "receipt.json": canonical_json(receipt),
        }
        depfile.unlink(missing_ok=True)
        for filename, raw in files.items():
            path = temporary / filename
            path.write_bytes(raw)
            path.chmod(0o444)
        temporary.chmod(0o555)
        validate_host_peer(
            temporary,
            plugin_id=plugin_id,
            bundle_digest=bundle_digest,
            component_manifest=component,
        )
        temporary.rename(destination)
    validate_host_peer(
        destination,
        plugin_id=plugin_id,
        bundle_digest=bundle_digest,
        component_manifest=component,
    )
    return {
        "path": os.fspath(destination),
        "reused": False,
        "host_artifact_sha256": receipt["host_artifact_sha256"],
        "host": receipt["host"],
    }


__all__ = [
    "HOST_PEER_RECEIPT_SCHEMA",
    "HOST_PEER_RECEIPT_VERSION",
    "build_host_peer",
    "validate_host_peer",
]
