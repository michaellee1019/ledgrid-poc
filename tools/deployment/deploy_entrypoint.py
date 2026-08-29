#!/usr/bin/env python3
"""Executable workstation entrypoint for coordinator-owned deployments.

The operator contract remains ``just``.  This module is the repository-specific
adapter below that interface: it freezes the selected source, generates previews
from that frozen tree, uploads only to a unique target-side ``.incoming`` path,
and composes the generic coordinator with target leaf commands.

The legacy shell scripts remain available through the explicit ``legacy``
subcommand.  ``shadow`` is non-activating and can optionally stage immutable
releases on the target to collect parity evidence without changing systemd,
firmware, or the selected application.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Optional, Sequence

try:
    from tools.deployment.deploy_coordinator import (
        Artifact,
        AtomicJSONReceiptStore,
        DeployContext,
        DeployCoordinator,
        DeploymentInterrupted,
        Operation,
        OperationResult,
        Redactor,
        SSHAtomicJSONReceiptStore,
        SSHRunner,
        SubprocessRunner,
        build_steps,
        interruption_signals,
    )
    from tools.deployment.deploy_manifest import (
        ManifestPlan,
        manifest_plan,
        source_identity,
        working_tree_dirty,
    )
    from tools.deployment.receiver_hybrid_config import (
        DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
        NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
        PRODUCTION_FIRMWARE_ENVIRONMENT,
    )
except ModuleNotFoundError:  # Direct ``python tools/deployment/deploy_entrypoint.py``.
    from deploy_coordinator import (  # type: ignore[no-redef]
        Artifact,
        AtomicJSONReceiptStore,
        DeployContext,
        DeployCoordinator,
        DeploymentInterrupted,
        Operation,
        OperationResult,
        Redactor,
        SSHAtomicJSONReceiptStore,
        SSHRunner,
        SubprocessRunner,
        build_steps,
        interruption_signals,
    )
    from deploy_manifest import (  # type: ignore[no-redef]
        ManifestPlan,
        manifest_plan,
        source_identity,
        working_tree_dirty,
    )
    from receiver_hybrid_config import (  # type: ignore[no-redef]
        DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
        NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
        PRODUCTION_FIRMWARE_ENVIRONMENT,
    )


SNAPSHOT_SCHEMA_VERSION = 1
ROLLBACK_LEGACY_HELPER_FILENAMES = (
    "deploy_target.py",
    "app_releases.py",
    "deploy_coordinator.py",
)
ROLLBACK_OPTIONAL_HELPER_FILENAMES = (
    "receiver_hybrid_config.py",
    "firmware_artifacts.py",
)
ROLLBACK_HELPER_FILENAMES = (
    *ROLLBACK_LEGACY_HELPER_FILENAMES,
    *ROLLBACK_OPTIONAL_HELPER_FILENAMES,
)
SUPPORT_ROOTS = frozenset({"firmware", "hardware"})
SUPPORT_FILES = frozenset({"requirements-platformio.lock"})
DEFAULT_TARGET = "ledgridwall@ledgridwall.local"
DEFAULT_DEPLOY_DIR = "ledgrid-pod"
DEFAULT_LOCAL_RECEIPTS = Path(".deploy-logs") / "receipts"
DEFAULT_RELEASE_RETENTION = 5
DEFAULT_SSH_OPTIONS = (
    "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=accept-new",
)

RECEIVER_FIRMWARE_HEALTH_CAPABILITIES = {
    # Every current firmware image advertises the complete aligned-envelope v2
    # FEC decoder and status-v7 accounting extension. Environment-specific
    # features remain additive below that common deployment floor.
    PRODUCTION_FIRMWARE_ENVIRONMENT: (7, 0x7C00C),
    DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT: (7, 0xC0FF),
    NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT: (7, 0xFFFF),
}
FINALIZED_FEC_RECEIVER_IDS = (3,)
FINALIZED_RECEIVER_ROUTES = ((0, 0), (0, 1), (1, 1), (1, 0), (1, 2))
FINALIZED_RECEIVER_WIDTHS = (8, 8, 8, 8, 1)
FINALIZED_RECEIVER_OFFSETS = (0, 8, 16, 24, 32)
FINALIZED_RECEIVER_LANE_MASKS = (0xFF, 0xFF, 0xFF, 0xFF, 0xFF)
FINALIZED_RECEIVER_HOST_REVERSALS = (False, False, False, False, False)
FINALIZED_RECEIVER_NATIVE_REVERSALS = (False, False, True, True, False)
FINALIZED_RECEIVER_SPI_MODE = 0
FINALIZED_RECEIVER_SPI_SPEEDS_HZ = (
    20_000_000, 20_000_000, 20_000_000, 12_000_000, 20_000_000,
)


_REMOTE_RELEASE_INSPECTOR = r"""
import json, pathlib, re, sys
root = pathlib.Path(sys.argv[1]).expanduser().resolve()
release_root = root / 'releases'
pattern = re.compile(r'[0-9a-f]{64}')
current = None
selected = root / 'current'
if selected.is_symlink():
    try:
        relative = selected.resolve(strict=False).relative_to(release_root.resolve())
        if len(relative.parts) == 1 and pattern.fullmatch(relative.name):
            current = relative.name
    except (OSError, ValueError):
        pass
