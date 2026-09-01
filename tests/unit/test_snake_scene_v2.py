"""Focused Snake Scene v2 catalog, simulation, and live-state proof."""

from __future__ import annotations

import copy
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.snake import SnakeAnimation
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_final_preview import current_component_catalog


class SnakeSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        self.client = self.interface.app.test_client()

    @staticmethod
    def _scene() -> dict:
        scene = _current_scene()
        scene["animation"] = {"component_id": "snake", "version": 1, "provider": "python", "role": "animation", "parameters": {}}
        scene["widgets"] = [{"id": "message", "component": {"component_id": "emoji_arranger", "version": 1, "provider": "python", "role": "widget", "parameters": {}}, "visible": True, "placement": {"mode": "manual", "strip_translation": 0, "led_translation": 0}}]
        return scene

    def test_exact_authored_choices_are_atomic_component_parameters(self) -> None:
        choices = self.interface.composer_presets.choices("snake")
        self.assertEqual([choice["preset_id"] for choice in choices], ["classic-orchard", "comet-garden", "electric-hive", "fire-serpents", "ice-labyrinth", "koi-at-midnight", "neon-duel", "portal-bloom", "prism-switchbacks", "rainbow-river"])
        scene = self._scene(); result = self.interface.composer_presets.apply(scene, "portal-bloom")
        self.assertEqual(result["background"], scene["background"]); self.assertEqual(result["widgets"], scene["widgets"]); self.assertEqual(result["look"], scene["look"])
        self.assertEqual(result["animation"]["component_id"], "snake")
        self.assertEqual(result["animation"]["parameters"]["ruleset"], "portal")
        self.assertNotIn("brightness", result["animation"]["parameters"])
        self.assertNotIn("plant_aware", result["animation"]["parameters"])
        self.assertEqual(self.client.get("/api/composer/components/snake/presets").status_code, 200)

    def test_deterministic_bounded_pacing_and_invalid_edits_preserve_state(self) -> None:
        config = {"seed": 314, "snake_count": 3, "ruleset": "portal", "obstacles": "pillars", "move_cadence": 10.0}
        first, second = SnakeAnimation(PreviewLEDController(strips=33, leds_per_strip=138), config), SnakeAnimation(PreviewLEDController(strips=33, leds_per_strip=138), config)
        frames_a = [first.generate_frame(time, index).pixels.copy() for index, time in enumerate((0., .5, 1., 3.))]
        frames_b = [second.generate_frame(time, index).pixels.copy() for index, time in enumerate((0., .5, 1., 3.))]
        for left, right in zip(frames_a, frames_b): self.assertTrue(np.array_equal(left, right))
        self.assertLessEqual(first.cadence_snapshot()["simulation_hz"], 24.0)
        before = copy.deepcopy(first.params)
        with self.assertRaisesRegex(ValueError, "non-local parameters"):
            first.update_parameters({"brightness": .4})
        self.assertEqual(first.params, before)
        with self.assertRaisesRegex(ValueError, "move_cadence"):
            first.update_parameters({"move_cadence": 100})
        self.assertEqual(first.params, before)
        before_edit = first.generate_frame(3.5, 4).pixels.copy()
        first.update_parameters({"snake_count": 7, "trails": .2})
        self.assertEqual(first.params["snake_count"], 7); self.assertEqual(first.params["trails"], .2)
        self.assertFalse(np.array_equal(before_edit, first.generate_frame(3.5, 5).pixels))

    def test_preview_live_stop_rearm_and_recovery_preserve_snake_identity(self) -> None:
        scene = self.interface.composer_presets.apply(self._scene(), "electric-hive")
        preview = self.client.post("/api/composer/preview", json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-08-31T12:00:00+00:00"}})
        self.assertEqual(preview.status_code, 200); self.assertEqual(preview.get_json()["frame"]["width"], 33)
        live = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "snake", "client_sequence": 1})
        self.assertEqual(live.status_code, 200); self.assertEqual(live.get_json()["state"], "live")
        self.assertEqual(self.client.post("/api/composer/stop", json={"client_id": "snake"}).get_json()["status"]["state"], "stopped")
        edited = copy.deepcopy(scene); edited["animation"]["parameters"]["glow"] = .19
        local = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": edited, "client_id": "snake", "client_sequence": 2})
        self.assertFalse(local.get_json()["published"])
        self.assertEqual(self.client.get("/api/composer/recovery?client_id=snake").get_json()["recovery"]["scene"]["animation"]["component_id"], "snake")
        self.assertEqual(self.client.post("/api/composer/go-live", json={"client_id": "snake"}).get_json()["status"]["state"], "live")
        self.assertIs(current_component_catalog().require(provider="python", component_id="snake", version=1), SnakeAnimation.component_descriptor())

    def test_portal_bloom_glow_remix_keeps_preview_live_and_unshown_params_in_lockstep(self) -> None:
        portal_bloom = self.interface.composer_presets.apply(self._scene(), "portal-bloom")
        preview = self.client.post("/api/composer/preview", json={"origin": "composer", "scene": portal_bloom, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-08-31T12:00:00+00:00"}}).get_json()
        published = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": portal_bloom, "client_id": "portal-bloom", "client_sequence": 1}).get_json()
        self.assertEqual(preview["basis"]["digest"], published["current"]["digest"])
        self.assertEqual(published["current"]["digest"], published["observed"]["digest"])
        remix = copy.deepcopy(portal_bloom); remix["animation"]["parameters"]["glow"] = .2
        remixed_preview = self.client.post("/api/composer/preview", json={"origin": "composer", "scene": remix, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-08-31T12:00:00+00:00"}}).get_json()
        remixed_live = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": remix, "client_id": "portal-bloom", "client_sequence": 2}).get_json()
        params = self.client.get("/api/composer/recovery?client_id=portal-bloom").get_json()["recovery"]["scene"]["animation"]["parameters"]
        self.assertEqual(remixed_preview["basis"]["digest"], remixed_live["current"]["digest"])
        self.assertEqual(remixed_live["current"]["digest"], remixed_live["observed"]["digest"])
        self.assertEqual(params["glow"], .2)
        for name, expected in {"background": "aurora", "visual_style": "prism", "trail_decay": 1.25, "initial_length": 8}.items():
            self.assertEqual(params[name], expected)

    def test_composer_card_keeps_snake_controls_local(self) -> None:
        root = __import__("pathlib").Path(__file__).resolve().parents[2]
        template = (root / "web" / "templates" / "composer.html").read_text(encoding="utf-8")
        script = (root / "web" / "static" / "js" / "composer_slice.js").read_text(encoding="utf-8")
        self.assertIn('id="snakePresetCards"', template); self.assertIn("components/snake/presets", script)
        self.assertIn("next.animation.parameters = preset.parameters", script); self.assertIn("'#snakeCadence'", script)
        self.assertIn("snakeParameters(next.animation.parameters)", script)
        self.assertNotIn("output_power", script)


if __name__ == "__main__":
    unittest.main()
