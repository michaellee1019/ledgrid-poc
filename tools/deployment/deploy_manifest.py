#!/usr/bin/env python3
"""Build deterministic, Git-tracked manifests for deployment syncs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys


RUNTIME_PRESETS = PurePosixPath("presets/animations")
FAST_CODE_SUFFIXES = {".css", ".html", ".js", ".py"}
FAST_CONFIG_FILES = {
    PurePosixPath("config/plant_globe_map_32x138.json"),
    PurePosixPath("config/plant_pixel_map.json"),
    PurePosixPath("config/plant_pixel_map_32x138.json"),
    PurePosixPath("config/webcam_pixel_map.json"),
}


def _is_beneath(path: PurePosixPath, parent: PurePosixPath) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _git_tracked_paths(root: Path) -> list[PurePosixPath]:
    result = subprocess.run(
        ["git", "-C", os.fspath(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    paths: list[PurePosixPath] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = PurePosixPath(os.fsdecode(raw_path))
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"Git returned unsafe deployment path: {path}")
        # ``git ls-files`` retains unstaged deletions in the index. A deploy
        # manifest describes the current working tree, so absent paths must not
        # be handed to rsync as source files.
        if (root / path.as_posix()).exists():
            paths.append(path)
    return paths


def _include_fast(path: PurePosixPath) -> bool:
    if _is_beneath(path, RUNTIME_PRESETS):
        return False
    # A plugin package owns its implementation, manifests, presets, tests, and
    # visual assets. Sync it as one unit so new asset types do not require a
    # deployment-script update.
    if _is_beneath(path, PurePosixPath("animation/plugins")):
        return True
    if path in FAST_CONFIG_FILES:
        return True
    return path.suffix in FAST_CODE_SUFFIXES


def tracked_paths(root: Path, scope: str) -> list[PurePosixPath]:
    """Return sorted tracked paths for a full or fast application sync."""
    paths = _git_tracked_paths(root)
    if scope == "full":
        selected = [path for path in paths if not _is_beneath(path, RUNTIME_PRESETS)]
    elif scope == "fast":
        selected = [path for path in paths if _include_fast(path)]
    else:
        raise ValueError(f"Unknown deployment scope: {scope}")
    return sorted(selected, key=lambda path: path.as_posix())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--scope", choices=("full", "fast"), required=True)
    parser.add_argument("--null", action="store_true", help="NUL-terminate paths for rsync")
    args = parser.parse_args()

    separator = b"\0" if args.null else b"\n"
    paths = tracked_paths(args.root, args.scope)
    output = separator.join(os.fsencode(path.as_posix()) for path in paths)
    if output:
        sys.stdout.buffer.write(output + separator)


if __name__ == "__main__":
    main()
