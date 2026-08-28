"""Static regression coverage for retired direct activation and mask authority."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class GuardedUiLegacyDebtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.browser_contract = (
            ROOT / "docs" / "browser-scene-contract-v1.md"
        ).read_text(encoding="utf-8")
        cls.studio_contract = (ROOT / "docs" / "STUDIO_NEXT.md").read_text(
            encoding="utf-8"
        )
        cls.studio_client = (
            ROOT / "web" / "static" / "js" / "studio_next.js"
        ).read_text(encoding="utf-8")
        cls.studio_template = (
            ROOT / "web" / "templates" / "studio_next.html"
        ).read_text(encoding="utf-8")
        cls.web_readme = (ROOT / "web" / "README.md").read_text(encoding="utf-8")
        cls.wall_conductor_readme = (
            ROOT / "prototypes" / "wall-conductor" / "README.md"
        ).read_text(encoding="utf-8")
        cls.finder_decisions = (
            ROOT / "prototypes" / "finder-wall-studio" / "PRODUCT_DECISIONS.md"
        ).read_text(encoding="utf-8")
        cls.studio_synthesis = (
            ROOT / "prototypes" / "evaluations" / "SYNTHESIS.md"
        ).read_text(encoding="utf-8")
        cls.web_app = (ROOT / "web" / "app.py").read_text(encoding="utf-8")

    def test_browser_contract_requires_check_envelope_and_correlated_resource(self) -> None:
        for claim in (
            "POST /api/v1/scene/checks",
            "check_token",
            "Idempotency-Key",
            "202",
            "acceptance is Pending, never Active",
            "GET /api/v1/scene/activations/<activation_id>",
        ):
            with self.subTest(claim=claim):
                self.assertIn(claim, self.browser_contract)
        self.assertNotIn(
            "accepts the browser-scene document directly,\n"
            "  independently validates it for activation",
            self.browser_contract,
        )
        self.assertNotIn("dispatches the existing `start_scene` command", self.browser_contract)
        self.assertNotIn("can prove only command acceptance", self.browser_contract)

    def test_studio_next_contract_and_client_have_no_direct_live_authority(self) -> None:
        self.assertIn("Check & activate in Composer", self.studio_contract)
        self.assertIn("fail closed with `428`", self.studio_contract)
        self.assertIn(
            "return {allowed: false, composerEligible, code, reason: String(reason)}",
            self.studio_client,
        )
        self.assertIn(
            "component.action?.composerEligible !== true", self.studio_client
        )
        self.assertNotIn("component.action?.allowed !== true", self.studio_client)
        for stale_claim in (
            "Reviewed direct look command",
            "Reviewed fixed-scene command",
            "route-backed live action",
            "ready to take live",
            "Ready for reviewed live action",
            "executable route verified by server catalog",
        ):
            with self.subTest(stale_claim=stale_claim):
                self.assertNotIn(stale_claim, self.studio_contract)
                self.assertNotIn(stale_claim, self.studio_client)
        self.assertGreaterEqual(self.studio_template.count('href="/composer"'), 2)

    def test_readiness_metadata_is_not_direct_activation_authority(self) -> None:
        self.assertNotIn("def _studio_next_look_action(", self.web_app)
        self.assertNotIn("def _validated_studio_next_scene_request(", self.web_app)
        self.assertIn("def _studio_next_composer_eligibility(", self.web_app)
        self.assertIn("never activation authority", self.web_app)
        self.assertIn("'take_look_enabled': False", self.web_app)
        self.assertIn("'composer_check_eligible': eligible", self.web_app)
        self.assertIn("action.composer_check_eligible === true", self.studio_client)

    def test_active_route_inventory_marks_preset_apply_fail_closed(self) -> None:
        compact = " ".join(self.web_readme.split())
        self.assertIn(
            "POST /api/animations/<animation_name>/presets/<preset_id>/apply` — "
            "rejected with 428; load the preset as a draft and use Composer Check",
            compact,
        )

    def test_unwired_prototype_maps_are_explicitly_historical(self) -> None:
        for artifact in (
            self.wall_conductor_readme,
            self.finder_decisions,
            self.studio_synthesis,
        ):
            with self.subTest(heading=artifact.splitlines()[0]):
                compact = " ".join(artifact.casefold().split())
                self.assertIn("superseded", compact)
                self.assertIn("composer check", compact)
                self.assertIn("managed", compact)
                self.assertIn("installation-profile", compact)

    def test_fail_closed_aliases_preserve_stop_and_read_only_mask_boundaries(self) -> None:
        for alias in (
            "'/api/v1/studio-next/take-look'",
            "'/api/v1/studio-next/take-scene'",
            "'/api/start/<animation_name>'",
        ):
            with self.subTest(alias=alias):
                self.assertIn(alias, self.web_app)
        self.assertGreaterEqual(self.web_app.count("_guarded_scene_error("), 6)
        self.assertIn("@self.app.route('/api/stop', methods=['POST'])", self.web_app)
        self.assertIn("@self.app.route('/api/painter/masks', methods=['GET'])", self.web_app)
        self.assertIn("@self.app.route('/api/painter/masks', methods=['POST'])", self.web_app)
        self.assertIn("response.status_code = 405", self.web_app)
        self.assertIn("response.headers['Allow'] = 'GET'", self.web_app)


if __name__ == "__main__":
    unittest.main()
