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
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import math
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
from urllib.request import Request, urlopen

try:
    from tools.deployment.app_releases import AppReleaseManager
    from tools.deployment.firmware_artifacts import inspect_firmware_installation
    from tools.deployment.receiver_hybrid_config import (
        FINALIZED_RECEIVER_COUNT,
        migrate_legacy_receiver_hybrid_config,
        resolve_receiver_hybrid_config,
    )
except ModuleNotFoundError:  # Direct execution from an uploaded snapshot.
    from app_releases import AppReleaseManager  # type: ignore[no-redef]
    from firmware_artifacts import inspect_firmware_installation  # type: ignore[no-redef]
    from receiver_hybrid_config import (  # type: ignore[no-redef]
        FINALIZED_RECEIVER_COUNT,
        migrate_legacy_receiver_hybrid_config,
        resolve_receiver_hybrid_config,
    )


SNAPSHOT_SCHEMA_VERSION = 1
SUPPORT_METADATA = ".support-release.json"
RELEASE_PATTERN = re.compile(r"[0-9a-f]{64}")
DEFAULT_RECEIPT_DIR = PurePosixPath("run_state/deploy_receipts")
DEFAULT_SYSTEMD_UNIT = "ledgrid.service"
SYSTEMD_UNIT_ROOT = Path("/etc/systemd/system")
DEFAULT_API_URL = "http://127.0.0.1:5000/api/status"
STRICT_RECEIVER_HEALTH_POLL_SECONDS = 0.75
PLATFORMIO_BUILD_CACHE = ".platformio-build-cache"
CCACHE_DIRECTORY = ".ccache"
PINNED_OPENOCD_VERSION = "v0.12.0-esp32-20260424"
PINNED_OPENOCD_ARCHIVE = (
    "openocd-esp32-linux-arm64-0.12.0-esp32-20260424.tar.gz"
)
PINNED_OPENOCD_URL = (
    "https://github.com/espressif/openocd-esp32/releases/download/"
    f"{PINNED_OPENOCD_VERSION}/{PINNED_OPENOCD_ARCHIVE}"
)
PINNED_OPENOCD_SHA256 = (
    "f1b87d408adf6f2eb08a2b067ff7de38310829cc952c0f5d1d09920b0200a6e4"
)
PINNED_OPENOCD_MAX_BYTES = 16 * 1024 * 1024
OPENOCD_BOARD_CONFIG = "board/esp32s3-builtin.cfg"
PINNED_OPENOCD_INSTALL_ROOT = Path("/opt/ledgrid-openocd")
ROOT_OWNED_FIRMWARE_INSTALL_ROOT = Path("/opt/ledgrid-receiver-firmware")
RECEIVER_USB_STABILIZATION_SECONDS = 20.0
RECEIVER_USB_STABILIZATION_POLL_SECONDS = 0.5
RECEIVER_FIRMWARE_COMMIT = PurePosixPath(
    "run_state/receiver_firmware_commit.json"
)
MAX_RECEIVER_FIRMWARE_COMMIT_BYTES = 64 * 1024
MAX_RECEIVER_FIRMWARE_ARTIFACT_BYTES = 32 * 1024 * 1024

# First-cutover bootstrap is deliberately narrower than a full deployment
# manifest.  These are the source roots needed by the legacy service; target
# state, firmware/support input, documentation, repository metadata and build
# products never enter the immutable rollback snapshot.
LEGACY_BOOTSTRAP_SOURCE_ROOTS = (
    PurePosixPath("animation"),
    PurePosixPath("config"),
    PurePosixPath("drivers"),
    PurePosixPath("ipc"),
    PurePosixPath("scripts"),
    PurePosixPath("tools"),
    PurePosixPath("web"),
)
LEGACY_BOOTSTRAP_ROOT_FILES = (
    PurePosixPath("pyproject.toml"),
    PurePosixPath("requirements-pi.lock"),
    PurePosixPath("requirements.txt"),
    PurePosixPath("uv.lock"),
)
LEGACY_BOOTSTRAP_REQUIRED_FILES = (
    PurePosixPath("scripts/start_systemd.sh"),
    PurePosixPath("scripts/start_server.py"),
    PurePosixPath("tools/deployment/preserve_deploy_settings.py"),
)
LEGACY_BOOTSTRAP_EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".mypy_cache", ".pio", ".pytest_cache", ".ruff_cache",
    ".tox", ".venv", ".venvs", "__pycache__", "build", "dist",
    "node_modules", "out", "releases", "support_releases", "venv",
})
LEGACY_BOOTSTRAP_SECRET_NAMES = frozenset({
    ".env", ".netrc", "credentials", "credentials.json", "id_ed25519",
    "id_rsa", "secrets", "secrets.json",
})
LEGACY_BOOTSTRAP_SECRET_SUFFIXES = frozenset({
    ".key", ".p12", ".pfx", ".pem",
})
LEGACY_BOOTSTRAP_RECORD = PurePosixPath("run_state/legacy_app_bootstrap.json")


def _path(value: os.PathLike[str] | str) -> Path:
    return Path(value).expanduser().resolve()


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        raise ValueError(f"unsafe snapshot path: {value!r}")
    return path


def _legacy_bootstrap_sensitive(path: PurePosixPath) -> bool:
    lowered = tuple(part.lower() for part in path.parts)
    return bool(
        any(
            part in LEGACY_BOOTSTRAP_SECRET_NAMES
            or part.startswith(".env.")
            for part in lowered
        )
        or path.suffix.lower() in LEGACY_BOOTSTRAP_SECRET_SUFFIXES
    )


def _legacy_bootstrap_sources(root: Path) -> Mapping[PurePosixPath, Path]:
    """Return the bounded, regular-file source set for first cutover."""

    sources: dict[PurePosixPath, Path] = {}
    for relative in LEGACY_BOOTSTRAP_ROOT_FILES:
        candidate = root / relative.as_posix()
        if not candidate.exists():
            continue
        metadata = candidate.lstat()
        if candidate.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(
                f"legacy bootstrap input must be a regular non-symlink file: {relative}"
            )
        sources[relative] = candidate

    for source_root in LEGACY_BOOTSTRAP_SOURCE_ROOTS:
        directory = root / source_root.as_posix()
        if not directory.exists():
            continue
        metadata = directory.lstat()
        if directory.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError(
                f"legacy bootstrap source root must be a real directory: {source_root}"
            )
        for current, raw_directories, raw_files in os.walk(directory, followlinks=False):
            current_path = Path(current)
            retained_directories: list[str] = []
            for name in sorted(raw_directories):
                child = current_path / name
                if name in LEGACY_BOOTSTRAP_EXCLUDED_DIRECTORIES:
                    continue
                if child.is_symlink():
                    raise RuntimeError(
                        "legacy bootstrap source contains a directory symlink: "
                        f"{child.relative_to(root)}"
                    )
                if not stat.S_ISDIR(child.lstat().st_mode):
                    raise RuntimeError(
                        "legacy bootstrap source contains a non-directory entry: "
                        f"{child.relative_to(root)}"
                    )
                retained_directories.append(name)
            raw_directories[:] = retained_directories

            for name in sorted(raw_files):
                candidate = current_path / name
                relative = PurePosixPath(candidate.relative_to(root).as_posix())
                if _legacy_bootstrap_sensitive(relative):
                    continue
                metadata = candidate.lstat()
                if candidate.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                    raise RuntimeError(
                        "legacy bootstrap input must be a regular non-symlink file: "
                        f"{relative}"
                    )
                sources[relative] = candidate

    missing = tuple(
        relative for relative in LEGACY_BOOTSTRAP_REQUIRED_FILES
        if relative not in sources
    )
    if missing:
        raise RuntimeError(
            "legacy mutable app lacks required boot inputs: "
            + ", ".join(path.as_posix() for path in missing)
        )
    return dict(sorted(sources.items(), key=lambda item: item[0].as_posix()))


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(dict(payload), stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _read_legacy_bootstrap_record(root: Path) -> Optional[dict[str, Any]]:
    path = root / LEGACY_BOOTSTRAP_RECORD.as_posix()
    if not path.exists():
        if path.is_symlink():
            raise RuntimeError(
                "legacy bootstrap record must be a regular non-symlink file"
            )
        return None
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        raise RuntimeError("legacy bootstrap record must be a regular non-symlink file")
    payload = _json_object(path)
    if payload.get("schema_version") != 1:
        raise RuntimeError("unsupported legacy bootstrap record version")
    release_id = payload.get("bootstrap_release_id")
    phase = payload.get("phase")
    if (
        not isinstance(release_id, str)
        or RELEASE_PATTERN.fullmatch(release_id) is None
        or phase not in {"prepared", "selected", "candidate_pending", "complete"}
    ):
        raise RuntimeError("legacy bootstrap record is malformed")
    candidate = payload.get("candidate_release_id")
    if candidate is not None and (
        not isinstance(candidate, str)
        or RELEASE_PATTERN.fullmatch(candidate) is None
    ):
        raise RuntimeError("legacy bootstrap candidate identity is malformed")
    if phase == "candidate_pending" and candidate is None:
        raise RuntimeError("pending legacy bootstrap record has no candidate")
    return payload


def _write_legacy_bootstrap_record(root: Path, payload: Mapping[str, Any]) -> None:
    _atomic_json(root / LEGACY_BOOTSTRAP_RECORD.as_posix(), payload)


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


def _read_pinned_regular_source(
    path: Path,
    *,
    expected_sha256: str,
    maximum_bytes: int,
    expected_size: Optional[int] = None,
) -> bytes:
    """Pin and verify unprivileged bytes without a privileged path re-open."""

    if (
        RELEASE_PATTERN.fullmatch(expected_sha256) is None
        or maximum_bytes <= 0
        or expected_size is not None
        and (expected_size < 0 or expected_size > maximum_bytes)
    ):
        raise RuntimeError("pinned source constraints are malformed")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"cannot safely open pinned source: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > maximum_bytes
            or expected_size is not None
            and before.st_size != expected_size
        ):
            raise RuntimeError(f"pinned source is not a bounded regular file: {path}")

        def read_once() -> bytes:
            os.lseek(descriptor, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            received = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - received))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > maximum_bytes:
                    raise RuntimeError(f"pinned source exceeds its size limit: {path}")
            return b"".join(chunks)

        payload = read_once()
        if (
            len(payload) != before.st_size
            or hashlib.sha256(payload).hexdigest() != expected_sha256
        ):
            raise RuntimeError(f"pinned source digest or size is invalid: {path}")
        repeated = read_once()
        after = os.fstat(descriptor)
        if (
            repeated != payload
            or hashlib.sha256(repeated).hexdigest() != expected_sha256
            or (after.st_dev, after.st_ino, after.st_mode, after.st_size)
            != (before.st_dev, before.st_ino, before.st_mode, before.st_size)
        ):
            raise RuntimeError(f"pinned source changed while being copied: {path}")
        return payload
    finally:
        os.close(descriptor)


def _validate_root_owned_regular_file(
    path: Path,
    *,
    expected_sha256: str,
    mode: str,
) -> None:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != int(mode, 8)
        or _sha256_file(path) != expected_sha256
    ):
        raise RuntimeError(f"root-owned pinned-byte destination is unsafe: {path}")


