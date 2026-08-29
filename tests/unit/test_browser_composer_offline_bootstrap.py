"""Focused first-load, reconnect, and bundled Composer catalog regressions."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import unittest
from pathlib import Path

from animation.core.manager import AnimationManager, PreviewLEDController
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT
from tools.build_browser_composer_bootstrap import (
    BUNDLED_PROFILE_DIGEST,
    BUNDLED_PROFILE_URL,
    encoded_bootstrap,
)
from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_PATH = ROOT / "web/static/generated/composer/bootstrap.v1.json"
PROFILE_PATH = ROOT / "web/static" / BUNDLED_PROFILE_URL.removeprefix("/static/")
COMPOSER_SOURCE = ROOT / "web/static/js/composer.js"


class _NoWallChannel:
    def read_status(self) -> dict:
        raise AssertionError("opening Composer must not read live wall state")

    def send_command(self, _action: str, **_data: object) -> dict:
        raise AssertionError("opening Composer must not mutate live wall state")


class BrowserComposerOfflineBootstrapTests(unittest.TestCase):
    def test_bundled_catalog_is_reproducible_versioned_and_runtime_bound(self) -> None:
        committed = BOOTSTRAP_PATH.read_bytes()
        self.assertEqual(committed, encoded_bootstrap(ROOT))
        payload = json.loads(committed)
        self.assertEqual(payload["artifact"]["kind"], "bundled")
        self.assertEqual(payload["artifact"]["version"], 1)
        self.assertRegex(payload["artifact"]["catalog_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(payload["generated_at"], 0)
        self.assertFalse(
            payload["capabilities"]["server_actions"]["activation_available"]
        )

        profile = PROFILE_PATH.read_bytes()
        self.assertEqual(profile[68:100].hex(), BUNDLED_PROFILE_DIGEST)
        self.assertEqual(
            payload["installation_profile"]["artifact_url"], BUNDLED_PROFILE_URL
        )
        self.assertEqual(payload["installation_profile"]["authority"], "bundled")

        previewable = [
            item for item in payload["components"]
            if item["role"] == "background"
            and item["browser_capabilities"]["previewable"]
        ]
        self.assertTrue(previewable)
        for component in previewable:
            runtime = component["browser_runtime"]
            asset = ROOT / "web" / runtime["asset_url"].lstrip("/")
            self.assertEqual(
                hashlib.sha256(asset.read_bytes()).hexdigest(), runtime["digest"]
            )
            identity = component["browser_capabilities"]["managed_identity"]
            self.assertEqual(identity["runtime_digest"], runtime["digest"])
            self.assertEqual(identity["component_digest"], component["component_digest"])

    def test_catalog_only_server_refresh_does_not_observe_or_mutate_wall(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            manager = AnimationManager(
                PreviewLEDController(DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP),
                auto_start=False,
            )

        def forbidden_profile_status() -> dict:
            raise AssertionError("catalog-only refresh read selected wall profile")

        manager.get_installation_profile_status = forbidden_profile_status  # type: ignore[method-assign]
        interface = AnimationWebInterface(
            _NoWallChannel(), manager, local_mode=True, project_root=ROOT
        )
        client = interface.app.test_client()

        response = client.get("/api/v1/composer/bootstrap?catalog_only=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        payload = response.get_json()
        self.assertEqual(payload["installation_profile"]["digest"], "0" * 64)
        self.assertIsNone(
            payload["capabilities"]["server_actions"]
            ["installation_profile_artifact_url"]
        )

        connectivity = client.get("/api/v1/composer/connectivity")
        self.assertEqual(connectivity.status_code, 200)
        self.assertEqual(
            connectivity.get_json()["bootstrap_url"],
            "/api/v1/composer/bootstrap?catalog_only=1",
        )

    def test_startup_and_reconnect_keep_local_work_independent_of_wall_reads(self) -> None:
        source = COMPOSER_SOURCE.read_text(encoding="utf-8")
        load_start = source.index("async function loadBootstrap()")
        configure_start = source.index("function configureCanvas()", load_start)
        load_body = source[load_start:configure_start]
        self.assertIn("fetch(BUNDLED_BOOTSTRAP_URL", load_body)
        self.assertNotIn("/api/v1/composer/bootstrap'", load_body)

        connectivity_start = source.index("async function checkConnectivity")
        readiness_start = source.index("function showOfflineReadiness", connectivity_start)
        connectivity_body = source[connectivity_start:readiness_start]
        self.assertIn("refreshServerBootstrap", connectivity_body)
        self.assertNotIn("refreshGlobalSettings", connectivity_body)
        self.assertNotIn("preloadMasks", connectivity_body)
        self.assertIn("state.bootstrap.capabilities.server_actions = actions", source)
        self.assertIn("globalActions().installation_profile_artifact_url", source)
        self.assertNotIn("state.bootstrap = payload", source)

        play_start = source.index("function syncPlayButton()")
        compare_start = source.index("async function setCompare", play_start)
        self.assertNotIn("serverOnline", source[play_start:compare_start])
        self.assertIn("scheduleAutosave();", source)
        self.assertIn("function runChecker", source)
        self.assertIn("Composer ready", source)
        self.assertIn("Wall connected", source)

        import_start = source.index("async function importJson")
        masks_start = source.index("function maskLayerById", import_start)
        import_body = source[import_start:masks_start]
        self.assertIn("locallyValidatedImport(payload)", import_body)
        self.assertNotIn("requestJson(", import_body)


if __name__ == "__main__":
    unittest.main()
