"""Shared declarations for the atomic Composer browser-asset publication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


GENERATED_DIRECTORY = Path("web/static/generated/composer")
CONFIG_NAME = "service_worker_config.v1.js"
MANIFEST_NAME = "offline_assets.json"
BOOTSTRAP_NAME = "bootstrap.v1.json"
PYTHON_RUNTIME_NAME = "ledgrid_python_runtime.zip"
NATIVE_NAMES = ("aurora_curtains_native.wasm", "compiled_rainbow.wasm")
PROFILE_PREFIX = "installation_profile_"
PROFILE_SUFFIX = ".bin"
CONFIG_URL = "/static/generated/composer/" + CONFIG_NAME
OFFLINE_MANIFEST_URL = "/static/generated/composer/" + MANIFEST_NAME
BOOTSTRAP_URL = "/static/generated/composer/" + BOOTSTRAP_NAME

# These are source-owned shell files. The generated directory is deliberately
# declared separately so publication can garbage-collect it as one unit.
FIXED_LOCAL_ASSETS: dict[str, Path] = {
    "/composer-service-worker.js": Path("web/static/js/composer_service_worker.js"),
    "/static/composer.webmanifest": Path("web/static/composer.webmanifest"),
    "/static/css/composer.css": Path("web/static/css/composer.css"),
    "/static/icons/composer-180.png": Path("web/static/icons/composer-180.png"),
    "/static/icons/composer-512.png": Path("web/static/icons/composer-512.png"),
    "/static/icons/composer.svg": Path("web/static/icons/composer.svg"),
    "/composer-app.js": Path("web/static/js/composer.js"),
    "/static/js/composer_compositor.js": Path("web/static/js/composer_compositor.js"),
    "/static/js/composer_interactions.js": Path("web/static/js/composer_interactions.js"),
    "/static/js/composer-operations.js": Path("web/static/js/composer-operations.js"),
    "/static/js/composer-maintenance.js": Path("web/static/js/composer-maintenance.js"),
    "/static/js/composer_native_worker.js": Path("web/static/js/composer_native_worker.js"),
    "/static/js/composer_python_worker.js": Path("web/static/js/composer_python_worker.js"),
    "/static/js/composer_runtime.js": Path("web/static/js/composer_runtime.js"),
    "/static/js/composer_sha256.js": Path("web/static/js/composer_sha256.js"),
    "/static/js/composer_state.js": Path("web/static/js/composer_state.js"),
}


def digest_file(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def profile_name(digest: str) -> str:
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("installation profile identity must be a lowercase SHA-256")
    return f"{PROFILE_PREFIX}{digest}{PROFILE_SUFFIX}"


def profile_url(digest: str) -> str:
    return "/static/generated/composer/" + profile_name(digest)


def generated_local_assets(profile_digest: str) -> dict[str, Path]:
    return {
        "/static/generated/composer/aurora_curtains_native.wasm": GENERATED_DIRECTORY / NATIVE_NAMES[0],
        "/static/generated/composer/compiled_rainbow.wasm": GENERATED_DIRECTORY / NATIVE_NAMES[1],
        BOOTSTRAP_URL: GENERATED_DIRECTORY / BOOTSTRAP_NAME,
        profile_url(profile_digest): GENERATED_DIRECTORY / profile_name(profile_digest),
        "/static/generated/composer/ledgrid_python_runtime.zip": GENERATED_DIRECTORY / PYTHON_RUNTIME_NAME,
        CONFIG_URL: GENERATED_DIRECTORY / CONFIG_NAME,
    }


def all_local_assets(profile_digest: str) -> dict[str, Path]:
    return {**FIXED_LOCAL_ASSETS, **generated_local_assets(profile_digest)}


def shell_assets(profile_digest: str) -> tuple[str, ...]:
    # /composer is observed at install time rather than hash-declared.
    return ("/composer", *sorted((*all_local_assets(profile_digest), OFFLINE_MANIFEST_URL)))


def generation_digest(repo_root: Path, profile_digest: str, staged_generated: Path) -> str:
    """Return identity of every cache-relevant input except generated config/manifest.

    Omitting those two derived files avoids a circular digest while every input
    that can change what the worker serves remains in the identity.
    """
    selected = all_local_assets(profile_digest)
    records = []
    for url, relative in sorted(selected.items()):
        if url in {CONFIG_URL, OFFLINE_MANIFEST_URL}:
            continue
        path = staged_generated / relative.name if relative.parent == GENERATED_DIRECTORY else repo_root / relative
        records.append({"url": url, **digest_file(path)})
    payload = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def render_service_worker_config(*, cache_version: str, previous_cache_version: str | None, profile_digest: str) -> bytes:
    payload = {
        "schema": "ledgrid.composer-service-worker-config",
        "schemaVersion": 1,
        "cacheVersion": cache_version,
        "previousCacheVersion": previous_cache_version,
        "bundledBootstrapUrl": BOOTSTRAP_URL,
        "bundledProfileUrl": profile_url(profile_digest),
        "offlineManifestUrl": OFFLINE_MANIFEST_URL,
        "shellAssets": list(shell_assets(profile_digest)),
    }
    return (
        "'use strict';\n"
        "self.LEDGRID_COMPOSER_ASSET_CONFIG = Object.freeze("
        + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        + ");\n"
    ).encode("utf-8")


def read_service_worker_config(path: Path) -> dict[str, object]:
    prefix = "self.LEDGRID_COMPOSER_ASSET_CONFIG = Object.freeze("
    source = path.read_text(encoding="utf-8")
    if not source.startswith("'use strict';\n" + prefix) or not source.endswith(");\n"):
        raise ValueError(f"invalid Composer service-worker config: {path}")
    payload = json.loads(source[len("'use strict';\n" + prefix):-3])
    if not isinstance(payload, dict):
        raise ValueError("Composer service-worker config must be an object")
    return payload
