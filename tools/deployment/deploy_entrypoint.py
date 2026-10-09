#!/usr/bin/env python3
"""Copy the current application and restart the installed wall.

Failure leaves the service stopped or the candidate installed. Rerun deployment
or inspect the service journal; there are no release receipts or rollbacks.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import tempfile

DEFAULT_TARGET = "ledgridwall@ledgridwall.local"
DEFAULT_DEPLOY_DIR = "ledgrid-pod"
SOURCE_ROOTS = {"animation", "config", "drivers", "ipc", "scripts", "tools", "web", "firmware"}
SOURCE_FILES = {"pyproject.toml", "uv.lock", "requirements-pi.lock", "requirements-platformio.lock", "requirements.txt"}
PERSONAL_ROOTS = {"run_state", "presets", "calibration_photos", "logs"}


def safe_deploy_dir(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or path == PurePosixPath("."):
        raise ValueError("DEPLOY_DIR must be a named directory below the target home")
    # The remote shell uses this as a literal subdirectory of $HOME.
    if any(not (char.isalnum() or char in "-_./") for char in value):
        raise ValueError("DEPLOY_DIR contains unsupported characters")
    return str(path)


def ssh_options(root: Path, key: str | None) -> list[str]:
    identity = Path(key or root / ".gpt-key").expanduser()
    if not identity.is_absolute():
        identity = root / identity
    if not identity.is_file() or not os.access(identity, os.R_OK):
        raise ValueError(f"Dedicated SSH key is missing or unreadable: {identity}")
    return ["-i", str(identity.resolve()), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


def source_paths(root: Path) -> list[Path]:
    """Select maintained source, never local personal data or generated assets."""
    output = subprocess.check_output(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"])
    paths = set()
    for name in output.split(b"\0"):
        if not name:
            continue
        relative = Path(os.fsdecode(name))
        if relative.parts[0] in SOURCE_ROOTS or relative.as_posix() in SOURCE_FILES:
            if relative.parts[:3] == ("web", "static", "generated"):
                continue
            source = root / relative
            if source.is_file() and not source.is_symlink() and ".pio" not in relative.parts:
                paths.add(relative)
    return sorted(paths)


def stage_source(root: Path, destination: Path) -> None:
    for relative in source_paths(root):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)


def fetch_calibration(stage: Path, target: str, deploy_dir: str, options: list[str]) -> None:
    """Build previews from the same editable calibration used by the wall."""
    remote_root = f'"$HOME/{deploy_dir}"'
    command = (
        f'for location in {remote_root}/data/config {remote_root}/current/config '
        f'{remote_root}/app/config {remote_root}/config; do '
        'if [ -d "$location" ]; then printf "%s" "$location"; break; fi; done'
    )
    source = subprocess.check_output(["ssh", *options, target, command], text=True).strip()
    if source:
        subprocess.run(["rsync", "-azL", "--chmod=u+w", "--include=*.json", "--exclude=*", "-e",
                        shlex.join(["ssh", *options]), f"{target}:{source}/", str(stage / "config") + "/"], check=True)


def run_deployment(root: Path, args: argparse.Namespace) -> None:
    deploy_dir = safe_deploy_dir(args.deploy_dir)
    options = ssh_options(root, args.ssh_key)
    if args.target.startswith("-"):
        raise ValueError("Invalid SSH target")
    remote_root = f'"$HOME/{deploy_dir}"'
    remote_incoming = f'"$HOME/{deploy_dir}/incoming"'
    with tempfile.TemporaryDirectory(prefix="ledgrid-copy-") as temporary:
        stage = Path(temporary)
        stage_source(root, stage)
        fetch_calibration(stage, args.target, deploy_dir, options)
        # Build from source in the upload staging tree before stopping the wall.
        # Never copy stale generated assets or write build output into the checkout.
        subprocess.run(["uv", "run", "--frozen", "--group", "calibration", "python", "tools/build_browser_composer_assets.py"], cwd=stage, check=True)
        subprocess.run(["ssh", *options, args.target, f"mkdir -p {remote_root}; rm -rf -- {remote_incoming}; mkdir -p {remote_incoming}"], check=True)
        subprocess.run(["rsync", "-az", "--delete", "--exclude=.venv/", "--exclude=__pycache__/", "-e", shlex.join(["ssh", *options]), f"{stage}/", f"{args.target}:{deploy_dir}/incoming/"], check=True)
        command = f"python3 {remote_incoming}/tools/deployment/deploy_target.py --root {remote_root} --mode {args.mode} --timeout {args.timeout}"
        subprocess.run(["ssh", *options, args.target, command], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "plan"])
    parser.add_argument("--mode", choices=["full", "python"], default="full")
    parser.add_argument("--target", default=os.environ.get("PI_HOST", DEFAULT_TARGET))
    parser.add_argument("--deploy-dir", default=os.environ.get("DEPLOY_DIR", DEFAULT_DEPLOY_DIR))
    parser.add_argument("--ssh-key", default=os.environ.get("SSH_KEY"))
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    root = Path(__file__).resolve().parents[2]
    try:
        safe_deploy_dir(args.deploy_dir)
        ssh_options(root, args.ssh_key)
        if args.command == "plan":
            print(f"{args.target}: ~/{args.deploy_dir}/app; personal data: ~/{args.deploy_dir}/data")
            print("Build browser assets, upload, stop, back up/migrate data, replace app, install dependencies,")
            print("flash the five mapped receivers, " if args.mode == "full" else "", end="")
            print("start, check service and HTTP readiness. Failures require manual recovery.")
        else:
            run_deployment(root, args)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Deployment failed: {exc}. Inspect the failing step and rerun deployment.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