releases = []
if release_root.is_dir():
    for path in sorted(release_root.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_dir() or not pattern.fullmatch(path.name):
            continue
        entry = {'id': path.name, 'active': path.name == current, 'valid_metadata': False}
        try:
            metadata = json.loads((path / '.release.json').read_text(encoding='utf-8'))
            files = metadata.get('files')
            entry.update({
                'created_at': metadata.get('created_at'),
                'file_count': len(files) if isinstance(files, list) else None,
                'valid_metadata': (
                    metadata.get('id') == path.name
                    and metadata.get('digest') == path.name
                    and isinstance(files, list)
                ),
            })
        except (OSError, ValueError, TypeError):
            pass
        releases.append(entry)
print(json.dumps({'current_release': current, 'releases': releases}, sort_keys=True))
""".strip()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def receiver_firmware_health_contract(
    environment: str,
    rollout: Mapping[str, Any],
    *,
    leds_per_strip: int,
    receiver_count: int,
) -> Mapping[str, Any]:
    capability_contract = RECEIVER_FIRMWARE_HEALTH_CAPABILITIES.get(environment)
    if capability_contract is None:
        raise RuntimeError(
            "firmware build selected an environment with no health contract"
        )
    widths = tuple(rollout.get("receiver_strip_counts", ()))
    offsets = tuple(rollout.get("receiver_global_strip_offsets", ()))
    lane_masks = tuple(rollout.get("physical_output_lane_masks", ()))
    host_reversals = tuple(
        rollout.get("reverse_strips_by_logical_receiver", ())
    )
    native_reversals = tuple(
        rollout.get("reverse_native_strips_by_logical_receiver", ())
    )
    if (
        receiver_count != 5
        or widths != FINALIZED_RECEIVER_WIDTHS
        or offsets != FINALIZED_RECEIVER_OFFSETS
        or lane_masks != FINALIZED_RECEIVER_LANE_MASKS
        or host_reversals != FINALIZED_RECEIVER_HOST_REVERSALS
        or native_reversals != FINALIZED_RECEIVER_NATIVE_REVERSALS
    ):
        raise RuntimeError(
            "firmware build did not select finalized receiver health topology"
        )
    minimum_version, required_capabilities = capability_contract
    return {
        "schema_version": 2,
        "firmware_environment": environment,
        "minimum_status_version": minimum_version,
        "required_capabilities": required_capabilities,
        "fec_receiver_ids": list(FINALIZED_FEC_RECEIVER_IDS),
        "devices": [
            {
                "logical_device": logical_id,
                "bus": FINALIZED_RECEIVER_ROUTES[logical_id][0],
                "chip_select": FINALIZED_RECEIVER_ROUTES[logical_id][1],
                "active_strips": widths[logical_id],
                "local_strip_count": widths[logical_id],
                "global_strip_offset": offsets[logical_id],
                "lane_mask": lane_masks[logical_id],
                "physical_output_lane_mask": lane_masks[logical_id],
                "reverse_host_strip_order": host_reversals[logical_id],
                "reverse_native_strip_order": native_reversals[logical_id],
                "spi_mode": FINALIZED_RECEIVER_SPI_MODE,
                "spi_speed_hz": FINALIZED_RECEIVER_SPI_SPEEDS_HZ[logical_id],
                "leds_per_strip": leds_per_strip,
            }
            for logical_id in range(receiver_count)
        ],
    }


def _safe_deploy_dir(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or path == PurePosixPath("."):
        raise ValueError(f"DEPLOY_DIR must be a safe path below the target home: {value!r}")
    return path


def _ssh_options(root: Path, ssh_key: Optional[str]) -> tuple[str, ...]:
    """Resolve an optional dedicated identity without consulting an SSH agent."""
    if ssh_key is None or not ssh_key.strip():
        return DEFAULT_SSH_OPTIONS
    candidate = Path(ssh_key).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"SSH_KEY does not exist: {candidate}") from exc
    if not resolved.is_file():
        raise ValueError(f"SSH_KEY is not a regular file: {resolved}")
    permissions = stat.S_IMODE(resolved.stat().st_mode)
    if permissions & 0o077:
        raise ValueError(
            f"SSH_KEY permissions must not grant group/other access: "
            f"{resolved} has mode {permissions:04o}"
        )
    return (
        *DEFAULT_SSH_OPTIONS,
        "-i", os.fspath(resolved),
        "-o", "IdentitiesOnly=yes",
    )


def _rsync_ssh_command(ssh_options: Sequence[str]) -> str:
    """Encode the exact OpenSSH argv for rsync's remote-shell argument."""
    return shlex.join(("ssh", *ssh_options))


def _is_support_path(path: PurePosixPath) -> bool:
    return bool(path.parts and (path.parts[0] in SUPPORT_ROOTS or path.as_posix() in SUPPORT_FILES))


def _is_native_build_path(path: PurePosixPath) -> bool:
    """Return whether a tracked plugin path is receiver-native build input.

    Native packages remain self-contained below the normal plugin root.  Only
    the package-owned ``native`` subtree is build-only: manifests, presets,
    tests, and gallery assets remain ordinary application inputs.
    """

    return bool(
        len(path.parts) >= 5
        and path.parts[0:2] == ("animation", "plugins")
        and path.parts[3] == "native"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _make_immutable(root: Path) -> None:
    directories: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"deployment snapshot unexpectedly contains a symlink: {path}")
        if path.is_dir():
            directories.append(path)
        elif path.is_file():
            path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        directory.chmod(0o555)
    root.chmod(0o555)


def _copy_regular(source: Path, destination: Path) -> None:
    source_stat = source.lstat()
    if not stat.S_ISREG(source_stat.st_mode):
        raise RuntimeError(f"deployment source is not a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o755 if source_stat.st_mode & 0o111 else 0o644)


@dataclass(frozen=True)
class SnapshotEvidence:
    path: Path
    snapshot_id: str
    source_identity: Mapping[str, Any]
    app_files: tuple[str, ...]
    support_files: tuple[str, ...]
    native_build_files: tuple[str, ...]
    file_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": os.fspath(self.path),
            "snapshot_id": self.snapshot_id,
            "source_identity": _json_safe(self.source_identity),
            "app_file_count": len(self.app_files),
            "support_file_count": len(self.support_files),
            "native_build_file_count": len(self.native_build_files),
            "file_count": self.file_count,
        }


def _preview_command(project_root: Path, snapshot_root: Path, output: Path) -> tuple[str, ...]:
    return (
        "uv",
        "run",
        "--frozen",
        "--project",
        os.fspath(project_root),
        "--group",
        "test",
        "--group",
        "calibration",
        "python",
        os.fspath(snapshot_root / "tools" / "generate_animation_previews.py"),
        "--output",
        os.fspath(output),
        "--workers",
        os.environ.get("PREVIEW_WORKERS", "0"),
    )


def _validate_source_policy(root: Path, plan: ManifestPlan, policy: str) -> Mapping[str, Any]:
    if policy not in {"clean", "dirty", "plan"}:
        raise ValueError(f"unknown source policy: {policy}")
    dirty = working_tree_dirty(root)
    if policy == "clean" and dirty:
        raise RuntimeError(
            "clean deployment refused: working tree has tracked or non-ignored "
            "untracked changes; use the explicit dirty deployment"
        )
    identity = dict(source_identity(root, plan))
    identity["dirty"] = dirty
    if policy == "clean":
        identity["diff_sha256"] = None
        identity["safe_untracked"] = []
    return identity


def freeze_snapshot(
    root: Path,
    scope: str,
    policy: str,
    destination: Path,
    *,
    generate_previews: bool = True,
    command_runner: Optional[Callable[[Sequence[str]], Any]] = None,
) -> SnapshotEvidence:
    """Freeze and account for one exact deployment input tree.

    The source identity and selection are re-evaluated after preview generation
    to reject source changes during a potentially long snapshot build.
    """
    root = root.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"deployment snapshot destination exists: {destination}")
    plan = manifest_plan(root, scope)
    identity = _validate_source_policy(root, plan, policy)
    destination.mkdir(parents=True)
    try:
        for relative in plan.selected:
            _copy_regular(root / relative.as_posix(), destination / relative.as_posix())

        preview_root = destination / "web" / "static" / "generated" / "animation-previews"
        if generate_previews:
            runner = command_runner or (
                lambda command: subprocess.run(
                    command, check=True, cwd=root, capture_output=True, text=True,
                )
            )
            # The original project supplies only the already-locked Python
            # environment. The executable and every preview input are read
            # from the frozen source tree, including for fast snapshots that
            # intentionally omit dependency metadata.
            runner(_preview_command(root, destination, preview_root))

        selected_after = manifest_plan(root, scope)
        identity_after = _validate_source_policy(root, selected_after, policy)
        if selected_after.selected != plan.selected or identity_after != identity:
            raise RuntimeError("deployment source changed while the immutable snapshot was built")
        for relative in plan.selected:
            source = root / relative.as_posix()
            frozen = destination / relative.as_posix()
            if (
                source.is_symlink()
                or not source.is_file()
                or source.stat().st_size != frozen.stat().st_size
                or _sha256_file(source) != _sha256_file(frozen)
                or bool(source.stat().st_mode & 0o111)
                != bool(frozen.stat().st_mode & 0o111)
            ):
                raise RuntimeError(
                    "deployment source changed while the immutable snapshot was built: "
                    f"{relative}"
                )

        app_files = [
            relative.as_posix()
            for relative in plan.selected
            if not _is_support_path(relative) and not _is_native_build_path(relative)
        ]
        support_files = [
            relative.as_posix() for relative in plan.selected if _is_support_path(relative)
        ]
        native_build_files = [
            relative.as_posix()
            for relative in plan.selected
            if _is_native_build_path(relative)
        ]
        if preview_root.is_dir():
            app_files.extend(
                path.relative_to(destination).as_posix()
                for path in preview_root.rglob("*")
                if path.is_file()
            )
        app_files = sorted(set(app_files))
        support_files = sorted(set(support_files))
        native_build_files = sorted(set(native_build_files))
        overlap = (
            (set(app_files) & set(support_files))
            | (set(app_files) & set(native_build_files))
            | (set(support_files) & set(native_build_files))
        )
        if overlap:
            raise RuntimeError(f"deployment lanes overlap: {sorted(overlap)}")

        deploy_metadata = destination / ".deploy"
        _write_json(
            deploy_metadata / "app-manifest.json",
            {"schema_version": SNAPSHOT_SCHEMA_VERSION, "files": app_files},
        )
        _write_json(
            deploy_metadata / "support-manifest.json",
            {"schema_version": SNAPSHOT_SCHEMA_VERSION, "files": support_files},
        )
        _write_json(
            deploy_metadata / "native-build-manifest.json",
            {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "files": native_build_files,
            },
        )

        evidence = []
        metadata_path = deploy_metadata / "snapshot.json"
        for path in sorted(destination.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file() or path == metadata_path:
                continue
            relative = path.relative_to(destination).as_posix()
            evidence.append(
                {
                    "path": relative,
                    "sha256": _sha256_file(path),
                    "size": path.stat().st_size,
                    "executable": bool(path.stat().st_mode & 0o111),
                }
            )
        snapshot_payload = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "source_identity": identity,
            "scope": scope,
            "app_files": app_files,
            "support_files": support_files,
            "native_build_files": native_build_files,
            "files": evidence,
        }
        snapshot_id = hashlib.sha256(
            json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        snapshot_payload["snapshot_id"] = snapshot_id
        _write_json(metadata_path, snapshot_payload)
        _make_immutable(destination)
        return SnapshotEvidence(
            path=destination,
            snapshot_id=snapshot_id,
            source_identity=identity,
            app_files=tuple(app_files),
            support_files=tuple(support_files),
            native_build_files=tuple(native_build_files),
            file_count=len(evidence),
        )
    except BaseException:
        if destination.exists():
            for path in destination.rglob("*"):
                if path.is_dir():
                    path.chmod(0o755)
            destination.chmod(0o755)
            shutil.rmtree(destination)
        raise


@dataclass(frozen=True)
class DeploymentConfig:
    root: Path
    mode: str
    policy: str
    target: str = DEFAULT_TARGET
    deploy_dir: str = DEFAULT_DEPLOY_DIR
    run_tests: bool = True
    verbose: bool = False
    generate_previews: bool = True
    strips: int = 33
    leds_per_strip: int = 138
    receiver_count: int = 5
    force_firmware: bool = False
    release_retention: int = DEFAULT_RELEASE_RETENTION
    health_timeout: float = 30.0
    ssh_options: tuple[str, ...] = DEFAULT_SSH_OPTIONS
    local_receipts: Path = DEFAULT_LOCAL_RECEIPTS

    def __post_init__(self) -> None:
        if self.mode not in {"full", "python"}:
            raise ValueError(f"unknown deployment mode: {self.mode}")
        if self.policy not in {"clean", "dirty", "plan"}:
            raise ValueError(f"unknown deployment source policy: {self.policy}")
        _safe_deploy_dir(self.deploy_dir)
        if not self.target or self.target.startswith("-"):
            raise ValueError(f"unsafe deployment target: {self.target!r}")
        for value, label in (
            (self.strips, "strips"),
            (self.leds_per_strip, "leds_per_strip"),
            (self.receiver_count, "receiver_count"),
        ):
            if isinstance(value, bool) or value <= 0:
                raise ValueError(f"{label} must be a positive integer")
        if isinstance(self.release_retention, bool) or self.release_retention < 2:
            raise ValueError("release_retention must preserve at least two releases")
        if self.force_firmware and self.mode != "full":
            raise ValueError("forced firmware flashing requires a full deployment")


@dataclass(frozen=True)
class ActivationFailureEvidence:
    candidate_release: str
    previous_release: Optional[str]
    candidate_error: str
    restored: bool
    restoration_health: Optional[Mapping[str, Any]] = None
    restoration_error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_release": self.candidate_release,
            "previous_release": self.previous_release,
            "candidate_error": self.candidate_error,
            "restored": self.restored,
            "restoration_health": _json_safe(self.restoration_health),
            "restoration_error": self.restoration_error,
        }


