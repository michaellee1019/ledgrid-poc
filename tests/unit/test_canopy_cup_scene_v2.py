"""Focused Scene v2 acceptance for the Canopy Cup race instrument."""

from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.canopy_cup import CanopyCupAnimation
from ipc.scene_contract import SceneContractError, normalize_composer_scene
from web.app import AnimationWebInterface
from web.composer_final_preview import NATIVE_AURORA_BUNDLE_DIGEST, current_component_catalog


CANOPY_PRESETS = {
    "barrel-temple-climb", "championship-speedrun", "cinematic-no-hud",
    "fern-gully-flutter", "impossible-valley-relay", "neon-vine-night",
    "plant-uprising", "power-up-pandemonium", "seven-realm-grand-prix",
    "storybook-slow-motion", "sunset-crystal-falls", "webline-rooftops",
}


def _scene(parameters: dict | None = None) -> dict:
    return {
        "schema": "ledgrid.scene.v2",
        "background": {"component_id": "native_aurora", "version": 1, "provider": "receiver_native", "role": "background", "bundle_digest": NATIVE_AURORA_BUNDLE_DIGEST, "parameters": {"gain": .62, "source_fps": 30, "seed": 4201}},
        "animation": {"component_id": "canopy_cup", "version": 1, "provider": "python", "role": "animation", "parameters": parameters or {}},
        "widgets": [], "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": "mist", "pace": .7, "presentation_brightness": .82},
    }


class CanopyCupSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def test_opaque_descriptor_and_local_race_controls(self) -> None:
        descriptor = CanopyCupAnimation.component_descriptor()
        self.assertEqual((descriptor.provider.value, descriptor.role.value, descriptor.timing_policy.value, descriptor.alpha_behavior.value, descriptor.palette_policy.value), ("python", "animation", "scaled_context", "opaque", "semantic"))
        self.assertEqual(tuple(capability.value for capability in descriptor.plant_capabilities), ("effect_intent",))
        self.assertEqual(descriptor.fidelity_exceptions, ())
        self.assertEqual(set(CanopyCupAnimation.DEFAULTS), {"seed", "world_theme", "qualifying_heats", "course_difficulty", "enemy_density", "rivalry", "powerup_rate", "show_hud"})
        for legacy in ("speed", "brightness", "render_fps", "plant_aware"):
            with self.assertRaisesRegex(ValueError, "non-local parameters"):
                CanopyCupAnimation(self.controller, {legacy: 1})
        with self.assertRaises(ValueError):
            CanopyCupAnimation(self.controller, {"plant_modifiers": {}})

    def test_all_authored_worlds_are_once_editable_and_keep_hidden_seed_on_remix(self) -> None:
        class Wall:
            def send_command(self, action: str, **data: object) -> None:
                del action, data

        class PreviewManager:
            controller = PreviewLEDController(strips=33, leds_per_strip=138)
            plugin_loader = None

        interface = AnimationWebInterface(Wall(), PreviewManager(), local_mode=True)
        response = interface.app.test_client().get("/api/composer/components/canopy_cup/presets")
        self.assertEqual(response.status_code, 200)
        choices = response.get_json()["presets"]
        self.assertEqual({choice["preset_id"] for choice in choices}, CANOPY_PRESETS)
        self.assertEqual(len(choices), 12)
        selected = next(choice for choice in choices if choice["preset_id"] == "power-up-pandemonium")
        remix = {**selected["parameters"], "rivalry": .35}
        self.assertEqual(remix["seed"], 9090)
        self.assertEqual(remix["show_hud"], True)
        self.assertEqual(CanopyCupAnimation._normalized_parameters(remix)["rivalry"], .35)

    def test_desktop_uses_shared_selected_and_remixed_preset_affordance(self) -> None:
        template = Path("web/templates/composer.html").read_text(encoding="utf-8")
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        for marker in ("canopyPresetCards", "canopyWorld", "canopyHeats", "canopyCourse", "canopyDensity", "canopyRivalry", "canopyPowerups"):
            self.assertIn(marker, template)
            self.assertIn(marker, script)
        self.assertIn("canopy_cup: 'canopyPresetCards'", script)
        self.assertIn("canopyParameters(next.animation.parameters)", script)

    def test_bounded_deterministic_33_by_138_cadence_and_rejected_edit(self) -> None:
        parameters = {"seed": 9090, "world_theme": "crystal_sunset", "qualifying_heats": 5, "course_difficulty": 1.2, "enemy_density": .7, "rivalry": 1., "powerup_rate": 1., "show_hud": True}
        first = CanopyCupAnimation(self.controller, parameters)
        second = CanopyCupAnimation(self.controller, parameters)
        frames = [first.generate_frame(index / 200.0, index) for index in range(200)]
        for index in range(200):
            second.generate_frame(index / 200.0, index)
        self.assertEqual(sum(frame.changed for frame in frames), 30)
        self.assertEqual(frames[0].pixels.shape, (33 * 138, 3))
        self.assertEqual(frames[0].pixels.dtype, np.uint8)
        np.testing.assert_array_equal(frames[-1].pixels, second.last_rendered_frame)
        before = first.logical_state()
        with self.assertRaisesRegex(ValueError, "course_difficulty"):
            first.update_parameters({"course_difficulty": 2.0})
        self.assertEqual(first.logical_state(), before)

    def test_preview_live_and_invalid_candidate_leave_current_scene_intact(self) -> None:
        class Wall:
            def send_command(self, action: str, **data: object) -> None:
                del action, data

        class PreviewManager:
            controller = PreviewLEDController(strips=33, leds_per_strip=138)
            plugin_loader = None

        catalog = current_component_catalog()
        canonical = normalize_composer_scene({"origin": "composer", "scene": _scene()}, catalog)
        self.assertEqual(canonical.scene["animation"]["parameters"]["world_theme"], "tournament")
        with self.assertRaises(SceneContractError):
            normalize_composer_scene({"origin": "composer", "scene": _scene({"brightness": .2})}, catalog)
        client = AnimationWebInterface(Wall(), PreviewManager(), local_mode=True).app.test_client()
        preview = client.post("/api/composer/preview", json={"origin": "composer", "scene": _scene(), "preview": {"monotonic_elapsed": 1.0, "wall_time": "2026-09-01T12:00:00+00:00"}})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual((preview.get_json()["frame"]["width"], preview.get_json()["frame"]["height"]), (33, 138))
        live = client.post("/api/composer/scene", json={"origin": "composer", "scene": _scene(), "client_id": "canopy-test", "client_sequence": 1})
        self.assertEqual(live.status_code, 200)
        rejected = client.post("/api/composer/scene", json={"origin": "composer", "scene": _scene({"enemy_density": 2.0}), "client_id": "canopy-test", "client_sequence": 2})
        self.assertEqual(rejected.status_code, 400)
        status = client.get("/api/composer/recovery?client_id=canopy-test").get_json()["status"]
        self.assertEqual(status["current"], live.get_json()["current"])


if __name__ == "__main__":
    unittest.main()
