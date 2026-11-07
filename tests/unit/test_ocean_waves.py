"""Focused Scene v2 contract tests for the Ocean Waves animation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.presentation_contracts import ComponentProvider, ComponentRef, ResolvedScene, SceneState
from animation.plugins.ocean_waves import OceanWavesAnimation
from ipc.scene_contract import SceneContractError, normalize_composer_scene
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_final_preview import current_component_catalog


ROOT = Path(__file__).resolve().parents[2]
PRESET_IDS = ["night", "quiet", "storm", "surf"]


def _scene(parameters: dict | None = None) -> dict:
    scene = _current_scene()
    scene["animation"] = {
        "component_id": "ocean_waves", "version": 1, "provider": "python", "role": "animation",
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


class OceanWavesSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def test_descriptor_and_manifest_controls_are_current_only(self) -> None:
        descriptor = OceanWavesAnimation.component_descriptor()
        self.assertEqual(
            (descriptor.provider.value, descriptor.role.value, descriptor.timing_policy.value,
             descriptor.alpha_behavior.value, descriptor.palette_policy.value),
            ("python", "animation", "scaled_context", "opaque", "semantic"),
        )
        self.assertEqual(tuple(capability.value for capability in descriptor.plant_capabilities), ("effect_intent",))
        for legacy in ("speed", "brightness", "plant_aware", "color", "role"):
            with self.assertRaisesRegex(ValueError, "non-local parameters"):
                OceanWavesAnimation(self.controller, {legacy: 1})

    def test_deterministic_swells_cache_unchanged_ticks_and_travel_down_the_wall(self) -> None:
        parameters = {"seed": 77, "swell": 0.8, "chop": 0.4, "foam": 0.6, "travel": 1.1}
        left = OceanWavesAnimation(self.controller, parameters)
        right = OceanWavesAnimation(self.controller, parameters)
        for index in range(48):
            a, b = left.generate_frame(index / 24.0, index), right.generate_frame(index / 24.0, index)
            np.testing.assert_array_equal(a.pixels, b.pixels)
        self.assertEqual(left.generate_frame(47 / 24.0, 100).pixels.shape, (33 * 138, 3))
        self.assertFalse(left.generate_frame(47 / 24.0, 101).changed)
        early = OceanWavesAnimation(self.controller, parameters).generate_frame(0.5, 0).pixels
        later = OceanWavesAnimation(self.controller, parameters).generate_frame(2.5, 0).pixels
        self.assertFalse(np.array_equal(early, later))
        canvas = later.reshape(33, 138, 3)[:, ::-1]
        self.assertGreater(int(np.count_nonzero(np.max(canvas, axis=2) > 40)), 80)

    def test_rainbow_and_holiday_looks_are_presentation_only(self) -> None:
        ember = OceanWavesAnimation(self.controller, {"seed": 3301})
        ember.set_presentation_context(_context(ember, "ember"))
        ember.generate_frame(1.0, 0)
        snapshot = dict(ember.semantic_snapshot())
        frames = {"ember": ember._pixels.copy()}
        for palette_id in ("rainbow", "xmas", "fall", "easter", "july4"):
            wave = OceanWavesAnimation(self.controller, {"seed": 3301})
            wave.set_presentation_context(_context(wave, palette_id))
            wave.generate_frame(1.0, 0)
            self.assertEqual(dict(wave.semantic_snapshot()), snapshot)
            frames[palette_id] = wave._pixels.copy()
        self.assertEqual(len({pixels.tobytes() for pixels in frames.values()}), len(frames))

    def test_catalog_preview_and_presets_keep_local_ocean_waves(self) -> None:
        catalog = current_component_catalog()
        self.assertIs(
            catalog.require(provider="python", component_id="ocean_waves", version=1),
            OceanWavesAnimation.component_descriptor(),
        )
        canonical = normalize_composer_scene({"origin": "composer", "scene": _scene()}, catalog)
        self.assertAlmostEqual(canonical.scene["animation"]["parameters"]["swell"], 0.68)
        invalid = _scene({"swell": 0.68, "brightness": 0.2})
        with self.assertRaises(SceneContractError):
            normalize_composer_scene({"origin": "composer", "scene": invalid}, catalog)
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        self.assertEqual(
            [choice["preset_id"] for choice in interface.composer_presets.choices("ocean_waves")],
            PRESET_IDS,
        )
        scene = interface.composer_presets.apply(_scene(), "surf")
        self.assertAlmostEqual(scene["animation"]["parameters"]["swell"], 0.88)
        preview = client.post(
            "/api/composer/preview",
            json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 1.0, "wall_time": "2026-11-07T12:00:00+00:00"}},
        )
        self.assertEqual(preview.status_code, 200)
        live = client.post(
            "/api/composer/scene",
            json={"origin": "composer", "scene": scene, "client_id": "ocean-waves", "client_sequence": 1},
        )
        self.assertEqual(live.status_code, 200)
        paths = sorted((ROOT / "animation/plugins/ocean_waves/presets").glob("*.json"))
        self.assertEqual([path.stem for path in paths], PRESET_IDS)
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertSetEqual(set(OceanWavesAnimation._normalized_parameters(payload["params"])), set(OceanWavesAnimation.DEFAULTS))

    def test_strict_live_manager_accepts_and_renders_ocean_waves(self) -> None:
        manager = AnimationManager(self.controller, auto_start=False)
        manager._launch_animation_loop = lambda: None
        component = ComponentRef(
            plugin_id="ocean_waves",
            provider=ComponentProvider.PYTHON,
            resolved_parameters=dict(OceanWavesAnimation.DEFAULTS),
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
