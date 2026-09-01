"""Focused Fireworks catalog, Composer, persistence, and ordered-widget proof."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from animation.plugins.fireworks import FireworksAnimation
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_final_preview import current_component_catalog


class FireworksSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        self.client = self.interface.app.test_client()

    @staticmethod
    def _scene() -> dict:
        scene = _current_scene()
        scene["animation"] = {"component_id": "fireworks", "version": 1, "provider": "python", "role": "animation", "parameters": {}}
        scene["widgets"] = [{"id": "message", "component": {"component_id": "emoji_arranger", "version": 1, "provider": "python", "role": "widget", "parameters": {}}, "visible": True, "placement": {"mode": "manual", "strip_translation": 0, "led_translation": 0}}]
        return scene

    def test_catalog_is_exactly_five_choices_and_changes_only_animation_parameters(self) -> None:
        choices = self.interface.composer_presets.choices("fireworks")
        self.assertEqual([choice["preset_id"] for choice in choices], ["golden-willows", "grand-finale", "neon-crackle", "patriotic-salute", "quiet-sparklers"])
        scene = self._scene(); result = self.interface.composer_presets.apply(scene, "neon-crackle")
        self.assertEqual(result["background"], scene["background"]); self.assertEqual(result["widgets"], scene["widgets"]); self.assertEqual(result["look"], scene["look"])
        self.assertEqual(result["animation"]["component_id"], "fireworks")
        self.assertEqual(result["animation"]["parameters"]["burst_style"], "burst")
        self.assertNotIn("brightness", result["animation"]["parameters"])
        self.assertEqual(self.client.get("/api/composer/components/fireworks/presets").status_code, 200)

    def test_preview_live_stop_rearm_and_recovery_preserve_fireworks_identity(self) -> None:
        scene = self.interface.composer_presets.apply(self._scene(), "grand-finale")
        preview = self.client.post("/api/composer/preview", json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-08-31T12:00:00+00:00"}})
        self.assertEqual(preview.status_code, 200); self.assertEqual(preview.get_json()["frame"]["width"], 33)
        live = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "fireworks", "client_sequence": 1})
        self.assertEqual(live.status_code, 200); self.assertEqual(live.get_json()["state"], "live")
        self.assertEqual(self.client.post("/api/composer/stop", json={"client_id": "fireworks"}).get_json()["status"]["state"], "stopped")
        edited = copy.deepcopy(scene); edited["animation"]["parameters"]["trails"] = .31
        local = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": edited, "client_id": "fireworks", "client_sequence": 2})
        self.assertFalse(local.get_json()["published"])
        self.assertEqual(self.client.get("/api/composer/recovery?client_id=fireworks").get_json()["recovery"]["scene"]["animation"]["component_id"], "fireworks")
        self.assertEqual(self.client.post("/api/composer/go-live", json={"client_id": "fireworks"}).get_json()["status"]["state"], "live")
        with self.assertRaisesRegex(ValueError, "non-local parameters"):
            FireworksAnimation._normalized_parameters({"brightness": .4})
        self.assertIs(current_component_catalog().require(provider="python", component_id="fireworks", version=1), FireworksAnimation.component_descriptor())

    def test_composer_card_keeps_preset_and_control_edits_in_one_scene_lane(self) -> None:
        root = Path(__file__).resolve().parents[2]
        template = (root / "web" / "templates" / "composer.html").read_text(encoding="utf-8")
        script = (root / "web" / "static" / "js" / "composer_slice.js").read_text(encoding="utf-8")
        self.assertIn('id="fireworksPresetCards"', template)
        self.assertIn("components/fireworks/presets", script)
        self.assertIn("next.animation.parameters = preset.parameters", script)
        self.assertIn("'#fireworksCadence'", script)
        self.assertNotIn("output_power", script)


if __name__ == "__main__":
    unittest.main()
