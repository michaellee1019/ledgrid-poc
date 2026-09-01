"""Focused Scene v2 contract tests for the Firefly Meadow animation."""

from __future__ import annotations

import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.firefly_synchrony import FireflySynchronyAnimation
from web.composer_final_preview import NATIVE_AURORA_BUNDLE_DIGEST, current_component_catalog
from ipc.scene_contract import SceneContractError, normalize_composer_scene
from web.app import AnimationWebInterface


def _scene(parameters: dict | None = None) -> dict:
    return {
        "schema": "ledgrid.scene.v2",
        "background": {"component_id": "native_aurora", "version": 1, "provider": "receiver_native", "role": "background", "bundle_digest": NATIVE_AURORA_BUNDLE_DIGEST, "parameters": {"gain": .62, "source_fps": 30, "seed": 4201}},
        "animation": {"component_id": "firefly_synchrony", "version": 1, "provider": "python", "role": "animation", "parameters": parameters or {}},
        "widgets": [], "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": "mist", "pace": .7, "presentation_brightness": .82},
    }


class FireflyMeadowSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def test_descriptor_and_manifest_controls_are_current_only(self) -> None:
        descriptor = FireflySynchronyAnimation.component_descriptor()
        self.assertEqual((descriptor.provider.value, descriptor.role.value, descriptor.timing_policy.value, descriptor.alpha_behavior.value, descriptor.palette_policy.value), ("python", "animation", "scaled_context", "opaque", "semantic"))
        self.assertEqual(tuple(capability.value for capability in descriptor.plant_capabilities), ("effect_intent",))
        self.assertEqual(descriptor.fidelity_exceptions, ())
        for legacy in ("speed", "brightness", "plant_aware", "color", "role"):
            with self.assertRaisesRegex(ValueError, "non-local parameters"):
                FireflySynchronyAnimation(self.controller, {legacy: 1})

    def test_deterministic_bounded_opaque_meadow_and_rejected_edits_keep_state(self) -> None:
        parameters = {"seed": 41, "population": 220, "synchrony": 1.1, "wandering": .65, "pulse_softness": .45, "meadow_glow": .16, "coupling_radius": 8.0}
        first = FireflySynchronyAnimation(self.controller, parameters)
        second = FireflySynchronyAnimation(self.controller, parameters)
        first_frame, second_frame = first.generate_frame(1.0, 0), second.generate_frame(1.0, 0)
        self.assertEqual(first_frame.pixels.shape, (33 * 138, 3))
        self.assertEqual(first_frame.pixels.dtype, np.uint8)
        np.testing.assert_array_equal(first_frame.pixels, second_frame.pixels)
        first.generate_frame(1.4, 0); second.generate_frame(1.4, 0)
        self.assertEqual(first.semantic_snapshot(), second.semantic_snapshot())
        before = first.semantic_snapshot()
        with self.assertRaisesRegex(ValueError, "population"):
            first.update_parameters({"population": 221})
        self.assertEqual(first.semantic_snapshot(), before)

    def test_catalog_accepts_firefly_and_rejects_legacy_alias_without_replacing_candidate(self) -> None:
        catalog = current_component_catalog()
        canonical = normalize_composer_scene({"origin": "composer", "scene": _scene()}, catalog)
        self.assertEqual(canonical.scene["animation"]["parameters"]["population"], 100)
        invalid = _scene({"population": 100, "brightness": .2})
        with self.assertRaises(SceneContractError):
            normalize_composer_scene({"origin": "composer", "scene": invalid}, catalog)

    def test_preview_and_live_publication_keep_an_ordered_widget_above_firefly(self) -> None:
        class Wall:
            def send_command(self, action: str, **data: object) -> None:
                del action, data

        class PreviewManager:
            controller = PreviewLEDController(strips=33, leds_per_strip=138)
            plugin_loader = None

        scene = _scene()
        scene["widgets"] = [{
            "id": "message", "component": {"component_id": "emoji_arranger", "version": 1, "provider": "python", "role": "widget", "parameters": {}},
            "visible": True, "placement": {"mode": "manual", "strip_translation": 0, "led_translation": 0},
        }]
        client = AnimationWebInterface(Wall(), PreviewManager(), local_mode=True).app.test_client()
        preview = client.post("/api/composer/preview", json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 1.0, "wall_time": "2026-08-31T12:00:00+00:00"}})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.get_json()["frame"]["width"], 33)
        live = client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "firefly-test", "client_sequence": 1})
        self.assertEqual(live.status_code, 200)
        bad = _scene({"population": 221})
        rejected = client.post("/api/composer/scene", json={"origin": "composer", "scene": bad, "client_id": "firefly-test", "client_sequence": 2})
        self.assertEqual(rejected.status_code, 400)
        status = client.get("/api/composer/recovery?client_id=firefly-test").get_json()["status"]
        self.assertEqual(status["current"], live.get_json()["current"])


if __name__ == "__main__":
    unittest.main()
