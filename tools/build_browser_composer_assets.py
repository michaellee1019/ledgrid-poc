#!/usr/bin/env python3
"""Atomically publish the complete deterministic Composer browser asset set."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import build_browser_python_bundle  # noqa: E402
from tools.build_browser_composer_bootstrap import build_profile, encoded_bootstrap  # noqa: E402
BOOTSTRAP_NAME = 'bootstrap.v1.json'
PYTHON_RUNTIME_NAME = 'ledgrid_python_runtime.zip'
GENERATED_DIRECTORY = Path('web/static/generated/composer')

def profile_name(digest: str) -> str:
    return f'installation_profile_{digest}.bin'

def profile_url(digest: str) -> str:
    return '/static/generated/composer/' + profile_name(digest)


def _generated_path(root: Path) -> Path:
    return root / GENERATED_DIRECTORY


def _profile_digest(path: Path) -> str:
    payload = path.read_bytes()
    if len(payload) < 100:
        raise ValueError("bundled installation profile is truncated")
    return payload[68:100].hex()


def build_stage(repo_root: Path, stage: Path) -> dict[str, object]:
    """Build every generated artifact into ``stage`` without touching publication."""
    stage.mkdir(parents=True, exist_ok=True)
    build_browser_python_bundle.build_bundle(repo_root, stage / PYTHON_RUNTIME_NAME)
    profile, profile_digest = build_profile(repo_root)
    profile_path = stage / profile_name(profile_digest)
    profile_path.write_bytes(profile)
    (stage / BOOTSTRAP_NAME).write_bytes(
        encoded_bootstrap(
            repo_root,
            bundled_profile_url=profile_url(profile_digest),
            runtime_asset_root=stage,
        )
    )
    validate_asset_set(repo_root, stage)
    return asset_manifest(stage)


def asset_manifest(directory: Path) -> dict[str, object]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*")) if path.is_file()
    }


def validate_asset_set(repo_root: Path, directory: Path) -> None:
    profiles = list(directory.glob('installation_profile_*.bin'))
    if len(profiles) != 1:
        raise ValueError('exactly one fixed calibration asset is required')
    profile_digest = _profile_digest(profiles[0])
    expected = {BOOTSTRAP_NAME, PYTHON_RUNTIME_NAME, profile_name(profile_digest)}
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob('*') if path.is_file()}
    if actual != expected:
        raise ValueError('generated Composer asset set contains missing or obsolete assets')
    bootstrap = json.loads((directory / BOOTSTRAP_NAME).read_text(encoding="utf-8"))
    for component in bootstrap.get("components", ()):
        runtime = component.get("browser_runtime", {})
        asset_url = runtime.get("asset_url")
        if not runtime.get("supported") or not isinstance(asset_url, str):
            continue
        asset = directory / Path(asset_url).name
        expected_digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        if runtime.get("digest") != expected_digest:
            raise ValueError(
                "Composer bootstrap runtime digest does not match staged asset: "
                + asset.name
            )


def check_published(repo_root: Path) -> dict[str, object]:
    published = _generated_path(repo_root)
    validate_asset_set(repo_root, published)
    with tempfile.TemporaryDirectory(prefix="composer-assets-check-", dir=published.parent) as temporary:
        staged = Path(temporary) / "composer"
        expected = build_stage(repo_root, staged)
        actual = asset_manifest(published)
    if actual != expected:
        raise ValueError("published Composer assets are stale; run build_browser_composer_assets.py")
    return actual


def publish(repo_root: Path) -> dict[str, object]:
    target = _generated_path(repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="composer-assets-stage-", dir=target.parent) as temporary:
        stage = Path(temporary) / "composer"
        result = build_stage(repo_root, stage)
        backup = Path(temporary) / "previous-composer"
        if target.exists():
            os.replace(target, backup)
        try:
            os.replace(stage, target)
        except BaseException:
            if backup.exists():
                os.replace(backup, target)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    from tools.generate_gallery_previews import build_gallery
    build_gallery(repo_root)
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="fail unless the published set is current and complete")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = check_published(args.repo_root.resolve()) if args.check else publish(args.repo_root.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
