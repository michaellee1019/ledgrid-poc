#!/usr/bin/env python3
"""Build deterministic manifests and explain their source-selection policy."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
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
UNTRACKED_DEPLOY_ROOTS = {
    "animation", "drivers", "firmware", "ipc", "scripts", "tools", "web",
}
UNTRACKED_DEPLOY_FILES = {
    "Justfile",
    "pyproject.toml",
    "requirements-calibration.txt",
    "requirements-pi.lock",
    "requirements-platformio.lock",
    "requirements.txt",
    "uv.lock",
}


@dataclass(frozen=True)
class ExcludedPath:
    """A worktree source candidate intentionally absent from a deployment."""

    path: PurePosixPath
    reason: str


@dataclass(frozen=True)
class ManifestPlan:
    """Complete, deterministic accounting for one deployment manifest."""

    scope: str
    selected: tuple[PurePosixPath, ...]
    safe_untracked: tuple[PurePosixPath, ...]
    excluded: tuple[ExcludedPath, ...]


def _is_beneath(path: PurePosixPath, parent: PurePosixPath) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_git_path(raw_path: bytes) -> PurePosixPath:
    path = PurePosixPath(os.fsdecode(raw_path))
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Git returned unsafe deployment path: {path}")
    return path


def _git_index_paths(root: Path) -> list[PurePosixPath]:
    result = subprocess.run(
        ["git", "-C", os.fspath(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    paths: list[PurePosixPath] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        paths.append(_validate_git_path(raw_path))
    return paths


def _git_untracked_paths(root: Path) -> list[PurePosixPath]:
    result = subprocess.run(
        ["git", "-C", os.fspath(root), "ls-files", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    )
    paths: list[PurePosixPath] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        paths.append(_validate_git_path(raw_path))
    return paths


def _is_safe_untracked(root: Path, path: PurePosixPath) -> bool:
    return bool(
        path.as_posix() in UNTRACKED_DEPLOY_FILES
        or (
        path.parts
        and path.parts[0] in UNTRACKED_DEPLOY_ROOTS
        and not _is_beneath(path, RUNTIME_PRESETS)
        and (root / path.as_posix()).is_file()
        )
    )


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


def manifest_plan(root: Path, scope: str) -> ManifestPlan:
    """Return selected paths and every Git-visible source-policy exclusion."""
    if scope not in {"full", "fast"}:
        raise ValueError(f"Unknown deployment scope: {scope}")

    selected: set[PurePosixPath] = set()
    excluded: list[ExcludedPath] = []
    safe_untracked: list[PurePosixPath] = []

    for path in _git_index_paths(root):
        if not (root / path.as_posix()).exists():
            excluded.append(ExcludedPath(path, "deleted from working tree"))
        elif _is_beneath(path, RUNTIME_PRESETS):
            excluded.append(ExcludedPath(path, "target-owned runtime preset"))
        elif scope == "fast" and not _include_fast(path):
            excluded.append(ExcludedPath(path, "outside fast application scope"))
        else:
            selected.add(path)

    for path in _git_untracked_paths(root):
        if not _is_safe_untracked(root, path):
            reason = (
                "target-owned runtime preset"
                if _is_beneath(path, RUNTIME_PRESETS)
                else "untracked path is outside safe deployment roots"
            )
            excluded.append(ExcludedPath(path, reason))
        elif scope == "fast" and not _include_fast(path):
            excluded.append(ExcludedPath(path, "outside fast application scope"))
        else:
            selected.add(path)
            safe_untracked.append(path)

    return ManifestPlan(
        scope=scope,
        selected=tuple(sorted(selected, key=lambda path: path.as_posix())),
        safe_untracked=tuple(sorted(safe_untracked, key=lambda path: path.as_posix())),
        excluded=tuple(sorted(excluded, key=lambda item: (item.path.as_posix(), item.reason))),
    )


def tracked_paths(root: Path, scope: str) -> list[PurePosixPath]:
    """Return sorted tracked plus safe, non-ignored new application paths."""
    return list(manifest_plan(root, scope).selected)


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        check=True,
        capture_output=True,
    ).stdout


def working_tree_dirty(root: Path) -> bool:
    """Return whether any tracked or non-ignored untracked source is modified."""
    return bool(_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"))


def source_identity(root: Path, plan: ManifestPlan) -> dict[str, object]:
    """Identify the exact tracked edits and safe untracked bytes being deployed."""
    base_commit = os.fsdecode(_git(root, "rev-parse", "HEAD")).strip()
    digest = hashlib.sha256()
    tracked_candidates = [
        path.as_posix() for path in plan.selected if path not in plan.safe_untracked
    ]
    # A full staging sync deletes tracked files removed from the worktree. Fast
    # syncs intentionally do not delete, so only full identities include them.
    if plan.scope == "full":
        tracked_candidates.extend(
            item.path.as_posix()
            for item in plan.excluded
            if item.reason == "deleted from working tree"
        )
    tracked_diff = (
        _git(root, "diff", "--binary", "HEAD", "--", *tracked_candidates)
        if tracked_candidates
        else b""
    )
    digest.update(b"tracked-diff\0")
    digest.update(len(tracked_diff).to_bytes(8, "big"))
    digest.update(tracked_diff)
    for path in plan.safe_untracked:
        path_bytes = os.fsencode(path.as_posix())
        contents = (root / path.as_posix()).read_bytes()
        digest.update(b"safe-untracked\0")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return {
        "base_commit": base_commit,
        "diff_sha256": digest.hexdigest(),
        "safe_untracked": [path.as_posix() for path in plan.safe_untracked],
        "scope": plan.scope,
    }


def _plan_payload(root: Path, plan: ManifestPlan) -> dict[str, object]:
    payload = source_identity(root, plan)
    payload.update(
        {
            "dirty": working_tree_dirty(root),
            "selected": [path.as_posix() for path in plan.selected],
            "excluded": [
                {"path": item.path.as_posix(), "reason": item.reason}
                for item in plan.excluded
            ],
        }
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--scope", choices=("full", "fast"), required=True)
    parser.add_argument("--null", action="store_true", help="NUL-terminate paths for rsync")
    parser.add_argument(
        "--policy",
        choices=("manifest", "clean", "dirty", "plan"),
        default="manifest",
        help="validate/describe the deployment source policy",
    )
    parser.add_argument("--json", action="store_true", help="emit plan/identity as JSON")
    args = parser.parse_args()

    plan = manifest_plan(args.root, args.scope)
    if args.policy == "clean" and working_tree_dirty(args.root):
        print(
            "clean deployment refused: working tree has tracked or non-ignored "
            "untracked changes; use 'just deploy-dirty' intentionally",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if args.policy in {"clean", "dirty", "plan"}:
        payload = _plan_payload(args.root, plan)
        if args.policy == "clean":
            payload["diff_sha256"] = None
            payload["safe_untracked"] = []
        if args.json or args.policy != "plan":
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        else:
            print(f"source: {'dirty' if payload['dirty'] else 'clean'}")
            print(f"scope: {plan.scope}")
            print(f"base commit: {payload['base_commit']}")
            print(f"working-tree digest: {payload['diff_sha256']}")
            print(f"selected ({len(plan.selected)}):")
            for path in plan.selected:
                suffix = " [safe untracked]" if path in plan.safe_untracked else ""
                print(f"  + {path}{suffix}")
            print(f"excluded ({len(plan.excluded)}):")
            for item in plan.excluded:
                print(f"  - {item.path}: {item.reason}")
        return

    separator = b"\0" if args.null else b"\n"
    paths = plan.selected
    output = separator.join(os.fsencode(path.as_posix()) for path in paths)
    if output:
        sys.stdout.buffer.write(output + separator)


if __name__ == "__main__":
    main()
