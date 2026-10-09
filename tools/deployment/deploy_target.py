#!/usr/bin/env python3
"""Target-side stop/copy/build/start deployment for the installed wall."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

DATA_DIRECTORIES = ("run_state", "presets", "calibration_photos", "logs", "config")
BACKUP_ONLY_DIRECTORIES = ("installation_profile_library",)


def copy_missing(source: Path, destination: Path) -> None:
    """Preserve the first existing value and allow an interrupted copy to resume."""
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            copy_missing(child, destination / child.name)
    elif source.is_file() and not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.migration")
        shutil.copy2(source, temporary)
        temporary.chmod(temporary.stat().st_mode | 0o200)
        temporary.replace(destination)


def migrate_data(root: Path) -> Path:
    """Copy existing personal data with a separate local backup before replacing app."""
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    marker = data / ".migration-complete"
    if marker.exists():
        return data
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = root / "data-backups" / stamp
    backup.mkdir(parents=True)
    # The former immutable layout kept most writable data at root; calibration
    # is release-owned. Prefer the selected release for calibration, and root
    # for writable state. Existing data/ remains authoritative on retries.
    sources = [root, root / "current", root / "app"]
    for index, source_root in enumerate(sources):
        for name in (*DATA_DIRECTORIES, *BACKUP_ONLY_DIRECTORIES):
            source = source_root / name
            if not source.exists() or source.resolve() == (data / name).resolve():
                continue
            copy_missing(source, backup / str(index) / name)
    for name in DATA_DIRECTORIES:
        order = (1, 2, 0) if name == "config" else (0, 1, 2)
        for index in order:
            source = backup / str(index) / name
            if source.exists():
                copy_missing(source, data / name)
    for name in DATA_DIRECTORIES:
        (data / name).mkdir(parents=True, exist_ok=True)
    marker.write_text(f"Backup: {backup}\n", encoding="utf-8")
    return data


def link_data(root: Path, app: Path, data: Path) -> None:
    for name in DATA_DIRECTORIES:
        target = app / name
        if name == "config":
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(target.stat().st_mode | 0o200)
            for source in list(target.glob("*.json")):
                copy_missing(source, data / "config" / source.name)
                source.unlink()
            for source in (data / "config").glob("*.json"):
                (target / source.name).symlink_to(source)
            continue
        if target.exists():
            shutil.rmtree(target)
        target.symlink_to(data / name, target_is_directory=True)
    (app / "venv").symlink_to(root / "venv", target_is_directory=True)
    for name in ("controller.log", "web.log"):
        (data / "logs" / name).touch(exist_ok=True)
        (app / name).symlink_to(data / "logs" / name)


def install_app(root: Path) -> Path:
    incoming = root / "incoming"
    if not (incoming / "scripts/start_systemd.sh").is_file():
        raise RuntimeError("Uploaded application is incomplete; rerun deployment")
    data = migrate_data(root)
    app = root / "app"
    if app.is_symlink():
        raise RuntimeError("Refusing to replace a symlink at app/")
    if app.exists():
        shutil.rmtree(app)
    incoming.rename(app)
    link_data(root, app, data)
    return app


def command(*args: str | Path, cwd: Path | None = None) -> None:
    print("Running:", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def write_service(root: Path, app: Path) -> dict[str, str]:
    user = os.environ.get("USER")
    if not user or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in user):
        raise RuntimeError("Cannot determine service user")
    unit = "\n".join([
        "[Unit]", "Description=LED plant wall", "After=network.target", "[Service]", "Type=simple",
        f"User={user}", f"WorkingDirectory={app}", f"ExecStart=/bin/bash {app}/scripts/start_systemd.sh",
        f"Environment=LEDGRID_DATA_DIR={root / 'data'}", "Environment=PYTHONUNBUFFERED=1",
        "Environment=LEDGRID_FEC_RECEIVER_IDS=3", "Environment=LEDGRID_HAT=0",
        "Environment=LEDGRID_SPI1_MODE=0", "Environment=STRIPS=33", "Environment=LEDS_PER_STRIP=138",
        "KillMode=control-group", "Restart=on-failure", "RestartSec=3", "[Install]", "WantedBy=multi-user.target", "",
    ])
    # Preserve the existing unit's operator environment overrides.
    existing = subprocess.run(["systemctl", "show", "ledgrid.service", "--property=Environment", "--value"], capture_output=True, text=True, check=False)
    import shlex
    values = dict(item.split("=", 1) for item in shlex.split(existing.stdout) if "=" in item)
    preserved = {key: value for key, value in values.items() if key in {"TARGET_FPS", "ANIMATION_SPEED_SCALE", "SPI_SPEED", "HOST", "PORT", "BRIGHTNESS"}}
    # systemd accepts quoted entire assignments; reject controls before writing.
    for key, value in preserved.items():
        if any(ord(char) < 32 for char in value):
            raise RuntimeError(f"Invalid saved service setting {key}")
        unit = unit.replace("KillMode=control-group", f"Environment={json.dumps(key + '=' + value)}\nKillMode=control-group", 1)
    file = root / "ledgrid.service"
    file.write_text(unit, encoding="utf-8")
    command("sudo", "-n", "install", "-m", "644", file, "/etc/systemd/system/ledgrid.service")
    command("sudo", "-n", "systemctl", "daemon-reload")
    return preserved


def readiness(timeout: float, api_url: str = "http://127.0.0.1:5000/api/v1/composer/operations/telemetry") -> None:
    deadline = time.monotonic() + timeout
    error = "No response"
    while time.monotonic() < deadline:
        try:
            subprocess.run(["systemctl", "is-active", "--quiet", "ledgrid.service"], check=True)
            with urllib.request.urlopen(api_url, timeout=min(2, max(.1, deadline - time.monotonic()))) as response:
                if response.status == 200:
                    print("Service and HTTP ready. Receiver connectivity is shown in the application.")
                    return
        except (OSError, subprocess.CalledProcessError) as exc:
            error = str(exc)
        time.sleep(min(.5, max(0, deadline - time.monotonic())))
    raise RuntimeError(f"Readiness timed out: {error}; inspect journalctl -u ledgrid.service")


def deploy(root: Path, mode: str, timeout: float) -> None:
    command("sudo", "-n", "systemctl", "stop", "ledgrid.service")
    app = install_app(root)
    # Save the last published controls, including brightness zero, after the
    # service is stopped and before launching the new controller.
    status = root / "data/run_state/status.json"
    if status.exists():
        command(sys.executable, app / "tools/deployment/preserve_deploy_settings.py", "save", "--status", status,
                "--state", root / "data/run_state/before_deploy.json", "--presets", root / "data/presets/animations")
    if not (root / "venv/bin/python").is_file():
        command("python3", "-m", "venv", root / "venv")
    command(root / "venv/bin/python", "-m", "pip", "install", "-r", app / "requirements-pi.lock")
    environment = write_service(root, app)
    if mode == "full":
        command(sys.executable, app / "tools/deployment/flash_receivers.py", "--root", root)
    command("sudo", "-n", "systemctl", "enable", "ledgrid.service")
    command("sudo", "-n", "systemctl", "start", "ledgrid.service")
    port = environment.get("PORT", "5000")
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise RuntimeError("Invalid service HTTP port")
    readiness(timeout, f"http://127.0.0.1:{port}/api/v1/composer/operations/telemetry")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", choices=["full", "python"], default="full")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    root = args.root.expanduser().absolute()
    # Only app/ is replaceable; refuse broad/ambiguous deployment roots.
    if root == Path.home() or root == Path("/") or root.is_symlink():
        parser.error("Deployment root must be a dedicated, regular directory")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        deploy(root, args.mode, args.timeout)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Deployment failed: {exc}. Data remains in {root / 'data'}; rerun deployment.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