def _write_root_owned_bytes(
    destination: Path,
    payload: bytes,
    *,
    expected_sha256: str,
    mode: str = "0444",
) -> None:
    """Pipe pinned bytes to a root writer that never opens the source path."""

    if mode not in {"0444", "0644"}:
        raise RuntimeError("root-owned destination mode is not allowlisted")
    completed = _command(
        (
            "sudo",
            "dd",
            f"of={destination}",
            "bs=1048576",
            "conv=fsync",
            "status=none",
        ),
        input_data=payload,
        timeout=30.0,
    )
    if completed.returncode != 0:
        raise RuntimeError("root-owned pinned-byte writer failed")
    _command(("sudo", "chown", "root:root", destination))
    _command(("sudo", "chmod", mode, destination))
    copied_digest = _command(
        ("sudo", "sha256sum", destination), timeout=15.0
    ).stdout.split()[0]
    if copied_digest != expected_sha256:
        raise RuntimeError("root-owned pinned-byte destination digest is invalid")
    _validate_root_owned_regular_file(
        destination, expected_sha256=expected_sha256, mode=mode
    )


def _validate_shared_firmware_marker(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError("shared firmware marker disappeared") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(
            "shared firmware marker must be a non-symlink regular file"
        )
    if metadata.st_uid != os.geteuid():
        raise RuntimeError("shared firmware marker is not target-owned")


def _prepare_shared_firmware_marker(root: Path, workspace: Path) -> Path:
    """Create/validate the target marker before linking an isolated workspace."""

    shared = root / ".esp32_firmware_hash"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(shared, flags, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    _validate_shared_firmware_marker(shared)

    workspace_marker = workspace / ".esp32_firmware_hash"
    workspace_marker.unlink(missing_ok=True)
    workspace_marker.symlink_to(os.path.relpath(shared, start=workspace))
    return shared


def _read_shared_firmware_marker(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("cannot safely read shared firmware marker") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise RuntimeError("shared firmware marker changed ownership or type")
        payload = os.read(descriptor, 4096)
        if os.read(descriptor, 1):
            raise RuntimeError("shared firmware marker is unexpectedly large")
    finally:
        os.close(descriptor)
    try:
        return payload.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeError("shared firmware marker is not UTF-8") from exc


def _receiver_device_identities(devices: Sequence[Any]) -> list[dict[str, str]]:
    return sorted(
        (
            {
                "hardware_serial": device.hardware_serial,
                "physical_location": device.physical_location,
            }
            for device in devices
        ),
        key=lambda item: (item["physical_location"], item["hardware_serial"]),
    )


def _receiver_firmware_commit_matches(
    root: Path,
    *,
    devices: Sequence[Any],
    installation_digest: str,
    firmware_environment: str,
    firmware_sha256: str,
    require_current_devices: bool = True,
) -> bool:
    if FINALIZED_RECEIVER_COUNT != 5 or len(devices) != FINALIZED_RECEIVER_COUNT:
        return False
    path = root / RECEIVER_FIRMWARE_COMMIT.as_posix()
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_size > MAX_RECEIVER_FIRMWARE_COMMIT_BYTES
        ):
            return False
        payload = _json_object(path)
        commit_devices = payload.get("devices")
        if (
            payload.get("schema_version") != 1
            or payload.get("installation_digest") != installation_digest
            or payload.get("firmware_environment") != firmware_environment
            or payload.get("firmware_sha256") != firmware_sha256
            or not isinstance(commit_devices, list)
            or len(commit_devices) != FINALIZED_RECEIVER_COUNT
            or (
                require_current_devices
                and commit_devices != _receiver_device_identities(devices)
            )
        ):
            return False
        raw_evidence = payload.get("evidence_path")
        expected_evidence_sha256 = payload.get("evidence_sha256")
        if (
            not isinstance(raw_evidence, str)
            or not isinstance(expected_evidence_sha256, str)
            or RELEASE_PATTERN.fullmatch(expected_evidence_sha256) is None
        ):
            return False
        evidence_relative = _safe_relative(raw_evidence)
        if not _is_beneath(
            evidence_relative, PurePosixPath("run_state/receiver_flash_attempts")
        ):
            return False
        evidence_path = root / evidence_relative.as_posix()
        evidence_metadata = evidence_path.lstat()
        if (
            evidence_path.is_symlink()
            or not stat.S_ISREG(evidence_metadata.st_mode)
            or evidence_metadata.st_uid != os.geteuid()
            or _sha256_file(evidence_path) != expected_evidence_sha256
        ):
            return False
        evidence = _json_object(evidence_path)
        boards = evidence.get("boards")
        targets = evidence.get("targets")
        evidence_devices = evidence.get("expected_devices")
        board_identities = sorted(
            (
                (board.get("hardware_serial"), board.get("physical_location"))
                for board in boards
                if isinstance(board, dict)
            ),
            key=lambda item: (str(item[1]), str(item[0])),
        ) if isinstance(boards, list) else []
        target_identities = sorted(
            (
                (target.get("hardware_serial"), target.get("physical_location"))
                for target in targets
                if isinstance(target, dict)
            ),
            key=lambda item: (str(item[1]), str(item[0])),
        ) if isinstance(targets, list) else []
        return bool(
            evidence.get("outcome") == "success"
            and evidence.get("installation_digest") == installation_digest
            and evidence.get("firmware_environment") == firmware_environment
            and evidence.get("firmware_sha256") == firmware_sha256
            and isinstance(evidence_devices, list)
            and len(evidence_devices) == FINALIZED_RECEIVER_COUNT
            and sorted(
                (
                    {
                        "hardware_serial": item.get("hardware_serial"),
                        "physical_location": item.get("physical_location"),
                    }
                    for item in evidence_devices
                    if isinstance(item, dict)
                ),
                key=lambda item: (
                    str(item["physical_location"]), str(item["hardware_serial"])
                ),
            )
            == commit_devices
            and isinstance(boards, list)
            and isinstance(targets, list)
            and len(boards) == len(targets)
            and 0 < len(boards) <= FINALIZED_RECEIVER_COUNT
            and board_identities == target_identities
            and all(
                isinstance(board, dict) and board.get("outcome") == "success"
                for board in boards
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected a JSON object in {path}")
    return payload


def _final_stdout_json_object(output: str, *, label: str) -> dict[str, Any]:
    """Parse one final single-line control object after optional tool progress."""

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"{label} returned no JSON result")
    final_line = lines[-1]
    try:
        payload = json.loads(final_line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{label} did not end with JSON: {final_line[-1000:]}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} final JSON result is not an object")
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
    input_data: Optional[bytes] = None,
) -> subprocess.CompletedProcess[str]:
    raw = subprocess.run(
        [os.fspath(arg) for arg in args],
        cwd=os.fspath(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=input_data is None,
        input=input_data,
        check=False,
        timeout=timeout,
    )
    completed = subprocess.CompletedProcess(
        raw.args,
        raw.returncode,
        (
            raw.stdout.decode("utf-8", errors="replace")
            if isinstance(raw.stdout, bytes)
            else raw.stdout
        ),
        (
            raw.stderr.decode("utf-8", errors="replace")
            if isinstance(raw.stderr, bytes)
            else raw.stderr
        ),
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


def _legacy_service_working_directory(root: Path, unit: str) -> Path:
    pid = _service_main_pid(unit)
    if pid <= 0:
        raise RuntimeError("legacy bootstrap requires a running mutable service")
    try:
        working_directory = Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
    except OSError as exc:
        raise RuntimeError("cannot identify the running legacy service source") from exc
    if working_directory != root.resolve():
        raise RuntimeError(
            "legacy service is not running from the mutable deployment root"
        )
    return working_directory


def bootstrap_legacy_app(
    root: Path, candidate_release_id: str, *, unit: str = DEFAULT_SYSTEMD_UNIT,
) -> Mapping[str, Any]:
    """Publish the first immutable rollback release without restarting it."""

    manager = _app_manager(root)
    manager.validate(candidate_release_id)
    current = manager.current_release_id()
    record = _read_legacy_bootstrap_record(root)

    if record is not None:
        bootstrap_id = str(record["bootstrap_release_id"])
        phase = str(record["phase"])
        pending_candidate = record.get("candidate_release_id")
        if phase != "complete":
            bootstrap = manager.validate(bootstrap_id)
            if bootstrap.digest != record.get("bootstrap_digest"):
                raise RuntimeError("legacy bootstrap record digest disagrees with release")
        else:
            bootstrap = None

        if current is None and phase == "prepared":
            previous, selected = manager.activate_if_unset(bootstrap_id)
            if previous is not None or not selected:
                raise RuntimeError("legacy bootstrap lost its no-clobber selection boundary")
            current = bootstrap_id
            phase = "selected"
            record = {
                **record,
                "phase": phase,
                "selected_at": time.time(),
            }
            _write_legacy_bootstrap_record(root, record)

        if phase == "candidate_pending":
            if current not in {bootstrap_id, pending_candidate}:
                raise RuntimeError(
                    "pending legacy bootstrap disagrees with current release selection"
                )
            return {
                "outcome": "skipped",
                "reason": "resuming a candidate guarded by the legacy bootstrap",
                "selected": current == bootstrap_id,
                "current_release": current,
                "bootstrap_release_id": bootstrap_id,
                "bootstrap_digest": record["bootstrap_digest"],
                "file_count": record.get("file_count"),
                "phase": phase,
                "recovery_release": bootstrap_id,
                "candidate_release_id": pending_candidate,
                "record_path": os.fspath(
                    root / LEGACY_BOOTSTRAP_RECORD.as_posix()
                ),
            }
        if phase in {"prepared", "selected"}:
            if current != bootstrap_id:
                raise RuntimeError(
                    "legacy bootstrap record disagrees with current release selection"
                )
            return {
                "outcome": "skipped",
                "reason": "legacy bootstrap release is already selected",
                "selected": True,
                "current_release": current,
                "bootstrap_release_id": bootstrap_id,
                "bootstrap_digest": record["bootstrap_digest"],
                "file_count": record.get("file_count"),
                "phase": phase,
                "recovery_release": None,
                "record_path": os.fspath(
                    root / LEGACY_BOOTSTRAP_RECORD.as_posix()
                ),
            }
        if current is None:
            raise RuntimeError(
                "completed legacy bootstrap has no immutable current release"
            )
        manager.validate(current)
        return {
            "outcome": "skipped",
            "reason": "legacy bootstrap lifecycle is complete",
            "selected": False,
            "current_release": current,
            "bootstrap_release_id": record["bootstrap_release_id"],
            "bootstrap_digest": record["bootstrap_digest"],
            "file_count": record.get("file_count"),
            "phase": phase,
            "recovery_release": None,
            "record_path": os.fspath(root / LEGACY_BOOTSTRAP_RECORD.as_posix()),
        }

    if current is not None:
        manager.validate(current)
        return {
            "outcome": "skipped",
            "reason": "an immutable app release is already selected",
            "selected": False,
            "current_release": current,
            "bootstrap_release_id": None,
            "recovery_release": None,
        }

    working_directory = _legacy_service_working_directory(root, unit)

    sources = _legacy_bootstrap_sources(root)
    info = manager.stage(sources)
    verified = manager.validate(info.id)
    record = {
        "schema_version": 1,
        "phase": "prepared",
        "bootstrap_release_id": verified.id,
        "bootstrap_digest": verified.digest,
        "candidate_release_id": None,
        "file_count": len(verified.files),
        "source_working_directory": os.fspath(working_directory),
        "prepared_at": time.time(),
    }
    _write_legacy_bootstrap_record(root, record)
    previous, selected = manager.activate_if_unset(verified.id)
    if previous is not None or not selected:
        raise RuntimeError("legacy bootstrap lost its no-clobber selection boundary")
    record = {**record, "phase": "selected", "selected_at": time.time()}
    _write_legacy_bootstrap_record(root, record)
    manager.validate(verified.id)
    return {
        "outcome": "executed",
        "reason": "snapshotted the running mutable app before first cutover",
        "selected": True,
        "current_release": verified.id,
        "bootstrap_release_id": verified.id,
        "bootstrap_digest": verified.digest,
        "file_count": len(verified.files),
        "phase": "selected",
        "recovery_release": None,
        "record_path": os.fspath(root / LEGACY_BOOTSTRAP_RECORD.as_posix()),
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
    return _final_stdout_json_object(result.stdout, label="runtime environment")


def _unit_text(
    root: Path, user: str, *, strips: int = 33, receivers: int = 5
) -> str:
    if isinstance(strips, bool) or strips <= 0:
        raise ValueError("strips must be a positive integer")
    if isinstance(receivers, bool) or receivers <= 0:
        raise ValueError("receivers must be a positive integer")
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
            f"Environment=STRIPS={strips}",
            f"Environment=EXPECTED_ESP32_DEVICES={receivers}",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        )
    )


def ensure_unit(
    root: Path,
    *,
    user: str,
    strips: int = 33,
    receivers: int = 5,
    unit: str = DEFAULT_SYSTEMD_UNIT,
) -> Mapping[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", unit):
        raise ValueError(f"unsafe systemd unit name: {unit!r}")
    desired = _unit_text(root, user, strips=strips, receivers=receivers)
    destination = SYSTEMD_UNIT_ROOT / unit
    current = ""
    try:
        current = destination.read_text(encoding="utf-8")
    except OSError:
        pass
    changed = current != desired
    if changed:
        desired_bytes = desired.encode("utf-8")
        desired_sha256 = hashlib.sha256(desired_bytes).hexdigest()
        staging = destination.parent / (
            f".{unit}.install-{os.getpid()}-{time.time_ns()}"
        )
        try:
            _write_root_owned_bytes(
                staging,
                desired_bytes,
                expected_sha256=desired_sha256,
                mode="0644",
            )
            _command(("sudo", "mv", "-T", "--", staging, destination))
            _validate_root_owned_regular_file(
                destination, expected_sha256=desired_sha256, mode="0644"
            )
            _command(("sudo", "systemctl", "daemon-reload"))
        finally:
            try:
                _command(
                    ("sudo", "rm", "-f", "--", staging),
                    check=False,
                    timeout=10.0,
                )
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                pass
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


def provision(
    root: Path,
    release_id: str,
    *,
    user: str,
    hat: bool,
    strips: int = 33,
    receivers: int = 5,
) -> Mapping[str, Any]:
    release = _release(root, release_id)
    runtime = ensure_runtime(root, release_id)
    spi = configure_spi(release, hat=hat)
    # Do not replace the boot-time unit while an SPI configuration reboot is
    # outstanding. On first coordinator cutover there may be no ``current``
    # symlink yet, so retaining the legacy unit keeps the existing service
    # bootable through the one allowed reboot. The idempotent resume installs
    # the current-aware unit after SPI reports ready.
    if spi.get("status") == "ready":
        unit = ensure_unit(
            root, user=user, strips=strips, receivers=receivers
        )
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


def migrate_receiver_topology(root: Path) -> Mapping[str, Any]:
    """Reconcile the retired four-receiver rollout file before firmware work."""

    config, migrated = migrate_legacy_receiver_hybrid_config(root)
    return {
        "outcome": "executed" if migrated else "skipped",
        "migrated": migrated,
        "receiver_hybrid_config": config.to_dict(),
        "receiver_hybrid_config_digest": config.selection_digest,
        "strips": config.strip_count,
        "receivers": len(config.receiver_strip_counts),
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


def _platformio_executable() -> str:
    pio = shutil.which("pio") or os.fspath(
        Path.home() / ".platformio-venv" / "bin" / "pio"
    )
    if not Path(pio).is_file() and shutil.which(pio) is None:
        raise RuntimeError("PlatformIO is unavailable on the target; run setup first")
    return pio


def build_firmware(root: Path, support_id: Optional[str]) -> Mapping[str, Any]:
    hybrid_config = resolve_receiver_hybrid_config(root)
    firmware_environment = hybrid_config.firmware_environment
    if support_id is None:
        return {
            "outcome": "skipped",
            "reason": "no support inputs",
            "receiver_hybrid_config": hybrid_config.to_dict(),
            "receiver_hybrid_config_digest": hybrid_config.selection_digest,
            "firmware_environment": firmware_environment,
        }
    workspace, reused = _copy_support_workspace(root, support_id)
    firmware = workspace / "firmware" / "esp32"
    if reused:
        try:
            installation = inspect_firmware_installation(
                firmware, firmware_environment
            )
        except RuntimeError:
            # A workspace created by the previous single-binary contract may
            # be incomplete. Rebuild it instead of accepting a partial cache.
            installation = None
        if installation is not None:
            return {
                "outcome": "skipped",
                "reason": "firmware build already exists",
                "workspace": os.fspath(workspace),
                "firmware_sha256": installation["firmware_sha256"],
                "firmware_installation_digest": installation[
                    "installation_digest"
                ],
                "firmware_artifacts": installation,
                "receiver_hybrid_config": hybrid_config.to_dict(),
                "receiver_hybrid_config_digest": hybrid_config.selection_digest,
                "firmware_environment": firmware_environment,
            }
    pio = _platformio_executable()
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
        (pio, "run", "-e", firmware_environment),
        cwd=firmware,
        env=build_env,
    )
    installation = inspect_firmware_installation(firmware, firmware_environment)
    return {
        "outcome": "executed",
        "workspace": os.fspath(workspace),
        "build_cache": os.fspath(build_cache),
        "ccache": os.fspath(ccache_dir),
        "firmware_sha256": installation["firmware_sha256"],
        "firmware_installation_digest": installation["installation_digest"],
        "firmware_artifacts": installation,
        "receiver_hybrid_config": hybrid_config.to_dict(),
        "receiver_hybrid_config_digest": hybrid_config.selection_digest,
        "firmware_environment": firmware_environment,
        "output_tail": (completed.stdout + completed.stderr)[-2000:],
    }


def _receiver_inventory_module():
    """Load the flash-only helper without coupling app-only rollback commands."""

    try:
        from tools.deployment import receiver_firmware_inventory
    except ModuleNotFoundError:
        import receiver_firmware_inventory  # type: ignore[no-redef]
    return receiver_firmware_inventory


def _discover_receiver_devices(
    *, receiver_count: int, timeout: float = 15.0
) -> tuple[Any, ...]:
    pio = _platformio_executable()
    completed = _command(
        (pio, "device", "list", "--json-output"), timeout=timeout
    )
    return _receiver_inventory_module().parse_platformio_receiver_devices(
        completed.stdout, receiver_count=receiver_count
    )


def _write_shared_firmware_marker(
    path: Path, *, expected_before: str, installation_digest: str
) -> None:
    """Atomically advance the aggregate marker after every board verifies."""

    _validate_shared_firmware_marker(path)
    if _read_shared_firmware_marker(path) != expected_before:
        raise RuntimeError("shared firmware marker changed during receiver flash")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(installation_digest + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _pinned_openocd_archive(root: Path) -> Path:
    cache = root / "build" / "tools" / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / PINNED_OPENOCD_ARCHIVE
    if archive.exists():
        if archive.is_symlink() or not archive.is_file():
            raise RuntimeError("pinned OpenOCD cache is not a regular file")
        if _sha256_file(archive) != PINNED_OPENOCD_SHA256:
            raise RuntimeError("cached pinned OpenOCD archive digest is invalid")
        return archive

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{archive.name}.", suffix=".tmp", dir=cache
    )
    temporary = Path(temporary_name)
    received = 0
    try:
        request = Request(PINNED_OPENOCD_URL, headers={"User-Agent": "ledgrid-deploy/1"})
        try:
            with urlopen(request, timeout=30.0) as response, os.fdopen(
                descriptor, "wb"
            ) as stream:
                descriptor = -1
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > PINNED_OPENOCD_MAX_BYTES:
                        raise RuntimeError("pinned OpenOCD archive exceeds size limit")
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, URLError) as exc:
            raise RuntimeError(f"cannot download pinned OpenOCD archive: {exc}") from exc
        if _sha256_file(temporary) != PINNED_OPENOCD_SHA256:
            raise RuntimeError("downloaded pinned OpenOCD archive digest is invalid")
        os.chmod(temporary, 0o600)
        os.replace(temporary, archive)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return archive


@contextmanager
def _pinned_openocd(root: Path) -> Iterable[tuple[Path, Path]]:
    """Yield a root-owned OpenOCD install derived from the verified archive."""

    archive = _pinned_openocd_archive(root)
    install = PINNED_OPENOCD_INSTALL_ROOT / PINNED_OPENOCD_SHA256
    if not install.exists():
        archive_payload = _read_pinned_regular_source(
            archive,
            expected_sha256=PINNED_OPENOCD_SHA256,
            maximum_bytes=PINNED_OPENOCD_MAX_BYTES,
        )
        staging = PINNED_OPENOCD_INSTALL_ROOT / (
            f".install-{PINNED_OPENOCD_SHA256}-{os.getpid()}-{time.time_ns()}"
        )
        root_archive = staging / PINNED_OPENOCD_ARCHIVE
        try:
            _command(("sudo", "mkdir", "-p", PINNED_OPENOCD_INSTALL_ROOT))
            _command(("sudo", "mkdir", "--", staging))
            _write_root_owned_bytes(
                root_archive,
                archive_payload,
                expected_sha256=PINNED_OPENOCD_SHA256,
            )
            _command(("sudo", "tar", "-xzf", root_archive, "-C", staging))
            _command(("sudo", "chown", "-R", "root:root", staging))
            _command(("sudo", "chmod", "-R", "go-w", staging))
            _command(("sudo", "mv", "--", staging, install))
        except Exception:
            _command(("sudo", "rm", "-rf", "--", staging), check=False, timeout=15.0)
            raise

    root_archive = install / PINNED_OPENOCD_ARCHIVE
    tool_root = install / "openocd-esp32"
    executable = tool_root / "bin" / "openocd"
    scripts = tool_root / "share" / "openocd" / "scripts"
    board_config = scripts / OPENOCD_BOARD_CONFIG
    required = (
        PINNED_OPENOCD_INSTALL_ROOT,
        install,
        root_archive,
        tool_root,
        executable,
        scripts,
        board_config,
    )
    for path in required:
        metadata = path.lstat()
        if path.is_symlink() or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise RuntimeError(
                f"pinned OpenOCD install is not root-owned and immutable: {path}"
            )
    if (
        not install.is_dir()
        or not root_archive.is_file()
        or not tool_root.is_dir()
        or not executable.is_file()
        or not scripts.is_dir()
        or not board_config.is_file()
        or _sha256_file(root_archive) != PINNED_OPENOCD_SHA256
    ):
        raise RuntimeError("pinned OpenOCD install has an invalid layout or digest")
    for path in tool_root.rglob("*"):
        metadata = path.lstat()
        if (
            path.is_symlink()
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
            or not (path.is_file() or path.is_dir())
        ):
            raise RuntimeError(f"pinned OpenOCD install contains an unsafe path: {path}")
    version = _command((executable, "--version"), timeout=10.0)
    if PINNED_OPENOCD_VERSION.removeprefix("v") not in (
        version.stdout + version.stderr
    ):
        raise RuntimeError("pinned OpenOCD executable reports an unexpected version")
    yield executable, scripts


def _receiver_identity(device: Any) -> tuple[str, str]:
    return device.hardware_serial, device.physical_location


def _assert_receiver_binding(
    expected: Sequence[Any], observed: Sequence[Any], *, phase: str
) -> None:
    expected_identities = sorted(_receiver_identity(item) for item in expected)
    observed_identities = sorted(_receiver_identity(item) for item in observed)
    if observed_identities != expected_identities:
        raise RuntimeError(
            f"receiver hardware serial/USB path changed {phase}; "
            f"expected={expected_identities}, observed={observed_identities}"
        )


def _wait_for_receiver_binding(
    expected: Sequence[Any],
    *,
    receiver_count: int,
    phase: str,
    timeout: float = RECEIVER_USB_STABILIZATION_SECONDS,
    poll_interval: float = RECEIVER_USB_STABILIZATION_POLL_SECONDS,
) -> tuple[Any, ...]:
    """Wait for the exact expected serial/path set after USB reset churn."""

    deadline = time.monotonic() + timeout
    last_error = "receiver discovery did not run"
    while True:
        remaining = deadline - time.monotonic()
        try:
            observed = _discover_receiver_devices(
                receiver_count=receiver_count,
                timeout=max(0.25, min(3.0, remaining)),
            )
            _assert_receiver_binding(expected, observed, phase=phase)
            return observed
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"exact receiver hardware serial/USB path set did not stabilize "
                f"{phase} within {timeout:.1f}s: {last_error}"
            )
        time.sleep(min(poll_interval, remaining))


def _receiver_flash_evidence_path(
    root: Path, *, installation_digest: str, targets: Sequence[Any]
) -> Path:
    nonce = {
        "installation_digest": installation_digest,
        "targets": [target.to_dict() for target in targets],
        "time_ns": time.time_ns(),
        "pid": os.getpid(),
    }
    attempt_id = hashlib.sha256(
        json.dumps(nonce, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return root / "run_state" / "receiver_flash_attempts" / f"{attempt_id}.json"


def _require_finalized_receiver_topology(hybrid_config: Any) -> int:
    """Return the immutable installed receiver count or fail before mutation."""

    receiver_count = len(hybrid_config.receiver_strip_counts)
    if FINALIZED_RECEIVER_COUNT != 5 or receiver_count != FINALIZED_RECEIVER_COUNT:
        raise RuntimeError(
            "firmware flash requires the finalized topology of exactly 5 receivers; "
            f"resolved={receiver_count}, finalized={FINALIZED_RECEIVER_COUNT}"
        )
    return receiver_count


def _require_finalized_receiver_devices(devices: Sequence[Any]) -> None:
    if FINALIZED_RECEIVER_COUNT != 5 or len(devices) != FINALIZED_RECEIVER_COUNT:
        raise RuntimeError(
            "firmware flash requires exactly 5 discovered receivers before mutation; "
            f"observed={len(devices)}"
        )


def _validate_root_owned_firmware_bundle(
    bundle_root: Path,
    artifacts: Sequence[Mapping[str, Any]],
    *,
    installation_digest: str,
) -> None:
    """Validate the exact immutable files that privileged OpenOCD will open."""

    expected_root = (
        ROOT_OWNED_FIRMWARE_INSTALL_ROOT / installation_digest
    ).resolve(strict=False)
    if (
        RELEASE_PATTERN.fullmatch(installation_digest) is None
        or bundle_root.resolve(strict=False) != expected_root
        or len(artifacts) < 1
    ):
        raise RuntimeError("root-owned firmware bundle identity is invalid")
    required_paths = (ROOT_OWNED_FIRMWARE_INSTALL_ROOT, bundle_root)
    for path in required_paths:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
        ):
            raise RuntimeError(
                f"firmware bundle is not root-owned and immutable: {path}"
            )
    expected_files: set[Path] = set()
    for index, artifact in enumerate(artifacts):
        raw_path = artifact.get("program_path")
        expected_sha256 = artifact.get("sha256")
        if (
            not isinstance(raw_path, str)
            or not isinstance(expected_sha256, str)
            or RELEASE_PATTERN.fullmatch(expected_sha256) is None
        ):
            raise RuntimeError("root-owned firmware artifact receipt is malformed")
        path = Path(raw_path)
        expected_path = bundle_root / f"artifact-{index:02d}.bin"
        if path != expected_path:
            raise RuntimeError("root-owned firmware artifact path is not canonical")
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
            or _sha256_file(path) != expected_sha256
        ):
            raise RuntimeError(
                f"firmware artifact is not root-owned, immutable, and verified: {path}"
            )
        expected_files.add(path)
    actual_files = set(bundle_root.iterdir())
    if actual_files != expected_files:
        raise RuntimeError("root-owned firmware bundle contains unexpected paths")


def _root_owned_firmware_bundle(
    firmware: Path,
    installation: Mapping[str, Any],
) -> tuple[Path, list[dict[str, Any]]]:
    """Copy inspected inputs into a re-hashed root-owned immutable bundle."""

    installation_digest = str(installation["installation_digest"])
    if RELEASE_PATTERN.fullmatch(installation_digest) is None:
        raise RuntimeError("firmware installation digest is malformed")
    build = firmware / ".pio" / "build" / str(installation["environment"])
    build_resolved = build.resolve(strict=True)
    bundle_root = ROOT_OWNED_FIRMWARE_INSTALL_ROOT / installation_digest
    source_artifacts = list(installation["flash_artifacts"])
    artifacts: list[dict[str, Any]] = []
    pinned_payloads: list[bytes] = []
    for index, artifact in enumerate(source_artifacts):
        source = build / str(artifact["build_path"])
        resolved = source.resolve(strict=True)
        try:
            resolved.relative_to(build_resolved)
        except ValueError as exc:
            raise RuntimeError(
                "validated flash artifact escapes its build directory"
            ) from exc
        payload = _read_pinned_regular_source(
            source,
            expected_sha256=str(artifact["sha256"]),
            maximum_bytes=MAX_RECEIVER_FIRMWARE_ARTIFACT_BYTES,
            expected_size=int(artifact["size"]),
        )
        pinned_payloads.append(payload)
        artifacts.append({
            **dict(artifact),
            "program_path": os.fspath(bundle_root / f"artifact-{index:02d}.bin"),
        })

    if not bundle_root.exists():
        staging = ROOT_OWNED_FIRMWARE_INSTALL_ROOT / (
            f".install-{installation_digest}-{os.getpid()}-{time.time_ns()}"
        )
        try:
            _command(("sudo", "mkdir", "-p", ROOT_OWNED_FIRMWARE_INSTALL_ROOT))
            _command(("sudo", "mkdir", "--", staging))
            for index, (artifact, payload) in enumerate(
                zip(artifacts, pinned_payloads, strict=True)
            ):
                destination = staging / f"artifact-{index:02d}.bin"
                _write_root_owned_bytes(
                    destination,
                    payload,
                    expected_sha256=str(artifact["sha256"]),
                )
            _command(("sudo", "chown", "-R", "root:root", staging))
            _command(("sudo", "chmod", "-R", "go-w", staging))
            _command(("sudo", "mv", "--", staging, bundle_root))
        except Exception:
            _command(("sudo", "rm", "-rf", "--", staging), check=False, timeout=15.0)
            raise

    _validate_root_owned_firmware_bundle(
        bundle_root, artifacts, installation_digest=installation_digest
    )
    return bundle_root, artifacts


def _invalidate_receiver_firmware_commit(
    root: Path,
    *,
    evidence_path: Path,
    installation_digest: str,
    devices: Sequence[Any],
) -> Path:
    """Durably revoke any prior authority before the first board mutation."""

    commit_path = root / RECEIVER_FIRMWARE_COMMIT.as_posix()
    previous_sha256: Optional[str] = None
    try:
        metadata = commit_path.lstat()
        if (
            not commit_path.is_symlink()
            and stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == os.geteuid()
            and metadata.st_size <= MAX_RECEIVER_FIRMWARE_COMMIT_BYTES
        ):
            previous_sha256 = _sha256_file(commit_path)
    except FileNotFoundError:
        pass
    _atomic_json(
        commit_path,
        {
            "schema_version": 1,
            "status": "invalidated",
            "installation_digest": installation_digest,
            "devices": _receiver_device_identities(devices),
            "evidence_path": evidence_path.relative_to(root).as_posix(),
            "previous_commit_sha256": previous_sha256,
        },
    )
    retained = _json_object(commit_path)
    if (
        retained.get("status") != "invalidated"
        or retained.get("installation_digest") != installation_digest
        or retained.get("devices") != _receiver_device_identities(devices)
        or retained.get("evidence_path") != evidence_path.relative_to(root).as_posix()
    ):
        raise RuntimeError("receiver firmware authority invalidation failed")
    return commit_path


def _program_receiver_openocd(
    *,
    executable: Path,
    scripts: Path,
    bundle_root: Path,
    artifacts: Sequence[Mapping[str, Any]],
    installation_digest: str,
    device: Any,
) -> dict[str, Any]:
    _validate_root_owned_firmware_bundle(
        bundle_root, artifacts, installation_digest=installation_digest
    )
    command: list[os.PathLike[str] | str] = [
        "sudo",
        executable,
        "-s",
        scripts,
        "-f",
        OPENOCD_BOARD_CONFIG,
        "-c",
        f"adapter serial {device.hardware_serial.upper()}",
    ]
    artifact_evidence = []
    for artifact in artifacts:
        artifact_argument = str(artifact["program_path"])
        if re.fullmatch(r"/[A-Za-z0-9_./:+-]+", artifact_argument) is None:
            raise RuntimeError(
                "root-owned firmware artifact has an unsafe Tcl path before "
                f"{device.hardware_serial}: {artifact_argument}"
            )
        command.extend(
            (
                "-c",
                f"program_esp {{{artifact_argument}}} {artifact['offset']} "
                "verify no_skip_loaded",
            )
        )
        artifact_evidence.append(dict(artifact))
    command.extend(("-c", "reset run", "-c", "shutdown"))
    try:
        completed = _command(command, check=False, timeout=180.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            **device.to_dict(),
            "operation": "openocd_program_verify",
            "artifacts": artifact_evidence,
            "returncode": None,
            "verify_count": 0,
            "expected_verify_count": len(artifact_evidence),
            "output": f"{type(exc).__name__}: {exc}",
            "outcome": "failed",
        }
    output = completed.stdout + completed.stderr
    verify_count = output.count("** Verify OK **")
    result = {
        **device.to_dict(),
        "operation": "openocd_program_verify",
        "artifacts": artifact_evidence,
        "returncode": completed.returncode,
        "verify_count": verify_count,
        "expected_verify_count": len(artifact_evidence),
        "output": output[-128 * 1024 :],
    }
    return {
        **result,
        "outcome": (
            "success"
            if completed.returncode == 0 and verify_count == len(artifact_evidence)
            else "failed"
        ),
    }


def _best_effort_stop_receiver_service() -> None:
    try:
        _command(
            ("sudo", "systemctl", "stop", DEFAULT_SYSTEMD_UNIT),
            check=False,
            timeout=10.0,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        pass


def flash_firmware(
    root: Path,
    support_id: Optional[str],
    *,
    expected_firmware_environment: Optional[str] = None,
    expected_config_digest: Optional[str] = None,
    expected_installation_digest: Optional[str] = None,
    force: bool = False,
) -> Mapping[str, Any]:
    hybrid_config = resolve_receiver_hybrid_config(root)
    receiver_count = _require_finalized_receiver_topology(hybrid_config)
    firmware_environment = hybrid_config.firmware_environment
    if (
        expected_firmware_environment is not None
        and expected_firmware_environment != firmware_environment
    ):
        raise RuntimeError(
            "receiver-hybrid firmware selection changed between build and flash"
        )
    if (
        expected_config_digest is not None
        and expected_config_digest != hybrid_config.selection_digest
    ):
        raise RuntimeError(
            "receiver-hybrid config changed between build and flash"
        )
    if support_id is None:
        return {
            "outcome": "skipped",
            "reason": "no support inputs",
            "receiver_hybrid_config": hybrid_config.to_dict(),
            "receiver_hybrid_config_digest": hybrid_config.selection_digest,
            "firmware_environment": firmware_environment,
        }
    workspace, _ = _copy_support_workspace(root, support_id)
    firmware = workspace / "firmware" / "esp32"
    try:
        installation = inspect_firmware_installation(firmware, firmware_environment)
    except RuntimeError as exc:
        raise RuntimeError(
            f"validated {firmware_environment} firmware installation is unavailable; "
            "run build-firmware before flash-firmware"
        ) from exc
    firmware_sha256 = str(installation["firmware_sha256"])
    installation_digest = str(installation["installation_digest"])
    if (
        expected_installation_digest is not None
        and expected_installation_digest != installation_digest
    ):
        raise RuntimeError(
            "firmware installation artifacts changed between build and flash"
        )
    devices = _discover_receiver_devices(receiver_count=receiver_count)
    _require_finalized_receiver_devices(devices)
    ports = [device.port for device in devices]
    receiver_inventory = _receiver_inventory_module()
    # Preserve the target-owned marker path and atomic update behavior. A
    # schema-v1 digest remains readable, but cannot equal the complete v3
    # artifact identity and therefore causes one deliberate migration flash.
    shared_marker = _prepare_shared_firmware_marker(root, workspace)
    installed_marker_before = _read_shared_firmware_marker(shared_marker)
    installed_inventory = receiver_inventory.read_firmware_inventory(root)
    commit_matches = _receiver_firmware_commit_matches(
        root,
        devices=devices,
        installation_digest=installation_digest,
        firmware_environment=firmware_environment,
        firmware_sha256=firmware_sha256,
    )
    installation_commit_matches = _receiver_firmware_commit_matches(
        root,
        devices=devices,
        installation_digest=installation_digest,
        firmware_environment=firmware_environment,
        firmware_sha256=firmware_sha256,
        require_current_devices=False,
    )
    authority_repair = (
        not commit_matches and installed_marker_before == installation_digest
    )
    targets = receiver_inventory.plan_receiver_flashes(
        devices,
        installed_inventory,
        installation_digest=installation_digest,
        firmware_environment=firmware_environment,
        firmware_sha256=firmware_sha256,
        force=force,
        aggregate_marker_matches=(
            installed_marker_before == installation_digest
            and installation_commit_matches
        ),
    )
    if not targets and not commit_matches:
        # Marker/inventory may be ahead of the last authoritative commit after
        # a boundary failure. Never convert that partial transaction into a
        # skip: re-verify every exact current device and write a fresh commit.
        targets = receiver_inventory.plan_receiver_flashes(
            devices,
            installed_inventory,
            installation_digest=installation_digest,
            firmware_environment=firmware_environment,
            firmware_sha256=firmware_sha256,
            force=True,
            aggregate_marker_matches=False,
        )
        authority_repair = True
    target_ports = [target.device.port for target in targets]
    inventory_details = {
        "schema_version": 1,
        "path": os.fspath(root / "run_state" / "receiver_firmware_inventory.json"),
        "observed_devices": [device.to_dict() for device in devices],
        "recorded_devices_before": sorted(installed_inventory),
        "commit_record_matches": commit_matches,
        "installation_commit_matches": installation_commit_matches,
        "authority_repair": authority_repair,
        "flash_targets": [target.to_dict() for target in targets],
        "forced": force,
    }
    if not targets:
        return {
            "outcome": "skipped",
            "ports": ports,
            "flashed_ports": [],
            "firmware_sha256": firmware_sha256,
            "firmware_artifacts": installation,
            "firmware_environment": firmware_environment,
            "firmware_installation_digest": installation_digest,
            "receiver_firmware_inventory": inventory_details,
            "receiver_hybrid_config": hybrid_config.to_dict(),
            "receiver_hybrid_config_digest": hybrid_config.selection_digest,
            "output_tail": "All attached receiver hardware already has successful install evidence.\n",
        }
    evidence_path = _receiver_flash_evidence_path(
        root, installation_digest=installation_digest, targets=targets
    )
    board_results: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "outcome": "in_progress",
        "phase": "programming",
        "installation_digest": installation_digest,
        "firmware_environment": firmware_environment,
        "firmware_sha256": firmware_sha256,
        "openocd": {
            "version": PINNED_OPENOCD_VERSION,
            "archive": PINNED_OPENOCD_ARCHIVE,
            "archive_sha256": PINNED_OPENOCD_SHA256,
        },
        "expected_devices": [device.to_dict() for device in devices],
        "targets": [target.to_dict() for target in targets],
        "boards": board_results,
    }
    _atomic_json(evidence_path, evidence)
    try:
        with _pinned_openocd(root) as (openocd, openocd_scripts):
            bundle_root, immutable_artifacts = _root_owned_firmware_bundle(
                firmware, installation
            )
            evidence.update({
                "phase": "authority_invalidation",
                "firmware_bundle_root": os.fspath(bundle_root),
                "firmware_bundle_artifacts": immutable_artifacts,
            })
            _atomic_json(evidence_path, evidence)
            commit_path = _invalidate_receiver_firmware_commit(
                root,
                evidence_path=evidence_path,
                installation_digest=installation_digest,
                devices=devices,
            )
            evidence["phase"] = "programming"
            _atomic_json(evidence_path, evidence)
            for target in targets:
                observed = _wait_for_receiver_binding(
                    devices,
                    receiver_count=receiver_count,
                    phase=f"before programming {target.device.hardware_serial}",
                )
                current = {
                    device.hardware_serial: device for device in observed
                }[target.device.hardware_serial]
                result = _program_receiver_openocd(
                    executable=openocd,
                    scripts=openocd_scripts,
                    bundle_root=bundle_root,
                    artifacts=immutable_artifacts,
                    installation_digest=installation_digest,
                    device=current,
                )
                board_results.append(result)
                _atomic_json(evidence_path, evidence)
                if result["outcome"] != "success":
                    raise RuntimeError(
                        "receiver OpenOCD program/readback verification failed for "
                        f"serial={current.hardware_serial} "
                        f"usb_path={current.physical_location} port={current.port} "
                        f"returncode={result['returncode']} "
                        f"verify_count={result['verify_count']}/"
                        f"{result['expected_verify_count']}"
                    )

        observed_after = _wait_for_receiver_binding(
            devices,
            receiver_count=receiver_count,
            phase="after programming",
        )
        _validate_root_owned_firmware_bundle(
            bundle_root,
            immutable_artifacts,
            installation_digest=installation_digest,
        )
        if _read_shared_firmware_marker(shared_marker) != installed_marker_before:
            raise RuntimeError("shared firmware marker changed during receiver flash")
        evidence.update({
            "outcome": "verified",
            "phase": "evidence_commit",
            "observed_devices_after": [
                device.to_dict() for device in observed_after
            ],
        })
        _atomic_json(evidence_path, evidence)
        inventory_path = receiver_inventory.write_firmware_inventory(
            root,
            observed_after,
            installation_digest=installation_digest,
            firmware_environment=firmware_environment,
            firmware_sha256=firmware_sha256,
        )
        _write_shared_firmware_marker(
            shared_marker,
            expected_before=installed_marker_before,
            installation_digest=installation_digest,
        )
        installed_marker = _read_shared_firmware_marker(shared_marker)
        if installed_marker != installation_digest:
            raise RuntimeError(
                "receiver firmware installed marker disagrees with selected artifacts"
            )
        evidence.update({
            "outcome": "success",
            "phase": "committed",
            "inventory_path": os.fspath(inventory_path),
        })
        _atomic_json(evidence_path, evidence)
        evidence_relative = evidence_path.relative_to(root).as_posix()
        evidence_sha256 = _sha256_file(evidence_path)
        _atomic_json(
            commit_path,
            {
                "schema_version": 1,
                "installation_digest": installation_digest,
                "firmware_environment": firmware_environment,
                "firmware_sha256": firmware_sha256,
                "devices": _receiver_device_identities(observed_after),
                "evidence_path": evidence_relative,
                "evidence_sha256": evidence_sha256,
            },
        )
        if not _receiver_firmware_commit_matches(
            root,
            devices=observed_after,
            installation_digest=installation_digest,
            firmware_environment=firmware_environment,
            firmware_sha256=firmware_sha256,
        ):
            raise RuntimeError("receiver firmware commit record failed validation")
    except Exception as exc:
        evidence.update({"outcome": "failed", "phase": "failed", "error": str(exc)})
        persistence_error = None
        try:
            _atomic_json(evidence_path, evidence)
        except Exception as evidence_exc:
            persistence_error = str(evidence_exc)
        _best_effort_stop_receiver_service()
        persistence_detail = (
            f"; failure evidence write also failed: {persistence_error}"
            if persistence_error is not None
            else ""
        )
        raise RuntimeError(
            f"receiver firmware flash failed; evidence={evidence_path}: {exc}"
            f"{persistence_detail}"
        ) from exc
    inventory_details = {
        **inventory_details,
        "path": os.fspath(inventory_path),
        "flash_evidence_path": os.fspath(evidence_path),
        "flash_commit_path": os.fspath(commit_path),
        "recorded_devices_after": sorted(
            device.hardware_serial for device in observed_after
        ),
    }
    return {
        "outcome": "executed",
        "ports": ports,
        "flashed_ports": target_ports,
        "firmware_sha256": firmware_sha256,
        "firmware_artifacts": installation,
        "firmware_environment": firmware_environment,
        "firmware_installation_digest": installed_marker,
        "receiver_firmware_inventory": inventory_details,
        "receiver_hybrid_config": hybrid_config.to_dict(),
        "receiver_hybrid_config_digest": hybrid_config.selection_digest,
        "output_tail": "\n".join(
            str(result["output"])[-1000:] for result in board_results
        )[-4000:],
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
    bootstrap = _read_legacy_bootstrap_record(root)
    if current == release_id:
        manager.validate(release_id)
        return {
            "release_id": release_id,
            "previous_release": current,
            "changed": False,
            "selected_at": time.time(),
        }
    if bootstrap is not None and bootstrap.get("phase") in {
        "selected", "candidate_pending"
    }:
        bootstrap_id = bootstrap["bootstrap_release_id"]
        pending_candidate = bootstrap.get("candidate_release_id")
        if current == bootstrap_id and release_id != bootstrap_id:
            if (
                bootstrap.get("phase") == "candidate_pending"
                and pending_candidate not in {None, release_id}
            ):
                raise RuntimeError(
                    "legacy bootstrap already guards a different candidate release"
                )
            bootstrap = {
                **bootstrap,
                "phase": "candidate_pending",
                "candidate_release_id": release_id,
                "candidate_selected_at": time.time(),
            }
            _write_legacy_bootstrap_record(root, bootstrap)
        elif (
            release_id == bootstrap_id
            and bootstrap.get("phase") == "candidate_pending"
            and current == pending_candidate
        ):
            # Compensation is allowed to move the lifecycle back to its
            # reusable selected state after the atomic app selection below.
            pass
        elif bootstrap.get("phase") == "candidate_pending":
            raise RuntimeError(
                "candidate activation disagrees with pending legacy bootstrap evidence"
            )
    previous = manager.activate(release_id)
    if (
        bootstrap is not None
        and release_id == bootstrap.get("bootstrap_release_id")
        and bootstrap.get("phase") == "candidate_pending"
    ):
        _write_legacy_bootstrap_record(
            root,
            {
                **bootstrap,
                "phase": "selected",
                "candidate_release_id": None,
                "restored_at": time.time(),
            },
        )
    return {
        "release_id": release_id,
        "previous_release": previous,
        "changed": True,
        "selected_at": time.time(),
    }


def complete_legacy_bootstrap(
    root: Path, candidate_release_id: str,
) -> Mapping[str, Any]:
    """Close pending bootstrap recovery only after candidate health succeeds."""

    record = _read_legacy_bootstrap_record(root)
    if record is None:
        return {"outcome": "skipped", "reason": "no legacy bootstrap lifecycle"}
    if record.get("phase") == "complete":
        return {
            "outcome": "skipped",
            "reason": "legacy bootstrap lifecycle is already complete",
            "bootstrap_release_id": record["bootstrap_release_id"],
        }
    current = _app_manager(root).current_release_id()
    bootstrap_id = record["bootstrap_release_id"]
    pending = record.get("candidate_release_id")
    if current != candidate_release_id:
        raise RuntimeError("cannot complete bootstrap for a non-current candidate")
    if record.get("phase") == "candidate_pending" and pending != candidate_release_id:
        raise RuntimeError("legacy bootstrap pending candidate identity changed")
    if record.get("phase") == "selected" and candidate_release_id != bootstrap_id:
        raise RuntimeError("legacy bootstrap was never bound to this candidate")
    completed = {
        **record,
        "phase": "complete",
        "candidate_release_id": candidate_release_id,
        "completed_at": time.time(),
    }
    _write_legacy_bootstrap_record(root, completed)
    return {
        "outcome": "executed",
        "bootstrap_release_id": bootstrap_id,
        "candidate_release_id": candidate_release_id,
        "phase": "complete",
        "record_path": os.fspath(root / LEGACY_BOOTSTRAP_RECORD.as_posix()),
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
    hybrid_config = resolve_receiver_hybrid_config(root)
    helper_root = _active_helper_root(root)
    runtime = root / "venv" / "bin" / "python"
    state = root / "run_state" / "before_deploy.json"
    if helper_root is None or not runtime.is_file() or not state.is_file():
        return {
            "restored": False,
            "reason": "no captured state",
            "receiver_hybrid_config": hybrid_config.to_dict(),
            "receiver_hybrid_config_digest": hybrid_config.selection_digest,
        }
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
    return {
        "restored": True,
        "receiver_hybrid_config": hybrid_config.to_dict(),
        "receiver_hybrid_config_digest": hybrid_config.selection_digest,
        "output_tail": completed.stdout[-1000:],
    }


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


def _request_receiver_status_refresh(
    api_url: str, timeout: float = 2.0,
) -> str:
    suffix = "/api/status"
    if not api_url.endswith(suffix):
        raise RuntimeError(
            "receiver health API URL must end with /api/status for explicit refresh"
        )
    refresh_url = (
        api_url[:-len(suffix)] + "/api/v1/receivers/status/refresh"
    )
    request = Request(refresh_url, method="POST")  # nosec: fixed/local operator URL
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec: fixed/local operator URL
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"cannot request fresh receiver status: {exc}"
        ) from exc
    request_id = payload.get("request_id") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("accepted") is not True
        or not isinstance(request_id, str)
        or not request_id
    ):
        raise RuntimeError("controller rejected fresh receiver status request")
    return request_id


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
    receiver_device_map: tuple[Mapping[str, Any], ...] = ()
    receiver_statuses: tuple[Mapping[str, Any], ...] = ()
    transport_envelope_devices: int = 0
    receiver_aggregate: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReceiverHealthContract:
    minimum_status_version: int
    required_capabilities: int
    devices: tuple[Mapping[str, Any], ...]


FULL_FRAME_SAMPLING_COUNTERS = (
    "full_frame_status_transfers",
    "full_frame_status_samples",
    "full_frame_status_sample_misses",
    "full_frame_write_only_transfers",
)
FULL_FRAME_SAMPLING_STATE_FIELDS = (
    "full_frame_frames_since_status_sample",
    "full_frame_max_status_sample_gap",
    "spidev_buffer_size",
    "full_frame_write_only_supported",
)
MAX_FULL_FRAME_STATUS_SAMPLE_GAP = 256


def _full_frame_sampling_snapshot_rejection(
    item: Mapping[str, Any], *, label: str, minimum_buffer_size: int
) -> Optional[str]:
    integer_fields = ("full_frame_transfers", *FULL_FRAME_SAMPLING_COUNTERS)
    if any(
        type(item.get(field)) is not int or item.get(field) < 0
        for field in integer_fields
    ):
        return f"{label} full-frame sampling counters are unavailable"
    if (
        item["full_frame_status_transfers"]
        + item["full_frame_write_only_transfers"]
        != item["full_frame_transfers"]
    ):
        return f"{label} full-frame sampling transfer invariant is broken"
    if item["full_frame_status_samples"] > item["full_frame_status_transfers"]:
        return f"{label} full-frame status samples exceed transfers"
    if (
        item["full_frame_status_samples"]
        + item["full_frame_status_sample_misses"]
        != item["full_frame_status_transfers"]
    ):
        return f"{label} full-frame status transfer classification is broken"
    current_gap = item.get("full_frame_frames_since_status_sample")
    maximum_gap = item.get("full_frame_max_status_sample_gap")
    if type(current_gap) is not int or type(maximum_gap) is not int:
        return f"{label} full-frame status sample gap is unavailable"
    if (
        current_gap < 0
        or current_gap > maximum_gap
        or maximum_gap > MAX_FULL_FRAME_STATUS_SAMPLE_GAP
    ):
        return f"{label} full-frame status sample gap is outside 0..256"
    buffer_size = item.get("spidev_buffer_size")
    if type(buffer_size) is not int or buffer_size < minimum_buffer_size:
        return f"{label} spidev buffer is below {minimum_buffer_size} bytes"
    if item.get("full_frame_write_only_supported") is not True:
        return f"{label} full-frame write-only fast path is unavailable"
    return None


def _full_frame_sampling_delta_rejection(
    before: Mapping[str, Any], after: Mapping[str, Any], *, label: str
) -> Optional[str]:
    total_delta = int(after["full_frame_transfers"]) - int(
        before["full_frame_transfers"]
    )
    deltas = {
        field: int(after[field]) - int(before[field])
        for field in FULL_FRAME_SAMPLING_COUNTERS
    }
    if total_delta < 0 or any(delta < 0 for delta in deltas.values()):
        return f"{label} full-frame sampling counter reset"
    if (
        deltas["full_frame_status_transfers"]
        + deltas["full_frame_write_only_transfers"]
        != total_delta
    ):
        return f"{label} full-frame sampling delta invariant is broken"
    if (
        deltas["full_frame_status_samples"]
        + deltas["full_frame_status_sample_misses"]
        != deltas["full_frame_status_transfers"]
    ):
        return f"{label} full-frame status transfer delta classification is broken"
    if total_delta > 0:
        if deltas["full_frame_status_samples"] <= 0:
            return f"{label} full-frame status samples did not advance"
        if deltas["full_frame_write_only_transfers"] <= 0:
            return f"{label} full-frame write-only transfers did not advance"
        if (
            deltas["full_frame_status_samples"]
            > deltas["full_frame_status_transfers"]
        ):
            return f"{label} full-frame status sample delta exceeds transfers"
        if deltas["full_frame_status_sample_misses"] != 0:
            return f"{label} full-frame status sample misses increased"
    if (
        int(after["full_frame_max_status_sample_gap"])
        < int(before["full_frame_max_status_sample_gap"])
    ):
        return f"{label} full-frame maximum status sample gap reset"
    return None


def _sample_health(root: Path, *, unit: str, api_url: str) -> TargetHealthSample:
    active = _command(("systemctl", "is-active", "--quiet", unit), check=False)
    if active.returncode:
        raise RuntimeError(f"systemd unit is not active: {unit}")
    sampled_at = time.time()
    status = _api_status(api_url)
    led_info = status.get("led_info") if isinstance(status.get("led_info"), dict) else {}
    driver = status.get("driver_stats") if isinstance(status.get("driver_stats"), dict) else {}
    aggregate = driver.get("aggregate") if isinstance(driver.get("aggregate"), dict) else {}
    raw_statuses = driver.get("devices")
    receiver_statuses = (
        tuple(dict(item) for item in raw_statuses if isinstance(item, dict))
        if isinstance(raw_statuses, list) else ()
    )
    service_release = _service_release(root, unit)
    api_release = status.get("release_id")
    if status.get("release_consistent") is not True or api_release != service_release:
        raise RuntimeError(
            "controller/web release identity is missing or inconsistent with systemd"
        )
    device_map = aggregate.get("device_map")
    if not isinstance(device_map, list):
        raise RuntimeError("controller status lacks the receiver device map")
    receiver_device_map = tuple(
        dict(entry) if isinstance(entry, dict) else {} for entry in device_map
    )
    logical_ids: list[int] = []
    for entry in receiver_device_map:
        logical_id = entry.get("logical_device") if isinstance(entry, dict) else None
        if isinstance(logical_id, bool) or not isinstance(logical_id, int):
            raise RuntimeError("controller receiver device map is malformed")
        logical_ids.append(logical_id)
    updated_at = status.get("updated_at") or status.get("timestamp")
    if (
        isinstance(updated_at, bool)
        or not isinstance(updated_at, (int, float))
        or not math.isfinite(float(updated_at))
        or any(type(value) is not int for value in (
            led_info.get("strip_count"),
            led_info.get("leds_per_strip"),
            aggregate.get("num_devices"),
            aggregate.get("transport_envelope_devices"),
        ))
    ):
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
        receiver_device_map=receiver_device_map,
        receiver_statuses=receiver_statuses,
        transport_envelope_devices=int(aggregate["transport_envelope_devices"]),
        receiver_aggregate=dict(aggregate),
    )


def _validate_receiver_health_contract(
    contract: Mapping[str, Any], *, receivers: int,
) -> ReceiverHealthContract:
    if contract.get("schema_version") != 1:
        raise ValueError("unsupported receiver health contract version")
    minimum_version = contract.get("minimum_status_version")
    required_capabilities = contract.get("required_capabilities")
    raw_devices = contract.get("devices")
    if (
        type(minimum_version) is not int
        or not 1 <= minimum_version <= 255
        or type(required_capabilities) is not int
        or not 0 <= required_capabilities <= 0xFFFFFFFF
        or not isinstance(raw_devices, list)
        or len(raw_devices) != receivers
    ):
        raise ValueError("receiver health contract is malformed")
    devices: list[Mapping[str, Any]] = []
    integer_fields = (
        "logical_device", "bus", "chip_select",
        "active_strips", "global_strip_offset",
        "local_strip_count", "lane_mask", "physical_output_lane_mask",
        "spi_mode", "leds_per_strip",
    )
    boolean_fields = (
        "reverse_host_strip_order", "reverse_native_strip_order",
    )
    for logical_id, item in enumerate(raw_devices):
        if not isinstance(item, dict) or any(
            type(item.get(field)) is not int for field in integer_fields
        ) or any(
            type(item.get(field)) is not bool for field in boolean_fields
        ):
            raise ValueError("receiver health device contract is malformed")
        if item["logical_device"] != logical_id:
            raise ValueError("receiver health contract logical roster is not exact")
        devices.append({
            field: item[field] for field in (*integer_fields, *boolean_fields)
        })
    return ReceiverHealthContract(
        minimum_status_version=minimum_version,
        required_capabilities=required_capabilities,
        devices=tuple(devices),
    )


def _receiver_health_rejection(
    sample: TargetHealthSample,
    *,
    minimum_version: int,
    required_capabilities: int,
    expected_devices: Sequence[Mapping[str, Any]],
) -> Optional[str]:
    if sample.transport_envelope_devices != len(expected_devices):
        return (
            "host aligned transport is enabled for "
            f"{sample.transport_envelope_devices} receivers; expected {len(expected_devices)}"
        )
    if len(sample.receiver_device_map) != len(expected_devices):
        return "host receiver device map is incomplete"
    host_by_id: dict[int, Mapping[str, Any]] = {}
    for item in sample.receiver_device_map:
        logical_id = item.get("logical_device")
        if type(logical_id) is not int or logical_id in host_by_id:
            return "host receiver device map identities are malformed"
        host_by_id[logical_id] = item
    if tuple(sorted(host_by_id)) != tuple(range(len(expected_devices))):
        return "host receiver device map roster is not exact"

    host_fields = (
        "bus", "chip_select", "local_strip_count", "global_strip_offset",
        "physical_output_lane_mask", "reverse_host_strip_order",
        "reverse_native_strip_order", "spi_mode",
    )
    for expected in expected_devices:
        logical_id = expected["logical_device"]
        observed = host_by_id[logical_id]
        for field in host_fields:
            value = observed.get(field)
            if type(value) is not type(expected[field]) or value != expected[field]:
                return (
                    f"host receiver {logical_id} reported {field}={value!r}, "
                    f"expected {expected[field]!r}"
                )

    if len(sample.receiver_statuses) != len(expected_devices):
        return "per-receiver status roster is incomplete"
    by_id: dict[int, Mapping[str, Any]] = {}
    for status in sample.receiver_statuses:
        logical_id = status.get("receiver_logical_device")
        if type(logical_id) is not int or logical_id in by_id:
            return "per-receiver reported logical identities are malformed"
        by_id[logical_id] = status
    if tuple(sorted(by_id)) != tuple(range(len(expected_devices))):
        return "per-receiver reported logical roster is not exact"

    field_mapping = {
        "active_strips": "receiver_active_strips",
        "global_strip_offset": "receiver_global_strip_offset",
        "lane_mask": "receiver_lane_mask",
        "leds_per_strip": "receiver_leds_per_strip",
    }
    for expected in expected_devices:
        logical_id = expected["logical_device"]
        status = by_id[logical_id]
        version = status.get("receiver_status_version")
        capabilities = status.get("receiver_capabilities")
        responses = status.get("receiver_status_responses")
        if status.get("receiver_status_seen") is not True:
            return f"receiver {logical_id} has no fresh status response"
        if type(responses) is not int or responses <= 0:
            return f"receiver {logical_id} status response evidence is not fresh"
        if type(version) is not int or version < minimum_version:
            return (
                f"receiver {logical_id} status v{version!r} is below required "
                f"v{minimum_version}"
            )
        if (
            type(capabilities) is not int
            or capabilities & required_capabilities != required_capabilities
        ):
            return f"receiver {logical_id} lacks required firmware capabilities"
        if status.get("transport_envelope_enabled") is not True:
            return f"receiver {logical_id} host aligned transport is not enabled"
        if (
            "transport_envelope_negotiation_candidate" not in status
            or status.get("transport_envelope_negotiation_candidate") is not None
            or type(status.get("transport_envelope_negotiation_streak")) is not int
            or status.get("transport_envelope_negotiation_streak") != 0
            or type(status.get("transport_envelope_negotiation_required")) is not int
            or status.get("transport_envelope_negotiation_required") != 3
        ):
            return f"receiver {logical_id} aligned transport negotiation is not settled"
        for expected_name, observed_name in field_mapping.items():
            if status.get(observed_name) != expected[expected_name]:
                return (
                    f"receiver {logical_id} reported {observed_name}="
                    f"{status.get(observed_name)!r}, expected {expected[expected_name]}"
                )
        sampling_rejection = _full_frame_sampling_snapshot_rejection(
            status,
            label=f"receiver {logical_id}",
            minimum_buffer_size=3320 if logical_id < 4 else 424,
        )
        if sampling_rejection is not None:
            return sampling_rejection
    transport_fields = (
        "spi_transfers", "bytes_sent", "semantic_bytes_sent",
        "transport_envelope_bytes_sent", "transport_padding_bytes_sent",
        "crc_bytes_sent", "full_frame_transfers",
        "full_frame_semantic_bytes_sent", "full_frame_wire_bytes_sent",
        *FULL_FRAME_SAMPLING_COUNTERS,
    )
    for field in transport_fields:
        values = [status.get(field) for status in sample.receiver_statuses]
        if any(type(value) is not int or value < 0 for value in values):
            return f"per-receiver aligned transport counter {field} is unavailable"
        aggregate_value = sample.receiver_aggregate.get(field)
        if type(aggregate_value) is not int or aggregate_value < 0:
            return f"aggregate aligned transport counter {field} is unavailable"
        if aggregate_value != sum(values):
            return f"aggregate {field} drifted from per-receiver total"
    aggregate_sampling_rejection = _full_frame_sampling_snapshot_rejection(
        sample.receiver_aggregate,
        label="aggregate",
        minimum_buffer_size=3320,
    )
    if aggregate_sampling_rejection is not None:
        return aggregate_sampling_rejection
    gauge_expectations = {
        "full_frame_frames_since_status_sample": max(
            int(status["full_frame_frames_since_status_sample"])
            for status in sample.receiver_statuses
        ),
        "full_frame_max_status_sample_gap": max(
            int(status["full_frame_max_status_sample_gap"])
            for status in sample.receiver_statuses
        ),
        "spidev_buffer_size": min(
            int(status["spidev_buffer_size"])
            for status in sample.receiver_statuses
        ),
        "full_frame_write_only_supported": all(
            status.get("full_frame_write_only_supported") is True
            for status in sample.receiver_statuses
        ),
    }
    for field, expected in gauge_expectations.items():
        if sample.receiver_aggregate.get(field) != expected:
            return f"aggregate {field} drifted from per-receiver value"
    return None


def _transport_accounting_delta_rejection(
    before: TargetHealthSample, after: TargetHealthSample
) -> Optional[str]:
    fields = (
        "spi_transfers", "bytes_sent", "semantic_bytes_sent",
        "transport_envelope_bytes_sent", "transport_padding_bytes_sent",
        "crc_bytes_sent",
    )

    def rejection(
        first: Mapping[str, Any], last: Mapping[str, Any], label: str
    ) -> Optional[str]:
        deltas = {field: int(last[field]) - int(first[field]) for field in fields}
        for field, delta in deltas.items():
            if delta <= 0:
                return f"{label} {field} did not advance"
        transfers = deltas["spi_transfers"]
        if deltas["transport_envelope_bytes_sent"] != 4 * transfers:
            return f"{label} envelope accounting is inconsistent"
        if deltas["crc_bytes_sent"] != 2 * transfers:
            return f"{label} CRC accounting is inconsistent"
        if deltas["bytes_sent"] != (
            deltas["semantic_bytes_sent"]
            + deltas["transport_envelope_bytes_sent"]
            + deltas["transport_padding_bytes_sent"]
            + deltas["crc_bytes_sent"]
        ):
            return f"{label} wire-byte accounting is inconsistent"
        return None

    aggregate_rejection = rejection(
        before.receiver_aggregate,
        after.receiver_aggregate,
        "aggregate aligned transport",
    )
    if aggregate_rejection is not None:
        return aggregate_rejection
    aggregate_sampling_rejection = _full_frame_sampling_delta_rejection(
        before.receiver_aggregate,
        after.receiver_aggregate,
        label="aggregate aligned transport",
    )
    if aggregate_sampling_rejection is not None:
        return aggregate_sampling_rejection
    before_by_id = {
        int(item["receiver_logical_device"]): item for item in before.receiver_statuses
    }
    after_by_id = {
        int(item["receiver_logical_device"]): item for item in after.receiver_statuses
    }
    for logical_id in sorted(after_by_id):
        device_rejection = rejection(
            before_by_id[logical_id],
            after_by_id[logical_id],
            f"receiver {logical_id} aligned transport",
        )
        if device_rejection is not None:
            return device_rejection
        device_sampling_rejection = _full_frame_sampling_delta_rejection(
            before_by_id[logical_id],
            after_by_id[logical_id],
            label=f"receiver {logical_id} aligned transport",
        )
        if device_sampling_rejection is not None:
            return device_sampling_rejection
    return None


def _transport_accounting_evidence(
    before: TargetHealthSample, after: TargetHealthSample
) -> Mapping[str, Any]:
    fields = (
        "spi_transfers", "bytes_sent", "semantic_bytes_sent",
        "transport_envelope_bytes_sent", "transport_padding_bytes_sent",
        "crc_bytes_sent", "full_frame_transfers",
        "full_frame_semantic_bytes_sent", "full_frame_wire_bytes_sent",
        *FULL_FRAME_SAMPLING_COUNTERS,
    )

    def counters(
        first: Mapping[str, Any], last: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return {
            field: {
                "before": int(first[field]),
                "after": int(last[field]),
                "delta": int(last[field]) - int(first[field]),
            }
            for field in fields
        }

    def sampling_state(item: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            field: item[field] for field in FULL_FRAME_SAMPLING_STATE_FIELDS
        }

    before_by_id = {
        int(item["receiver_logical_device"]): item for item in before.receiver_statuses
    }
    after_by_id = {
        int(item["receiver_logical_device"]): item for item in after.receiver_statuses
    }
    full_frame_traffic_proven = True
    full_frame_sampling_proven = True
    for logical_id, item in after_by_id.items():
        prior = before_by_id[logical_id]
        transfers = int(item["full_frame_transfers"]) - int(
            prior["full_frame_transfers"]
        )
        semantic_bytes = int(item["full_frame_semantic_bytes_sent"]) - int(
            prior["full_frame_semantic_bytes_sent"]
        )
        wire_bytes = int(item["full_frame_wire_bytes_sent"]) - int(
            prior["full_frame_wire_bytes_sent"]
        )
        expected_semantic = (
            1
            + int(item["receiver_active_strips"])
            * int(item["receiver_leds_per_strip"])
            * 3
        )
        expected_wire = ((expected_semantic + 9) // 4) * 4
        full_frame_traffic_proven &= (
            transfers > 0
            and semantic_bytes == expected_semantic * transfers
            and wire_bytes == expected_wire * transfers
        )
        status_transfers = int(item["full_frame_status_transfers"]) - int(
            prior["full_frame_status_transfers"]
        )
        samples = int(item["full_frame_status_samples"]) - int(
            prior["full_frame_status_samples"]
        )
        misses = int(item["full_frame_status_sample_misses"]) - int(
            prior["full_frame_status_sample_misses"]
        )
        write_only = int(item["full_frame_write_only_transfers"]) - int(
            prior["full_frame_write_only_transfers"]
        )
        full_frame_sampling_proven &= (
            transfers > 0
            and status_transfers == samples + misses
            and samples > 0
            and misses == 0
            and write_only > 0
        )
    return {
        "enabled_devices": after.transport_envelope_devices,
        "negotiation_required": 3,
        "negotiation_settled": all(
            item.get("transport_envelope_enabled") is True
            and item.get("transport_envelope_negotiation_candidate") is None
            and item.get("transport_envelope_negotiation_streak") == 0
            for item in after.receiver_statuses
        ),
        "full_frame_traffic_proven": full_frame_traffic_proven,
        "full_frame_sampling_proven": (
            full_frame_traffic_proven and full_frame_sampling_proven
        ),
        "aggregate": counters(
            before.receiver_aggregate, after.receiver_aggregate
        ),
        "aggregate_sampling_state": {
            "before": sampling_state(before.receiver_aggregate),
            "after": sampling_state(after.receiver_aggregate),
        },
        "devices": [
            {
                "logical_device": logical_id,
                "counters": counters(
                    before_by_id[logical_id], after_by_id[logical_id]
                ),
                "state_before": sampling_state(before_by_id[logical_id]),
                "state_after": sampling_state(after_by_id[logical_id]),
            }
            for logical_id in sorted(after_by_id)
        ],
    }


def _receiver_response_counters(
    sample: TargetHealthSample,
) -> dict[int, int]:
    return {
        int(status["receiver_logical_device"]): int(
            status["receiver_status_responses"]
        )
        for status in sample.receiver_statuses
    }


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
    receiver_contract: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    if stable_samples < 1:
        raise ValueError("stable_samples must be positive")
    contract = None
    if receiver_contract is not None:
        contract = _validate_receiver_health_contract(
            receiver_contract, receivers=receivers
        )
    deadline = time.monotonic() + timeout
    accepted: list[TargetHealthSample] = []
    last_reason = "no health sample"
    while time.monotonic() <= deadline:
        try:
            if contract is not None:
                _request_receiver_status_refresh(api_url)
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
            elif contract is not None and (
                receiver_rejection := _receiver_health_rejection(
                    sample,
                    minimum_version=contract.minimum_status_version,
                    required_capabilities=contract.required_capabilities,
                    expected_devices=contract.devices,
                )
            ) is not None:
                rejection = receiver_rejection
            elif accepted and sample.controller_updated_at <= accepted[-1].controller_updated_at:
                rejection = "controller status did not advance between stable samples"
            elif contract is not None and accepted:
                previous_responses = _receiver_response_counters(accepted[-1])
                current_responses = _receiver_response_counters(sample)
                stale_ids = [
                    logical_id for logical_id in range(receivers)
                    if current_responses[logical_id] <= previous_responses[logical_id]
                ]
                if stale_ids:
                    rejection = (
                        "receiver status responses did not advance for logical "
                        f"devices {stale_ids}"
                    )
                elif (
                    transport_rejection := _transport_accounting_delta_rejection(
                        accepted[-1], sample
                    )
                ) is not None:
                    rejection = transport_rejection

            if rejection is not None:
                accepted.clear()
                last_reason = rejection
            else:
                accepted.append(sample)
                required_samples = max(stable_samples, 2 if contract is not None else 1)
                if len(accepted) >= required_samples:
                    health = {
                        "desired_release": release_id,
                        "observed_release": sample.release_id,
                        "stable_samples": len(accepted),
                        "last_controller_updated_at": sample.controller_updated_at,
                        "geometry": {"strip_count": strips, "leds_per_strip": leds_per_strip},
                        "receiver_count": receivers,
                    }
                    if contract is not None:
                        first_responses = _receiver_response_counters(accepted[0])
                        last_responses = _receiver_response_counters(sample)
                        health["receiver_contract"] = {
                            "minimum_status_version": contract.minimum_status_version,
                            "required_capabilities": contract.required_capabilities,
                            "verified_logical_devices": list(range(receivers)),
                            "status_response_evidence": [
                                {
                                    "logical_device": logical_id,
                                    "before": first_responses[logical_id],
                                    "after": last_responses[logical_id],
                                }
                                for logical_id in range(receivers)
                            ],
                            "aligned_transport_evidence": (
                                _transport_accounting_evidence(
                                    accepted[0], sample
                                )
                            ),
                        }
                    return health
        except Exception as exc:
            accepted.clear()
            last_reason = str(exc)
        time.sleep(
            STRICT_RECEIVER_HEALTH_POLL_SECONDS
            if contract is not None else 0.25
        )
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
    bootstrap = subparsers.add_parser("bootstrap-legacy-app")
    bootstrap.add_argument("candidate_release_id")
    bootstrap.add_argument("--unit", default=DEFAULT_SYSTEMD_UNIT)
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
    provision_parser.add_argument("--strips", type=int, default=33)
    provision_parser.add_argument("--receivers", type=int, default=5)
    subparsers.add_parser("migrate-receiver-topology")
    build = subparsers.add_parser("build-firmware")
    build.add_argument("support_id", nargs="?")
    flash = subparsers.add_parser("flash-firmware")
    flash.add_argument("support_id", nargs="?")
    flash.add_argument("--expected-environment")
    flash.add_argument("--expected-config-digest")
    flash.add_argument("--expected-installation-digest")
    flash.add_argument("--force", action="store_true")
    subparsers.add_parser("capture-state")
    activate_parser = subparsers.add_parser("activate")
    activate_parser.add_argument("release_id")
    complete_bootstrap = subparsers.add_parser("complete-legacy-bootstrap")
    complete_bootstrap.add_argument("candidate_release_id")
    subparsers.add_parser("restart")
    restore = subparsers.add_parser("restore-state")
    restore.add_argument("--timeout", type=float, default=20.0)
    health = subparsers.add_parser("health")
    health.add_argument("release_id")
    health.add_argument("--boundary", type=float, required=True)
    health.add_argument("--strips", type=int, default=33)
    health.add_argument("--leds-per-strip", type=int, default=138)
    health.add_argument("--receivers", type=int, default=5)
    health.add_argument("--stable-samples", type=int, default=2)
    health.add_argument("--timeout", type=float, default=30.0)
    health.add_argument("--unit", default=DEFAULT_SYSTEMD_UNIT)
    health.add_argument("--api-url", default=DEFAULT_API_URL)
    health.add_argument("--receiver-contract-json")
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
    elif args.command == "bootstrap-legacy-app":
        result = bootstrap_legacy_app(
            root, args.candidate_release_id, unit=args.unit
        )
    elif args.command == "stage-support":
        result = stage_support(root, args.snapshot)
    elif args.command == "cleanup-snapshot":
        result = cleanup_snapshot(root, args.snapshot)
    elif args.command == "validate-app":
        result = validate_app(root, args.release_id)
    elif args.command == "provision":
        result = provision(
            root,
            args.release_id,
            user=args.user,
            hat=args.hat,
            strips=args.strips,
            receivers=args.receivers,
        )
    elif args.command == "migrate-receiver-topology":
        result = migrate_receiver_topology(root)
    elif args.command == "build-firmware":
        result = build_firmware(root, args.support_id)
    elif args.command == "flash-firmware":
        result = flash_firmware(
            root,
            args.support_id,
            expected_firmware_environment=args.expected_environment,
            expected_config_digest=args.expected_config_digest,
            expected_installation_digest=args.expected_installation_digest,
            force=args.force,
        )
    elif args.command == "capture-state":
        result = capture_state(root)
    elif args.command == "activate":
        result = activate(root, args.release_id)
    elif args.command == "complete-legacy-bootstrap":
        result = complete_legacy_bootstrap(root, args.candidate_release_id)
    elif args.command == "restart":
        result = restart_service()
    elif args.command == "restore-state":
        result = restore_state(root, timeout=args.timeout)
    elif args.command == "health":
        receiver_contract = None
        if args.receiver_contract_json is not None:
            try:
                receiver_contract = json.loads(args.receiver_contract_json)
            except json.JSONDecodeError as exc:
                raise ValueError("receiver health contract is not valid JSON") from exc
            if not isinstance(receiver_contract, dict):
                raise ValueError("receiver health contract must be an object")
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
            receiver_contract=receiver_contract,
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
