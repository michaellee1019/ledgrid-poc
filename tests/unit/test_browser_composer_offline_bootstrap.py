"""Focused first-load, reconnect, and bundled Composer catalog regressions."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
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


def _exact_key_count(value: object, key: str) -> int:
    if isinstance(value, dict):
        return int(key in value) + sum(
            _exact_key_count(item, key) for item in value.values()
        )
    if isinstance(value, list):
        return sum(_exact_key_count(item, key) for item in value)
    return 0


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
        self.assertIsNone(
            payload["capabilities"]["server_actions"]
            ["installation_profile_artifact_url"]
        )
        self.assertNotIn("masks_url", payload["capabilities"]["server_actions"])
        self.assertNotIn(
            "python:painter", {component["key"] for component in payload["components"]}
        )
        self.assertNotIn(b"painter", committed)
        self.assertEqual(_exact_key_count(payload, "preview"), 0)
        self.assertEqual(_exact_key_count(payload, "poster_url"), 0)
        self.assertEqual(_exact_key_count(payload, "loop_url"), 0)

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

        clock = next(
            item for item in payload["components"]
            if item["key"] == "python:clock_overlay"
        )
        self.assertEqual(len(clock["presets"]), 24)
        self.assertTrue(all(
            preset["ownership"] == "built_in" for preset in clock["presets"]
        ))
        self.assertNotIn("CLOCK_STARTING_POINTS", COMPOSER_SOURCE.read_text())

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
        bootstrap_start = source.index("async function refreshServerBootstrap")
        bootstrap_body = source[bootstrap_start:source.index("function setServerOnline", bootstrap_start)]
        self.assertNotIn("refreshGlobalSettings", bootstrap_body)
        self.assertNotIn("preloadMasks", bootstrap_body)
        self.assertNotIn("updateSelectedInstallationProfile", bootstrap_body)
        self.assertIn("state.bootstrap.capabilities.server_actions = actions", source)
        self.assertIn("function mergeServerPresetCatalog", source)
        self.assertIn("managedComponentIdentityMatches", source)
        self.assertIn("Number.isInteger(localIdentity.parameter_schema_version)", source)
        self.assertIn("localComponent.presets = clone(serverComponent.presets)", source)
        self.assertIn("mergeServerPresetCatalog(payload);", source)
        self.assertIn(
            "ComposerState.localInstallationProfile(state.bootstrap)", source
        )
        self.assertNotIn(
            "actions.installation_profile_artifact_url = globalActions()", source
        )
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

    @unittest.skipUnless(shutil.which("node"), "node is required for Composer action regression")
    def test_catalog_refresh_keeps_trusted_profile_draft_action_for_save(self) -> None:
        """A catalog-only reconnect must not erase an open draft's PUT target."""
        source = COMPOSER_SOURCE.read_text(encoding="utf-8")
        refresh_start = source.index("async function refreshServerBootstrap")
        refresh_body = source[refresh_start:source.index("function setServerOnline", refresh_start)]
        self.assertIn(
            "preserveTrustedProfileAuthoringActions(actions, payload);",
            refresh_body,
        )
        self.assertLess(
            refresh_body.index("preserveTrustedProfileAuthoringActions(actions, payload);"),
            refresh_body.index("state.bootstrap.capabilities.server_actions = actions;"),
        )

        helper_start = source.index("const PROFILE_AUTHORING_ACTIONS")
        helper_end = source.index("function initializeInstallationProfileState", helper_start)
        helpers = source[helper_start:helper_end]
        digest = "a" * 64
        script = f"""
const state = {{installationProfile: {{
  selectedDigest: 'selected-profile-must-not-change',
  authoringActions: null,
  authoringDigest: null,
}}}};
const EMPTY_PROFILE_DIGEST = '0'.repeat(64);
{helpers}
const digest = {json.dumps(digest)};
const observed = {{
  installation_profile_draft_url: `/api/v1/installation-profiles/${{digest}}/draft`,
  installation_profile_publish_url: `/api/v1/installation-profiles/${{digest}}/publish`,
  installation_profile_artifact_url: `/api/v1/installation-profiles/${{digest}}/artifact`,
}};
preserveTrustedProfileAuthoringActions(observed, {{installation_profile: {{digest}}}});
const catalogOnly = {{
  installation_profile_draft_url: null,
  installation_profile_publish_url: null,
  installation_profile_artifact_url: null,
}};
preserveTrustedProfileAuthoringActions(catalogOnly, {{
  installation_profile: {{digest: EMPTY_PROFILE_DIGEST}},
}});
preserveTrustedProfileAuthoringActions(catalogOnly, {{
  installation_profile: {{digest: EMPTY_PROFILE_DIGEST}},
}});
console.log(JSON.stringify({{
  actions: catalogOnly,
  authoringDigest: state.installationProfile.authoringDigest,
  selectedDigest: state.installationProfile.selectedDigest,
}}));
"""
        completed = subprocess.run(
            [shutil.which("node") or "node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["authoringDigest"], digest)
        self.assertEqual(result["selectedDigest"], "selected-profile-must-not-change")
        self.assertEqual(result["actions"], {
            "installation_profile_draft_url": (
                f"/api/v1/installation-profiles/{digest}/draft"
            ),
            "installation_profile_publish_url": (
                f"/api/v1/installation-profiles/{digest}/publish"
            ),
            "installation_profile_artifact_url": (
                f"/api/v1/installation-profiles/{digest}/artifact"
            ),
        })

        save_start = source.index("async function saveMasks()")
        save_body = source[save_start:source.index("async function publishProfileDraft", save_start)]
        self.assertIn(
            "requestJsonResource(globalActions().installation_profile_draft_url, {",
            save_body,
        )
        self.assertIn("method: 'PUT'", save_body)
        self.assertIn("'If-Match': `\"${submittedRevision}\"`", save_body)


if __name__ == "__main__":
    unittest.main()
