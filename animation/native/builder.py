"""Deterministic dual-toolchain builder for repository native backgrounds."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from animation.core.plugin_loader import AnimationPluginLoader

from .bundle import build_bundle, inspect_bundle, sha256
from .constants import (
    ABI_HEADER_PATH,
    ABI_SCHEMA,
    ABI_VERSION,
    BUNDLE_SCHEMA,
    BUNDLE_VERSION,
    COMPONENT_ENTRYPOINT,
    EXPECTED_PLATFORMIO_VERSION,
    EXPECTED_TARGET_TOOLCHAIN_VERSION,
    GLOBAL_STRIPS,
    HOST_IDENTITY_FLAGS,
    HOST_LINK_FLAGS,
    LEDS_PER_STRIP,
    LOCAL_STRIPS,
    PAYLOAD_PATH,
    PLUGIN_ROOT,
    PREVIEW_PATH,
    RECEIVER_OFFSETS,
    RECEIVER_VIEWS,
    TARGET,
    TARGET_COMPILER_NAME,
    TARGET_DYNCONFIG_NAME,
    TARGET_IDENTITY_FLAGS,
    TARGET_TOOLCHAIN_PACKAGE,
)
from .errors import NativeBuildError, NativeElfError
from .elf import validate_target_elf
from .preview import (
    PreviewTiming,
    generate_preview_webp,
    preview_codec_identity,
    render_host_frames,
    stress_parameters,
)
from .schema import canonical_json, validate_parameter_schema

_SOURCE_PATH = PurePosixPath("native/background.cpp")
_RECEIPT_SCHEMA = "ledgrid.native-background-build-receipt"


@dataclass(frozen=True)
class NativeBuildResult:
    """Paths and identities produced by one build.

    ``execute=False`` performs source-policy and catalog validation only; its
    artifact paths and digests are therefore ``None``.
    """

    bundle_path: Path | None
    bundle_digest: str | None
    payload_digest: str | None
    receipt_path: Path | None
    preview_path: Path | None
    manifest: dict[str, Any]
    host_library_path: Path | None = None
    payload_path: Path | None = None
    default_timing: PreviewTiming | None = None
    stress_timing: PreviewTiming | None = None
    default_missed_deadlines: int | None = None
    stress_missed_deadlines: int | None = None
    default_changed_frames: int | None = None
    stress_changed_frames: int | None = None
    default_total_frames: int | None = None
    stress_total_frames: int | None = None
    executed: bool = False


@dataclass(frozen=True)
class _CompilerIdentity:
    path: Path
    manifest: dict[str, str]
    environment: dict[str, str]


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        tuple(command),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        if len(detail) > 4000:
            detail = detail[-4000:]
        raise NativeBuildError(
            f"native build command failed ({Path(command[0]).name}, "
            f"exit {completed.returncode}): {detail or 'no diagnostic'}"
        )
    return completed


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular_nonsymlink(path: Path, *, root: Path, label: str) -> Path:
    try:
        lexical = path.absolute()
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
        metadata = lexical.lstat()
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise NativeBuildError(f"{label} is missing or escapes the repository") from exc
    if not stat.S_ISREG(metadata.st_mode) or lexical.is_symlink() or resolved != lexical:
        raise NativeBuildError(f"{label} must be a non-symlink regular file")
    cursor = lexical.parent
    while cursor != root:
        if cursor.is_symlink():
            raise NativeBuildError(f"{label} path must not traverse a symlink")
        if root not in cursor.parents:
            raise NativeBuildError(f"{label} path escapes the repository")
        cursor = cursor.parent
    return lexical


def _git(root: Path, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ("git", "-C", os.fspath(root), *arguments),
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise NativeBuildError(f"native source policy requires a Git worktree: {exc}") from exc


def _require_tracked_inputs(
    root: Path, plugin_id: str, package_dir: Path
) -> tuple[Path, Path, Path]:
    manifest_path = package_dir / "manifest.json"
    source_path = package_dir / _SOURCE_PATH.as_posix()
    header_path = root / ABI_HEADER_PATH
    inputs = (
        _regular_nonsymlink(manifest_path, root=root, label="component manifest"),
        _regular_nonsymlink(source_path, root=root, label="native source"),
        _regular_nonsymlink(header_path, root=root, label="native ABI header"),
    )
    relative = tuple(path.relative_to(root).as_posix() for path in inputs)
    tracked = {
        os.fsdecode(item)
        for item in _git(root, "ls-files", "-z", "--", *relative).split(b"\0")
        if item
    }
    missing = sorted(set(relative) - tracked)
    if missing:
        raise NativeBuildError(f"native build rejects untracked required inputs: {missing}")
    native_relative = f"{PLUGIN_ROOT}/{plugin_id}/native"
    untracked = sorted(
        os.fsdecode(item)
        for item in _git(
            root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            native_relative,
        ).split(b"\0")
        if item
    )
    if untracked:
        raise NativeBuildError(f"native build rejects untracked package sources: {untracked}")
    return inputs


def _clean_component_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if not key.startswith("_")}


def _load_component(
    root: Path, plugin_id: str
) -> tuple[dict[str, Any], Path, tuple[Path, Path, Path]]:
    loader = AnimationPluginLoader(os.fspath(root / PLUGIN_ROOT))
    try:
        loader.scan_components()
    except (OSError, ValueError, TypeError) as exc:
        raise NativeBuildError(f"component catalog rejected native source: {exc}") from exc
    manifest = loader.component_manifests.get(plugin_id)
    package_dir = loader.component_dirs.get(plugin_id)
    if manifest is None or package_dir is None:
        raise NativeBuildError(f"unknown receiver-native component {plugin_id!r}")
    if manifest.get("provider") != "receiver_native":
        raise NativeBuildError(f"component {plugin_id!r} is not receiver-native")
    expected_package = (root / PLUGIN_ROOT / plugin_id).absolute()
    if package_dir != expected_package:
        raise NativeBuildError("native component package is outside its canonical repository path")
    tracked = _require_tracked_inputs(root, plugin_id, package_dir)
    return _clean_component_manifest(manifest), package_dir, tracked


def _platformio_version(root: Path) -> str:
    executable = shutil.which("platformio")
    if executable is None:
        raise NativeBuildError("PlatformIO 6.1.19 is required for native target builds")
    completed = _run((executable, "--version"), cwd=root)
    version = completed.stdout.strip().rsplit(" ", 1)[-1]
    if version != EXPECTED_PLATFORMIO_VERSION:
        raise NativeBuildError(
            f"PlatformIO must be {EXPECTED_PLATFORMIO_VERSION}, got {version!r}"
        )
    return version


def _target_compiler(root: Path) -> _CompilerIdentity:
    platformio_version = _platformio_version(root)
    core_dir = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio"))
    package_dir = core_dir / "packages" / TARGET_TOOLCHAIN_PACKAGE
    package_json = package_dir / "package.json"
    compiler = package_dir / "bin" / TARGET_COMPILER_NAME
    dynconfig = package_dir / "lib" / TARGET_DYNCONFIG_NAME
    try:
        package = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NativeBuildError(f"pinned Xtensa package metadata is unavailable: {exc}") from exc
    if package.get("name") != TARGET_TOOLCHAIN_PACKAGE or package.get("version") != EXPECTED_TARGET_TOOLCHAIN_VERSION:
        raise NativeBuildError(
            "installed Xtensa package does not match the pinned native build identity"
        )
    if compiler.is_symlink() or not compiler.is_file():
        raise NativeBuildError(f"pinned target compiler is unavailable: {compiler}")
    if dynconfig.is_symlink() or not dynconfig.is_file():
        raise NativeBuildError(f"pinned ESP32-S3 compiler configuration is unavailable: {dynconfig}")
    version = _run((os.fspath(compiler), "--version"), cwd=root).stdout.strip()
    return _CompilerIdentity(
        compiler,
        {
            "platformio_version": platformio_version,
            "package": TARGET_TOOLCHAIN_PACKAGE,
            "package_version": EXPECTED_TARGET_TOOLCHAIN_VERSION,
            "compiler": TARGET_COMPILER_NAME,
            "compiler_sha256": _sha256_file(compiler),
            "compiler_version": version,
            "dynconfig": TARGET_DYNCONFIG_NAME,
            "dynconfig_sha256": _sha256_file(dynconfig),
        },
        {"XTENSA_GNU_CONFIG": os.fspath(dynconfig)},
    )


def _host_compiler(root: Path) -> _CompilerIdentity:
    if sys.byteorder != "little":
        raise NativeBuildError("native host preview requires a little-endian host")
    selected = os.environ.get("CXX", "c++")
    resolved = shutil.which(selected)
    if resolved is None:
        raise NativeBuildError(f"host C++ compiler is unavailable: {selected}")
    compiler = Path(resolved).resolve(strict=True)
    host_platform = "darwin" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else ""
    if host_platform not in HOST_LINK_FLAGS:
        raise NativeBuildError(f"native host preview does not support {sys.platform!r}")
    version = _run((os.fspath(compiler), "--version"), cwd=root).stdout.strip()
    target = _run((os.fspath(compiler), "-dumpmachine"), cwd=root).stdout.strip()
    return _CompilerIdentity(
        compiler,
        {
            "compiler": compiler.name,
            "compiler_sha256": _sha256_file(compiler),
            "compiler_version": version,
            "platform": host_platform,
            "target": target,
            "endianness": sys.byteorder,
        },
        {},
    )


def _dependencies(depfile: Path, root: Path) -> set[str]:
    try:
        raw = depfile.read_text(encoding="utf-8").replace("\\\n", " ")
        _target, values = raw.split(":", 1)
        paths = shlex.split(values)
    except (OSError, UnicodeError, ValueError) as exc:
        raise NativeBuildError(f"compiler dependency evidence is malformed: {exc}") from exc
    dependencies: set[str] = set()
    for value in paths:
        path = Path(value)
        absolute = path if path.is_absolute() else root / path
        try:
            relative = absolute.resolve(strict=True).relative_to(root).as_posix()
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise NativeBuildError(f"compiler consumed unsafe dependency {value!r}") from exc
        dependencies.add(relative)
    return dependencies


def _compile(
    compiler: Path,
    flags: Sequence[str],
    *,
    root: Path,
    source_relative: str,
    output: Path,
    depfile: Path,
    environment_extra: Mapping[str, str] | None = None,
) -> None:
    environment = {
        **os.environ,
        "LC_ALL": "C",
        "LANG": "C",
        "SOURCE_DATE_EPOCH": "0",
        "ZERO_AR_DATE": "1",
        **dict(environment_extra or {}),
    }
    _run(
        (
            os.fspath(compiler),
            *flags,
            "-MMD",
            "-MF",
            os.fspath(depfile),
            "-o",
            os.fspath(output),
            source_relative,
        ),
        cwd=root,
        env=environment,
    )
    if output.is_symlink() or not output.is_file():
        raise NativeBuildError("compiler did not produce a regular artifact")
    expected_dependencies = {source_relative, ABI_HEADER_PATH}
    actual_dependencies = _dependencies(depfile, root)
    if actual_dependencies != expected_dependencies:
        raise NativeBuildError(
            "native compiler consumed inputs outside the allowlist: "
            f"expected {sorted(expected_dependencies)}, got {sorted(actual_dependencies)}"
        )


def _manifest(
    component: Mapping[str, Any],
    *,
    source_inputs: list[dict[str, str]],
    target_flags: Sequence[str],
    host_flags: Sequence[str],
    target_toolchain: Mapping[str, str],
    host_toolchain: Mapping[str, str],
    header_digest: str,
    source_digest: str,
    host_digest: str,
    payload: bytes,
    preview: bytes,
) -> dict[str, Any]:
    parameter_schema, defaults = validate_parameter_schema(component["parameter_schema"])
    authored_preview = component["preview"]
    simulation_fps = int(authored_preview["simulation_fps"])
    duration_ms = max(1, round(1000 / simulation_fps))
    captures = [float(value) for value in authored_preview["capture_seconds"]]
    return {
        "schema": BUNDLE_SCHEMA,
        "schema_version": BUNDLE_VERSION,
        "plugin_id": component["plugin_id"],
        "component_manifest_sha256": sha256(canonical_json(component)),
        "entrypoint": COMPONENT_ENTRYPOINT,
        "abi": {
            "schema": ABI_SCHEMA,
            "version": ABI_VERSION,
            "header_path": ABI_HEADER_PATH,
            "header_sha256": header_digest,
        },
        "target": {
            "name": TARGET,
            "elf_class": 32,
            "endianness": "little",
            "machine": "xtensa",
            "type": "shared_object",
        },
        "geometry": {
            "global_strips": GLOBAL_STRIPS,
            "local_strips": LOCAL_STRIPS,
            "leds_per_strip": LEDS_PER_STRIP,
            "receiver_offsets": list(RECEIVER_OFFSETS),
            "receiver_views": [
                {
                    "logical_receiver_id": logical_receiver_id,
                    "global_strip_offset": offset,
                    "reverse_local_strip_order": reverse,
                }
                for logical_receiver_id, offset, reverse in RECEIVER_VIEWS
            ],
        },
        "cadence": {
            **component["cadence"],
            "abi_next_deadline_semantics": (
                "absolute_unscaled_microseconds_since_scene_epoch"
            ),
        },
        "parameter_schema": parameter_schema,
        "defaults": defaults,
        "vibe": component["vibe"],
        "installation_profile_requirements": component[
            "installation_profile_requirements"
        ],
        "build": {
            "source_path": _SOURCE_PATH.as_posix(),
            "source_sha256": source_digest,
            "source_inputs": source_inputs,
            "target_flags": list(target_flags),
            "host_flags": list(host_flags),
            "toolchains": {
                "target": dict(target_toolchain),
                "host": dict(host_toolchain),
                "preview_codec": preview_codec_identity(),
            },
            "host_artifact_sha256": host_digest,
        },
        "payload": {
            "path": PAYLOAD_PATH,
            "size": len(payload),
            "sha256": sha256(payload),
        },
        "preview": {
            "path": PREVIEW_PATH,
            "size": len(preview),
            "sha256": sha256(preview),
            "width": GLOBAL_STRIPS,
            "height": LEDS_PER_STRIP,
            "frame_count": len(captures),
            "duration_ms": duration_ms,
            "capture_seconds": captures,
            "simulation_fps": simulation_fps,
        },
    }


def _write_artifacts(
    output_root: Path,
    plugin_id: str,
    *,
    bundle: bytes,
    payload: bytes,
    host_library: bytes,
    preview: bytes,
    receipt: bytes,
) -> tuple[Path, Path, Path, Path, Path]:
    digest = sha256(bundle)
    output_root.mkdir(parents=True, exist_ok=True)
    if output_root.is_symlink() or output_root.resolve() != output_root:
        raise NativeBuildError("native output root must not traverse symbolic links")
    plugin_root = output_root / plugin_id
    destination = plugin_root / digest
    plugin_root.mkdir(parents=True, exist_ok=True)
    if plugin_root.is_symlink() or not plugin_root.is_dir():
        raise NativeBuildError("native output root must be a real directory")
    expected = {
        "bundle.zip": bundle,
        "module.so": payload,
        "host-preview.so": host_library,
        "preview.webp": preview,
    }
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise NativeBuildError("content-addressed native output is unsafe")
        for name, data in expected.items():
            path = destination / name
            if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                raise NativeBuildError("content-addressed native output conflicts with its digest")
    else:
        with tempfile.TemporaryDirectory(prefix=f".{digest}.", dir=plugin_root) as name:
            temporary = Path(name)
            for filename, data in {**expected, "receipt.json": receipt}.items():
                (temporary / filename).write_bytes(data)
            temporary.rename(destination)
    receipt_path = destination / "receipt.json"
    if not receipt_path.exists():
        receipt_path.write_bytes(receipt)
    return (
        destination / "bundle.zip",
        destination / "module.so",
        destination / "host-preview.so",
        destination / "preview.webp",
        receipt_path,
    )


def build_plugin(
    repo_root: str | Path,
    plugin_id: str,
    output_root: str | Path,
    *,
    execute: bool = True,
) -> NativeBuildResult:
    """Build, preview, validate, and package one tracked native component."""

    root_input = Path(repo_root).absolute()
    root = root_input.resolve(strict=True)
    if root.is_symlink() or not (root / ".git").exists():
        raise NativeBuildError("repo_root must be a real Git worktree")
    component, _package_dir, _tracked = _load_component(root, plugin_id)
    if not execute:
        return NativeBuildResult(None, None, None, None, None, component)

    output_input = Path(output_root).absolute()
    cursor = output_input
    while cursor != root_input:
        if cursor.exists() and cursor.is_symlink():
            raise NativeBuildError("native output root must not traverse symbolic links")
        if root_input not in cursor.parents:
            raise NativeBuildError("native output root must remain beneath repo_root")
        cursor = cursor.parent
    output = output_input.resolve(strict=False)
    try:
        output.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise NativeBuildError("native output root must remain beneath repo_root") from exc
    target_compiler = _target_compiler(root)
    host_compiler = _host_compiler(root)
    host_platform = host_compiler.manifest["platform"]
    host_flags = HOST_IDENTITY_FLAGS + HOST_LINK_FLAGS[host_platform]
    source_relative = f"{PLUGIN_ROOT}/{plugin_id}/{_SOURCE_PATH.as_posix()}"

    with tempfile.TemporaryDirectory(prefix="ledgrid-native-build-", dir=output.parent if output.parent.exists() else root) as name:
        scratch = Path(name)
        target_path = scratch / "module.so"
        host_path = scratch / "host-preview.so"
        _compile(
            target_compiler.path,
            TARGET_IDENTITY_FLAGS,
            root=root,
            source_relative=source_relative,
            output=target_path,
            depfile=scratch / "target.d",
            environment_extra=target_compiler.environment,
        )
        _compile(
            host_compiler.path,
            host_flags,
            root=root,
            source_relative=source_relative,
            output=host_path,
            depfile=scratch / "host.d",
            environment_extra=host_compiler.environment,
        )
        payload = target_path.read_bytes()
        host_library = host_path.read_bytes()
        # Target bytes are only parsed, never loaded on the workstation. Reject
        # the complete ELF contract before executing the separately built host peer.
        try:
            validate_target_elf(payload)
        except NativeElfError as exc:
            raise NativeBuildError(f"target ELF validation failed: {exc}") from exc
        authored_preview = component["preview"]
        duration_ms = max(1, round(1000 / int(authored_preview["simulation_fps"])))
        default_run = render_host_frames(host_path, component)
        stress_run = render_host_frames(
            host_path,
            component,
            parameters=stress_parameters(component["parameter_schema"]),
            frame_count=60,
            duration_ms=duration_ms,
        )
        for profile, run in (("default", default_run), ("stress", stress_run)):
            if run.missed_deadlines != 0 or run.timing.p95_ms >= 4.0:
                raise NativeBuildError(
                    f"{profile} host preview missed its acceptance budget: "
                    f"p95={run.timing.p95_ms:.3f}ms "
                    f"missed_deadlines={run.missed_deadlines}"
                )
        preview = generate_preview_webp(default_run.frames, duration_ms=duration_ms)
        source_inputs = sorted(
            (
                {
                    "path": source_relative,
                    "sha256": _sha256_file(root / source_relative),
                },
                {
                    "path": ABI_HEADER_PATH,
                    "sha256": _sha256_file(root / ABI_HEADER_PATH),
                },
            ),
            key=lambda item: item["path"],
        )
        manifest = _manifest(
            component,
            source_inputs=source_inputs,
            target_flags=TARGET_IDENTITY_FLAGS,
            host_flags=host_flags,
            target_toolchain=target_compiler.manifest,
            host_toolchain=host_compiler.manifest,
            header_digest=_sha256_file(root / ABI_HEADER_PATH),
            source_digest=_sha256_file(root / source_relative),
            host_digest=sha256(host_library),
            payload=payload,
            preview=preview,
        )
        bundle = build_bundle(manifest, payload, preview)
        verified = inspect_bundle(bundle)
        receipt = canonical_json(
            {
                "schema": _RECEIPT_SCHEMA,
                "schema_version": 1,
                "plugin_id": plugin_id,
                "bundle_digest": verified.bundle_digest,
                "payload_digest": verified.payload_digest,
                "source_inputs": source_inputs,
                "performance_scope": "host preview proxy; not ESP32 hardware",
                "performance": {
                    "default": {
                        **default_run.timing.to_dict(),
                        "missed_deadlines": default_run.missed_deadlines,
                        "changed_frames": default_run.changed_frames,
                        "total_frames": len(default_run.frames),
                        "changed_frame_ratio": (
                            default_run.changed_frames / len(default_run.frames)
                        ),
                    },
                    "stress": {
                        **stress_run.timing.to_dict(),
                        "missed_deadlines": stress_run.missed_deadlines,
                        "changed_frames": stress_run.changed_frames,
                        "total_frames": len(stress_run.frames),
                        "changed_frame_ratio": (
                            stress_run.changed_frames / len(stress_run.frames)
                        ),
                    },
                },
            }
        )
        bundle_path, payload_path, host_library_path, preview_path, receipt_path = _write_artifacts(
            output,
            plugin_id,
            bundle=bundle,
            payload=payload,
            host_library=host_library,
            preview=preview,
            receipt=receipt,
        )
    return NativeBuildResult(
        bundle_path=bundle_path,
        bundle_digest=verified.bundle_digest,
        payload_digest=verified.payload_digest,
        receipt_path=receipt_path,
        preview_path=preview_path,
        manifest=verified.manifest,
        host_library_path=host_library_path,
        payload_path=payload_path,
        default_timing=default_run.timing,
        stress_timing=stress_run.timing,
        default_missed_deadlines=default_run.missed_deadlines,
        stress_missed_deadlines=stress_run.missed_deadlines,
        default_changed_frames=default_run.changed_frames,
        stress_changed_frames=stress_run.changed_frames,
        default_total_frames=len(default_run.frames),
        stress_total_frames=len(stress_run.frames),
        executed=True,
    )


__all__ = ["NativeBuildResult", "build_plugin"]
