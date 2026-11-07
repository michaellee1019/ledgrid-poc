"""Focused Scene v2 contract tests for the Mini Metro animation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.presentation_contracts import ComponentProvider, ComponentRef, SceneState
from animation.plugins.mini_metro import MiniMetroAnimation
from ipc.scene_contract import SceneContractError, normalize_composer_scene
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_final_preview import current_component_catalog


ROOT = Path(__file__).resolve().parents[2]
PRESET_IDS = ["night", "quiet", "rush", "weekend"]


def _scene(parameters: dict | None = None) -> dict:
    scene = _current_scene()
    scene["animation"] = {
        "component_id": "mini_metro", "version": 1, "provider": "python", "role": "animation",
        "parameters": parameters or {},
    }
    return scene


class MiniMetroSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def test_descriptor_and_manifest_controls_are_current_only(self) -> None:
        descriptor = MiniMetroAnimation.component_descriptor()
        self.assertEqual(
            (descriptor.provider.value, descriptor.role.value, descriptor.timing_policy.value,
             descriptor.alpha_behavior.value, descriptor.palette_policy.value),
            ("python", "animation", "scaled_context", "opaque", "semantic"),
        )
        self.assertEqual(tuple(capability.value for capability in descriptor.plant_capabilities), ("effect_intent",))
        for legacy in ("speed_scale", "brightness", "plant_aware", "color", "role"):
            with self.assertRaisesRegex(ValueError, "non-local parameters"):
                MiniMetroAnimation(self.controller, {legacy: 1})

    def test_octilinear_lines_move_trains_and_cache_unchanged_ticks(self) -> None:
        parameters = {"seed": 77, "stations": 9, "lines": 3, "trains": 4, "traffic": 0.7, "train_speed": 1.1}
        left = MiniMetroAnimation(self.controller, parameters)
        right = MiniMetroAnimation(self.controller, parameters)
        for index in range(48):
            a, b = left.generate_frame(index / 24.0, index), right.generate_frame(index / 24.0, index)
            np.testing.assert_array_equal(a.pixels, b.pixels)
        self.assertEqual(left.generate_frame(47 / 24.0, 100).pixels.shape, (33 * 138, 3))
        self.assertFalse(left.generate_frame(47 / 24.0, 101).changed)
        for points in left.line_points:
            steps = np.diff(points.astype(np.int32), axis=0)
            self.assertTrue(np.all(np.abs(steps) <= 1))
            self.assertTrue(np.all(np.max(np.abs(steps), axis=1) == 1))
        self.assertNotEqual(
            left.semantic_snapshot()["trains"],
            MiniMetroAnimation(self.controller, parameters).semantic_snapshot()["trains"],
        )
        self.assertGreater(left._delivered + sum(len(train.cargo) for train in left.trains), 0)
        self.assertGreater(int(np.count_nonzero(np.max(left._pixels, axis=1) > 40)), 40)

    def test_rainbow_and_holiday_looks_are_presentation_only(self) -> None:
        ember = MiniMetroAnimation(self.controller, {"seed": 8080})
        for index in range(36):
            ember.generate_frame(index / 24.0, index)
        snapshot = dict(ember.semantic_snapshot())
        ember._paint("ember")
        frames = {"ember": ember._pixels.copy()}
        rng = ember._rng.bit_generator.state
        for palette_id in ("rainbow", "xmas", "fall", "easter", "july4"):
            city = MiniMetroAnimation(self.controller, {"seed": 8080})
            for index in range(36):
                city.generate_frame(index / 24.0, index)
            self.assertEqual(dict(city.semantic_snapshot()), snapshot)
            self.assertEqual(city._rng.bit_generator.state, rng)
            city._paint(palette_id)
            frames[palette_id] = city._pixels.copy()
        self.assertEqual(len({pixels.tobytes() for pixels in frames.values()}), len(frames))
        hues = [round(float(hue), 3) for hue in ember.line_hue]
        self.assertGreaterEqual(len(set(hues)), min(3, len(hues)))

    def test_catalog_preview_and_presets_keep_local_mini_metro(self) -> None:
        catalog = current_component_catalog()
        self.assertIs(
            catalog.require(provider="python", component_id="mini_metro", version=1),
            MiniMetroAnimation.component_descriptor(),
        )
        canonical = normalize_composer_scene({"origin": "composer", "scene": _scene()}, catalog)
        self.assertEqual(canonical.scene["animation"]["parameters"]["stations"], 9)
        invalid = _scene({"stations": 9, "brightness": 0.2})
        with self.assertRaises(SceneContractError):
            normalize_composer_scene({"origin": "composer", "scene": invalid}, catalog)
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        self.assertEqual(
            [choice["preset_id"] for choice in interface.composer_presets.choices("mini_metro")],
            PRESET_IDS,
        )
        scene = interface.composer_presets.apply(_scene(), "rush")
        self.assertEqual(scene["animation"]["parameters"]["trains"], 7)
        preview = client.post(
            "/api/composer/preview",
            json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 1.0, "wall_time": "2026-11-07T12:00:00+00:00"}},
        )
        self.assertEqual(preview.status_code, 200)
        live = client.post(
            "/api/composer/scene",
            json={"origin": "composer", "scene": scene, "client_id": "mini-metro", "client_sequence": 1},
        )
        self.assertEqual(live.status_code, 200)
        paths = sorted((ROOT / "animation/plugins/mini_metro/presets").glob("*.json"))
        self.assertEqual([path.stem for path in paths], PRESET_IDS)
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertSetEqual(set(MiniMetroAnimation._normalized_parameters(payload["params"])), set(MiniMetroAnimation.DEFAULTS))

    def test_strict_live_manager_accepts_and_renders_mini_metro(self) -> None:
        manager = AnimationManager(self.controller, auto_start=False)
        manager._launch_animation_loop = lambda: None
        component = ComponentRef(
            plugin_id="mini_metro",
            provider=ComponentProvider.PYTHON,
            resolved_parameters=dict(MiniMetroAnimation.DEFAULTS),
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
