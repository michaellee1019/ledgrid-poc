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

from tools import build_browser_native, build_browser_python_bundle  # noqa: E402
from tools.build_browser_composer_bootstrap import build_profile, encoded_bootstrap  # noqa: E402
from tools.build_browser_offline_manifest import build_manifest, encoded_manifest  # noqa: E402
from tools.composer_asset_publication import (  # noqa: E402
    BOOTSTRAP_NAME,
    CONFIG_NAME,
    GENERATED_DIRECTORY,
    MANIFEST_NAME,
    NATIVE_NAMES,
    PYTHON_RUNTIME_NAME,
    all_local_assets,
    generation_digest,
    profile_name,
    profile_url,
    read_service_worker_config,
    render_service_worker_config,
    shell_assets,
)


GHOST_TERMS = ("emoji", "painter", "preview")


def _generated_path(root: Path) -> Path:
    return root / GENERATED_DIRECTORY


def _profile_digest(path: Path) -> str:
    payload = path.read_bytes()
    if len(payload) < 100:
        raise ValueError("bundled installation profile is truncated")
    return payload[68:100].hex()


def _staged_local_assets(profile_digest: str, stage: Path) -> dict[str, Path]:
    return {
        url: (stage / relative.name if relative.parent == GENERATED_DIRECTORY else relative)
        for url, relative in all_local_assets(profile_digest).items()
    }


def _previous_generation(published: Path) -> str | None:
    config = published / CONFIG_NAME
    if not config.is_file():
        return "v26"  # migration from the last pre-atomic checked-in shell.
    return str(read_service_worker_config(config).get("cacheVersion") or "v26")


def build_stage(repo_root: Path, stage: Path) -> dict[str, object]:
    """Build every generated artifact into ``stage`` without touching publication."""
    stage.mkdir(parents=True, exist_ok=True)
    build_browser_python_bundle.build_bundle(repo_root, stage / PYTHON_RUNTIME_NAME)
    build_browser_native.build(stage / NATIVE_NAMES[0], repo_root=repo_root)
    build_browser_native.build_compiled_rainbow(stage / NATIVE_NAMES[1], repo_root=repo_root)
    profile, profile_digest = build_profile()
    profile_path = stage / profile_name(profile_digest)
    profile_path.write_bytes(profile)
    (stage / BOOTSTRAP_NAME).write_bytes(
        encoded_bootstrap(
            repo_root,
            bundled_profile_url=profile_url(profile_digest),
            runtime_asset_root=stage,
        )
    )
    prior = _previous_generation(_generated_path(repo_root))
    cache_version = "g-" + generation_digest(repo_root, profile_digest, stage)[:20]
    # A repeated build of the same generation retains its previous lineage.
    if (existing := _generated_path(repo_root) / CONFIG_NAME).is_file():
        current = read_service_worker_config(existing)
        if current.get("cacheVersion") == cache_version:
            prior = current.get("previousCacheVersion") if isinstance(current.get("previousCacheVersion"), str) else None
    (stage / CONFIG_NAME).write_bytes(render_service_worker_config(
        cache_version=cache_version,
        previous_cache_version=prior,
        profile_digest=profile_digest,
    ))
    local_assets = _staged_local_assets(profile_digest, stage)
    (stage / MANIFEST_NAME).write_bytes(encoded_manifest(
        repo_root,
        local_assets=local_assets,
        cache_version=cache_version,
        previous_cache_version=prior,
    ))
    validate_asset_set(repo_root, stage)
    return asset_manifest(stage)


def asset_manifest(directory: Path) -> dict[str, object]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*")) if path.is_file()
    }


def validate_asset_set(repo_root: Path, directory: Path) -> None:
    config = read_service_worker_config(directory / CONFIG_NAME)
    profile = str(config["bundledProfileUrl"]).rsplit("/", 1)[-1]
    profile_digest = _profile_digest(directory / profile)
    expected = {
        path.name
        for path in _staged_local_assets(profile_digest, directory).values()
        if path.parent == directory
    }
    expected.add(MANIFEST_NAME)
    actual = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
    }
    ghosts = sorted(
        name for name in actual if any(term in name.lower() for term in GHOST_TERMS)
    )
    if ghosts:
        raise ValueError("ghost Composer artifacts are forbidden: " + ", ".join(ghosts))
    if actual != expected:
        raise ValueError(
            "generated Composer asset set is stale, missing, or orphaned: "
            + "expected " + repr(sorted(expected)) + " got " + repr(sorted(actual))
        )
    if config.get("bundledProfileUrl") != profile_url(profile_digest):
        raise ValueError("service-worker config does not name the staged installation profile")
    if tuple(config.get("shellAssets", ())) != shell_assets(profile_digest):
        raise ValueError("service-worker config does not declare the complete shell")
    manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    expected_manifest = build_manifest(
        repo_root,
        local_assets=_staged_local_assets(profile_digest, directory),
        cache_version=str(config["cacheVersion"]),
        previous_cache_version=config.get("previousCacheVersion"),
    )
    if manifest != expected_manifest:
        raise ValueError("offline manifest is stale or does not match the staged shell")
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
