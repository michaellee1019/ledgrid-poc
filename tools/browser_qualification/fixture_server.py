#!/usr/bin/env python3
"""Loopback-only, no-wall Composer server for portable browser qualification."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from flask import request

from animation.core.installation_profile_authoring import InstallationProfileAuthoring
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.activation_qualification import canonical_json_sha256
from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.native_background_library import NativeBackgroundLibrary
from animation.core.presentation_contracts import resolve_vibe
from animation.native.builder import build_plugin
from ipc.control_channel import FileControlChannel
from tools.browser_qualification.source_identity import (
    resolve_fixture_source_identity,
)
from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]
PROFILE_FIXTURE = ROOT / "tests" / "fixtures" / "installation_profile_v1.bin"
NATIVE_BUILD_ROOT = ROOT / "run_state" / "browser_qualification_native_builds"
NATIVE_PLUGIN_ID = "aurora_curtains_native"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
OFFLINE_GATE_COOKIE = "ledgrid_rel01_origin_offline"


class WallMutationAttempt(RuntimeError):
    """Raised whenever the qualification fixture receives a live-wall write."""


class NoWallControlChannel(FileControlChannel):
    """Real read/status channel whose every controller write is retained and rejected."""

    def __init__(self, state_dir: Path) -> None:
        super().__init__(
            os.fspath(state_dir / "control.json"),
            os.fspath(state_dir / "status.json"),
            os.fspath(state_dir / "activations"),
        )
        self.attempt_log = state_dir / "wall-mutation-attempts.jsonl"
        self.attempts: list[dict[str, Any]] = []

    def _reject(self, operation: str, payload: Any) -> None:
        record = {
            "operation": operation,
            "payload": payload,
            "captured_at": time.time(),
        }
        self.attempts.append(record)
        self.attempt_log.parent.mkdir(parents=True, exist_ok=True)
        with self.attempt_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        raise WallMutationAttempt(
            f"qualification fixture rejected controller mutation {operation}"
        )

    def send_command(self, action: str, **data: Any) -> dict[str, Any]:
        self._reject("send_command", {"action": action, "data": data})

    def enqueue_activation(self, command: dict[str, Any]) -> dict[str, Any]:
        self._reject("enqueue_activation", command)

    def write_activation_status(self, status: dict[str, Any]) -> dict[str, Any]:
        self._reject("write_activation_status", status)

    def request_activation_cancel(self, activation_id: str) -> dict[str, Any]:
        self._reject("request_activation_cancel", {"activation_id": activation_id})

    def request_activation_rollback(
        self, activation_id: str, **data: Any
    ) -> dict[str, Any]:
        self._reject(
            "request_activation_rollback",
            {"activation_id": activation_id, "data": data},
        )


def create_fixture_server(
    state_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> tuple[AnimationWebInterface, NoWallControlChannel, str]:
    """Build the real Composer surface around isolated, non-consuming state."""
    if host not in LOOPBACK_HOSTS:
        raise ValueError("qualification fixture must bind to a loopback host")
    state_dir = state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    library = InstallationProfileLibrary(state_dir / "installation-profile-library")
    receipt = library.publish(PROFILE_FIXTURE.read_bytes())
    authoring = InstallationProfileAuthoring(
        library, state_dir / "installation-profile-authoring"
    )
    native_build = build_plugin(
        ROOT,
        NATIVE_PLUGIN_ID,
        NATIVE_BUILD_ROOT,
        execute=True,
    )
    if native_build.bundle_path is None:
        raise RuntimeError("qualification fixture did not build a managed native bundle")
    native_library = NativeBackgroundLibrary(state_dir / "native-background-library")
    native_receipt = native_library.publish(native_build.bundle_path)
    manager = AnimationManager(
        PreviewLEDController(33, 138),
        feature_flags=AnimationPipelineFeatureFlags(
            receiver_local_background=True,
            receiver_sparse_overlay=True,
            receiver_native_modules=True,
        ),
        installation_profile_library=library,
        installation_profile_digest=receipt.id,
        native_background_library=native_library,
        auto_start=False,
    )
    channel = NoWallControlChannel(state_dir)
    vibe = resolve_vibe("neutral").state.to_dict()
    source_commit, release_id = resolve_fixture_source_identity(ROOT)
    active_identity = {
        "scene_identity": None,
        "component_identities": [],
        "global_settings_identity": {
            "revision": 1,
            "digest": canonical_json_sha256(
                {
                    "vibe": vibe,
                    "plant_modifiers": {
                        "version": 1,
                        "active": [],
                        "strengths": {},
                    },
                    "brightness": 128,
                    "target_fps": 30,
                    "animation_speed_scale": 0.3,
                }
            ),
        },
        "installation_profile_digest": receipt.id,
    }
    channel.write_status(
        {
            "release_id": release_id,
            "controller_session_id": "a" * 32,
            "controller_state_revision": 1,
            "active_identity": active_identity,
            "current_identity_digest": canonical_json_sha256(active_identity),
            "installation_profile_digest": receipt.id,
            "brightness": 128,
            "target_fps": 30,
            "animation_speed_scale": 0.3,
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            "vibe": {"state": vibe},
            "is_running": False,
            "painter_active": False,
            "current_animation": None,
        }
    )
    interface = AnimationWebInterface(
        channel,
        manager,
        host=host,
        port=port,
        local_mode=True,
        activation_enabled=True,
        activation_token_store_path=state_dir / "activation-tokens.sqlite3",
        installation_profile_authoring=authoring,
        project_root=ROOT,
        release_id=release_id,
    )
    interface.animation_presets_dir = state_dir / "presets" / "animations"
    interface.scene_presets_dir = state_dir / "presets" / "scenes"
    interface.painter_presets_dir = state_dir / "presets" / "painter"
    interface.deployment_status_path = state_dir / "deployment.json"
    network_outage_blocks: list[dict[str, Any]] = []

    @interface.app.before_request
    def qualification_network_outage() -> tuple[dict[str, Any], int, dict[str, str]] | None:
        if request.cookies.get(OFFLINE_GATE_COOKIE) != "1":
            return None
        network_outage_blocks.append(
            {
                "method": request.method,
                "path": request.path,
                "captured_at": time.time(),
            }
        )
        return (
            {"error": "qualification fixture origin is intentionally offline"},
            503,
            {"Cache-Control": "no-store", "X-LEDGrid-Qualification-Offline": "1"},
        )

    @interface.app.get("/__qualification__/status")
    def qualification_status() -> dict[str, Any]:
        return {
            "schema": "ledgrid.browser-qualification-fixture-status",
            "schema_version": 2,
            "profile_digest": receipt.id,
            "native_plugin_id": NATIVE_PLUGIN_ID,
            "native_bundle_digest": native_receipt.bundle_digest,
            "native_payload_digest": native_receipt.payload_digest,
            "source_commit": source_commit,
            "release_id": release_id,
            "controller_release_id": channel.read_status().get("release_id"),
            "release_consistent": (
                channel.read_status().get("release_id") == release_id
            ),
            "network_outage_blocks": len(network_outage_blocks),
            "network_outage_paths": sorted(
                {item["path"] for item in network_outage_blocks}
            ),
            "wall_mutation_attempts": len(channel.attempts),
            "wall_consumer_attached": False,
        }

    return interface, channel, receipt.id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve an isolated Composer fixture that rejects wall mutations."
    )
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1", choices=sorted(LOOPBACK_HOSTS))
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    interface, _channel, digest = create_fixture_server(
        args.state_dir, host=args.host, port=args.port
    )
    print(
        json.dumps(
            {
                "base_url": f"http://{args.host}:{args.port}",
                "profile_digest": digest,
                "native_plugin_id": NATIVE_PLUGIN_ID,
                "state_dir": os.fspath(args.state_dir.resolve()),
                "wall_consumer_attached": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    interface.run(debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
