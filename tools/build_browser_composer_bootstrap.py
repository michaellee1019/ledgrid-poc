#!/usr/bin/env python3
"""Build the deterministic offline-first Composer catalog and profile assets."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE  # noqa: E402
from animation.core.feature_flags import AnimationPipelineFeatureFlags  # noqa: E402
from animation.core.installation_profile import (  # noqa: E402
    compile_installation_profile,
    encode_installation_profile,
)
from animation.core.manager import AnimationManager, PreviewLEDController  # noqa: E402
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT  # noqa: E402
from web.app import AnimationWebInterface  # noqa: E402


ARTIFACT_VERSION = 1
DEFAULT_BOOTSTRAP_OUTPUT = Path(
    "web/static/generated/composer/bootstrap.v1.json"
)
DEFAULT_PROFILE_OUTPUT = Path("web/static/generated/composer/installation_profile.bin")


class _NoWallChannel:
    """Prove that static catalog generation cannot observe or mutate a wall."""

    def read_status(self) -> dict[str, Any]:
        raise AssertionError("static Composer generation must not read wall status")

    def send_command(self, _action: str, **_data: object) -> dict[str, Any]:
        raise AssertionError("static Composer generation must not mutate wall state")


def build_profile() -> tuple[bytes, str]:
    payload = encode_installation_profile(compile_installation_profile())
    digest = payload[68:100].hex()
    return payload, digest


# Compatibility exports for callers; values are derived from the canonical
# binary profile rather than carried as independently maintained literals.
BUNDLED_PROFILE_DIGEST = build_profile()[1]
BUNDLED_PROFILE_URL = (
    "/static/generated/composer/installation_profile_"
    + BUNDLED_PROFILE_DIGEST
    + ".bin"
)


def build_bootstrap(
    repo_root: Path, *, bundled_profile_url: str | None = None,
    runtime_asset_root: Path | None = None,
) -> dict[str, Any]:
    controller = PreviewLEDController(DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP)
    flags = AnimationPipelineFeatureFlags(
        receiver_local_background=True,
        receiver_sparse_overlay=True,
        receiver_native_modules=True,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        manager = AnimationManager(
            controller,
            animation_speed_scale=DEFAULT_ANIMATION_SPEED_SCALE,
            plant_aware=DEFAULT_PLANT_AWARE,
            feature_flags=flags,
            auto_start=False,
        )
        interface = AnimationWebInterface(
            _NoWallChannel(),
            manager,
            local_mode=True,
            project_root=repo_root,
            activation_enabled=False,
        )
        payload = interface._browser_composer_bootstrap(
            observe_installation_profile=False,
            runtime_asset_root=runtime_asset_root,
        )

    _profile, profile_digest = build_profile()
    profile_url = bundled_profile_url or (
        "/static/generated/composer/installation_profile_" + profile_digest + ".bin"
    )
    payload["generated_at"] = 0
    payload["artifact"] = {
        "kind": "bundled",
        "version": ARTIFACT_VERSION,
    }
    payload["installation_profile"] = {
        **payload["installation_profile"],
        "digest": profile_digest,
        "authority": "bundled",
        "draft_url": None,
        "publish_url": None,
        "artifact_url": profile_url,
    }
    actions = payload["capabilities"]["server_actions"]
    actions.update({
        "activation_available": False,
        "activation_mode": "offline",
        "installation_profile_draft_url": None,
        "installation_profile_publish_url": None,
        "installation_profile_artifact_url": None,
    })
    identity_payload = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    payload["artifact"]["catalog_digest"] = hashlib.sha256(
        identity_payload
    ).hexdigest()
    return payload


def encoded_bootstrap(
    repo_root: Path, *, bundled_profile_url: str | None = None,
    runtime_asset_root: Path | None = None,
) -> bytes:
    return (
        json.dumps(
            build_bootstrap(
                repo_root.resolve(),
                bundled_profile_url=bundled_profile_url,
                runtime_asset_root=runtime_asset_root,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def write_assets(
    repo_root: Path,
    bootstrap_output: Path = DEFAULT_BOOTSTRAP_OUTPUT,
    profile_output: Path | None = None,
) -> tuple[bytes, bytes]:
    root = repo_root.resolve()
    profile, digest = build_profile()
    if profile_output is None:
        profile_output = Path("web/static/generated/composer/") / (
            f"installation_profile_{digest}.bin"
        )
    resolved_bootstrap = (
        bootstrap_output if bootstrap_output.is_absolute() else root / bootstrap_output
    )
    resolved_profile = (
        profile_output if profile_output.is_absolute() else root / profile_output
    )
    resolved_bootstrap.parent.mkdir(parents=True, exist_ok=True)
    resolved_profile.parent.mkdir(parents=True, exist_ok=True)
    bootstrap = encoded_bootstrap(
        root, bundled_profile_url="/static/generated/composer/" + resolved_profile.name
    )
    resolved_bootstrap.write_bytes(bootstrap)
    resolved_profile.write_bytes(profile)
    return bootstrap, profile


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=ROOT
    )
    parser.add_argument("--bootstrap-output", type=Path, default=DEFAULT_BOOTSTRAP_OUTPUT)
    parser.add_argument("--profile-output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    bootstrap, profile = write_assets(
        args.repo_root, args.bootstrap_output, args.profile_output
    )
    print(
        "built Composer bootstrap "
        f"{len(bootstrap)} bytes sha256={hashlib.sha256(bootstrap).hexdigest()} "
        f"and profile {len(profile)} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
