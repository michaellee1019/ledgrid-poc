#!/usr/bin/env python3
"""Create, verify, and atomically select a digest-addressed runtime venv."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from typing import Callable


IDENTITY_MARKER = ".ledgrid-runtime-identity.json"
ENVIRONMENTS_DIR = ".venvs"


@dataclass(frozen=True)
class RuntimeIdentity:
    """Inputs that determine whether an environment can be reused safely."""

    implementation: str
    python_version: str
    soabi: str
    machine: str
    platform: str
    lock_sha256: str

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> dict[str, str]:
        return {
            "implementation": self.implementation,
            "python_version": self.python_version,
            "soabi": self.soabi,
            "machine": self.machine,
            "platform": self.platform,
            "lock_sha256": self.lock_sha256,
        }


@dataclass(frozen=True)
class EnsureResult:
    path: Path
    identity: RuntimeIdentity
    installed: bool
    migrated_legacy: Path | None = None


def _lock_digest(lock_path: Path) -> str:
    digest = hashlib.sha256()
    with lock_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_interpreter_identity(lock_path: Path) -> RuntimeIdentity:
    """Resolve lock and interpreter/platform identity without environment mutation."""
    return RuntimeIdentity(
        implementation=sys.implementation.name,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        soabi=sysconfig.get_config_var("SOABI") or "unknown",
        machine=platform.machine() or "unknown",
        platform=sys.platform,
        lock_sha256=_lock_digest(lock_path),
    )


def environment_path(root: Path, identity: RuntimeIdentity) -> Path:
    implementation = "".join(
        char if char.isalnum() or char in "-_" else "-"
        for char in identity.implementation
    )
    python_minor = ".".join(identity.python_version.split(".")[:2])
    return root / ENVIRONMENTS_DIR / (
        f"runtime-{implementation}-{python_minor}-{identity.digest[:24]}"
    )


def _marker_matches(path: Path, identity: RuntimeIdentity) -> bool:
    try:
        payload = json.loads((path / IDENTITY_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return payload == identity.as_dict() and (path / "bin" / "python").is_file()


def _create_venv(base_python: Path, path: Path) -> None:
    subprocess.run([os.fspath(base_python), "-m", "venv", os.fspath(path)], check=True)


def _install_lock(venv_python: Path, lock_path: Path) -> None:
    # Prefer uv's standalone binary: Python 3.12+ venvs do not necessarily
    # contain pip, and the lock is already a complete, hash-checked set.
    uv = shutil.which("uv")
    if uv:
        subprocess.run(
            [
                uv,
                "pip",
                "sync",
                "--python",
                os.fspath(venv_python),
                "--require-hashes",
                os.fspath(lock_path),
            ],
            check=True,
        )
        return
    subprocess.run(
        [
            os.fspath(venv_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--require-hashes",
            "--no-deps",
            "--requirement",
            os.fspath(lock_path),
        ],
        check=True,
    )


def smoke_runtime(root: Path) -> None:
    """Import both deployed entrypoint surfaces without starting hardware or Flask."""
    root = root.resolve()
    sys.path.insert(0, os.fspath(root))
    try:
        import scripts.start_server as controller_entrypoint  # noqa: F401
        from web.app import create_app  # noqa: F401
    finally:
        try:
            sys.path.remove(os.fspath(root))
        except ValueError:
            pass


def _smoke_subprocess(venv_python: Path, root: Path) -> None:
    subprocess.run(
        [
            os.fspath(venv_python),
            os.fspath(root / "tools" / "deployment" / "runtime_env.py"),
            "smoke",
            "--root",
            os.fspath(root),
        ],
        check=True,
        cwd=root,
    )


def _activate_link(root: Path, selected: Path, link_name: str) -> Path | None:
    link = root / link_name
    migrated: Path | None = None
    if link.exists() and not link.is_symlink():
        legacy_digest = hashlib.sha256(os.fsencode(str(link.resolve()))).hexdigest()[:12]
        migrated = root / ENVIRONMENTS_DIR / f"legacy-{link.name}-{legacy_digest}"
        if migrated.exists():
            raise RuntimeError(
                f"cannot preserve legacy environment: destination exists: {migrated}"
            )
        link.rename(migrated)
    temporary_link = link.with_name(f".{link.name}.next-{os.getpid()}")
    try:
        temporary_link.unlink(missing_ok=True)
        temporary_link.symlink_to(os.path.relpath(selected, start=link.parent))
        os.replace(temporary_link, link)
    finally:
        temporary_link.unlink(missing_ok=True)
    return migrated


def ensure_runtime_environment(
    root: Path,
    lock_path: Path,
    *,
    base_python: Path = Path(sys.executable),
    link_name: str = "venv",
    create_venv: Callable[[Path, Path], None] = _create_venv,
    install_lock: Callable[[Path, Path], None] = _install_lock,
    smoke: Callable[[Path, Path], None] = _smoke_subprocess,
) -> EnsureResult:
    """Build a fresh candidate, smoke it, then atomically select it."""
    root = root.resolve()
    lock_path = lock_path.resolve()
    identity = current_interpreter_identity(lock_path)
    environments = root / ENVIRONMENTS_DIR
    environments.mkdir(parents=True, exist_ok=True)
    selected = environment_path(root, identity)
    installed = False

    if not _marker_matches(selected, identity):
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{selected.name}.build-", dir=environments)
        )
        try:
            create_venv(base_python, temporary)
            venv_python = temporary / "bin" / "python"
            if not venv_python.is_file():
                raise RuntimeError("virtual environment creation produced no bin/python")
            install_lock(venv_python, lock_path)
            smoke(venv_python, root)
            (temporary / IDENTITY_MARKER).write_text(
                json.dumps(identity.as_dict(), sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            invalid: Path | None = None
            if selected.exists():
                invalid = Path(
                    tempfile.mkdtemp(
                        prefix=f".{selected.name}.invalid-", dir=environments
                    )
                )
                invalid.rmdir()
                selected.rename(invalid)
            try:
                temporary.rename(selected)
            except BaseException:
                if invalid is not None and not selected.exists():
                    invalid.rename(selected)
                raise
            if invalid is not None:
                shutil.rmtree(invalid, ignore_errors=True)
            installed = True
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    migrated = _activate_link(root, selected, link_name)
    return EnsureResult(
        path=selected,
            identity=identity,
        installed=installed,
        migrated_legacy=migrated,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure_parser = subparsers.add_parser("ensure")
    ensure_parser.add_argument("--root", type=Path, default=Path.cwd())
    ensure_parser.add_argument("--lock", type=Path, default=Path("requirements-pi.lock"))
    ensure_parser.add_argument("--link", default="venv")

    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.add_argument("--root", type=Path, default=Path.cwd())

    identity_parser = subparsers.add_parser("identity")
    identity_parser.add_argument("--lock", type=Path, default=Path("requirements-pi.lock"))

    args = parser.parse_args()
    if args.command == "smoke":
        smoke_runtime(args.root)
        return
    if args.command == "identity":
        identity = current_interpreter_identity(args.lock)
        print(json.dumps({**identity.as_dict(), "digest": identity.digest}, sort_keys=True))
        return

    result = ensure_runtime_environment(
        args.root,
        args.lock,
        base_python=Path(sys.executable),
        link_name=args.link,
    )
    print(
        json.dumps(
            {
                "identity": result.identity.digest,
                "installed": result.installed,
                "path": os.fspath(result.path),
                "migrated_legacy": (
                    os.fspath(result.migrated_legacy)
                    if result.migrated_legacy is not None
                    else None
                ),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
