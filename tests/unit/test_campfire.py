"""Focused Scene v2 contract tests for the Campfire animation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.presentation_contracts import ComponentProvider, ComponentRef, ResolvedScene, SceneState
from animation.plugins.campfire import CampfireAnimation
from ipc.scene_contract import SceneContractError, normalize_composer_scene
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_final_preview import current_component_catalog


ROOT = Path(__file__).resolve().parents[2]
PRESET_IDS = ["ember-pit", "night", "quiet", "roaring"]


def _scene(parameters: dict | None = None) -> dict:
    scene = _current_scene()
    scene["animation"] = {
        "component_id": "campfire", "version": 1, "provider": "python", "role": "animation",
        "parameters": parameters or {},
    }
    return scene


def _context(animation, palette_id: str, phase_time: float = 1.0) -> ResolvedScene:
    return ResolvedScene(
        canonical_scene={"plants": {"effects": {"version": 1, "active": [], "strengths": {}}}},
        canonical_bytes=b"look", digest="look",
        descriptor=animation.component_descriptor(),
        parameters=dict(animation.params),
        palette={"palette_id": palette_id}, phase_time=phase_time, plant_inputs={},
    )


class CampfireSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def test_descriptor_and_manifest_controls_are_current_only(self) -> None:
        descriptor = CampfireAnimation.component_descriptor()
        self.assertEqual(
            (descriptor.provider.value, descriptor.role.value, descriptor.timing_policy.value,
             descriptor.alpha_behavior.value, descriptor.palette_policy.value),
            ("python", "animation", "scaled_context", "opaque", "semantic"),
        )
        self.assertEqual(tuple(capability.value for capability in descriptor.plant_capabilities), ("effect_intent",))
        for legacy in ("speed", "brightness", "plant_aware", "color", "role"):
            with self.assertRaisesRegex(ValueError, "non-local parameters"):
                CampfireAnimation(self.controller, {legacy: 1})

    def test_fire_stays_on_the_ground_and_sparks_climb_the_sky(self) -> None:
        parameters = {"seed": 77, "heat": 0.88, "sparks": 0.9, "smoke": 0.6, "flicker": 0.4}
        left = CampfireAnimation(self.controller, parameters)
        right = CampfireAnimation(self.controller, parameters)
        for index in range(48):
            a, b = left.generate_frame(index / 24.0, index), right.generate_frame(index / 24.0, index)
            np.testing.assert_array_equal(a.pixels, b.pixels)
        self.assertEqual(left.generate_frame(47 / 24.0, 100).pixels.shape, (33 * 138, 3))
        self.assertFalse(left.generate_frame(47 / 24.0, 101).changed)
        warmed = CampfireAnimation(self.controller, parameters)
        for index in range(48):
            warmed.generate_frame(index / 24.0, index)
        fresh = CampfireAnimation(self.controller, parameters)
        self.assertNotEqual(warmed.semantic_snapshot()["spark_y"], fresh.semantic_snapshot()["spark_y"])
        wall = warmed._pixels.reshape(33, 138, 3)
        bottom = float(wall[:, :24].astype(np.float32).mean())
        top = float(wall[:, -24:].astype(np.float32).mean())
        self.assertGreater(bottom, top)
        _, fire_y = fresh._fire_center()
        fresh.spark_y.fill(fire_y)
        fresh.spark_life.fill(1.2)
        before = float(np.mean(fresh.spark_y))
        fresh._simulate_step(1.0 / fresh.SIM_HZ)
        self.assertLess(float(np.mean(fresh.spark_y)), before - 0.4)

    def test_spark_ignition_points_use_physical_bottom_origin(self) -> None:
        animation = CampfireAnimation(self.controller, {"sparks": 1.0, "seed": 11})
        animation.spark_x[:] = 8.0
        animation.spark_y[:] = 0.0
        points = animation.spark_ignition_points()
        self.assertEqual(points.shape, (animation.MAX_SPARKS, 2))
        np.testing.assert_allclose(points[:, 0], 8.0)
        np.testing.assert_allclose(points[:, 1], 137.0)
        animation.params["sparks"] = 0.0
        self.assertEqual(animation.spark_ignition_points().shape, (0, 2))

    def test_rainbow_and_holiday_looks_are_presentation_only(self) -> None:
        ember = CampfireAnimation(self.controller, {"seed": 1903})
        ember.set_presentation_context(_context(ember, "ember"))
        ember.generate_frame(1.0, 0)
        snapshot = dict(ember.semantic_snapshot())
        frames = {"ember": ember._pixels.copy()}
        for palette_id in ("rainbow", "xmas", "fall", "easter", "july4"):
            fire = CampfireAnimation(self.controller, {"seed": 1903})
            fire.set_presentation_context(_context(fire, palette_id))
            fire.generate_frame(1.0, 0)
            self.assertEqual(dict(fire.semantic_snapshot()), snapshot)
            frames[palette_id] = fire._pixels.copy()
        self.assertEqual(len({pixels.tobytes() for pixels in frames.values()}), len(frames))

    def test_catalog_preview_and_presets_keep_local_campfire(self) -> None:
        catalog = current_component_catalog()
        self.assertIs(
            catalog.require(provider="python", component_id="campfire", version=1),
            CampfireAnimation.component_descriptor(),
        )
        canonical = normalize_composer_scene({"origin": "composer", "scene": _scene()}, catalog)
        self.assertAlmostEqual(canonical.scene["animation"]["parameters"]["heat"], 0.78)
        invalid = _scene({"heat": 0.78, "brightness": 0.2})
        with self.assertRaises(SceneContractError):
            normalize_composer_scene({"origin": "composer", "scene": invalid}, catalog)
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        self.assertEqual(
            [choice["preset_id"] for choice in interface.composer_presets.choices("campfire")],
            PRESET_IDS,
        )
        scene = interface.composer_presets.apply(_scene(), "roaring")
        self.assertAlmostEqual(scene["animation"]["parameters"]["heat"], 0.96)
        preview = client.post(
            "/api/composer/preview",
            json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 1.0, "wall_time": "2026-11-07T12:00:00+00:00"}},
        )
        self.assertEqual(preview.status_code, 200)
        live = client.post(
            "/api/composer/scene",
            json={"origin": "composer", "scene": scene, "client_id": "campfire", "client_sequence": 1},
        )
        self.assertEqual(live.status_code, 200)
        paths = sorted((ROOT / "animation/plugins/campfire/presets").glob("*.json"))
        self.assertEqual([path.stem for path in paths], PRESET_IDS)
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            CampfireAnimation._normalized_parameters(payload["params"])

    def test_strict_live_manager_accepts_and_renders_campfire(self) -> None:
        manager = AnimationManager(self.controller, auto_start=False)
        manager._launch_animation_loop = lambda: None
        component = ComponentRef(
            plugin_id="campfire",
            provider=ComponentProvider.PYTHON,
            resolved_parameters=dict(CampfireAnimation.DEFAULTS),
        )
        try:
            self.assertTrue(manager.start_scene(SceneState(
                revision=1, background=component, overlays=(), known_python_fallback=component,
            )))
            self.assertEqual(manager.render_composed_scene_frame().pixels.shape, (33 * 138, 3))
        finally:
            manager.stop_animation(clear_leds=False)


if __name__ == "__main__":
    unittest.main()