class RemoteActivationFailed(RuntimeError):
    def __init__(self, failure: ActivationFailureEvidence) -> None:
        self.failure = failure
        message = f"candidate release {failure.candidate_release} failed: {failure.candidate_error}"
        if failure.restored:
            message += f"; restored {failure.previous_release}"
        elif failure.restoration_error:
            message += f"; restoration failed: {failure.restoration_error}"
        super().__init__(message)


class RemoteTarget:
    """JSON-only adapter for one uploaded target helper."""

    def __init__(
        self,
        context: DeployContext,
        deploy_dir: str,
        *,
        helper_path: Optional[str] = None,
    ) -> None:
        self.context = context
        self.deploy_dir = _safe_deploy_dir(deploy_dir).as_posix()
        self._helper_path = helper_path

    @property
    def incoming(self) -> str:
        return f"{self.deploy_dir}/.incoming/{self.context.attempt_id}"

    @property
    def helper(self) -> str:
        return self._helper_path or f"{self.incoming}/tools/deployment/deploy_target.py"

    def run(self, command: str, *args: str) -> Mapping[str, Any]:
        result = self.context.ssh(
            ("python3", self.helper, "--root", self.deploy_dir, command, *args),
        )
        raw = result.stdout.strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"target command {command!r} returned invalid JSON: {raw[-2000:]}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"target command {command!r} returned a non-object")
        return payload


