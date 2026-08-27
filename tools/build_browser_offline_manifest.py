#!/usr/bin/env python3
"""Build the deterministic, digest-pinned composer offline asset manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Sequence

if __package__:
    from tools.build_browser_python_bundle import PYODIDE_VERSION
else:  # Direct script execution puts tools/ rather than the repository on sys.path.
    from build_browser_python_bundle import PYODIDE_VERSION


CACHE_VERSION = "v13"
DEFAULT_OUTPUT = Path("web/static/generated/composer/offline_assets.json")
LOCAL_ASSETS = {
    "/composer-service-worker.js": Path("web/static/js/composer_service_worker.js"),
    "/static/composer.webmanifest": Path("web/static/composer.webmanifest"),
    "/static/css/composer.css": Path("web/static/css/composer.css"),
    "/static/generated/composer/aurora_curtains_native.wasm": Path(
        "web/static/generated/composer/aurora_curtains_native.wasm"
    ),
    "/static/generated/composer/compiled_rainbow.wasm": Path(
        "web/static/generated/composer/compiled_rainbow.wasm"
    ),
    "/static/generated/composer/ledgrid_python_runtime.zip": Path(
        "web/static/generated/composer/ledgrid_python_runtime.zip"
    ),
    "/static/icons/composer-180.png": Path("web/static/icons/composer-180.png"),
    "/static/icons/composer-512.png": Path("web/static/icons/composer-512.png"),
    "/static/icons/composer.svg": Path("web/static/icons/composer.svg"),
    "/static/js/composer.js": Path("web/static/js/composer.js"),
    "/static/js/composer_compositor.js": Path(
        "web/static/js/composer_compositor.js"
    ),
    "/static/js/composer_native_worker.js": Path(
        "web/static/js/composer_native_worker.js"
    ),
    "/static/js/composer_python_worker.js": Path(
        "web/static/js/composer_python_worker.js"
    ),
    "/static/js/composer_runtime.js": Path("web/static/js/composer_runtime.js"),
    "/static/js/composer_state.js": Path("web/static/js/composer_state.js"),
}


def build_manifest(repo_root: Path) -> dict[str, object]:
    assets = []
    for url, relative_path in sorted(LOCAL_ASSETS.items()):
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
        "cacheVersion": CACHE_VERSION,
        "localAssets": assets,
        "capturedAssets": ["/composer", "/api/v1/composer/bootstrap"],
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


def write_manifest(repo_root: Path, output: Path) -> bytes:
    payload = (
        json.dumps(build_manifest(repo_root.resolve()), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
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
