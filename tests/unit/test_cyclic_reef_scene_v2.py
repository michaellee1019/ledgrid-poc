"""Focused Cyclic Reef Scene v2 catalog, cadence, and live-state proof."""

from __future__ import annotations

import copy
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.cyclic_reef import CyclicReefAnimation
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_final_preview import current_component_catalog


class CyclicReefSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        self.client = self.interface.app.test_client()

    @staticmethod
    def _scene() -> dict:
        scene = _current_scene()
        scene["animation"] = {"component_id": "cyclic_reef", "version": 1, "provider": "python", "role": "animation", "parameters": {}}
        return scene

    def test_exact_authored_choices_are_atomic_component_parameters(self) -> None:
        choices = self.interface.composer_presets.choices("cyclic_reef")
        self.assertEqual([choice["preset_id"] for choice in choices], ["night", "quiet", "showcase"])
        scene = self._scene()
        result = self.interface.composer_presets.apply(scene, "showcase")
        self.assertEqual(result["background"], scene["background"])
        self.assertEqual(result["widgets"], scene["widgets"])
        self.assertEqual(result["look"], scene["look"])
        self.assertEqual(result["animation"]["parameters"]["species_count"], 6)
        self.assertNotIn("brightness", result["animation"]["parameters"])
        self.assertNotIn("plant_aware", result["animation"]["parameters"])
        self.assertEqual(self.client.get("/api/composer/components/cyclic_reef/presets").status_code, 200)

    def test_deterministic_bounded_cadence_and_invalid_edits_preserve_state(self) -> None:
        config = {"seed": 314, "species_count": 6, "takeover_threshold": 2, "mutation": .006, "grazers": 18, "pace": 1.3}
        first = CyclicReefAnimation(PreviewLEDController(strips=33, leds_per_strip=138), config)
        second = CyclicReefAnimation(PreviewLEDController(strips=33, leds_per_strip=138), config)
        frames_a = [first.generate_frame(time, index).pixels.copy() for index, time in enumerate((0., .5, 1., 3.))]
        frames_b = [second.generate_frame(time, index).pixels.copy() for index, time in enumerate((0., .5, 1., 3.))]
        for left, right in zip(frames_a, frames_b):
            self.assertTrue(np.array_equal(left, right))
        self.assertLessEqual(first.cadence_snapshot()["simulation_hz"], 24.0)
        before = copy.deepcopy(first.params)
        with self.assertRaisesRegex(ValueError, "non-local parameters"):
            first.update_parameters({"brightness": .4})
        self.assertEqual(first.params, before)
        with self.assertRaisesRegex(ValueError, "grazers"):
            first.update_parameters({"grazers": 49})
        self.assertEqual(first.params, before)
        original = first.generate_frame(3.5, 4).pixels.copy()
        first.update_parameters({"boundary_glow": .1, "topology": "closed"})
        self.assertFalse(np.array_equal(original, first.generate_frame(3.5, 5).pixels))

    def test_preview_live_stop_and_remix_keep_hidden_parameters(self) -> None:
        scene = self.interface.composer_presets.apply(self._scene(), "showcase")
        preview = self.client.post("/api/composer/preview", json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-09-01T12:00:00+00:00"}})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.get_json()["frame"]["width"], 33)
        live = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "cyclic-reef", "client_sequence": 1})
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.get_json()["state"], "live")
        self.assertEqual(self.client.post("/api/composer/stop", json={"client_id": "cyclic-reef"}).get_json()["status"]["state"], "stopped")
        remix = copy.deepcopy(scene)
        remix["animation"]["parameters"]["boundary_glow"] = .2
        remixed_preview = self.client.post("/api/composer/preview", json={"origin": "composer", "scene": remix, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-09-01T12:00:00+00:00"}}).get_json()
        remixed_live = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": remix, "client_id": "cyclic-reef", "client_sequence": 2}).get_json()
        rearmed = self.client.post("/api/composer/go-live", json={"client_id": "cyclic-reef"}).get_json()
        params = self.client.get("/api/composer/recovery?client_id=cyclic-reef").get_json()["recovery"]["scene"]["animation"]["parameters"]
        self.assertEqual(remixed_preview["basis"]["digest"], remixed_live["current"]["digest"])
        self.assertEqual(remixed_live["current"]["digest"], rearmed["status"]["observed"]["digest"])
        self.assertEqual(params["boundary_glow"], .2)
        for name, expected in {"species_count": 6, "takeover_threshold": 2, "mutation": .006, "grazers": 18, "topology": "wrap", "pace": 1.3}.items():
            self.assertEqual(params[name], expected)
        self.assertIs(current_component_catalog().require(provider="python", component_id="cyclic_reef", version=1), CyclicReefAnimation.component_descriptor())

    def test_invalid_reef_controls_reject_before_live_or_preview_mutation(self) -> None:
        scene = self.interface.composer_presets.apply(self._scene(), "showcase")
        preview_request = {"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-09-01T12:00:00+00:00"}}
        preview = self.client.post("/api/composer/preview", json=preview_request)
        published = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "reef-validator", "client_sequence": 1})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(published.status_code, 200)
        before_status = self.client.get("/api/composer/status").get_json()
        before_commands = copy.deepcopy(self.interface.composer_control.commands)
        invalid = copy.deepcopy(scene)
        invalid["animation"]["parameters"]["grazers"] = 49
        rejected_live = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": invalid, "client_id": "reef-validator", "client_sequence": 2})
        rejected_preview = self.client.post("/api/composer/preview", json={**preview_request, "scene": invalid})
        self.assertEqual(rejected_live.status_code, 400)
        self.assertIn("grazers", rejected_live.get_json()["error"])
        self.assertEqual(rejected_preview.status_code, 400)
        self.assertIn("grazers", rejected_preview.get_json()["error"])
        after_status = self.client.get("/api/composer/status").get_json()
        self.assertEqual(after_status["current"], before_status["current"])
        self.assertEqual(after_status["desired"], before_status["desired"])
        self.assertEqual(after_status["observed"], before_status["observed"])
        self.assertEqual(self.interface.composer_control.commands, before_commands)
        self.assertEqual(self.client.post("/api/composer/preview", json=preview_request).get_json()["basis"], preview.get_json()["basis"])

    def test_composer_card_keeps_reef_controls_local(self) -> None:
        root = __import__("pathlib").Path(__file__).resolve().parents[2]
        template = (root / "web" / "templates" / "composer.html").read_text(encoding="utf-8")
        script = (root / "web" / "static" / "js" / "composer_slice.js").read_text(encoding="utf-8")
        self.assertIn('id="reefPresetCards"', template)
        self.assertIn("components/cyclic_reef/presets", script)
        self.assertIn("reefParameters(next.animation.parameters)", script)
        self.assertNotIn("output_power", script)


if __name__ == "__main__":
    unittest.main()