class CoordinatorDeployment:
    """Compose full/python coordinator steps from repository target leaves."""

    def __init__(self, config: DeploymentConfig, context: DeployContext) -> None:
        self.config = config
        self.context = context
        self.target = RemoteTarget(context, config.deploy_dir)
        self.plan = manifest_plan(config.root, self.scope)
        self.identity = _validate_source_policy(config.root, self.plan, config.policy)
        self._temporary: Optional[tempfile.TemporaryDirectory[str]] = None

    @property
    def scope(self) -> str:
        return "full" if self.config.mode == "full" else "fast"

    def close(self) -> None:
        if self._temporary is not None:
            snapshot = Path(self._temporary.name) / "snapshot"
            if snapshot.exists():
                for path in snapshot.rglob("*"):
                    if path.is_dir():
                        path.chmod(0o755)
                snapshot.chmod(0o755)
            self._temporary.cleanup()
            self._temporary = None

    def _source_validate(self, _context: DeployContext) -> OperationResult:
        return OperationResult(
            details={
                **self.identity,
                "selected_count": len(self.plan.selected),
                "excluded_count": len(self.plan.excluded),
            }
        )

    def _tests(self, context: DeployContext) -> OperationResult:
        if not self.config.run_tests:
            return OperationResult(outcome="skipped", details={"reason": "explicit TEST=false"})
        command = (
            ("just", "deploy-precheck")
            if self.config.mode == "full"
            else ("just", "test-unit", "test-rendering", "test-deployment")
        )
        result = context.command(command, cwd=self.config.root)
        return OperationResult(
            details={"command": list(result.args), "duration_seconds": result.duration_seconds}
        )

    def _target_connect(self, context: DeployContext) -> OperationResult:
        result = context.ssh(
            (
                "mkdir",
                "-p",
                self.config.deploy_dir,
                f"{self.config.deploy_dir}/.incoming",
                f"{self.config.deploy_dir}/run_state/deploy_receipts",
            )
        )
        return OperationResult(details={"target": self.config.target, "returncode": result.returncode})

    def _freeze(self, context: DeployContext) -> SnapshotEvidence:
        if self._temporary is not None:
            raise RuntimeError("deployment snapshot was already constructed")
        self._temporary = tempfile.TemporaryDirectory(prefix="ledgrid-deploy-")
        snapshot_path = Path(self._temporary.name) / "snapshot"

        def preview_runner(command: Sequence[str]) -> Any:
            return context.command(command, cwd=self.config.root)

        return freeze_snapshot(
            self.config.root,
            self.scope,
            self.config.policy,
            snapshot_path,
            generate_previews=self.config.generate_previews,
            command_runner=preview_runner,
        )

    def _stage(self, context: DeployContext) -> OperationResult:
        evidence = self._freeze(context)
        destination = f"{self.config.target}:{self.target.incoming}/"
        upload = context.command(
            (
                "rsync",
                "-az",
                "--delete",
                "-e",
                _rsync_ssh_command(self.config.ssh_options),
                os.fspath(evidence.path) + "/",
                destination,
            ),
            cwd=self.config.root,
        )
        support = self.target.run("stage-support", "--snapshot", self.target.incoming)
        app = self.target.run("stage-app", "--snapshot", self.target.incoming)
        release_id = app.get("release_id")
        if not isinstance(release_id, str):
            raise RuntimeError("target app staging returned no release identity")
        support_id = support.get("support_release_id")
        if support_id is not None and not isinstance(support_id, str):
            raise RuntimeError("target support staging returned an invalid identity")
        # Every later leaf must execute from the exact candidate bytes that
        # were just verified and staged. The incoming snapshot is deliberately
        # removed below, and ``current`` still names the prior app until the
        # activation boundary.
        self.target._helper_path = (
            f"{self.config.deploy_dir}/releases/{release_id}"
            "/tools/deployment/deploy_target.py"
        )
        context.state.update(
            {
                "snapshot": evidence,
                "release_id": release_id,
                "support_id": support_id,
                "app_stage": app,
                "support_stage": support,
            }
        )
        cleanup = self.target.run(
            "cleanup-snapshot", "--snapshot", self.target.incoming,
        )
        artifacts = [Artifact("app_release", release_id, release_id, "1")]
        if support_id is not None:
            artifacts.append(Artifact("support_release", support_id, support_id, "1"))
        return OperationResult(
            artifacts=tuple(artifacts),
            details={
                "snapshot": evidence.to_dict(),
                "upload_duration_seconds": upload.duration_seconds,
                "app": app,
                "support": support,
                "incoming_cleanup": cleanup,
            },
        )

    def _firmware_build(self, _context: DeployContext) -> OperationResult:
        support_id = self.context.state.get("support_id")
        args = (support_id,) if isinstance(support_id, str) else ()
        result = self.target.run("build-firmware", *args)
        environment = result.get("firmware_environment")
        config_digest = result.get("receiver_hybrid_config_digest")
        firmware_sha256 = result.get("firmware_sha256")
        installation_digest = result.get("firmware_installation_digest")
        if not isinstance(environment, str) or not environment:
            raise RuntimeError("firmware build returned no selected environment")
        if (
            not isinstance(config_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", config_digest) is None
        ):
            raise RuntimeError("firmware build returned no rollout config digest")
        migrated_digest = self.context.state.get("receiver_topology_digest")
        if (
            isinstance(migrated_digest, str)
            and config_digest != migrated_digest
        ):
            raise RuntimeError(
                "receiver rollout selection changed after topology migration"
            )
        if firmware_sha256 is not None and (
            not isinstance(firmware_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", firmware_sha256) is None
        ):
            raise RuntimeError("firmware build returned an invalid binary digest")
        if installation_digest is not None and (
            not isinstance(installation_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", installation_digest) is None
        ):
            raise RuntimeError(
                "firmware build returned an invalid installation digest"
            )
        if isinstance(firmware_sha256, str) and not isinstance(
            installation_digest, str
        ):
            raise RuntimeError(
                "firmware build returned no complete installation digest"
            )
        rollout = result.get("receiver_hybrid_config")
        if (
            not isinstance(rollout, dict)
            or tuple(rollout.get("receiver_strip_counts", ()))
            != (8, 8, 8, 8, 1)
            or tuple(rollout.get("receiver_global_strip_offsets", ()))
            != FINALIZED_RECEIVER_OFFSETS
        ):
            raise RuntimeError(
                "firmware build did not select the finalized receiver topology"
            )
        receiver_health_contract = receiver_firmware_health_contract(
            environment,
            rollout,
            leds_per_strip=self.config.leds_per_strip,
            receiver_count=self.config.receiver_count,
        )
        self.context.state["firmware_selection"] = {
            "firmware_environment": environment,
            "receiver_hybrid_config_digest": config_digest,
            "firmware_sha256": firmware_sha256,
            "firmware_installation_digest": installation_digest,
            "receiver_health_contract": receiver_health_contract,
        }
        artifacts = ()
        if isinstance(firmware_sha256, str):
            artifacts = (
                Artifact(
                    "receiver_firmware_build",
                    environment,
                    str(installation_digest),
                    "2",
                    target_id=firmware_sha256,
                ),
            )
        return OperationResult(
            outcome=str(result.get("outcome", "executed")),
            details=result,
            artifacts=artifacts,
        )

    def _bootstrap_legacy(self, context: DeployContext) -> OperationResult:
        candidate = str(context.state["release_id"])
        result = self.target.run("bootstrap-legacy-app", candidate)
        bootstrap_id = result.get("bootstrap_release_id")
        bootstrap_digest = result.get("bootstrap_digest")
        recovery = result.get("recovery_release")
        if bootstrap_id is not None and (
            not isinstance(bootstrap_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", bootstrap_id) is None
        ):
            raise RuntimeError("legacy bootstrap returned an invalid release identity")
        if bootstrap_digest is not None and bootstrap_digest != bootstrap_id:
            raise RuntimeError("legacy bootstrap release/digest evidence disagrees")
        if recovery is not None and recovery != bootstrap_id:
            raise RuntimeError("legacy bootstrap returned an invalid recovery release")
        context.state["bootstrap_release_id"] = bootstrap_id
        context.state["bootstrap_recovery_release"] = recovery
        artifacts = ()
        if isinstance(bootstrap_id, str) and isinstance(bootstrap_digest, str):
            artifacts = (
                Artifact(
                    "legacy_app_bootstrap",
                    bootstrap_id,
                    bootstrap_digest,
                    "1",
                ),
            )
        return OperationResult(
            outcome=str(result.get("outcome", "executed")),
            details=result,
            artifacts=artifacts,
        )

    def _topology_migrate(self, _context: DeployContext) -> OperationResult:
        result = self.target.run("migrate-receiver-topology")
        if (
            result.get("strips") != self.config.strips
            or result.get("receivers") != self.config.receiver_count
        ):
            raise RuntimeError(
                "target receiver topology disagrees with deployment geometry"
            )
        digest = result.get("receiver_hybrid_config_digest")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise RuntimeError("receiver topology migration returned no digest")
        selection = self.context.state.get("firmware_selection")
        if (
            isinstance(selection, dict)
            and digest != selection.get("receiver_hybrid_config_digest")
        ):
            raise RuntimeError(
                "post-health topology migration changed rollout semantics"
            )
        self.context.state["receiver_topology_digest"] = digest
        return OperationResult(
            outcome=str(result.get("outcome", "executed")), details=result
        )

    def _provision(self, context: DeployContext) -> OperationResult:
        release_id = str(context.state["release_id"])
        target_user = self.config.target.split("@", 1)[0] if "@" in self.config.target else os.environ.get("USER", "pi")
        args = [
            release_id,
            "--user",
            target_user,
            "--strips",
            str(self.config.strips),
            "--receivers",
            str(self.config.receiver_count),
        ]
        if os.environ.get("LEDGRID_HAT", "0").lower() in {"1", "true", "yes"}:
            args.append("--hat")
        first = self.target.run("provision", *args)
        spi = first.get("spi") if isinstance(first.get("spi"), dict) else {}
        rebooted = False
        if spi.get("status") == "needs_reboot":
            # A reboot commonly closes SSH before the command can return.  The
            # request is therefore best-effort; readiness is proved by a bounded
            # reconnect and one rediscovery, never by the reboot command status.
            context.ssh(("sudo", "reboot"), check=False)
            rebooted = True
            time.sleep(3.0)
            for _attempt in range(60):
                probe = context.ssh(("true",), check=False, timeout=5.0)
                if probe.returncode == 0:
                    break
                time.sleep(2.0)
            else:
                raise RuntimeError("target did not return after the one allowed provisioning reboot")
            second = self.target.run("provision", *args)
            second_spi = second.get("spi") if isinstance(second.get("spi"), dict) else {}
            if second_spi.get("status") != "ready":
                raise RuntimeError("SPI is still not ready after the one allowed provisioning reboot")
            first = {"initial": first, "after_reboot": second}
        outcome = "executed" if rebooted else str(first.get("outcome", "executed"))
        return OperationResult(
            outcome=outcome,
            details={"rebooted": rebooted, "provisioning": first},
        )

    def _firmware_flash(self, _context: DeployContext) -> OperationResult:
        support_id = self.context.state.get("support_id")
        selection = self.context.state.get("firmware_selection")
        if not isinstance(selection, dict):
            raise RuntimeError("firmware flash has no build-phase rollout selection")
        environment = selection.get("firmware_environment")
        config_digest = selection.get("receiver_hybrid_config_digest")
        firmware_sha256 = selection.get("firmware_sha256")
        installation_digest = selection.get("firmware_installation_digest")
        if not isinstance(environment, str) or not isinstance(config_digest, str):
            raise RuntimeError("firmware build-phase rollout selection is malformed")
        if firmware_sha256 is not None and not isinstance(installation_digest, str):
            raise RuntimeError(
                "firmware build-phase installation selection is malformed"
            )
        args: list[str] = []
        if isinstance(support_id, str):
            args.append(support_id)
        args.extend(
            (
                "--expected-environment",
                environment,
                "--expected-config-digest",
                config_digest,
            )
        )
        if isinstance(installation_digest, str):
            args.extend(("--expected-installation-digest", installation_digest))
        if self.config.force_firmware:
            args.append("--force")
        result = self.target.run("flash-firmware", *args)
        if (
            result.get("firmware_environment") != environment
            or result.get("receiver_hybrid_config_digest") != config_digest
            or (
                firmware_sha256 is not None
                and result.get("firmware_sha256") != firmware_sha256
            )
            or (
                installation_digest is not None
                and result.get("firmware_installation_digest")
                != installation_digest
            )
        ):
            raise RuntimeError("firmware build and flash receipts disagree")
        context = self.context
        context.state["firmware_mutated"] = (
            str(result.get("outcome", "executed")) == "executed"
        )
        installed_digest = result.get("firmware_installation_digest")
        if (
            not isinstance(installed_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", installed_digest) is None
        ):
            raise RuntimeError("firmware flash returned no installed-image receipt")
        return OperationResult(
            outcome=str(result.get("outcome", "executed")),
            details=result,
            artifacts=(
                Artifact(
                    "receiver_firmware_installation",
                    environment,
                    installed_digest,
                    "2",
                    target_id=installed_digest,
                ),
            ),
        )

    def _validate_app(self, context: DeployContext) -> OperationResult:
        result = self.target.run("validate-app", str(context.state["release_id"]))
        return OperationResult(details=result)

    def _capture(self, context: DeployContext) -> OperationResult:
        result = self.target.run("capture-state")
        context.state["state_captured"] = bool(result.get("captured"))
        return OperationResult(
            outcome="executed" if result.get("captured") else "skipped",
            details=result,
        )

    def _activate(self, context: DeployContext) -> OperationResult:
        candidate = str(context.state["release_id"])
        before = self.target.run("current-release").get("current_release")
        recovery = context.state.get("bootstrap_recovery_release")
        recovery_guarded = bool(
            before == candidate
            and isinstance(recovery, str)
            and recovery
            and recovery != candidate
        )
        context.state["previous_release"] = recovery if recovery_guarded else before
        try:
            result = self.target.run("activate", candidate)
        except Exception as exc:
            # The target may have switched ``current`` before an SSH/JSON
            # acknowledgement was lost. Resolve that ambiguous boundary and
            # compensate just as rigorously as later restart/restore failures.
            try:
                selected = self.target.run("current-release").get("current_release")
            except Exception:
                selected = None
            if selected == candidate and before != candidate:
                context.state["activated"] = True
                raise RemoteActivationFailed(self._compensate(exc)) from exc
            raise
        context.state["activated"] = bool(result.get("changed")) or recovery_guarded
        selected_at = result.get("selected_at")
        if isinstance(selected_at, (int, float)) and not isinstance(selected_at, bool):
            context.state["selection_boundary"] = float(selected_at)
        reported_previous = result.get("previous_release")
        if reported_previous != before:
            mismatch = RuntimeError(
                "target activation previous-release evidence changed during activation"
            )
            if context.state["activated"]:
                raise RemoteActivationFailed(self._compensate(mismatch)) from mismatch
            raise mismatch
        return OperationResult(
            outcome="executed" if result.get("changed") else "skipped",
            details={
                **result,
                "recovery_guarded": recovery_guarded,
                "effective_previous_release": context.state["previous_release"],
            },
        )

    def _compensate(self, original_error: Exception) -> ActivationFailureEvidence:
        candidate = str(self.context.state["release_id"])
        previous = self.context.state.get("previous_release")
        if self.context.state.get("compensated"):
            return self.context.state["compensation"]
        if not isinstance(previous, str) or not previous:
            failure = ActivationFailureEvidence(
                candidate_release=candidate,
                previous_release=None,
                candidate_error=str(original_error),
                restored=False,
                restoration_error="no previous immutable app release exists",
            )
        else:
            try:
                self.target.run("activate", previous)
                restart = self.target.run("restart")
                self.target.run("restore-state", "--timeout", "20")
                boundary = restart.get("restart_started_at")
                if not isinstance(boundary, (int, float)) or isinstance(boundary, bool):
                    raise RuntimeError("restoration restart returned no boundary")
                health = self.target.run(
                    "health",
                    previous,
                    "--boundary",
                    str(boundary),
                    "--strips",
                    str(self.config.strips),
                    "--leds-per-strip",
                    str(self.config.leds_per_strip),
                    "--receivers",
                    str(self.config.receiver_count),
                    "--timeout",
                    str(self.config.health_timeout),
                )
                failure = ActivationFailureEvidence(
                    candidate_release=candidate,
                    previous_release=previous,
                    candidate_error=str(original_error),
                    restored=True,
                    restoration_health=health,
                )
            except Exception as restoration_error:
                failure = ActivationFailureEvidence(
                    candidate_release=candidate,
                    previous_release=previous,
                    candidate_error=str(original_error),
                    restored=False,
                    restoration_error=str(restoration_error),
                )
        self.context.state["compensated"] = True
        self.context.state["compensation"] = failure
        return failure

    def _receiver_health_args(self) -> tuple[str, ...]:
        selection = self.context.state.get("firmware_selection")
        contract = (
            selection.get("receiver_health_contract")
            if isinstance(selection, dict) else None
        )
        if not isinstance(contract, dict):
            return ()
        return (
            "--receiver-contract-json",
            json.dumps(contract, sort_keys=True, separators=(",", ":")),
        )

    def _post_activation(self, operation: Callable[[], OperationResult]) -> OperationResult:
        try:
            return operation()
        except (KeyboardInterrupt, DeploymentInterrupted) as exc:
            if self.context.state.get("activated"):
                failure = self._compensate(exc)
                restored = (
                    f"restored {failure.previous_release}"
                    if failure.restored
                    else f"restoration failed: {failure.restoration_error}"
                )
                raise DeploymentInterrupted(f"{exc or 'deployment interrupted'}; {restored}") from exc
            raise
        except Exception as exc:
            if self.context.state.get("activated"):
                raise RemoteActivationFailed(self._compensate(exc)) from exc
            raise

    def _restart(self, context: DeployContext) -> OperationResult:
        if not context.state.get("activated") and not context.state.get(
            "firmware_mutated"
        ):
            # The target-provided selection time shares the API clock. This
            # prevents workstation/Pi skew from invalidating an otherwise safe
            # unchanged-release health check.
            context.state["acceptance_boundary"] = context.state.get(
                "selection_boundary", time.time()
            )
            return OperationResult(outcome="skipped", details={"reason": "release already active"})

        def execute() -> OperationResult:
            result = self.target.run("restart")
            boundary = result.get("restart_started_at")
            if not isinstance(boundary, (int, float)) or isinstance(boundary, bool):
                raise RuntimeError("target restart returned no timestamp boundary")
            context.state["acceptance_boundary"] = float(boundary)
            return OperationResult(details=result)

        return self._post_activation(execute)

    def _restore(self, context: DeployContext) -> OperationResult:
        if (
            not (
                context.state.get("activated")
                or context.state.get("firmware_mutated")
            )
            or not context.state.get("state_captured")
        ):
            return OperationResult(outcome="skipped", details={"reason": "no captured state to restore"})

        def execute() -> OperationResult:
            result = self.target.run("restore-state", "--timeout", "20")
            selection = context.state.get("firmware_selection")
            if isinstance(selection, dict):
                expected = selection.get("receiver_hybrid_config_digest")
                if result.get("receiver_hybrid_config_digest") != expected:
                    raise RuntimeError(
                        "state restore used a different receiver-hybrid config"
                    )
            return OperationResult(details=result)

        return self._post_activation(execute)

    def _health(self, context: DeployContext) -> OperationResult:
        def execute() -> OperationResult:
            boundary = context.state.get("acceptance_boundary")
            if not isinstance(boundary, (int, float)) or isinstance(boundary, bool):
                boundary = time.time()
            result = self.target.run(
                "health",
                str(context.state["release_id"]),
                "--boundary",
                str(boundary),
                "--strips",
                str(self.config.strips),
                "--leds-per-strip",
                str(self.config.leds_per_strip),
                "--receivers",
                str(self.config.receiver_count),
                "--timeout",
                str(self.config.health_timeout),
                *self._receiver_health_args(),
            )
            bootstrap = self.target.run(
                "complete-legacy-bootstrap", str(context.state["release_id"])
            )
            recorded = self.target.run("record-deploy")
            return OperationResult(details={
                **result,
                "legacy_bootstrap": bootstrap,
                "deployment_status": recorded,
            })

        return self._post_activation(execute)

    def _prune(self, _context: DeployContext) -> OperationResult:
        try:
            result = self.target.run(
                "prune-releases", "--retain", str(self.config.release_retention)
            )
        except Exception as exc:
            # Retention maintenance happens only after fresh health acceptance.
            # It must not roll back or fail an otherwise healthy deployment.
            return OperationResult(
                outcome="warning",
                details={"retain": self.config.release_retention, "error": str(exc)},
            )
        return OperationResult(
            outcome=str(result.get("outcome", "executed")),
            details=result,
        )

    def operations(self) -> Mapping[str, Operation]:
        operations: dict[str, Operation] = {
            "source.validate": self._source_validate,
            "tests.run": self._tests,
            "target.connect": self._target_connect,
            "app.stage": self._stage,
            "app.bootstrap_legacy": self._bootstrap_legacy,
            "app.validate": self._validate_app,
            "state.capture": self._capture,
            "app.activate": self._activate,
            "host.restart": self._restart,
            "state.restore": self._restore,
            "health.readiness": self._health,
            "release.prune": self._prune,
        }
        if self.config.mode == "full":
            operations.update(
                {
                    "receiver.topology_migrate": self._topology_migrate,
                    "receiver.firmware_build": self._firmware_build,
                    "host.provision": self._provision,
                    "receiver.firmware_flash": self._firmware_flash,
                }
            )
        return operations

    def steps(self):
        return build_steps(self.config.mode, self.operations())


class CoordinatorRollback(CoordinatorDeployment):
    """Coordinator-owned app-only selection of an existing immutable release."""

    def __init__(
        self,
        config: DeploymentConfig,
        context: DeployContext,
        release_id: str,
    ) -> None:
        if len(release_id) != 64 or any(character not in "0123456789abcdef" for character in release_id):
            raise ValueError(f"invalid immutable app release ID: {release_id!r}")
        self.config = config
        self.context = context
        self.release_id = release_id
        self.target = RemoteTarget(
            context,
            config.deploy_dir,
            helper_path=(
                f"{_safe_deploy_dir(config.deploy_dir).as_posix()}"
                "/current/tools/deployment/deploy_target.py"
            ),
        )
        self.identity = {
            "operation": "app_rollback",
            "requested_release": release_id,
        }
        self._temporary = None
        self._helper_pinned = False
        context.state["release_id"] = release_id

    def close(self) -> Optional[str]:
        if not self._helper_pinned:
            return None
        try:
            self.target.run("cleanup-snapshot", "--snapshot", self.target.incoming)
        except Exception as exc:
            # A unique, non-active .incoming helper is safe to retain for
            # diagnostics when cleanup itself is unavailable.
            return str(exc)
        finally:
            self._helper_pinned = False
        return None

    def _capture(self, context: DeployContext) -> OperationResult:
        captured = super()._capture(context)
        if not isinstance(self.target, RemoteTarget):
            return captured
        helper_directory = self.target.incoming
        current_tools = f"{self.config.deploy_dir}/current/tools/deployment"
        context.ssh(("mkdir", "-p", helper_directory))
        context.ssh(
            (
                "cp",
                *(
                    f"{current_tools}/{name}"
                    for name in ROLLBACK_LEGACY_HELPER_FILENAMES
                ),
                helper_directory,
            )
        )
        # Target helpers are versioned with the active app release. Copy newer
        # direct-import dependencies when that release contains them, while
        # retaining rollback support for older helpers that predate the files.
        for name in ROLLBACK_OPTIONAL_HELPER_FILENAMES:
            source = f"{current_tools}/{name}"
            present = context.ssh(("test", "-f", source), check=False)
            if present.returncode == 0:
                context.ssh(("cp", source, helper_directory))
        self.target._helper_path = f"{helper_directory}/deploy_target.py"
        self._helper_pinned = True
        return OperationResult(
            outcome=captured.outcome,
            details={**captured.details, "pinned_helper": self.target._helper_path},
        )

    def _source_validate(self, _context: DeployContext) -> OperationResult:
        inspected = self.target.run("inspect")
        current = inspected.get("current_release")
        releases = inspected.get("releases")
        if not isinstance(current, str):
            raise RuntimeError("app rollback requires an active immutable release")
        if not isinstance(releases, list) or self.release_id not in releases:
            raise RuntimeError(f"requested app release is unavailable: {self.release_id}")
        if current == self.release_id:
            raise RuntimeError(f"requested app release is already active: {self.release_id}")
        return OperationResult(
            details={
                "requested_release": self.release_id,
                "current_release": current,
                "available_release_count": len(releases),
            }
        )

    def operations(self) -> Mapping[str, Operation]:
        return {
            "source.validate": self._source_validate,
            "app.validate": self._validate_app,
            "state.capture": self._capture,
            "app.activate": self._activate,
            "host.restart": self._restart,
            "state.restore": self._restore,
            "health.readiness": self._health,
            "release.prune": self._prune,
        }

    def steps(self):
        return build_steps("rollback", self.operations())


def _context(config: DeploymentConfig) -> DeployContext:
    redactor = Redactor()
    local_runner = SubprocessRunner(redactor)
    ssh_runner = SSHRunner(local_runner, config.target, ssh_options=config.ssh_options)
    deploy_dir = _safe_deploy_dir(config.deploy_dir).as_posix()
    local_receipts = config.root / config.local_receipts
    return DeployContext(
        target=config.target,
        mode=config.mode,
        source_identity={},
        source_policy=config.policy,
        flags={
            "tests": config.run_tests,
            "previews": config.generate_previews,
            "strips": config.strips,
            "leds_per_strip": config.leds_per_strip,
            "receiver_count": config.receiver_count,
        },
        paths={"root": config.root},
        redactor=redactor,
        command_runner=local_runner,
        ssh_runner=ssh_runner,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
        receipt_sinks=(
            AtomicJSONReceiptStore(local_receipts),
            SSHAtomicJSONReceiptStore(
                ssh_runner, f"{deploy_dir}/run_state/deploy_receipts",
            ),
        ),
    )


def run_deployment(config: DeploymentConfig):
    """Run one authoritative deployment and require both receipt copies."""
    if config.policy == "plan":
        raise ValueError("the plan source policy is read-only and cannot run a deployment")
    context = _context(config)
    deployment = CoordinatorDeployment(config, context)
    context.source_identity = deployment.identity
    try:
        with interruption_signals():
            receipt = DeployCoordinator().run(context, deployment.steps())
    finally:
        deployment.close()
    return receipt


def run_rollback(config: DeploymentConfig, release_id: str):
    """Select an existing app release through the paired-receipt coordinator."""
    if config.policy == "plan":
        raise ValueError("the plan source policy is read-only and cannot run rollback")
    context = _context(config)
    context.mode = "rollback"
    rollback = CoordinatorRollback(config, context, release_id)
    context.source_identity = rollback.identity
    cleanup_error: Optional[str] = None
    try:
        with interruption_signals():
            receipt = DeployCoordinator().run(context, rollback.steps())
    finally:
        cleanup_error = rollback.close()
    if cleanup_error is not None:
        receipt.persistence_errors = (
            *receipt.persistence_errors,
            f"rollback helper cleanup failed: {cleanup_error}",
        )
    return receipt


def list_releases(config: DeploymentConfig) -> Mapping[str, Any]:
    """Inspect immutable app releases without requiring a selected ``current``."""
    context = _context(config)
    result = context.ssh(("python3", "-c", _REMOTE_RELEASE_INSPECTOR, config.deploy_dir))
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("target release inspection returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("releases"), list):
        raise RuntimeError("target release inspection returned a malformed result")
    return payload


def shadow_deployment(config: DeploymentConfig, *, target_stage: bool = False) -> Mapping[str, Any]:
    """Build parity evidence without activation, provisioning, restart, or flash."""
    plan = manifest_plan(config.root, "full" if config.mode == "full" else "fast")
    identity = _validate_source_policy(config.root, plan, config.policy)
    temporary = tempfile.TemporaryDirectory(prefix="ledgrid-shadow-")
    snapshot_path = Path(temporary.name) / "snapshot"
    try:
        evidence = freeze_snapshot(
            config.root,
            "full" if config.mode == "full" else "fast",
            config.policy,
            snapshot_path,
            generate_previews=config.generate_previews,
        )
        result: dict[str, Any] = {
            "mode": config.mode,
            "source_policy": config.policy,
            "source_identity": identity,
            "snapshot": evidence.to_dict(),
            "steps": [
                {"id": step.id, "mutating": step.mutating, "description": step.description}
                for step in CoordinatorDeployment(config, _context(config)).steps()
            ],
            "target_staged": False,
        }
        if target_stage:
            context = _context(config)
            target = RemoteTarget(context, config.deploy_dir)
            context.ssh(
                (
                    "mkdir", "-p", config.deploy_dir, f"{config.deploy_dir}/.incoming",
                    f"{config.deploy_dir}/run_state/deploy_receipts",
                )
            )
            context.command(
                (
                    "rsync", "-az", "--delete", "-e",
                    _rsync_ssh_command(config.ssh_options),
                    os.fspath(evidence.path) + "/",
                    f"{config.target}:{target.incoming}/",
                ),
                cwd=config.root,
            )
            result["support_stage"] = target.run(
                "stage-support", "--snapshot", target.incoming,
            )
            result["app_stage"] = target.run("stage-app", "--snapshot", target.incoming)
            result["incoming_cleanup"] = target.run(
                "cleanup-snapshot", "--snapshot", target.incoming,
            )
            result["target_staged"] = True
        return result
    finally:
        if snapshot_path.exists():
            for path in snapshot_path.rglob("*"):
                if path.is_dir():
                    path.chmod(0o755)
            snapshot_path.chmod(0o755)
        temporary.cleanup()


def run_legacy(config: DeploymentConfig) -> int:
    """Run the retained monolithic leaf only through an explicit compatibility path."""
    if config.policy == "plan":
        raise ValueError("the plan source policy is read-only and cannot run the legacy leaf")
    if config.force_firmware:
        raise ValueError("forced firmware flashing is unavailable through the legacy leaf")
    scope = "full" if config.mode == "full" else "fast"
    plan = manifest_plan(config.root, scope)
    _validate_source_policy(config.root, plan, config.policy)
    if config.run_tests:
        command = (
            ("just", "deploy-precheck")
            if config.mode == "full"
            else ("just", "test-unit", "test-rendering", "test-deployment")
        )
        subprocess.run(command, cwd=config.root, check=True)
    leaf = (
        config.root / "tools" / "deployment" / "deploy.sh"
        if config.mode == "full"
        else config.root / "tools" / "deployment" / "deploy_python.sh"
    )
    completed = subprocess.run((os.fspath(leaf),), cwd=config.root, check=False)
    return completed.returncode


def deployment_plan(config: DeploymentConfig) -> Mapping[str, Any]:
    scope = "full" if config.mode == "full" else "fast"
    plan = manifest_plan(config.root, scope)
    identity = _validate_source_policy(config.root, plan, "plan")
    plan_config = replace(config, policy="plan")
    context = _context(plan_config)
    deployment = CoordinatorDeployment(plan_config, context)
    try:
        return {
            "mode": config.mode,
            "requested_policy": config.policy,
            "source": identity,
            "selected": [path.as_posix() for path in plan.selected],
            "excluded": [
                {"path": item.path.as_posix(), "reason": item.reason}
                for item in plan.excluded
            ],
            "app_inputs": [
                path.as_posix()
                for path in plan.selected
                if not _is_support_path(path) and not _is_native_build_path(path)
            ],
            "support_inputs": [path.as_posix() for path in plan.selected if _is_support_path(path)],
            "native_build_inputs": [
                path.as_posix()
                for path in plan.selected
                if _is_native_build_path(path)
            ],
            "receiver_background_work": {
                "outcome": "skipped",
                "reason": "native build/publish uses the explicit native workflow",
            },
            "force_firmware": config.force_firmware,
            "steps": [
                {"id": step.id, "mutating": step.mutating, "description": step.description}
                for step in deployment.steps()
            ],
            "target_layout": {
                "incoming": f"{config.deploy_dir}/.incoming/<deployment-id>",
                "app_releases": f"{config.deploy_dir}/releases/<content-digest>",
                "support_releases": f"{config.deploy_dir}/support_releases/<content-digest>",
                "native_background_library": (
                    f"{config.deploy_dir}/receiver_library/native_backgrounds"
                ),
                "current": f"{config.deploy_dir}/current",
                "receipts": f"{config.deploy_dir}/run_state/deploy_receipts",
                "legacy_app_bootstrap": (
                    f"{config.deploy_dir}/run_state/legacy_app_bootstrap.json"
                ),
            },
        }
    finally:
        deployment.close()


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=("full", "python"), required=True)
    parser.add_argument("--policy", choices=("clean", "dirty", "plan"), default="clean")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--target", default=os.environ.get("PI_HOST", DEFAULT_TARGET))
    parser.add_argument("--ssh-key", default=os.environ.get("SSH_KEY"))
    parser.add_argument("--deploy-dir", default=os.environ.get("DEPLOY_DIR", DEFAULT_DEPLOY_DIR))
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-previews", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--force-firmware",
        action="store_true",
        default=os.environ.get("FORCE_FIRMWARE_FLASH", "0").lower()
        in {"1", "true", "yes"},
        help="flash every attached receiver even when per-device evidence matches",
    )
    parser.add_argument("--strips", type=int, default=int(os.environ.get("STRIPS", "33")))
    parser.add_argument("--leds-per-strip", type=int, default=int(os.environ.get("LEDS_PER_STRIP", "138")))
    parser.add_argument("--receivers", type=int, default=int(os.environ.get("EXPECTED_ESP32_DEVICES", "5")))
    parser.add_argument(
        "--retain-releases",
        type=int,
        default=int(os.environ.get("DEPLOY_RETAIN_RELEASES", str(DEFAULT_RELEASE_RETENTION))),
    )


def _add_rollback_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("release_id")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--target", default=os.environ.get("PI_HOST", DEFAULT_TARGET))
    parser.add_argument("--ssh-key", default=os.environ.get("SSH_KEY"))
    parser.add_argument("--deploy-dir", default=os.environ.get("DEPLOY_DIR", DEFAULT_DEPLOY_DIR))
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--strips", type=int, default=int(os.environ.get("STRIPS", "33")))
    parser.add_argument("--leds-per-strip", type=int, default=int(os.environ.get("LEDS_PER_STRIP", "138")))
    parser.add_argument("--receivers", type=int, default=int(os.environ.get("EXPECTED_ESP32_DEVICES", "5")))
    parser.add_argument(
        "--retain-releases",
        type=int,
        default=int(os.environ.get("DEPLOY_RETAIN_RELEASES", str(DEFAULT_RELEASE_RETENTION))),
    )
    parser.set_defaults(
        mode="python",
        policy="clean",
        skip_tests=True,
        skip_previews=True,
        force_firmware=False,
    )


def _add_readonly_target_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--target", default=os.environ.get("PI_HOST", DEFAULT_TARGET))
    parser.add_argument("--ssh-key", default=os.environ.get("SSH_KEY"))
    parser.add_argument("--deploy-dir", default=os.environ.get("DEPLOY_DIR", DEFAULT_DEPLOY_DIR))
    parser.set_defaults(
        mode="python",
        policy="plan",
        skip_tests=True,
        skip_previews=True,
        verbose=False,
        strips=33,
        leds_per_strip=138,
        receivers=5,
        retain_releases=DEFAULT_RELEASE_RETENTION,
        force_firmware=False,
    )


def _config(args: argparse.Namespace) -> DeploymentConfig:
    test_env = os.environ.get("TEST", "true").lower() not in {"false", "0", "no"}
    root = args.root.resolve()
    return DeploymentConfig(
        root=root,
        mode=args.mode,
        policy=args.policy,
        target=args.target,
        deploy_dir=args.deploy_dir,
        run_tests=test_env and not args.skip_tests,
        verbose=args.verbose,
        generate_previews=not args.skip_previews,
        strips=args.strips,
        leds_per_strip=args.leds_per_strip,
        receiver_count=args.receivers,
        force_firmware=args.force_firmware,
        release_retention=args.retain_releases,
        ssh_options=_ssh_options(root, args.ssh_key),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run", "legacy"):
        child = subparsers.add_parser(name)
        _add_common(child)
    shadow = subparsers.add_parser("shadow")
    _add_common(shadow)
    shadow.add_argument(
        "--target-stage",
        action="store_true",
        help="upload and stage content-addressed releases without activating them",
    )
    rollback = subparsers.add_parser("rollback")
    _add_rollback_options(rollback)
    releases = subparsers.add_parser("releases")
    _add_readonly_target_options(releases)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    config = _config(args)
    if args.command == "plan":
        print(json.dumps(deployment_plan(config), indent=2, sort_keys=True))
        return 0
    if args.command == "shadow":
        print(
            json.dumps(
                shadow_deployment(config, target_stage=args.target_stage),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "legacy":
        return run_legacy(config)
    if args.command == "releases":
        print(json.dumps(list_releases(config), indent=2, sort_keys=True))
        return 0

    receipt = (
        run_rollback(config, args.release_id)
        if args.command == "rollback"
        else run_deployment(config)
    )
    print(json.dumps(receipt.to_dict(Redactor()), indent=2, sort_keys=True))
    if receipt.outcome != "success":
        return 1
    if receipt.persistence_errors:
        print(
            "deployment operations succeeded but required paired receipt persistence failed: "
            + "; ".join(receipt.persistence_errors),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
