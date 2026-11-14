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

    def test_catalog_is_exactly_six_choices_and_changes_only_animation_parameters(self) -> None:
        choices = self.interface.composer_presets.choices("fireworks")
        self.assertEqual([choice["preset_id"] for choice in choices], ["golden-willows", "grand-finale", "neon-crackle", "patriotic-salute", "quiet-sparklers", "rainbow-palm"])
        scene = self._scene(); result = self.interface.composer_presets.apply(scene, "neon-crackle")
        self.assertEqual(result["background"], scene["background"]); self.assertEqual(result["widgets"], scene["widgets"]); self.assertEqual(result["look"], scene["look"])
        self.assertEqual(result["animation"]["component_id"], "fireworks")
        self.assertEqual(result["animation"]["parameters"]["burst_style"], "burst")
        self.assertNotIn("brightness", result["animation"]["parameters"])
        self.assertEqual(self.client.get("/api/composer/components/fireworks/presets").status_code, 200)

    def test_rainbow_palm_sets_palm_shells_rainbow_look_and_black_background(self) -> None:
        scene = self._scene()
        scene["look"] = {"palette_id": "ember", "pace": 0.7, "presentation_brightness": 0.82}
        scene["background"]["parameters"]["gain"] = 0.62
        result = self.interface.composer_presets.apply(scene, "rainbow-palm")
        self.assertEqual(result["widgets"], scene["widgets"])
        self.assertEqual(result["animation"]["parameters"]["burst_style"], "palm")
        self.assertEqual(result["animation"]["parameters"]["launch_cadence"], 1.5)
        self.assertEqual(result["animation"]["parameters"]["burst_size"], 0.55)
        self.assertEqual(result["animation"]["parameters"]["shell_population"], 50)
        self.assertEqual(result["look"], {"palette_id": "rainbow", "pace": 1.0, "presentation_brightness": 1.5})
        self.assertEqual(result["background"]["parameters"]["gain"], 0.0)
        self.assertEqual(result["background"]["parameters"]["seed"], scene["background"]["parameters"]["seed"])

    def test_preview_live_stop_auto_resume_and_recovery_preserve_fireworks_identity(self) -> None:
        scene = self.interface.composer_presets.apply(self._scene(), "grand-finale")
        preview = self.client.post("/api/composer/preview", json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-08-31T12:00:00+00:00"}})
        self.assertEqual(preview.status_code, 200); self.assertEqual(preview.get_json()["frame"]["width"], 33)
        live = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "fireworks", "client_sequence": 1})
        self.assertEqual(live.status_code, 200); self.assertEqual(live.get_json()["state"], "live")
        self.assertEqual(self.client.post("/api/composer/stop", json={"client_id": "fireworks"}).get_json()["status"]["state"], "stopped")
        edited = copy.deepcopy(scene); edited["animation"]["parameters"]["trails"] = .31
        local = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": edited, "client_id": "fireworks", "client_sequence": 2})
        self.assertTrue(local.get_json()["published"])
        self.assertEqual(local.get_json()["state"], "live")
        self.assertEqual(self.client.get("/api/composer/recovery?client_id=fireworks").get_json()["recovery"]["scene"]["animation"]["component_id"], "fireworks")
        with self.assertRaisesRegex(ValueError, "non-local parameters"):
            FireworksAnimation._normalized_parameters({"brightness": .4})
        self.assertEqual(current_component_catalog().require(provider="python", component_id="fireworks", version=1).factory.__name__, FireworksAnimation.__name__)

    def test_composer_card_keeps_preset_and_control_edits_in_one_scene_lane(self) -> None:
        gallery = self.client.get("/api/composer/gallery").get_json()["entries"]
        entry = next(item for item in gallery if item["component_id"] == "fireworks")
        self.assertIn("launch_cadence", entry["parameter_schema"])
        self.assertEqual(entry["preset_count"], 6)
        presets = self.client.get("/api/composer/components/fireworks/presets").get_json()["presets"]
        self.assertEqual(next(item for item in presets if item["preset_id"] == "rainbow-palm")["presentation"]["background_gain"], 0)

if __name__ == "__main__":
    unittest.main()
