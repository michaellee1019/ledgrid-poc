#!/usr/bin/env python3
"""Build the deterministic, digest-pinned composer offline asset manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

if __package__:
    from tools.build_browser_python_bundle import PYODIDE_VERSION
    from tools.composer_asset_publication import all_local_assets, read_service_worker_config
else:  # Direct script execution puts tools/ rather than the repository on sys.path.
    from build_browser_python_bundle import PYODIDE_VERSION
    from composer_asset_publication import all_local_assets, read_service_worker_config


DEFAULT_OUTPUT = Path("web/static/generated/composer/offline_assets.json")
CONFIG_OUTPUT = Path("web/static/generated/composer/service_worker_config.v1.js")


def publication_inputs(repo_root: Path) -> tuple[Mapping[str, Path], str, str | None]:
    """Load every publication identity from its generated source of truth."""
    config = read_service_worker_config(repo_root / CONFIG_OUTPUT)
    profile_url = str(config["bundledProfileUrl"])
    profile_digest = profile_url.rsplit("_", 1)[1].removesuffix(".bin")
    previous = config.get("previousCacheVersion")
    if previous is not None and not isinstance(previous, str):
        raise ValueError("Composer cache lineage must be a string or null")
    return all_local_assets(profile_digest), str(config["cacheVersion"]), previous


def build_manifest(
    repo_root: Path,
    *,
    local_assets: Mapping[str, Path] | None = None,
    cache_version: str | None = None,
    previous_cache_version: str | None = None,
) -> dict[str, object]:
    if local_assets is None or cache_version is None:
        configured_assets, configured_cache, configured_previous = publication_inputs(repo_root)
        local_assets = local_assets or configured_assets
        cache_version = cache_version or configured_cache
        if previous_cache_version is None:
            previous_cache_version = configured_previous
    assets = []
    for url, relative_path in sorted(local_assets.items()):
        payload = (repo_root / relative_path).read_bytes()
        assets.append(
            {
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "url": url,
            }
        )
    return {
        "schema": "ledgrid.composer-offline-assets",
        "schemaVersion": 1,
        "cacheVersion": cache_version,
        "previousCacheVersion": previous_cache_version,
        "localAssets": assets,
        "capturedAssets": ["/composer"],
        "pythonRuntime": {
            "baseUrl": (
                f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
            ),
            "delivery": "pinned-cross-origin-cache",
            "entrypoint": "pyodide.mjs",
            "integrity": "sha256-observed-and-reverified",
            "packages": ["numpy", "pillow"],
            "selfHosted": False,
            "version": PYODIDE_VERSION,
        },
        "readyRequiresExplicitPreparation": True,
    }


def encoded_manifest(
    repo_root: Path,
    *,
    local_assets: Mapping[str, Path] | None = None,
    cache_version: str | None = None,
    previous_cache_version: str | None = None,
) -> bytes:
    payload = (
        json.dumps(
            build_manifest(
                repo_root.resolve(),
                local_assets=local_assets,
                cache_version=cache_version,
                previous_cache_version=previous_cache_version,
            ),
            indent=2,
            sort_keys=True,
        ) + "\n"
    ).encode("utf-8")
    return payload


def write_manifest(repo_root: Path, output: Path) -> bytes:
    payload = encoded_manifest(repo_root)
    resolved = output if output.is_absolute() else repo_root / output
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(payload)
    return payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    payload = write_manifest(args.repo_root.resolve(), args.output)
    print(
        f"built {len(payload)} bytes sha256={hashlib.sha256(payload).hexdigest()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
