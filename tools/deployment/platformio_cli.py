#!/usr/bin/env python3
"""Pick a single PlatformIO Core install and exec it."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MIN_PLATFORMIO_VERSION = (6, 1, 19)


def _parse_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"version\s+(\d+(?:\.\d+)*)", text, flags=re.IGNORECASE)
    if not match:
        return None
    parts = [int(piece) for piece in match.group(1).split(".")]
    return tuple(parts)


def _version_tuple(version: tuple[int, ...]) -> tuple[int, int, int]:
    major, minor, patch = 0, 0, 0
    if len(version) >= 1:
        major = version[0]
    if len(version) >= 2:
        minor = version[1]
    if len(version) >= 3:
        patch = version[2]
    return major, minor, patch


def _meets_minimum(version: tuple[int, ...]) -> bool:
    return _version_tuple(version) >= _version_tuple(MIN_PLATFORMIO_VERSION)


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    home_penv = Path.home() / ".platformio" / "penv" / "bin" / "pio"
    if home_penv.is_file():
        paths.append(home_penv)
    which = shutil.which("pio")
    if which:
        resolved = Path(which).resolve()
        if resolved not in paths:
            paths.append(resolved)
    local_bin = Path.home() / ".local" / "bin" / "pio"
    if local_bin.is_file() and local_bin.resolve() not in paths:
        paths.append(local_bin.resolve())
    return paths


def _inspect(path: Path) -> tuple[tuple[int, ...] | None, str]:
    try:
        output = subprocess.check_output([os.fspath(path), "--version"], text=True, stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError) as exc:
        return None, str(exc)
    return _parse_version(output), output.strip()


def select_platformio() -> Path:
    """Return the newest PlatformIO Core that satisfies the espressif32 platform."""
    inspected: list[tuple[Path, tuple[int, ...] | None, str]] = []
    for path in _candidate_paths():
        version, detail = _inspect(path)
        inspected.append((path, version, detail))

    eligible = [(path, version) for path, version, _ in inspected if version and _meets_minimum(version)]
    if eligible:
        path, _ = max(eligible, key=lambda item: _version_tuple(item[1]))
        return path

    lines = [
        "No PlatformIO Core >= "
        f"{MIN_PLATFORMIO_VERSION[0]}.{MIN_PLATFORMIO_VERSION[1]}.{MIN_PLATFORMIO_VERSION[2]} is available.",
        "Multiple installs often leave an obsolete `pio` on PATH (for example 6.1.18).",
        "",
        "Detected:",
    ]
    for path, version, detail in inspected:
        label = ".".join(str(p) for p in version) if version else "unknown"
        lines.append(f"  {path} -> {label} ({detail})")
    lines.extend(
        [
            "",
            "Fix (recommended):",
            "  ~/.platformio/penv/bin/python -m pip install -U 'platformio>=6.1.19'",
            "  hash -r   # or open a new shell so PATH picks up the upgraded Core",
            "",
            "See: https://docs.platformio.org/en/latest/core/installation/troubleshooting.html",
        ]
    )
    raise SystemExit("\n".join(lines))


def main(argv: list[str]) -> None:
    if not argv:
        print("usage: platformio_cli.py <pio-args...>", file=sys.stderr)
        raise SystemExit(2)
    pio = select_platformio()
    os.execv(os.fspath(pio), [os.fspath(pio), *argv])


if __name__ == "__main__":
    main(sys.argv[1:])
