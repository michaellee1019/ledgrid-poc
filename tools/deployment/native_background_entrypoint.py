#!/usr/bin/env python3
"""Build and publish repository-owned receiver-native backgrounds.

This is deliberately separate from application and firmware deployment.  It
never provisions, flashes, reboots, restarts systemd, changes display state, or
contacts a receiver.  Publication writes only to the Pi-authoritative managed
bundle library.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from animation.core.native_background_library import NativeBackgroundLibrary
    from tools.deployment.deploy_coordinator import (
        Artifact,
        AtomicJSONReceiptStore,
        DeployContext,
        DeployCoordinator,
        OperationResult,
        Redactor,
        SSHAtomicJSONReceiptStore,
        SSHRunner,
        Step,
        SubprocessRunner,
    )
    from tools.deployment.deploy_entrypoint import (
        DEFAULT_DEPLOY_DIR,
        DEFAULT_LOCAL_RECEIPTS,
        DEFAULT_TARGET,
        _rsync_ssh_command,
        _safe_deploy_dir,
        _ssh_options,
    )
except ModuleNotFoundError:  # Direct target-side invocation from current release.
    repository_root = Path(__file__).resolve().parents[2]
    if os.fspath(repository_root) not in sys.path:
        sys.path.insert(0, os.fspath(repository_root))
    from animation.core.native_background_library import NativeBackgroundLibrary
    from tools.deployment.deploy_coordinator import (
        Artifact,
        AtomicJSONReceiptStore,
        DeployContext,
        DeployCoordinator,
        OperationResult,
        Redactor,
        SSHAtomicJSONReceiptStore,
        SSHRunner,
        Step,
        SubprocessRunner,
    )
    from tools.deployment.deploy_entrypoint import (
        DEFAULT_DEPLOY_DIR,
        DEFAULT_LOCAL_RECEIPTS,
        DEFAULT_TARGET,
        _rsync_ssh_command,
        _safe_deploy_dir,
        _ssh_options,
    )


PLUGIN_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]*\Z")
TOKEN_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
NATIVE_BUILD_ROOT = Path("run_state/native_background_builds")
NATIVE_RECEIPT_DIRECTORY = DEFAULT_LOCAL_RECEIPTS.parent / "native-receipts"
FINALIZED_NATIVE_TOPOLOGY = {
    0: (8, 0, False),
    1: (8, 8, False),
    2: (8, 24, True),
    3: (8, 16, True),
    4: (1, 32, False),
}
PACKAGE_GLOBAL_INPUTS = (
    PurePosixPath("animation/core/component_catalog.py"),
    PurePosixPath("animation/core/plugin_loader.py"),
    PurePosixPath("animation/native"),
    PurePosixPath("firmware/esp32/include/ledgrid/native_background_abi_v2.h"),
    PurePosixPath("firmware/esp32/platformio.ini"),
    PurePosixPath("pyproject.toml"),
    PurePosixPath("uv.lock"),
)


class NativeBackgroundWorkflowError(RuntimeError):
    """A native build/publish request is unsafe or incomplete."""


def _inspect_bundle(source: bytes | Path):
    try:
        from animation.native.bundle import inspect_bundle
    except (ImportError, AttributeError) as exc:
        raise NativeBackgroundWorkflowError(
            "native bundle validator is unavailable"
        ) from exc
    return inspect_bundle(source)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return os.fspath(value)
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ("git", "-C", os.fspath(root), *args),
        check=True,
        capture_output=True,
    ).stdout


def _safe_plugin_id(value: str) -> str:
    if PLUGIN_ID_PATTERN.fullmatch(value) is None:
        raise NativeBackgroundWorkflowError(f"invalid native plugin ID: {value!r}")
    return value


def _tracked_paths(root: Path, plugin_id: str) -> tuple[PurePosixPath, ...]:
    plugin_id = _safe_plugin_id(plugin_id)
    plugin_root = PurePosixPath("animation/plugins") / plugin_id
    raw = _git(
        root,
        "ls-files",
        "-z",
        "--",
        (plugin_root / "manifest.json").as_posix(),
        (plugin_root / "native").as_posix(),
        *(path.as_posix() for path in PACKAGE_GLOBAL_INPUTS),
    )
    paths = tuple(
        sorted(
            (PurePosixPath(os.fsdecode(item)) for item in raw.split(b"\0") if item),
            key=lambda path: path.as_posix(),
        )
    )
    required = {
        plugin_root / "manifest.json",
        plugin_root / "native" / "background.cpp",
        PurePosixPath("firmware/esp32/include/ledgrid/native_background_abi_v2.h"),
    }
    missing = required - set(paths)
    if missing:
        raise NativeBackgroundWorkflowError(
            f"native package has untracked or missing required inputs: "
            f"{[path.as_posix() for path in sorted(missing, key=str)]}"
        )
    try:
        manifest = json.loads(
            (root / (plugin_root / "manifest.json").as_posix()).read_text(
                encoding="utf-8"
            )
        )
        build = manifest["build"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise NativeBackgroundWorkflowError(
            "native package manifest has no valid build contract"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("plugin_id") != plugin_id
        or manifest.get("provider") != "receiver_native"
        or not isinstance(build, dict)
        or build.get("source") != "native/background.cpp"
    ):
        raise NativeBackgroundWorkflowError(
            "native package manifest must bind its plugin ID and canonical native source"
        )
    untracked = _git(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        (plugin_root / "native").as_posix(),
    )
    untracked_paths = [
        os.fsdecode(item) for item in untracked.split(b"\0") if item
    ]
    if untracked_paths:
        raise NativeBackgroundWorkflowError(
            f"native build rejects untracked package inputs: {sorted(untracked_paths)}"
        )
    return paths


def native_source_plan(root: Path, plugin_id: str) -> dict[str, Any]:
    """Return the exact tracked working-tree identity for one native package."""

    root = root.resolve()
    paths = _tracked_paths(root, plugin_id)
    files: list[dict[str, Any]] = []
    digest = hashlib.sha256(b"ledgrid-native-source-v1\0")
    for relative in paths:
        path = root / relative.as_posix()
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise NativeBackgroundWorkflowError(
                f"native build input is unavailable: {relative}"
            ) from exc
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise NativeBackgroundWorkflowError(
                f"native build input must be a non-symlink regular file: {relative}"
            )
        payload = path.read_bytes()
        file_digest = hashlib.sha256(payload).hexdigest()
        executable = bool(metadata.st_mode & 0o111)
        relative_bytes = relative.as_posix().encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        digest.update(b"x" if executable else b"-")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        files.append(
            {
                "path": relative.as_posix(),
                "sha256": file_digest,
                "size": len(payload),
                "executable": executable,
            }
        )
    changed = {
        os.fsdecode(item)
        for item in _git(
            root,
            "diff",
            "--name-only",
            "-z",
            "HEAD",
            "--",
            *(path.as_posix() for path in paths),
        ).split(b"\0")
        if item
    }
    return {
        "schema_version": 1,
        "plugin_id": _safe_plugin_id(plugin_id),
        "base_commit": os.fsdecode(_git(root, "rev-parse", "HEAD")).strip(),
        "source_digest": digest.hexdigest(),
        "modified_tracked_inputs": sorted(changed),
        "files": files,
        "steps": [
            {"id": "receiver_background.build", "mutating": True},
            {"id": "receiver_background.publish", "mutating": True},
        ],
        "ordinary_deploy": {
            "app_restart": False,
            "dependency_work": False,
            "host_provision": False,
            "reboot": False,
            "firmware_build": False,
            "firmware_flash": False,
            "receiver_mutation": False,
        },
    }


def _build_plugin(root: Path, plugin_id: str):
    try:
        from animation.native.builder import build_plugin
    except (ImportError, AttributeError) as exc:
        raise NativeBackgroundWorkflowError("native builder is unavailable") from exc
    plan = native_source_plan(root, plugin_id)
    output_root = (root / NATIVE_BUILD_ROOT).resolve(strict=False)
    result = build_plugin(root, plugin_id, output_root, execute=True)
    verified = _inspect_bundle(result.bundle_path)
    if (
        verified.bundle_digest != result.bundle_digest
        or verified.payload_digest != result.payload_digest
    ):
        raise NativeBackgroundWorkflowError(
            "native builder result disagrees with bundle validation"
        )
    return plan, result, verified


def _current_native_provenance(root: Path, plugin_id: str) -> dict[str, str]:
    """Recreate the repository identities embedded by the native builder."""

    try:
        from animation.core.plugin_loader import AnimationPluginLoader
        from animation.native.schema import canonical_json

        loader = AnimationPluginLoader(os.fspath(root / "animation/plugins"))
        loader.scan_components()
        component = loader.component_manifests[plugin_id]
        package_directory = loader.component_dirs[plugin_id]
    except (ImportError, KeyError, OSError, TypeError, ValueError) as exc:
        raise NativeBackgroundWorkflowError(
            "current native component cannot be normalized for provenance"
        ) from exc
    expected_directory = (root / "animation/plugins" / plugin_id).absolute()
    if package_directory != expected_directory:
        raise NativeBackgroundWorkflowError(
            "current native component provenance escapes its canonical package"
        )
    normalized = {
        key: item for key, item in component.items() if not key.startswith("_")
    }
    source_path = expected_directory / "native/background.cpp"
    header_path = root / "firmware/esp32/include/ledgrid/native_background_abi_v2.h"
    return {
        "component_manifest_sha256": hashlib.sha256(
            canonical_json(normalized)
        ).hexdigest(),
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "header_sha256": hashlib.sha256(header_path.read_bytes()).hexdigest(),
    }


def _require_current_bundle_provenance(root: Path, plugin_id: str, verified: Any) -> None:
    current = _current_native_provenance(root, plugin_id)
    try:
        embedded = {
            "component_manifest_sha256": verified.manifest[
                "component_manifest_sha256"
            ],
            "source_sha256": verified.manifest["build"]["source_sha256"],
            "header_sha256": verified.manifest["abi"]["header_sha256"],
        }
    except (KeyError, TypeError) as exc:  # inspect_bundle normally rejects this first.
        raise NativeBackgroundWorkflowError(
            "native bundle has no complete repository provenance"
        ) from exc
    stale = sorted(key for key, value in current.items() if embedded.get(key) != value)
    if stale:
        raise NativeBackgroundWorkflowError(
            "prebuilt native bundle does not match current tracked source: "
            + ", ".join(stale)
        )


def _bundle_from_argument(root: Path, value: str):
    if PLUGIN_ID_PATTERN.fullmatch(value):
        return native_source_plan(root, value), None, None, None
    lexical_root = root.expanduser().absolute()
    lexical_build_root = lexical_root / NATIVE_BUILD_ROOT
    build_root = lexical_build_root.resolve(strict=False)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = lexical_root / candidate
    candidate = Path(os.path.abspath(os.fspath(candidate)))
    try:
        current = lexical_root
        for part in NATIVE_BUILD_ROOT.parts:
            current /= part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise NativeBackgroundWorkflowError(
                    "managed native build root contains a symbolic link"
                )
        relative = candidate.relative_to(lexical_build_root)
        current = lexical_build_root
        for part in relative.parts:
            current /= part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise NativeBackgroundWorkflowError(
                    "managed native bundle path contains a symbolic link"
                )
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(build_root)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise NativeBackgroundWorkflowError(
            "bundle path must resolve beneath the managed native build root"
        ) from exc
    if not stat.S_ISREG(candidate.lstat().st_mode):
        raise NativeBackgroundWorkflowError(
            "managed native bundle must be a non-symlink regular file"
        )
    verified = _inspect_bundle(candidate)
    plugin_id = verified.manifest.get("plugin_id")
    if not isinstance(plugin_id, str):
        raise NativeBackgroundWorkflowError("native bundle has no plugin_id")
    plan = native_source_plan(root, plugin_id)
    _require_current_bundle_provenance(root, plugin_id, verified)
    return plan, None, verified, candidate


def _context(
    *,
    root: Path,
    target: str,
    mode: str,
    source_identity: Mapping[str, Any],
    deploy_dir: str,
    ssh_options: Sequence[str],
) -> DeployContext:
    redactor = Redactor(
        secret_paths=(
            ssh_options[ssh_options.index("-i") + 1],
        )
        if "-i" in ssh_options
        else (),
    )
    runner = SubprocessRunner(redactor)
    ssh = SSHRunner(runner, target, ssh_options=ssh_options)
    sinks: list[Any] = [
        AtomicJSONReceiptStore(root / NATIVE_RECEIPT_DIRECTORY),
    ]
    if mode == "native-publish":
        sinks.append(
            SSHAtomicJSONReceiptStore(
                ssh,
                f"{deploy_dir}/run_state/deploy_receipts",
            )
        )
    return DeployContext(
        target=target,
        mode=mode,
        source_identity=source_identity,
        source_policy="tracked-native-package",
        paths={"root": root},
        redactor=redactor,
        command_runner=runner,
        ssh_runner=ssh,
        receipt_sinks=tuple(sinks),
        progress=lambda message: print(message, file=sys.stderr),
    )


def run_build(root: Path, plugin_id: str) -> Mapping[str, Any]:
    plan = native_source_plan(root, plugin_id)
    context = _context(
        root=root,
        target="local",
        mode="native-build",
        source_identity=plan,
        deploy_dir=DEFAULT_DEPLOY_DIR,
        ssh_options=(),
    )

    def build(_context: DeployContext) -> OperationResult:
        _plan, result, verified = _build_plugin(root, plugin_id)
        _context.state["build_result"] = result
        return OperationResult(
            details=_json_safe(result),
            artifacts=(
                Artifact(
                    "receiver_background_bundle",
                    plugin_id,
                    verified.bundle_digest,
                    "1",
                    target_id=verified.payload_digest,
                ),
            ),
        )

    receipt = DeployCoordinator().run(
        context,
        (
            Step(
                "receiver_background.build",
                True,
                build,
                "build, preview, and validate one tracked native package",
            ),
        ),
    )
    if receipt.outcome != "success" or receipt.persistence_errors:
        detail = receipt.error or "; ".join(receipt.persistence_errors)
        raise NativeBackgroundWorkflowError(detail or "native build failed")
    return {
        "deployment_id": receipt.deployment_id,
        "outcome": receipt.outcome,
        "artifacts": [artifact.to_dict() for artifact in receipt.artifacts],
        "build": _json_safe(context.state["build_result"]),
    }


def _run_publish_receipt(
    root: Path,
    bundle_or_plugin: str,
    *,
    target: str,
    deploy_dir: str,
    ssh_options: Sequence[str],
    mode: str,
    extra_steps: Sequence[Step] = (),
):
    plan, prebuilt_result, verified, bundle_path = _bundle_from_argument(
        root, bundle_or_plugin
    )
    context = _context(
        root=root,
        target=target,
        mode=mode,
        source_identity=plan,
        deploy_dir=deploy_dir,
        ssh_options=ssh_options,
    )
    context.state["verified"] = verified
    context.state["build_result"] = prebuilt_result
    context.state["bundle_path"] = bundle_path
    safe_deploy_dir = _safe_deploy_dir(deploy_dir).as_posix()
    library_root = f"{safe_deploy_dir}/receiver_library/native_backgrounds"
    helper = f"{safe_deploy_dir}/current/tools/deployment/native_background_entrypoint.py"
    runtime_python = f"{safe_deploy_dir}/venv/bin/python"

    def build(_context: DeployContext) -> OperationResult:
        outcome = "skipped"
        details: Mapping[str, Any] = {
            "reason": "validated managed bundle supplied"
        }
        if _context.state["build_result"] is None and PLUGIN_ID_PATTERN.fullmatch(
            bundle_or_plugin
        ):
            _plan, result, rebuilt = _build_plugin(root, bundle_or_plugin)
            _context.state["build_result"] = result
            _context.state["verified"] = rebuilt
            _context.state["bundle_path"] = Path(result.bundle_path)
            outcome = "executed"
            details = _json_safe(result)
        candidate = _context.state["verified"]
        if candidate is None:
            raise NativeBackgroundWorkflowError(
                "native build produced no validated managed bundle"
            )
        return OperationResult(
            outcome=outcome,
            details=details,
            artifacts=(
                Artifact(
                    "receiver_background_bundle",
                    str(candidate.manifest["plugin_id"]),
                    candidate.bundle_digest,
                    "1",
                    target_id=candidate.payload_digest,
                ),
            ),
        )

    def publish(_context: DeployContext) -> OperationResult:
        candidate = _context.state["verified"]
        if candidate is None or _context.state["bundle_path"] is None:
            raise NativeBackgroundWorkflowError(
                "native publication has no validated managed bundle"
            )
        token = _context.attempt_id
        prepared = _context.ssh(
            (
                runtime_python,
                helper,
                "library-prepare",
                "--library-root",
                library_root,
                "--token",
                token,
            )
        )
        try:
            preparation = json.loads(prepared.stdout)
            remote_path = preparation["incoming_path"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise NativeBackgroundWorkflowError(
                "target native library returned malformed upload preparation"
            ) from exc
        expected_remote_path = f"{library_root}/.incoming/{token}.zip"
        if remote_path != expected_remote_path:
            raise NativeBackgroundWorkflowError(
                "target native library returned an unexpected managed upload path"
            )
        local_recheck = _inspect_bundle(_context.state["bundle_path"])
        if (
            local_recheck.bundle_digest != candidate.bundle_digest
            or local_recheck.payload_digest != candidate.payload_digest
        ):
            raise NativeBackgroundWorkflowError(
                "managed local bundle changed after workflow validation"
            )
        _context.command(
            (
                "rsync",
                "-az",
                "-e",
                _rsync_ssh_command(ssh_options),
                os.fspath(_context.state["bundle_path"]),
                f"{target}:{remote_path}",
            ),
            cwd=root,
        )
        completed = _context.ssh(
            (
                runtime_python,
                helper,
                "library-publish",
                "--library-root",
                library_root,
                "--token",
                token,
            )
        )
        try:
            details = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise NativeBackgroundWorkflowError(
                "target native library returned malformed publication evidence"
            ) from exc
        if (
            details.get("package_id") != candidate.manifest.get("plugin_id")
            or details.get("bundle_digest") != candidate.bundle_digest
            or details.get("payload_digest") != candidate.payload_digest
        ):
            raise NativeBackgroundWorkflowError(
                "target native publication evidence disagrees with local validation"
            )
        _context.state["publication_result"] = details
        return OperationResult(
            details=details,
            artifacts=(
                Artifact(
                    "receiver_background_library_bundle",
                    str(candidate.manifest["plugin_id"]),
                    candidate.bundle_digest,
                    "1",
                    target_id=candidate.payload_digest,
                ),
            ),
        )

    steps = (
        Step(
            "receiver_background.build",
            True,
            build,
            "build one tracked package or validate a managed local bundle",
        ),
        Step(
            "receiver_background.publish",
            True,
            publish,
            "publish only to the Pi-authoritative native bundle library",
        ),
    ) + tuple(extra_steps)
    receipt = DeployCoordinator().run(context, steps)
    if receipt.outcome != "success" or receipt.persistence_errors:
        detail = receipt.error or "; ".join(receipt.persistence_errors)
        raise NativeBackgroundWorkflowError(detail or "native publish failed")
    return receipt, context


def run_publish(
    root: Path,
    bundle_or_plugin: str,
    *,
    target: str,
    deploy_dir: str,
    ssh_options: Sequence[str],
) -> Mapping[str, Any]:
    receipt, _context = _run_publish_receipt(
        root,
        bundle_or_plugin,
        target=target,
        deploy_dir=deploy_dir,
        ssh_options=ssh_options,
        mode="native-publish",
    )
    return {
        "deployment_id": receipt.deployment_id,
        "outcome": receipt.outcome,
        "artifacts": [artifact.to_dict() for artifact in receipt.artifacts],
    }


def _safe_library_root(value: Path) -> Path:
    root = value.expanduser().absolute()
    if root.name != "native_backgrounds" or root.parent.name != "receiver_library":
        raise NativeBackgroundWorkflowError(
            "native publication root must be receiver_library/native_backgrounds"
        )
    return root


def _incoming_path(library_root: Path, token: str) -> Path:
    if TOKEN_PATTERN.fullmatch(token) is None:
        raise NativeBackgroundWorkflowError("invalid native upload token")
    incoming = library_root / ".incoming"
    candidate = incoming / f"{token}.zip"
    candidate.resolve(strict=False).relative_to(incoming.resolve(strict=False))
    return candidate


def library_prepare(library_root: Path, token: str) -> Mapping[str, Any]:
    root = _safe_library_root(library_root)
    incoming_path = _incoming_path(root, token)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise NativeBackgroundWorkflowError("native library root is unsafe")
    incoming_path.parent.mkdir(exist_ok=True)
    if incoming_path.parent.is_symlink() or not incoming_path.parent.is_dir():
        raise NativeBackgroundWorkflowError("native incoming directory is unsafe")
    if os.path.lexists(incoming_path):
        raise NativeBackgroundWorkflowError("native incoming upload token already exists")
    return {"incoming_path": os.fspath(incoming_path)}


def library_publish(library_root: Path, token: str) -> Mapping[str, Any]:
    root = _safe_library_root(library_root)
    incoming_path = _incoming_path(root, token)
    if incoming_path.is_symlink() or not incoming_path.is_file():
        raise NativeBackgroundWorkflowError("native incoming bundle is unavailable")
    library = NativeBackgroundLibrary(root)
    try:
        receipt = library.publish(incoming_path)
        resolved = library.resolve(receipt.bundle_digest)
    finally:
        incoming_path.unlink(missing_ok=True)
    return {
        **receipt.to_dict(),
        "bundle_path": os.fspath(resolved.bundle_path),
        "payload_path": os.fspath(resolved.payload_path),
    }


def _api_json(
    target: str,
    path: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    host = target.rsplit("@", 1)[-1]
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"http://{host}:5000{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise NativeBackgroundWorkflowError(
            f"receiver-native API request failed: {exc}"
        ) from exc
    if not isinstance(result, dict):
        raise NativeBackgroundWorkflowError("receiver-native API returned no object")
    return result


def _remote_native_descriptor(
    target: str, selector: str, *, timeout: float
) -> dict[str, Any]:
    catalog = _api_json(
        target,
        "/api/v1/components?provider=receiver_native&role=background",
        timeout=timeout,
    ).get("components")
    if not isinstance(catalog, list):
        raise NativeBackgroundWorkflowError("target returned no native catalog")
    matches = []
    for item in catalog:
        if not isinstance(item, dict):
            continue
        build = item.get("build") if isinstance(item.get("build"), dict) else {}
        if item.get("plugin_id") == selector or build.get("bundle_digest") == selector:
            matches.append(item)
    if len(matches) != 1:
        raise NativeBackgroundWorkflowError(
            f"native selector must identify one managed catalog entry: {selector}"
        )
    build = matches[0].get("build") or {}
    if (
        DIGEST_PATTERN.fullmatch(str(build.get("bundle_digest", ""))) is None
        or DIGEST_PATTERN.fullmatch(
            str(build.get("expected_payload_digest", ""))
        ) is None
    ):
        raise NativeBackgroundWorkflowError(
            "target native catalog entry has no managed artifact binding"
        )
    return matches[0]


def _wait_native_command(
    target: str,
    command_id: Any,
    *,
    bundle_digest: str,
    payload_digest: str,
    expected_state: str,
    expected_operation: str,
    expected_parameter_digest: str | None = None,
    expected_scene: Mapping[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _api_json(target, "/api/status", timeout=min(timeout, 10.0))
        if (
            status.get("last_command_id") == command_id
            and _native_command_status_error(
                status,
                bundle_digest=bundle_digest,
                payload_digest=payload_digest,
                expected_state=expected_state,
                expected_operation=expected_operation,
                expected_parameter_digest=expected_parameter_digest,
                expected_scene=expected_scene,
            ) is None
        ):
            return status
        time.sleep(0.1)
    raise NativeBackgroundWorkflowError(
        f"target did not prove native {expected_state} state before timeout"
    )


def _native_command_status_error(
    status: Mapping[str, Any],
    *,
    bundle_digest: str,
    payload_digest: str,
    expected_state: str,
    expected_operation: str,
    expected_parameter_digest: str | None = None,
    expected_scene: Mapping[str, Any] | None = None,
) -> str | None:
    """Return why a command status cannot prove one exact native outcome."""

    receiver = status.get("receiver_hybrid")
    if not isinstance(receiver, Mapping):
        return "receiver-native status is unavailable"
    driver = receiver.get("driver")
    if not isinstance(driver, Mapping):
        return "native driver status is unavailable"
    if (
        driver.get("state") != expected_state
        or driver.get("operation") != expected_operation
        or driver.get("bundle_digest") != bundle_digest
        or driver.get("payload_digest") != payload_digest
        or driver.get("error") is not None
    ):
        return "native driver outcome does not match the command"
    report = driver.get("capability_report")
    devices = report.get("devices") if isinstance(report, Mapping) else None
    required = (
        report.get("required_capabilities")
        if isinstance(report, Mapping) else None
    )
    if type(required) is not int or required <= 0 or not isinstance(devices, list):
        return "native capability report is incomplete"
    topology = {
        item.get("logical_device"): (
            item.get("local_strip_count"),
            item.get("global_strip_offset"),
            item.get("reverse_native_strip_order"),
        )
        for item in devices if isinstance(item, Mapping)
    }
    capabilities = {
        item.get("logical_device"): item.get("capabilities")
        for item in devices if isinstance(item, Mapping)
    }
    if topology != FINALIZED_NATIVE_TOPOLOGY:
        return "native capability topology is not the finalized exact roster"
    if any(
        type(capabilities.get(receiver_id)) is not int
        or capabilities[receiver_id] & required != required
        for receiver_id in FINALIZED_NATIVE_TOPOLOGY
    ):
        return "native capability mask is incomplete"

    if expected_state != "active":
        return None
    scene_status = status.get("scene")
    if (
        expected_scene is None
        or status.get("scene_state") != dict(expected_scene)
        or not isinstance(scene_status, Mapping)
        or scene_status.get("provider_mode") != "receiver_native"
        or receiver.get("healthy") is not True
        or receiver.get("operational") is not True
        or receiver.get("fallback_active") is not False
        or receiver.get("error") is not None
    ):
        return "active scene/receiver authority is incomplete"
    agreement = driver.get("agreement")
    if (
        not isinstance(agreement, Mapping)
        or agreement.get("exact_roster") is not True
        or agreement.get("verified_receiver_ids") != [0, 1, 2, 3, 4]
    ):
        return "native active agreement is not exact"
    if (
        expected_parameter_digest is None
        or driver.get("parameter_digest") != expected_parameter_digest
    ):
        return "native active parameter binding is stale"
    context_digest = driver.get("context_digest")
    profile_digest = driver.get("installation_profile_digest")
    if (
        not isinstance(context_digest, str)
        or DIGEST_PATTERN.fullmatch(context_digest) is None
        or not isinstance(profile_digest, str)
        or DIGEST_PATTERN.fullmatch(profile_digest) is None
        or status.get("installation_profile_digest") != profile_digest
    ):
        return "native context/profile authority is unavailable"
    driver_stats = status.get("driver_stats")
    statuses = (
        driver_stats.get("devices")
        if isinstance(driver_stats, Mapping) else None
    )
    if not isinstance(statuses, list) or len(statuses) != 5:
        return "native per-receiver status is incomplete"
    by_id = {
        item.get("receiver_logical_device"): item
        for item in statuses if isinstance(item, Mapping)
    }
    if set(by_id) != set(FINALIZED_NATIVE_TOPOLOGY):
        return "native per-receiver identities are incomplete"
    shared_fields = (
        "receiver_vibe_revision",
        "receiver_vibe_digest",
        "receiver_plant_modifier_revision",
        "receiver_plant_modifier_digest",
    )
    shared = {field: by_id[0].get(field) for field in shared_fields}
    if any(value in (None, "") for value in shared.values()):
        return "native vibe/plant agreement is unavailable"
    for receiver_id, device in by_id.items():
        if (
            device.get("receiver_status_seen") is not True
            or int(device.get("receiver_status_version", 0) or 0) < 6
            or device.get("receiver_native_executing") is not True
            or device.get("receiver_native_cache_integrity_ok") is not True
            or device.get("receiver_native_active_bundle_digest") != bundle_digest
            or device.get("receiver_native_active_payload_digest") != payload_digest
            or device.get("receiver_native_active_parameter_digest")
            != expected_parameter_digest
            or device.get("receiver_active_context_digest") != context_digest
            or device.get("receiver_profile_active_global_digest") != profile_digest
            or any(device.get(field) != value for field, value in shared.items())
        ):
            return f"native receiver {receiver_id} agreement is stale or incomplete"
    return None


def run_install(
    target: str, selector: str, *, timeout: float = 60.0
) -> Mapping[str, Any]:
    descriptor = _remote_native_descriptor(target, selector, timeout=timeout)
    build = descriptor["build"]
    bundle_digest = build["bundle_digest"]
    response = _api_json(
        target,
        f"/api/v1/native-backgrounds/{bundle_digest}/install",
        method="POST",
        timeout=timeout,
    )
    status = _wait_native_command(
        target,
        response.get("command_id"),
        bundle_digest=bundle_digest,
        payload_digest=build["expected_payload_digest"],
        expected_state="ready",
        expected_operation="install",
        timeout=timeout,
    )
    return {
        "operation": "install",
        "plugin_id": descriptor["plugin_id"],
        "bundle_digest": bundle_digest,
        "payload_digest": build["expected_payload_digest"],
        "command_id": response.get("command_id"),
        "native_background": status["receiver_hybrid"]["driver"],
    }


def run_start(
    target: str,
    selector: str,
    *,
    fallback_plugin: str = "aurora_curtains",
    timeout: float = 60.0,
) -> Mapping[str, Any]:
    raise NativeBackgroundWorkflowError(
        "native start is retired and made no target changes; select the managed "
        "native component in Composer, run Check, and use guarded activation"
    )


def run_native_run(
    root: Path,
    plugin_id: str,
    *,
    target: str,
    deploy_dir: str,
    ssh_options: Sequence[str],
    fallback_plugin: str = "aurora_curtains",
    timeout: float = 60.0,
) -> Mapping[str, Any]:
    """Reject before building, publishing, installing, or contacting a target."""

    raise NativeBackgroundWorkflowError(
        "native run is retired and made no build, publication, installation, or "
        "target changes; build/publish/install explicitly if needed, then select "
        "the managed component in Composer and use Check + guarded activation"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("plugin_id")
    build = subparsers.add_parser("build")
    build.add_argument("plugin_id")
    publish = subparsers.add_parser("publish")
    publish.add_argument("bundle_or_plugin")
    publish.add_argument("--target", default=os.environ.get("PI_HOST", DEFAULT_TARGET))
    publish.add_argument("--ssh-key", default=os.environ.get("SSH_KEY"))
    publish.add_argument(
        "--deploy-dir", default=os.environ.get("DEPLOY_DIR", DEFAULT_DEPLOY_DIR)
    )

    for name in ("install", "start", "run"):
        operation = subparsers.add_parser(name)
        operation.add_argument("selector")
        operation.add_argument(
            "--target", default=os.environ.get("PI_HOST", DEFAULT_TARGET)
        )
        operation.add_argument("--timeout", type=float, default=60.0)
        if name in {"start", "run"}:
            operation.add_argument("--fallback", default="aurora_curtains")
        if name == "run":
            operation.add_argument("--ssh-key", default=os.environ.get("SSH_KEY"))
            operation.add_argument(
                "--deploy-dir", default=os.environ.get("DEPLOY_DIR", DEFAULT_DEPLOY_DIR)
            )

    for name in ("library-prepare", "library-publish"):
        library = subparsers.add_parser(name)
        library.add_argument("--library-root", type=Path, required=True)
        library.add_argument("--token", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "plan":
            result = native_source_plan(root, args.plugin_id)
        elif args.command == "build":
            result = run_build(root, args.plugin_id)
        elif args.command == "publish":
            result = run_publish(
                root,
                args.bundle_or_plugin,
                target=args.target,
                deploy_dir=args.deploy_dir,
                ssh_options=_ssh_options(root, args.ssh_key),
            )
        elif args.command == "install":
            result = run_install(
                args.target, args.selector, timeout=args.timeout
            )
        elif args.command == "start":
            result = run_start(
                args.target,
                args.selector,
                fallback_plugin=args.fallback,
                timeout=args.timeout,
            )
        elif args.command == "run":
            result = run_native_run(
                root,
                args.selector,
                target=args.target,
                deploy_dir=args.deploy_dir,
                ssh_options=_ssh_options(root, args.ssh_key),
                fallback_plugin=args.fallback,
                timeout=args.timeout,
            )
        elif args.command == "library-prepare":
            result = library_prepare(args.library_root, args.token)
        else:
            result = library_publish(args.library_root, args.token)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(_json_safe(result), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
