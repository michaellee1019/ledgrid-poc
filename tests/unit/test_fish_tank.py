"""Focused Scene v2 contract tests for the Fish Tank animation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.presentation_contracts import ComponentProvider, ComponentRef, SceneState
from animation.plugins.fish_tank import FishTankAnimation
from ipc.scene_contract import SceneContractError, normalize_composer_scene
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_final_preview import current_component_catalog


ROOT = Path(__file__).resolve().parents[2]
PRESET_IDS = ["night", "quiet", "school", "showcase"]


def _scene(parameters: dict | None = None) -> dict:
    scene = _current_scene()
    scene["animation"] = {
        "component_id": "fish_tank", "version": 1, "provider": "python", "role": "animation",
        "parameters": parameters or {},
    }
    return scene


class FishTankSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def test_descriptor_and_manifest_controls_are_current_only(self) -> None:
        descriptor = FishTankAnimation.component_descriptor()
        self.assertEqual(
            (descriptor.provider.value, descriptor.role.value, descriptor.timing_policy.value,
             descriptor.alpha_behavior.value, descriptor.palette_policy.value),
            ("python", "animation", "scaled_context", "opaque", "semantic"),
        )
        self.assertEqual(tuple(capability.value for capability in descriptor.plant_capabilities), ("effect_intent",))
        for legacy in ("speed", "brightness", "plant_aware", "color", "role"):
            with self.assertRaisesRegex(ValueError, "non-local parameters"):
                FishTankAnimation(self.controller, {legacy: 1})

    def test_deterministic_opaque_tank_keeps_fish_in_the_water(self) -> None:
        parameters = {"seed": 77, "fish_count": 8, "swim_speed": 1.1, "schooling": 0.7, "bubbles": 0.6, "weeds": 0.5}
        left = FishTankAnimation(self.controller, parameters)
        right = FishTankAnimation(self.controller, parameters)
        for index in range(48):
            a, b = left.generate_frame(index / 24.0, index), right.generate_frame(index / 24.0, index)
            np.testing.assert_array_equal(a.pixels, b.pixels)
        self.assertEqual(left.generate_frame(47 / 24.0, 100).pixels.shape, (33 * 138, 3))
        self.assertFalse(left.generate_frame(47 / 24.0, 101).changed)
        self.assertGreaterEqual(float(np.min(left.y)), left._water_top())
        self.assertLessEqual(float(np.max(left.y)), left._water_bottom())
        self.assertNotEqual(
            left.semantic_snapshot()["x"],
            FishTankAnimation(self.controller, parameters).semantic_snapshot()["x"],
        )

    def test_rainbow_is_presentation_only_and_gives_each_fish_its_own_color(self) -> None:
        ember = FishTankAnimation(self.controller, {"seed": 4240, "fish_count": 8})
        rainbow = FishTankAnimation(self.controller, {"seed": 4240, "fish_count": 8})
        ember._paint("ember", 1.0)
        snapshot = dict(ember.semantic_snapshot())
        rng = ember._rng.bit_generator.state
        rainbow._paint("rainbow", 1.0)
        self.assertEqual(dict(rainbow.semantic_snapshot()), snapshot)
        self.assertEqual(rainbow._rng.bit_generator.state, rng)
        self.assertFalse(np.array_equal(ember._pixels, rainbow._pixels))
        hues = [round(float(hue), 3) for hue in rainbow.hue]
        self.assertGreaterEqual(len(set(hues)), 3)
        self.assertGreater(int(np.count_nonzero(np.max(rainbow._pixels, axis=1) > 40)), 20)

    def test_holiday_palettes_recolor_fish_without_moving_them(self) -> None:
        ember = FishTankAnimation(self.controller, {"seed": 4240, "fish_count": 8})
        ember._paint("ember", 1.0)
        snapshot = dict(ember.semantic_snapshot())
        rng = ember._rng.bit_generator.state
        frames = {"ember": ember._pixels.copy()}
        for palette_id in ("xmas", "fall", "easter", "july4"):
            tank = FishTankAnimation(self.controller, {"seed": 4240, "fish_count": 8})
            tank._paint(palette_id, 1.0)
            self.assertEqual(dict(tank.semantic_snapshot()), snapshot)
            self.assertEqual(tank._rng.bit_generator.state, rng)
            frames[palette_id] = tank._pixels.copy()
        self.assertEqual(len({pixels.tobytes() for pixels in frames.values()}), len(frames))

    def test_catalog_preview_and_presets_keep_a_local_fish_tank(self) -> None:
        catalog = current_component_catalog()
        self.assertIs(
            catalog.require(provider="python", component_id="fish_tank", version=1),
            FishTankAnimation.component_descriptor(),
        )
        canonical = normalize_composer_scene({"origin": "composer", "scene": _scene()}, catalog)
        self.assertEqual(canonical.scene["animation"]["parameters"]["fish_count"], 8)
        invalid = _scene({"fish_count": 8, "brightness": 0.2})
        with self.assertRaises(SceneContractError):
            normalize_composer_scene({"origin": "composer", "scene": invalid}, catalog)
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        self.assertEqual(
            [choice["preset_id"] for choice in interface.composer_presets.choices("fish_tank")],
            PRESET_IDS,
        )
        scene = interface.composer_presets.apply(_scene(), "school")
        self.assertEqual(scene["animation"]["parameters"]["fish_count"], 12)
        preview = client.post(
            "/api/composer/preview",
            json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 1.0, "wall_time": "2026-11-07T12:00:00+00:00"}},
        )
        self.assertEqual(preview.status_code, 200)
        live = client.post(
            "/api/composer/scene",
            json={"origin": "composer", "scene": scene, "client_id": "fish-tank", "client_sequence": 1},
        )
        self.assertEqual(live.status_code, 200)
        paths = sorted((ROOT / "animation/plugins/fish_tank/presets").glob("*.json"))
        self.assertEqual([path.stem for path in paths], PRESET_IDS)
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertSetEqual(set(FishTankAnimation._normalized_parameters(payload["params"])), set(FishTankAnimation.DEFAULTS))

    def test_strict_live_manager_accepts_and_renders_fish_tank(self) -> None:
        manager = AnimationManager(self.controller, auto_start=False)
        manager._launch_animation_loop = lambda: None
        component = ComponentRef(
            plugin_id="fish_tank",
            provider=ComponentProvider.PYTHON,
            resolved_parameters=dict(FishTankAnimation.DEFAULTS),
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
