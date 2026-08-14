#!/usr/bin/env python3
"""Target-side primitives for coordinator-owned LED wall deployments.

This module is intentionally a leaf adapter.  It stages content-addressed app
and support releases, manages the ``current`` app symlink, and reports fresh,
release-aware health.  Workstation source is never synchronized over the live
deployment root: callers upload one immutable snapshot below ``.incoming`` and
pass that snapshot to these commands.

Every command emits exactly one JSON object on stdout.  Human-oriented output
from subprocesses is captured and returned only as a bounded diagnostic tail.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import glob
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.error import URLError
from urllib.request import urlopen

try:
    from tools.deployment.app_releases import AppReleaseManager, ReleaseValidationError
except ModuleNotFoundError:  # Direct execution from an uploaded snapshot.
    from app_releases import AppReleaseManager, ReleaseValidationError  # type: ignore[no-redef]


SNAPSHOT_SCHEMA_VERSION = 1
SUPPORT_METADATA = ".support-release.json"
RELEASE_PATTERN = re.compile(r"[0-9a-f]{64}")
DEFAULT_RECEIPT_DIR = PurePosixPath("run_state/deploy_receipts")
DEFAULT_SYSTEMD_UNIT = "ledgrid.service"
DEFAULT_API_URL = "http://127.0.0.1:5000/api/status"
PLATFORMIO_BUILD_CACHE = ".platformio-build-cache"
CCACHE_DIRECTORY = ".ccache"


def _path(value: os.PathLike[str] | str) -> Path:
    return Path(value).expanduser().resolve()


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        raise ValueError(f"unsafe snapshot path: {value!r}")
    return path


def _is_beneath(path: PurePosixPath, parent: PurePosixPath) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected a JSON object in {path}")
    return payload


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(dict(payload), sort_keys=True, separators=(",", ":")))


def _command(
    args: Sequence[os.PathLike[str] | str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    check: bool = True,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [os.fspath(arg) for arg in args],
        cwd=os.fspath(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if check and completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise RuntimeError(
            f"command exited {completed.returncode}: {detail[-4000:]}"
        )
    return completed


def _manifest_paths(snapshot: Path, manifest_name: str) -> tuple[PurePosixPath, ...]:
    manifest_path = snapshot / ".deploy" / manifest_name
    payload = _json_object(manifest_path)
    if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise RuntimeError(f"unsupported snapshot manifest version in {manifest_path}")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not all(isinstance(item, str) for item in raw_files):
        raise RuntimeError(f"snapshot manifest files are malformed in {manifest_path}")
    paths = tuple(_safe_relative(item) for item in raw_files)
    if len(paths) != len(set(paths)):
        raise RuntimeError(f"snapshot manifest contains duplicate paths: {manifest_path}")
    for relative in paths:
        candidate = snapshot / relative.as_posix()
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError(f"snapshot input is missing or not regular: {relative}")
    return paths


def verify_snapshot(snapshot: Path) -> Mapping[str, Any]:
    """Verify every immutable snapshot byte before target-side staging."""
    metadata_path = snapshot / ".deploy" / "snapshot.json"
    payload = _json_object(metadata_path)
    if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise RuntimeError("unsupported deployment snapshot version")
    if metadata_path.is_symlink() or metadata_path.stat().st_mode & 0o222:
        raise RuntimeError("deployment snapshot metadata must be an immutable regular file")
    snapshot_id = payload.get("snapshot_id")
    if not isinstance(snapshot_id, str) or RELEASE_PATTERN.fullmatch(snapshot_id) is None:
        raise RuntimeError("deployment snapshot has an invalid identity")
    identity_payload = dict(payload)
    identity_payload.pop("snapshot_id", None)
    expected_snapshot_id = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if snapshot_id != expected_snapshot_id:
        raise RuntimeError("deployment snapshot identity does not match its evidence")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise RuntimeError("deployment snapshot file evidence is malformed")

    seen: set[PurePosixPath] = set()
    for item in raw_files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RuntimeError("deployment snapshot file evidence is malformed")
        relative = _safe_relative(item["path"])
        if relative in seen:
            raise RuntimeError(f"duplicate deployment snapshot path: {relative}")
        seen.add(relative)
        path = snapshot / relative.as_posix()
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"deployment snapshot file is missing: {relative}")
        mode = path.stat().st_mode
        if mode & 0o222:
            raise RuntimeError(f"deployment snapshot file is writable: {relative}")
        if path.stat().st_size != item.get("size") or _sha256_file(path) != item.get("sha256"):
            raise RuntimeError(f"deployment snapshot digest mismatch: {relative}")
        executable = bool(mode & 0o111)
        if executable != item.get("executable"):
            raise RuntimeError(f"deployment snapshot mode mismatch: {relative}")

    expected = {
        PurePosixPath(path.relative_to(snapshot).as_posix())
        for path in snapshot.rglob("*")
        if path.is_file() and path != metadata_path
    }
    # The self-describing snapshot metadata is intentionally outside its own
    # digest; all other regular files, including lane manifests, are covered.
    if seen != expected:
        missing = sorted(str(path) for path in expected - seen)
        unexpected = sorted(str(path) for path in seen - expected)
        raise RuntimeError(
            f"deployment snapshot accounting mismatch; missing={missing}, unexpected={unexpected}"
        )
    return {
        "snapshot_id": snapshot_id,
        "file_count": len(seen),
        "source_identity": payload.get("source_identity"),
    }


def _app_manager(root: Path) -> AppReleaseManager:
    return AppReleaseManager(root)


def stage_app(root: Path, snapshot: Path) -> Mapping[str, Any]:
    evidence = verify_snapshot(snapshot)
    files = {
        relative.as_posix(): snapshot / relative.as_posix()
        for relative in _manifest_paths(snapshot, "app-manifest.json")
    }
    info = _app_manager(root).stage(files)
    return {
        **evidence,
        "release_id": info.id,
        "release_digest": info.digest,
        "file_count": len(info.files),
        "reused": info.reused,
    }


def _support_digest(files: Iterable[tuple[PurePosixPath, Path]]) -> str:
    digest = hashlib.sha256(b"ledgrid-support-release-v1\0")
    for relative, source in sorted(files, key=lambda item: item[0].as_posix()):
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(source).encode("ascii"))
        digest.update(b"\0x\0" if source.stat().st_mode & 0o111 else b"\0-\0")
    return digest.hexdigest()


def _make_immutable(root: Path) -> None:
    directories: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"immutable release unexpectedly contains symlink: {path}")
        if path.is_dir():
            directories.append(path)
        elif path.is_file():
            path.chmod(path.stat().st_mode & ~0o222)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        directory.chmod(0o555)
    root.chmod(0o555)


def stage_support(root: Path, snapshot: Path) -> Mapping[str, Any]:
    verify_snapshot(snapshot)
    sources = tuple(
        (relative, snapshot / relative.as_posix())
        for relative in _manifest_paths(snapshot, "support-manifest.json")
    )
    if not sources:
        return {"support_release_id": None, "file_count": 0, "reused": True}
    release_id = _support_digest(sources)
    releases = root / "support_releases"
    destination = releases / release_id
    releases.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        metadata = _json_object(destination / SUPPORT_METADATA)
        if metadata.get("id") != release_id:
            raise RuntimeError(f"support release metadata mismatch: {release_id}")
        for relative, source in sources:
            target = destination / relative.as_posix()
            if not target.is_file() or _sha256_file(target) != _sha256_file(source):
                raise RuntimeError(f"existing support release is corrupt: {relative}")
        return {"support_release_id": release_id, "file_count": len(sources), "reused": True}

    temporary = Path(tempfile.mkdtemp(prefix=f".{release_id}.", dir=releases))
    try:
        metadata_files = []
        for relative, source in sources:
            target = temporary / relative.as_posix()
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o555 if source.stat().st_mode & 0o111 else 0o444)
            metadata_files.append(
                {
                    "path": relative.as_posix(),
                    "sha256": _sha256_file(source),
                    "size": source.stat().st_size,
                    "executable": bool(source.stat().st_mode & 0o111),
                }
            )
        (temporary / SUPPORT_METADATA).write_text(
            json.dumps(
                {"schema_version": 1, "id": release_id, "files": metadata_files},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        _make_immutable(temporary)
        try:
            temporary.rename(destination)
        except FileExistsError:
            pass
    finally:
        if temporary.exists():
            for path in temporary.rglob("*"):
                if path.is_dir():
                    path.chmod(0o755)
            temporary.chmod(0o755)
            shutil.rmtree(temporary)
    return {"support_release_id": release_id, "file_count": len(sources), "reused": False}


def cleanup_snapshot(root: Path, snapshot: Path) -> Mapping[str, Any]:
    """Remove one successfully consumed incoming snapshot and nothing else."""
    incoming = (root / ".incoming").resolve()
    candidate = snapshot.resolve()
    if candidate.parent != incoming or candidate == incoming:
        raise ValueError("snapshot cleanup is restricted to one direct .incoming child")
    if not candidate.exists() or candidate.is_symlink() or not candidate.is_dir():
        raise FileNotFoundError(f"incoming snapshot is unavailable: {candidate}")
    for path in candidate.rglob("*"):
        if not path.is_symlink() and path.is_dir():
            path.chmod(0o755)
    candidate.chmod(0o755)
    shutil.rmtree(candidate)
    return {"removed": True, "snapshot": os.fspath(candidate)}


def _release(root: Path, release_id: str) -> Path:
    if not RELEASE_PATTERN.fullmatch(release_id):
        raise ValueError(f"invalid release ID: {release_id!r}")
    return root / "releases" / release_id


def _support_release(root: Path, release_id: str) -> Path:
    if not RELEASE_PATTERN.fullmatch(release_id):
        raise ValueError(f"invalid support release ID: {release_id!r}")
    path = root / "support_releases" / release_id
    if not path.is_dir() or path.is_symlink():
        raise FileNotFoundError(f"unknown support release: {release_id}")
    return path


def validate_app(root: Path, release_id: str) -> Mapping[str, Any]:
    manager = _app_manager(root)
    info = manager.validate(release_id)
    runtime_python = root / "venv" / "bin" / "python"
    if not runtime_python.is_file():
        raise RuntimeError("selected target runtime is missing; provision dependencies first")
    smoke = _command(
        (
            runtime_python,
            info.path / "tools" / "deployment" / "runtime_env.py",
            "smoke",
            "--root",
            info.path,
        ),
        cwd=info.path,
    )
    return {
        "release_id": info.id,
        "digest": info.digest,
        "file_count": len(info.files),
        "smoke_output": (smoke.stdout + smoke.stderr)[-1000:],
    }


def ensure_runtime(root: Path, release_id: str) -> Mapping[str, Any]:
    release = _release(root, release_id)
    result = _command(
        (
            "python3",
            release / "tools" / "deployment" / "runtime_env.py",
            "ensure",
            "--root",
            root,
            "--lock",
            release / "requirements-pi.lock",
            "--link",
            "venv",
            "--smoke-root",
            release,
        ),
        cwd=release,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"runtime environment did not return JSON: {result.stdout[-1000:]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("runtime environment result is not an object")
    return payload


def _unit_text(root: Path, user: str) -> str:
    current = root / "current"
    return "\n".join(
        (
            "[Unit]",
            "Description=LED Grid Animation System",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"User={user}",
            f"WorkingDirectory={current}",
            f"ExecStart=/bin/bash {current / 'scripts' / 'start_systemd.sh'}",
            "Restart=always",
            "RestartSec=2",
            "Environment=PYTHONUNBUFFERED=1",
            "Environment=LEDGRID_SPI1_MODE=0",
            "Environment=LEDGRID_HAT=0",
            "Environment=STRIPS=32",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        )
    )


def ensure_unit(root: Path, *, user: str, unit: str = DEFAULT_SYSTEMD_UNIT) -> Mapping[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", unit):
        raise ValueError(f"unsafe systemd unit name: {unit!r}")
    desired = _unit_text(root, user)
    destination = Path("/etc/systemd/system") / unit
    current = ""
    try:
        current = destination.read_text(encoding="utf-8")
    except OSError:
        pass
    changed = current != desired
    if changed:
        fd, temporary_name = tempfile.mkstemp(prefix="ledgrid-unit-", suffix=".service")
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(desired)
                stream.flush()
                os.fsync(stream.fileno())
            _command(("sudo", "install", "-m", "0644", temporary, destination))
            _command(("sudo", "systemctl", "daemon-reload"))
        finally:
            temporary.unlink(missing_ok=True)
    enabled = _command(("systemctl", "is-enabled", "--quiet", unit), check=False)
    enabled_changed = enabled.returncode != 0
    if enabled_changed:
        _command(("sudo", "systemctl", "enable", unit))
    return {
        "unit": unit,
        "changed": changed,
        "enabled_changed": enabled_changed,
        "working_directory": os.fspath(root / "current"),
    }


def configure_spi(release: Path, *, hat: bool) -> Mapping[str, Any]:
    env = dict(os.environ)
    env["LEDGRID_HAT"] = "1" if hat else "0"
    completed = _command(
        ("bash", release / "tools" / "deployment" / "configure_spi.sh"),
        env=env,
    )
    output = completed.stdout + completed.stderr
    status_match = re.search(r"^STATUS=(\S+)$", output, re.MULTILINE)
    changed_match = re.search(r"^CONFIG_CHANGED=(\S+)$", output, re.MULTILINE)
    if status_match is None or changed_match is None:
        raise RuntimeError(f"SPI configuration returned no structured status: {output[-2000:]}")
    return {
        "status": status_match.group(1),
        "config_changed": changed_match.group(1) == "1",
        "output_tail": output[-2000:],
    }


def provision(root: Path, release_id: str, *, user: str, hat: bool) -> Mapping[str, Any]:
    release = _release(root, release_id)
    runtime = ensure_runtime(root, release_id)
    spi = configure_spi(release, hat=hat)
    # Do not replace the boot-time unit while an SPI configuration reboot is
    # outstanding. On first coordinator cutover there may be no ``current``
    # symlink yet, so retaining the legacy unit keeps the existing service
    # bootable through the one allowed reboot. The idempotent resume installs
    # the current-aware unit after SPI reports ready.
    if spi.get("status") == "ready":
        unit = ensure_unit(root, user=user)
    else:
        unit = {
            "unit": DEFAULT_SYSTEMD_UNIT,
            "changed": False,
            "enabled_changed": False,
            "deferred": True,
            "reason": "SPI configuration requires reboot",
        }
    changed = bool(
        runtime.get("installed")
        or unit.get("changed")
        or unit.get("enabled_changed")
        or spi.get("config_changed")
        or spi.get("status") != "ready"
    )
    return {
        "outcome": "executed" if changed else "skipped",
        "runtime": runtime,
        "unit": unit,
        "spi": spi,
    }


def _copy_support_workspace(root: Path, support_id: str) -> tuple[Path, bool]:
    source = _support_release(root, support_id)
    metadata = _json_object(source / SUPPORT_METADATA)
    firmware_entries = [
        item for item in metadata.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
        and (
            _is_beneath(_safe_relative(item["path"]), PurePosixPath("firmware"))
            or item["path"] == "requirements-platformio.lock"
        )
    ]
    digest = hashlib.sha256(
        json.dumps(firmware_entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    workspace = root / "build" / "firmware" / digest
    marker = workspace / ".source-digest"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == digest:
        _make_build_workspace_writable(workspace)
        return workspace, True
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{digest}.", dir=workspace.parent))
    try:
        firmware_source = source / "firmware"
        if not firmware_source.is_dir():
            raise RuntimeError("support release has no firmware source")
        shutil.copytree(firmware_source, temporary / "firmware", copy_function=shutil.copy2)
        for optional in ("requirements-platformio.lock",):
            candidate = source / optional
            if candidate.is_file():
                shutil.copy2(candidate, temporary / optional)
        (temporary / ".source-digest").write_text(digest + "\n", encoding="utf-8")
        _make_build_workspace_writable(temporary)
        temporary.rename(workspace)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return workspace, False


def _make_build_workspace_writable(workspace: Path) -> None:
    """Normalize copied immutable inputs for target-owned build outputs."""
    for path in workspace.rglob("*"):
        if path.is_symlink():
            expected_marker = workspace.parents[2] / ".esp32_firmware_hash"
            if (
                path == workspace / ".esp32_firmware_hash"
                and path.resolve(strict=False) == expected_marker.resolve(strict=False)
            ):
                continue
            raise RuntimeError(f"firmware build workspace contains an unsafe symlink: {path}")
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
    workspace.chmod(0o755)


def build_firmware(root: Path, support_id: Optional[str]) -> Mapping[str, Any]:
    if support_id is None:
        return {"outcome": "skipped", "reason": "no support inputs"}
    workspace, reused = _copy_support_workspace(root, support_id)
    firmware = workspace / "firmware" / "esp32"
    binary = firmware / ".pio" / "build" / "esp32-s3-devkitc-1" / "firmware.bin"
    if reused and binary.is_file():
        return {
            "outcome": "skipped",
            "reason": "firmware build already exists",
            "workspace": os.fspath(workspace),
            "firmware_sha256": _sha256_file(binary),
        }
    pio = shutil.which("pio") or os.fspath(Path.home() / ".platformio-venv" / "bin" / "pio")
    if not Path(pio).is_file() and shutil.which(pio) is None:
        raise RuntimeError("PlatformIO is unavailable on the target; run setup first")
    if shutil.which("ccache") is None:
        raise RuntimeError("ccache is unavailable on the target; run setup first")
    build_cache = root / "build" / "firmware" / PLATFORMIO_BUILD_CACHE
    build_cache.mkdir(parents=True, exist_ok=True)
    ccache_dir = root / "build" / "firmware" / CCACHE_DIRECTORY
    ccache_dir.mkdir(parents=True, exist_ok=True)
    build_env = dict(os.environ)
    build_env["PLATFORMIO_BUILD_CACHE_DIR"] = os.fspath(build_cache)
    build_env["IDF_CCACHE_ENABLE"] = "1"
    build_env["CCACHE_DIR"] = os.fspath(ccache_dir)
    version = _command((pio, "--version"))
    if re.search(r"\bversion 6\.1\.19$", version.stdout.strip()) is None:
        raise RuntimeError(
            "PlatformIO 6.1.19 is required on the target; found: "
            f"{version.stdout.strip() or version.stderr.strip() or 'unknown'}"
        )
    completed = _command(
        (pio, "run", "-e", "esp32-s3-devkitc-1"),
        cwd=firmware,
        env=build_env,
    )
    if not binary.is_file():
        raise RuntimeError("firmware build produced no firmware.bin")
    return {
        "outcome": "executed",
        "workspace": os.fspath(workspace),
        "build_cache": os.fspath(build_cache),
        "ccache": os.fspath(ccache_dir),
        "firmware_sha256": _sha256_file(binary),
        "output_tail": (completed.stdout + completed.stderr)[-2000:],
    }


def flash_firmware(
    root: Path,
    support_id: Optional[str],
    *,
    app_release_id: Optional[str] = None,
    receiver_count: int,
    debug: bool,
) -> Mapping[str, Any]:
    if support_id is None:
        return {"outcome": "skipped", "reason": "no support inputs"}
    workspace, _ = _copy_support_workspace(root, support_id)
    ports = sorted(set(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")))
    if len(ports) != receiver_count:
        raise RuntimeError(
            f"expected exactly {receiver_count} ESP32 serial devices; found {len(ports)}: {ports}"
        )
    # Preserve the legacy marker so the first coordinator cutover does not
    # mistake an unchanged installed image for a required all-board flash.
    shared_marker = root / ".esp32_firmware_hash"
    shared_marker.touch(exist_ok=True)
    workspace_marker = workspace / ".esp32_firmware_hash"
    workspace_marker.unlink(missing_ok=True)
    workspace_marker.symlink_to(os.path.relpath(shared_marker, start=workspace))
    env = dict(os.environ)
    env.update(
        {
            "DEPLOY_DIR": os.fspath(workspace),
            "DEBUG": "1" if debug else "0",
            "IDF_CCACHE_ENABLE": "1",
            "CCACHE_DIR": os.fspath(
                root / "build" / "firmware" / CCACHE_DIRECTORY
            ),
        }
    )
    # The flash helper uploads to all receivers concurrently. PlatformIO's
    # build-cache directory contains a shared SCons signature database, so
    # giving every uploader the same directory races its atomic replacement.
    # The firmware is already built at this point; uploads need only the
    # workspace-local .pio output and the concurrency-safe compiler cache.
    env.pop("PLATFORMIO_BUILD_CACHE_DIR", None)
    # Deployment helpers are app-lane source, not support-lane source. Use the
    # explicitly selected candidate helper while directing all build/hash state
    # at the isolated firmware workspace. Falling back to ``current`` retains
    # direct-call compatibility, but an authoritative coordinator always passes
    # the candidate identity and never guesses from hash-sorted releases.
    if app_release_id is not None:
        helper = _release(root, app_release_id) / "tools" / "deployment" / "flash_esp32.sh"
    else:
        helper = root / "current" / "tools" / "deployment" / "flash_esp32.sh"
    if not helper.is_file():
        raise RuntimeError("selected app release has no flash helper")
    completed = _command(("bash", helper), env=env, check=False)
    output = completed.stdout + completed.stderr
    if completed.returncode or "Flash FAILED" in output or "hash NOT updated" in output:
        _command(("sudo", "systemctl", "stop", DEFAULT_SYSTEMD_UNIT), check=False)
        raise RuntimeError(f"receiver firmware flash failed: {output[-4000:]}")
    skipped = "Firmware unchanged; skipping" in output
    return {
        "outcome": "skipped" if skipped else "executed",
        "ports": ports,
        "output_tail": output[-2000:],
    }


def current_release(root: Path) -> Optional[str]:
    return _app_manager(root).current_release_id()


def prune_releases(root: Path, *, retain: int) -> Mapping[str, Any]:
    manager = _app_manager(root)
    removed = manager.prune(retain=retain)
    retained = sorted(
        path.name
        for path in manager.releases_dir.iterdir()
        if (
            not path.is_symlink()
            and path.is_dir()
            and RELEASE_PATTERN.fullmatch(path.name)
        )
    ) if manager.releases_dir.exists() else []
    return {
        "outcome": "executed" if removed else "skipped",
        "retain": retain,
        "current_release": manager.current_release_id(),
        "removed_releases": list(removed),
        "retained_releases": retained,
    }


def activate(root: Path, release_id: str) -> Mapping[str, Any]:
    manager = _app_manager(root)
    current = manager.current_release_id()
    if current == release_id:
        manager.validate(release_id)
        return {
            "release_id": release_id,
            "previous_release": current,
            "changed": False,
            "selected_at": time.time(),
        }
    previous = manager.activate(release_id)
    return {
        "release_id": release_id,
        "previous_release": previous,
        "changed": True,
        "selected_at": time.time(),
    }


def _active_helper_root(root: Path) -> Optional[Path]:
    if (root / "current" / "tools" / "deployment" / "preserve_deploy_settings.py").is_file():
        return root / "current"
    if (root / "tools" / "deployment" / "preserve_deploy_settings.py").is_file():
        return root
    return None


def capture_state(root: Path) -> Mapping[str, Any]:
    helper_root = _active_helper_root(root)
    runtime = root / "venv" / "bin" / "python"
    if helper_root is None or not runtime.is_file():
        return {"captured": False, "reason": "no active compatible app/runtime"}
    completed = _command(
        (runtime, helper_root / "tools" / "deployment" / "preserve_deploy_settings.py", "save"),
        cwd=helper_root,
        check=False,
    )
    if completed.returncode:
        # An unhealthy/idle legacy service is a valid first cutover condition;
        # no state is preferable to fabricating a restorable snapshot.
        return {
            "captured": False,
            "reason": (completed.stderr.strip() or completed.stdout.strip() or "capture unavailable")[-1000:],
        }
    return {"captured": True, "output_tail": completed.stdout[-1000:]}


def restore_state(root: Path, *, timeout: float) -> Mapping[str, Any]:
    helper_root = _active_helper_root(root)
    runtime = root / "venv" / "bin" / "python"
    state = root / "run_state" / "before_deploy.json"
    if helper_root is None or not runtime.is_file() or not state.is_file():
        return {"restored": False, "reason": "no captured state"}
    completed = _command(
        (
            runtime,
            helper_root / "tools" / "deployment" / "preserve_deploy_settings.py",
            "restore",
            "--wait",
            str(timeout),
        ),
        cwd=helper_root,
    )
    return {"restored": True, "output_tail": completed.stdout[-1000:]}


def restart_service(unit: str = DEFAULT_SYSTEMD_UNIT) -> Mapping[str, Any]:
    boundary = time.time()
    _command(("sudo", "systemctl", "restart", unit))
    return {"restart_started_at": boundary, "unit": unit}


def reboot_host() -> Mapping[str, Any]:
    requested_at = time.time()
    _command(("sudo", "reboot"), check=False)
    return {"reboot_requested_at": requested_at}


def _service_main_pid(unit: str) -> int:
    result = _command(("systemctl", "show", unit, "--property", "MainPID", "--value"))
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"systemd returned invalid MainPID: {result.stdout!r}") from exc


def _service_release(root: Path, unit: str) -> Optional[str]:
    pid = _service_main_pid(unit)
    if pid <= 0:
        return None
    try:
        cwd = Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
        relative = cwd.relative_to((root / "releases").resolve())
    except (OSError, ValueError):
        return None
    return relative.parts[0] if len(relative.parts) == 1 else None


def _api_status(api_url: str, timeout: float = 2.0) -> Mapping[str, Any]:
    try:
        with urlopen(api_url, timeout=timeout) as response:  # nosec: fixed/local operator URL
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read controller API status: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("controller API status is not an object")
    return payload


@dataclass(frozen=True)
class TargetHealthSample:
    sampled_at: float
    controller_updated_at: float
    release_id: Optional[str]
    strip_count: int
    leds_per_strip: int
    receiver_count: int
    receiver_logical_ids: tuple[int, ...]
    ready: bool = True


def _sample_health(root: Path, *, unit: str, api_url: str) -> TargetHealthSample:
    active = _command(("systemctl", "is-active", "--quiet", unit), check=False)
    if active.returncode:
        raise RuntimeError(f"systemd unit is not active: {unit}")
    sampled_at = time.time()
    status = _api_status(api_url)
    led_info = status.get("led_info") if isinstance(status.get("led_info"), dict) else {}
    driver = status.get("driver_stats") if isinstance(status.get("driver_stats"), dict) else {}
    aggregate = driver.get("aggregate") if isinstance(driver.get("aggregate"), dict) else {}
    service_release = _service_release(root, unit)
    api_release = status.get("release_id")
    if status.get("release_consistent") is not True or api_release != service_release:
        raise RuntimeError(
            "controller/web release identity is missing or inconsistent with systemd"
        )
    device_map = aggregate.get("device_map")
    if not isinstance(device_map, list):
        raise RuntimeError("controller status lacks the receiver device map")
    logical_ids: list[int] = []
    for entry in device_map:
        logical_id = entry.get("logical_device") if isinstance(entry, dict) else None
        if isinstance(logical_id, bool) or not isinstance(logical_id, int):
            raise RuntimeError("controller receiver device map is malformed")
        logical_ids.append(logical_id)
    updated_at = status.get("updated_at") or status.get("timestamp")
    values = (
        updated_at,
        led_info.get("strip_count"),
        led_info.get("leds_per_strip"),
        aggregate.get("num_devices"),
    )
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise RuntimeError("controller status lacks timestamp, geometry, or receiver topology")
    return TargetHealthSample(
        sampled_at=sampled_at,
        controller_updated_at=float(updated_at),
        release_id=service_release,
        strip_count=int(led_info["strip_count"]),
        leds_per_strip=int(led_info["leds_per_strip"]),
        receiver_count=int(aggregate["num_devices"]),
        receiver_logical_ids=tuple(logical_ids),
        ready=status.get("is_running") is True,
    )


def fresh_health(
    root: Path,
    release_id: str,
    *,
    restart_started_at: float,
    strips: int,
    leds_per_strip: int,
    receivers: int,
    stable_samples: int,
    timeout: float,
    unit: str,
    api_url: str,
) -> Mapping[str, Any]:
    deadline = time.monotonic() + timeout
    accepted: list[TargetHealthSample] = []
    last_reason = "no health sample"
    while time.monotonic() <= deadline:
        try:
            sample = _sample_health(root, unit=unit, api_url=api_url)
            rejection: Optional[str] = None
            if sample.release_id != release_id:
                rejection = f"service release is {sample.release_id!r}, expected {release_id!r}"
            elif not sample.ready:
                rejection = "controller did not report ready"
            elif sample.controller_updated_at <= restart_started_at:
                rejection = "controller status predates restart/acceptance boundary"
            elif sample.controller_updated_at > sample.sampled_at + 1.0:
                rejection = "controller status timestamp is implausibly in the future"
            elif sample.sampled_at - sample.controller_updated_at > 3.0:
                rejection = "controller status is stale"
            elif (sample.strip_count, sample.leds_per_strip) != (strips, leds_per_strip):
                rejection = "controller geometry does not match desired geometry"
            elif sample.receiver_count != receivers:
                rejection = "receiver topology does not match desired topology"
            elif sample.receiver_logical_ids != tuple(range(receivers)):
                rejection = "receiver logical device map does not match desired topology"
            elif accepted and sample.controller_updated_at <= accepted[-1].controller_updated_at:
                rejection = "controller status did not advance between stable samples"

            if rejection is not None:
                accepted.clear()
                last_reason = rejection
            else:
                accepted.append(sample)
                if len(accepted) >= stable_samples:
                    return {
                        "desired_release": release_id,
                        "observed_release": sample.release_id,
                        "stable_samples": len(accepted),
                        "last_controller_updated_at": sample.controller_updated_at,
                        "geometry": {"strip_count": strips, "leds_per_strip": leds_per_strip},
                        "receiver_count": receivers,
                    }
        except Exception as exc:
            accepted.clear()
            last_reason = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"fresh release-aware readiness timed out: {last_reason}")


def record_deploy(root: Path) -> Mapping[str, Any]:
    helper_root = _active_helper_root(root)
    runtime = root / "venv" / "bin" / "python"
    if helper_root is None or not runtime.is_file():
        raise RuntimeError("cannot record deployment without an active app/runtime")
    completed = _command(
        (runtime, helper_root / "tools" / "deployment" / "preserve_deploy_settings.py", "record-deploy"),
        cwd=helper_root,
    )
    return {"recorded": True, "output_tail": completed.stdout[-1000:]}


def inspect_target(root: Path) -> Mapping[str, Any]:
    manager = _app_manager(root)
    current = manager.current_release_id()
    releases = [info.id for info in manager.list()] if manager.releases_dir.exists() else []
    support = sorted(
        path.name for path in (root / "support_releases").glob("*")
        if path.is_dir() and not path.is_symlink() and RELEASE_PATTERN.fullmatch(path.name)
    )
    return {
        "root": os.fspath(root),
        "current_release": current,
        "releases": releases,
        "support_releases": support,
        "receipt_directory": os.fspath(root / DEFAULT_RECEIPT_DIR.as_posix()),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=_path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify-snapshot")
    verify.add_argument("--snapshot", type=_path, required=True)
    stage_app_parser = subparsers.add_parser("stage-app")
    stage_app_parser.add_argument("--snapshot", type=_path, required=True)
    stage_support_parser = subparsers.add_parser("stage-support")
    stage_support_parser.add_argument("--snapshot", type=_path, required=True)
    cleanup = subparsers.add_parser("cleanup-snapshot")
    cleanup.add_argument("--snapshot", type=_path, required=True)
    validate = subparsers.add_parser("validate-app")
    validate.add_argument("release_id")
    provision_parser = subparsers.add_parser("provision")
    provision_parser.add_argument("release_id")
    provision_parser.add_argument("--user", required=True)
    provision_parser.add_argument("--hat", action="store_true")
    build = subparsers.add_parser("build-firmware")
    build.add_argument("support_id", nargs="?")
    flash = subparsers.add_parser("flash-firmware")
    flash.add_argument("support_id", nargs="?")
    flash.add_argument("--app-release")
    flash.add_argument("--receivers", type=int, default=4)
    flash.add_argument("--debug", action="store_true")
    subparsers.add_parser("capture-state")
    activate_parser = subparsers.add_parser("activate")
    activate_parser.add_argument("release_id")
    subparsers.add_parser("restart")
    restore = subparsers.add_parser("restore-state")
    restore.add_argument("--timeout", type=float, default=20.0)
    health = subparsers.add_parser("health")
    health.add_argument("release_id")
    health.add_argument("--boundary", type=float, required=True)
    health.add_argument("--strips", type=int, default=32)
    health.add_argument("--leds-per-strip", type=int, default=138)
    health.add_argument("--receivers", type=int, default=4)
    health.add_argument("--stable-samples", type=int, default=2)
    health.add_argument("--timeout", type=float, default=30.0)
    health.add_argument("--unit", default=DEFAULT_SYSTEMD_UNIT)
    health.add_argument("--api-url", default=DEFAULT_API_URL)
    subparsers.add_parser("record-deploy")
    subparsers.add_parser("reboot")
    subparsers.add_parser("current-release")
    prune = subparsers.add_parser("prune-releases")
    prune.add_argument("--retain", type=int, required=True)
    subparsers.add_parser("inspect")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    root: Path = args.root
    root.mkdir(parents=True, exist_ok=True)
    if args.command == "verify-snapshot":
        result = verify_snapshot(args.snapshot)
    elif args.command == "stage-app":
        result = stage_app(root, args.snapshot)
    elif args.command == "stage-support":
        result = stage_support(root, args.snapshot)
    elif args.command == "cleanup-snapshot":
        result = cleanup_snapshot(root, args.snapshot)
    elif args.command == "validate-app":
        result = validate_app(root, args.release_id)
    elif args.command == "provision":
        result = provision(root, args.release_id, user=args.user, hat=args.hat)
    elif args.command == "build-firmware":
        result = build_firmware(root, args.support_id)
    elif args.command == "flash-firmware":
        result = flash_firmware(
            root,
            args.support_id,
            app_release_id=args.app_release,
            receiver_count=args.receivers,
            debug=args.debug,
        )
    elif args.command == "capture-state":
        result = capture_state(root)
    elif args.command == "activate":
        result = activate(root, args.release_id)
    elif args.command == "restart":
        result = restart_service()
    elif args.command == "restore-state":
        result = restore_state(root, timeout=args.timeout)
    elif args.command == "health":
        result = fresh_health(
            root,
            args.release_id,
            restart_started_at=args.boundary,
            strips=args.strips,
            leds_per_strip=args.leds_per_strip,
            receivers=args.receivers,
            stable_samples=args.stable_samples,
            timeout=args.timeout,
            unit=args.unit,
            api_url=args.api_url,
        )
    elif args.command == "record-deploy":
        result = record_deploy(root)
    elif args.command == "reboot":
        result = reboot_host()
    elif args.command == "current-release":
        result = {"current_release": current_release(root)}
    elif args.command == "prune-releases":
        result = prune_releases(root, retain=args.retain)
    elif args.command == "inspect":
        result = inspect_target(root)
    else:  # pragma: no cover - argparse guarantees this
        return 2
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
