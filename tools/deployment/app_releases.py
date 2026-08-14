#!/usr/bin/env python3
"""Immutable application release staging, activation and app-only rollback."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from tools.deployment.deploy_coordinator import (
        Operation,
        OperationResult,
        ROLLBACK_STEP_ORDER,
        Step,
        build_steps,
    )
except ModuleNotFoundError:  # Direct ``python tools/deployment/app_releases.py``.
    from deploy_coordinator import (  # type: ignore[no-redef]
        Operation,
        OperationResult,
        ROLLBACK_STEP_ORDER,
        Step,
        build_steps,
    )


RELEASE_METADATA = ".release.json"
DEFAULT_SHARED_PATHS: Mapping[PurePosixPath, PurePosixPath] = {
    PurePosixPath("venv"): PurePosixPath("venv"),
    PurePosixPath("presets"): PurePosixPath("presets"),
    PurePosixPath("run_state"): PurePosixPath("run_state"),
    PurePosixPath("logs"): PurePosixPath("logs"),
    PurePosixPath("calibration_photos"): PurePosixPath("calibration_photos"),
    PurePosixPath("firmware"): PurePosixPath("firmware"),
    PurePosixPath("receiver_library"): PurePosixPath("receiver_library"),
}
DEFAULT_SHARED_FILES: Mapping[PurePosixPath, PurePosixPath] = {
    PurePosixPath("web.log"): PurePosixPath("logs/web.log"),
    PurePosixPath("controller.log"): PurePosixPath("logs/controller.log"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_relative_path(value: os.PathLike[str] | str) -> PurePosixPath:
    path = PurePosixPath(os.fspath(value))
    if not path.parts or path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        raise ValueError(f"unsafe release path: {value!r}")
    if path.name == RELEASE_METADATA:
        raise ValueError(f"release path is reserved: {value!r}")
    return path


def _is_beneath(path: PurePosixPath, parent: PurePosixPath) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ReleaseFile:
    path: str
    digest: str
    size: int
    executable: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "digest": self.digest,
            "size": self.size,
            "executable": self.executable,
        }


@dataclass(frozen=True)
class ReleaseInfo:
    id: str
    digest: str
    path: Path
    created_at: str
    files: Tuple[ReleaseFile, ...]
    shared_links: Mapping[str, str]
    active: bool = False
    reused: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "digest": self.digest,
            "path": os.fspath(self.path),
            "created_at": self.created_at,
            "file_count": len(self.files),
            "files": [item.to_dict() for item in self.files],
            "shared_links": dict(self.shared_links),
            "active": self.active,
            "reused": self.reused,
        }


class ReleaseValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActivationFailure:
    candidate_release: str
    previous_release: Optional[str]
    candidate_error: str
    restored: bool
    restoration_health: Optional[Mapping[str, Any]] = None
    restoration_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_release": self.candidate_release,
            "previous_release": self.previous_release,
            "candidate_error": self.candidate_error,
            "restored": self.restored,
            "restoration_health": self.restoration_health,
            "restoration_error": self.restoration_error,
        }


class CandidateHealthFailed(RuntimeError):
    def __init__(self, failure: ActivationFailure) -> None:
        self.failure = failure
        message = f"candidate release {failure.candidate_release} failed health: {failure.candidate_error}"
        if failure.restored:
            message += f"; restored {failure.previous_release}"
        elif failure.restoration_error:
            message += f"; restoration failed: {failure.restoration_error}"
        super().__init__(message)


Validator = Callable[[Path], None]
Restart = Callable[[str], float]
RestoreSettings = Callable[[], None]
HealthCheck = Callable[[str, float], Mapping[str, Any]]


class AppReleaseManager:
    """Manage one app lane below a deployment root.

    Application files are copied into a new digest directory.  Target-owned
    state is represented only by relative symlinks back outside ``releases``.
    """

    def __init__(
        self,
        root: Path,
        *,
        releases_name: str = "releases",
        current_name: str = "current",
        shared_paths: Optional[Mapping[PurePosixPath, PurePosixPath]] = None,
        shared_files: Optional[Mapping[PurePosixPath, PurePosixPath]] = None,
    ) -> None:
        self.root = root.resolve()
        self.releases_dir = self.root / releases_name
        self.current_path = self.root / current_name
        self.shared_paths = dict(DEFAULT_SHARED_PATHS if shared_paths is None else shared_paths)
        self.shared_files = dict(DEFAULT_SHARED_FILES if shared_files is None else shared_files)
        for release_path, target_path in (*self.shared_paths.items(), *self.shared_files.items()):
            _safe_relative_path(release_path)
            _safe_relative_path(target_path)
        self._protected_roots = tuple(self.shared_paths)
        self._protected_files = frozenset(self.shared_files)

    def stage(
        self,
        files: Mapping[os.PathLike[str] | str, Path],
        *,
        validators: Iterable[Validator] = (),
    ) -> ReleaseInfo:
        normalized = self._normalize_sources(files)
        release_files, digest = self._describe_sources(normalized)
        release_id = digest
        destination = self.releases_dir / release_id
        self.releases_dir.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            info = self.validate(release_id, validators=validators)
            return ReleaseInfo(**{**info.__dict__, "reused": True})

        temporary = Path(tempfile.mkdtemp(prefix=f".{release_id}.", dir=self.releases_dir))
        try:
            for relative, source in normalized:
                target = temporary / relative.as_posix()
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                source_mode = source.stat().st_mode
                target.chmod(0o555 if source_mode & 0o111 else 0o444)

            for item in release_files:
                copied = temporary / item.path
                if copied.stat().st_size != item.size or _sha256_file(copied) != item.digest:
                    raise ReleaseValidationError(
                        f"release input changed while staging: {item.path}",
                    )

            shared_links = self._create_shared_links(temporary)
            metadata = {
                "schema_version": 1,
                "id": release_id,
                "digest": digest,
                "created_at": _utc_now(),
                "files": [item.to_dict() for item in release_files],
                "shared_links": shared_links,
            }
            metadata_path = temporary / RELEASE_METADATA
            metadata_path.write_text(
                json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            metadata_path.chmod(0o444)
            self._make_tree_immutable(temporary)

            for validator in validators:
                validator(temporary)
            try:
                temporary.rename(destination)
            except FileExistsError:
                # An identical concurrent stage won the race.
                pass
            _fsync_directory(self.releases_dir)
        finally:
            if temporary.exists():
                self._remove_staging_tree(temporary)

        return self.validate(release_id, validators=validators)

    def _normalize_sources(
        self, files: Mapping[os.PathLike[str] | str, Path],
    ) -> List[Tuple[PurePosixPath, Path]]:
        if not files:
            raise ValueError("an app release cannot be empty")
        normalized: List[Tuple[PurePosixPath, Path]] = []
        seen: set[PurePosixPath] = set()
        for raw_relative, raw_source in files.items():
            relative = _safe_relative_path(raw_relative)
            if relative in seen:
                raise ValueError(f"duplicate release path: {relative}")
            if relative in self._protected_files or any(
                _is_beneath(relative, root) for root in self._protected_roots
            ):
                raise ValueError(f"target-owned path cannot enter an app release: {relative}")
            source = Path(raw_source)
            source_stat = source.lstat()
            if not stat.S_ISREG(source_stat.st_mode):
                raise ValueError(f"release input must be a regular file: {source}")
            seen.add(relative)
            normalized.append((relative, source))
        return sorted(normalized, key=lambda item: item[0].as_posix())

    def _describe_sources(
        self, normalized: Sequence[Tuple[PurePosixPath, Path]],
    ) -> Tuple[Tuple[ReleaseFile, ...], str]:
        files: List[ReleaseFile] = []
        for relative, source in normalized:
            executable = bool(source.stat().st_mode & 0o111)
            file_digest = _sha256_file(source)
            item = ReleaseFile(
                path=relative.as_posix(),
                digest=file_digest,
                size=source.stat().st_size,
                executable=executable,
            )
            files.append(item)
        links = {**self.shared_paths, **self.shared_files}
        return tuple(files), self._digest_descriptions(files, links)

    @staticmethod
    def _digest_descriptions(
        files: Iterable[ReleaseFile],
        links: Mapping[PurePosixPath, PurePosixPath],
    ) -> str:
        release_digest = hashlib.sha256()
        release_digest.update(b"ledgrid-app-release-v1\0")
        for item in sorted(files, key=lambda candidate: candidate.path):
            release_digest.update(item.path.encode("utf-8"))
            release_digest.update(b"\0")
            release_digest.update(item.digest.encode("ascii"))
            release_digest.update(b"\0x\0" if item.executable else b"\0-\0")
        for release_path, target_path in sorted(
            links.items(),
            key=lambda item: item[0].as_posix(),
        ):
            release_digest.update(b"link\0")
            release_digest.update(release_path.as_posix().encode("utf-8"))
            release_digest.update(b"\0")
            release_digest.update(target_path.as_posix().encode("utf-8"))
            release_digest.update(b"\0")
        return release_digest.hexdigest()

    def _create_shared_links(self, release_root: Path) -> Mapping[str, str]:
        links: Dict[str, str] = {}
        for release_path, target_path in (*self.shared_paths.items(), *self.shared_files.items()):
            link = release_root / release_path.as_posix()
            link.parent.mkdir(parents=True, exist_ok=True)
            shared_target = self.root / target_path.as_posix()
            shared_target.parent.mkdir(parents=True, exist_ok=True)
            if release_path in self.shared_paths:
                shared_target.mkdir(parents=True, exist_ok=True)
            relative_target = os.path.relpath(shared_target, start=link.parent)
            link.symlink_to(relative_target)
            links[release_path.as_posix()] = target_path.as_posix()
        return links

    @staticmethod
    def _make_tree_immutable(root: Path) -> None:
        directories: List[Path] = []
        for path in root.rglob("*"):
            if path.is_symlink():
                continue
            if path.is_dir():
                directories.append(path)
            elif path.is_file():
                path.chmod(path.stat().st_mode & ~0o222)
        for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            directory.chmod(0o555)
        root.chmod(0o555)

    @staticmethod
    def _remove_staging_tree(root: Path) -> None:
        for path in root.rglob("*"):
            if not path.is_symlink() and path.is_dir():
                path.chmod(0o755)
        root.chmod(0o755)
        shutil.rmtree(root)

    def validate(
        self,
        release_id: str,
        *,
        validators: Iterable[Validator] = (),
    ) -> ReleaseInfo:
        release = self._release_path(release_id)
        metadata_path = release / RELEASE_METADATA
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseValidationError(f"invalid release metadata for {release_id}: {exc}") from exc
        if not isinstance(metadata, dict):
            raise ReleaseValidationError("release metadata must be an object")
        if metadata.get("id") != release_id or metadata.get("digest") != release_id:
            raise ReleaseValidationError("release identity does not match directory")

        raw_files = metadata.get("files")
        raw_links = metadata.get("shared_links")
        if not isinstance(raw_files, list):
            raise ReleaseValidationError("release files metadata must be a list")
        if not isinstance(raw_links, dict) or not all(
            isinstance(path, str) and isinstance(target, str)
            for path, target in raw_links.items()
        ):
            raise ReleaseValidationError("release shared links metadata must be an object of strings")

        expected_paths: set[PurePosixPath] = set()
        files: List[ReleaseFile] = []
        for raw_item in raw_files:
            if not isinstance(raw_item, dict):
                raise ReleaseValidationError("release file metadata is malformed")
            if not isinstance(raw_item.get("path"), str):
                raise ReleaseValidationError("release file path metadata is malformed")
            if not isinstance(raw_item.get("digest"), str) or not re.fullmatch(
                r"[0-9a-f]{64}", raw_item["digest"],
            ):
                raise ReleaseValidationError("release file digest metadata is malformed")
            if (
                isinstance(raw_item.get("size"), bool)
                or not isinstance(raw_item.get("size"), int)
                or raw_item["size"] < 0
                or not isinstance(raw_item.get("executable"), bool)
            ):
                raise ReleaseValidationError("release file size/mode metadata is malformed")
            relative = _safe_relative_path(raw_item["path"])
            if relative in expected_paths:
                raise ReleaseValidationError(f"duplicate release file metadata: {relative}")
            expected_paths.add(relative)
            path = release / relative.as_posix()
            if path.is_symlink() or not path.is_file():
                raise ReleaseValidationError(f"release file is missing or not regular: {relative}")
            digest = _sha256_file(path)
            if digest != raw_item.get("digest") or path.stat().st_size != raw_item.get("size"):
                raise ReleaseValidationError(f"release file digest/size mismatch: {relative}")
            executable = bool(path.stat().st_mode & 0o111)
            if executable != bool(raw_item.get("executable")):
                raise ReleaseValidationError(f"release file mode mismatch: {relative}")
            if path.stat().st_mode & 0o222:
                raise ReleaseValidationError(f"release file is writable: {relative}")
            files.append(
                ReleaseFile(relative.as_posix(), digest, path.stat().st_size, executable),
            )

        expected_links = {
            _safe_relative_path(path): _safe_relative_path(target)
            for path, target in raw_links.items()
        }
        for relative, target in expected_links.items():
            link = release / relative.as_posix()
            if not link.is_symlink():
                raise ReleaseValidationError(f"shared-state link is missing: {relative}")
            resolved = (link.parent / os.readlink(link)).resolve()
            if resolved != (self.root / target.as_posix()).resolve():
                raise ReleaseValidationError(f"shared-state link escapes its declared target: {relative}")

        actual_paths = {
            PurePosixPath(path.relative_to(release).as_posix())
            for path in release.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        allowed = expected_paths | set(expected_links) | {PurePosixPath(RELEASE_METADATA)}
        unexpected = sorted((path.as_posix() for path in actual_paths - allowed))
        if unexpected:
            raise ReleaseValidationError(f"unexpected paths in immutable release: {unexpected}")
        if set(expected_links) != set(self.shared_paths) | set(self.shared_files):
            raise ReleaseValidationError("release shared-state contract does not match manager")
        recomputed = self._digest_descriptions(files, expected_links)
        if recomputed != release_id:
            raise ReleaseValidationError(
                f"release content digest mismatch: expected {release_id}, computed {recomputed}",
            )
        if release.stat().st_mode & 0o222:
            raise ReleaseValidationError("release directory is writable")

        for validator in validators:
            validator(release)
        current = self.current_release_id()
        return ReleaseInfo(
            id=release_id,
            digest=release_id,
            path=release,
            created_at=str(metadata.get("created_at", "")),
            files=tuple(files),
            shared_links={path.as_posix(): target.as_posix() for path, target in expected_links.items()},
            active=current == release_id,
        )

    def _release_path(self, release_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", release_id):
            raise ValueError(f"invalid release ID: {release_id!r}")
        path = self.releases_dir / release_id
        if not path.is_dir() or path.is_symlink():
            raise FileNotFoundError(f"unknown release: {release_id}")
        return path

    def current_release_id(self) -> Optional[str]:
        if not self.current_path.is_symlink():
            return None
        resolved = self.current_path.resolve(strict=False)
        try:
            relative = resolved.relative_to(self.releases_dir.resolve())
        except ValueError as exc:
            raise ReleaseValidationError("current symlink points outside releases") from exc
        if len(relative.parts) != 1:
            raise ReleaseValidationError("current symlink does not point to one release")
        return relative.name

    def activate(self, release_id: str, *, validators: Iterable[Validator] = ()) -> Optional[str]:
        self.validate(release_id, validators=validators)
        previous = self.current_release_id()
        temporary = self.root / f".current.{os.getpid()}.{time.time_ns()}"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            temporary.symlink_to(Path("releases") / release_id)
            os.replace(temporary, self.current_path)
            _fsync_directory(self.root)
        finally:
            temporary.unlink(missing_ok=True)
        return previous

    def list(self) -> List[ReleaseInfo]:
        if not self.releases_dir.exists():
            return []
        releases: List[ReleaseInfo] = []
        for path in self.releases_dir.iterdir():
            if path.is_dir() and not path.is_symlink() and not path.name.startswith("."):
                releases.append(self.validate(path.name))
        return sorted(releases, key=lambda info: (info.created_at, info.id), reverse=True)

    def prune(self, *, retain: int) -> Tuple[str, ...]:
        """Remove old, well-formed releases while preserving rollback safety.

        Retention is a total-release ceiling, including ``current``.  Selection
        uses immutable metadata only; pruning must not re-hash every historical
        release merely to decide which old directories can be removed.  An
        unrecognized or malformed directory is left untouched for diagnosis.
        """
        if isinstance(retain, bool) or retain < 2:
            raise ValueError("release retention must preserve at least two releases")
        if not self.releases_dir.exists():
            return ()

        current = self.current_release_id()
        candidates: List[Tuple[str, str, Path]] = []
        for path in self.releases_dir.iterdir():
            if (
                path.is_symlink()
                or not path.is_dir()
                or re.fullmatch(r"[0-9a-f]{64}", path.name) is None
            ):
                continue
            try:
                metadata = json.loads((path / RELEASE_METADATA).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(metadata, dict):
                continue
            created_at = metadata.get("created_at")
            if (
                metadata.get("id") != path.name
                or metadata.get("digest") != path.name
                or not isinstance(created_at, str)
                or not created_at
            ):
                continue
            candidates.append((created_at, path.name, path))

        candidates.sort(reverse=True)
        keep = {release_id for _created_at, release_id, _path in candidates[:retain]}
        if current is not None and current not in keep:
            keep.add(current)
            for _created_at, release_id, _path in reversed(candidates):
                if release_id != current and release_id in keep:
                    keep.remove(release_id)
                    break

        removed: List[str] = []
        for _created_at, release_id, path in reversed(candidates):
            if release_id in keep:
                continue
            self._remove_staging_tree(path)
            removed.append(release_id)
        if removed:
            _fsync_directory(self.releases_dir)
        return tuple(removed)

    def rollback_target(self, requested_release: Optional[str] = None) -> str:
        current = self.current_release_id()
        if requested_release is not None:
            if requested_release == current:
                raise ValueError(f"release is already active: {requested_release}")
            self.validate(requested_release)
            return requested_release
        for info in self.list():
            if info.id != current:
                return info.id
        raise ReleaseValidationError("no previous app release is available")


@dataclass
class AppActivation:
    """Coordinator operations for one staged app activation transaction."""

    manager: AppReleaseManager
    candidate_release: str
    restart: Restart
    health_check: HealthCheck
    restore_settings: RestoreSettings = lambda: None
    validators: Tuple[Validator, ...] = ()
    release_retention: int = 5
    previous_release: Optional[str] = field(default=None, init=False)
    restart_boundary: Optional[float] = field(default=None, init=False)

    def validate_operation(self, _context: Any) -> OperationResult:
        info = self.manager.validate(self.candidate_release, validators=self.validators)
        return OperationResult(details={"release": info.id, "digest": info.digest})

    def activate_operation(self, _context: Any) -> OperationResult:
        self.previous_release = self.manager.activate(
            self.candidate_release, validators=self.validators,
        )
        return OperationResult(
            details={
                "candidate_release": self.candidate_release,
                "previous_release": self.previous_release,
            },
        )

    def restart_operation(self, _context: Any) -> OperationResult:
        self.restart_boundary = self.restart(self.candidate_release)
        return OperationResult(
            details={
                "release": self.candidate_release,
                "restart_started_at": self.restart_boundary,
            },
        )

    def restore_settings_operation(self, _context: Any) -> OperationResult:
        self.restore_settings()
        return OperationResult(details={"release": self.candidate_release})

    def readiness_operation(self, _context: Any) -> OperationResult:
        if self.restart_boundary is None:
            raise RuntimeError("candidate restart was not recorded")
        try:
            health = self.health_check(self.candidate_release, self.restart_boundary)
        except Exception as candidate_error:
            failure = self._restore_after_failure(candidate_error)
            raise CandidateHealthFailed(failure) from candidate_error
        return OperationResult(details=dict(health))

    def prune_operation(self, _context: Any) -> OperationResult:
        removed = self.manager.prune(retain=self.release_retention)
        return OperationResult(
            outcome="executed" if removed else "skipped",
            details={
                "retain": self.release_retention,
                "removed_releases": list(removed),
            },
        )

    def _restore_after_failure(self, candidate_error: Exception) -> ActivationFailure:
        if self.previous_release is None:
            return ActivationFailure(
                candidate_release=self.candidate_release,
                previous_release=None,
                candidate_error=str(candidate_error),
                restored=False,
                restoration_error="no previous app release exists",
            )
        try:
            self.manager.activate(self.previous_release, validators=self.validators)
            restart_boundary = self.restart(self.previous_release)
            self.restore_settings()
            health = self.health_check(self.previous_release, restart_boundary)
        except Exception as restoration_error:
            return ActivationFailure(
                candidate_release=self.candidate_release,
                previous_release=self.previous_release,
                candidate_error=str(candidate_error),
                restored=False,
                restoration_error=str(restoration_error),
            )
        return ActivationFailure(
            candidate_release=self.candidate_release,
            previous_release=self.previous_release,
            candidate_error=str(candidate_error),
            restored=True,
            restoration_health=dict(health),
        )

    def operations(self) -> Mapping[str, Callable[[Any], OperationResult]]:
        return {
            "app.validate": self.validate_operation,
            "app.activate": self.activate_operation,
            "host.restart": self.restart_operation,
            "state.restore": self.restore_settings_operation,
            "health.readiness": self.readiness_operation,
            "release.prune": self.prune_operation,
        }


def build_app_rollback_steps(
    activation: AppActivation,
    *,
    capture_settings: Operation,
    validate_request: Optional[Operation] = None,
) -> List[Step]:
    """Build the complete app-only rollback transaction.

    This is the only supported rollback construction path. It cannot acquire
    provisioning, build, reboot or receiver-flash operations because those IDs
    are absent from the rollback policy.
    """
    operations: Dict[str, Operation] = dict(activation.operations())
    operations["source.validate"] = validate_request or (
        lambda _context: OperationResult(
            details={"requested_release": activation.candidate_release},
        )
    )
    operations["state.capture"] = capture_settings
    return build_steps("rollback", operations)


def _files_from_manifest(root: Path, manifest: Path) -> Mapping[str, Path]:
    files: Dict[str, Path] = {}
    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        relative = _safe_relative_path(line)
        files[relative.as_posix()] = root / relative.as_posix()
    return files


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="target app root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage_parser = subparsers.add_parser("stage", help="stage explicit manifest files")
    stage_parser.add_argument("--source-root", type=Path, required=True)
    stage_parser.add_argument("--manifest", type=Path, required=True)
    subparsers.add_parser("releases", help="list immutable app releases")
    rollback_parser = subparsers.add_parser(
        "rollback", help="plan a coordinated app-only rollback",
    )
    rollback_parser.add_argument("release_id", nargs="?")
    rollback_parser.add_argument(
        "--plan",
        action="store_true",
        help="show the exact non-firmware rollback transaction (execution is API-integrated)",
    )
    args = parser.parse_args(argv)

    manager = AppReleaseManager(args.root)
    if args.command == "stage":
        info = manager.stage(_files_from_manifest(args.source_root, args.manifest))
        print(json.dumps(info.to_dict(), indent=2, sort_keys=True))
    elif args.command == "releases":
        print(json.dumps([info.to_dict() for info in manager.list()], indent=2, sort_keys=True))
    elif args.command == "rollback":
        if not args.plan:
            parser.error(
                "rollback execution requires the integrated coordinator with "
                "capture/restart/fresh-health operations; use --plan until it is configured",
            )
        target = manager.rollback_target(args.release_id)
        steps = [
            {"id": step_id, "mutating": mutating, "description": description}
            for step_id, mutating, description in ROLLBACK_STEP_ORDER
        ]
        print(json.dumps({"target_release": target, "steps": steps}, indent=2, sort_keys=True))
    else:  # pragma: no cover - argparse guarantees this
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
