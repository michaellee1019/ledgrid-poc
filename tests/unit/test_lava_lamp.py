"""Focused Scene v2, thermal-physics, and interaction proof for Lava Lamp."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.lava_lamp import LavaLampAnimation
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_final_preview import current_component_catalog
from web.local_control import LocalControlChannel

ROOT = Path(__file__).resolve().parents[2]
PRESET_IDS = ["bowl-bumpers", "bowl-emitter", "busy-bubbles", "classic-amber", "cotton-candy", "foliage-refraction", "habitat-pools", "night", "ocean-blue", "quiet", "ruby-vintage", "seven-bowl-portals", "showcase", "slow-giants", "solar-flare", "stormy-wax", "toxic-lime", "violet-glass"]


class LavaLampSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    @staticmethod
    def _scene() -> dict:
        scene = _current_scene()
        scene["animation"] = {"component_id": "lava_lamp", "version": 1, "provider": "python", "role": "animation", "parameters": {}}
        return scene

    def test_descriptor_and_parameters_are_opaque_semantic_and_local(self) -> None:
        descriptor = LavaLampAnimation.component_descriptor()
        self.assertEqual((descriptor.component_id, descriptor.role.value, descriptor.alpha_behavior.value, descriptor.palette_policy.value), ("lava_lamp", "animation", "opaque", "semantic"))
        self.assertSetEqual(set(LavaLampAnimation.PLANT_MODIFIER_SUPPORT), set())
        self.assertNotIn("brightness", LavaLampAnimation.DEFAULTS)
        with self.assertRaisesRegex(ValueError, "non-local parameters"):
            LavaLampAnimation._normalized_parameters({"brightness": .5})

    def test_bounded_thermal_cycle_and_stir_keep_wax_area_stable(self) -> None:
        lamp = LavaLampAnimation(self.controller, {"blob_count": 5, "seed": 77})
        initial_area = lamp.wax_area
        target = int(np.flatnonzero(lamp.active)[0])
        velocity = (float(lamp.vx[target]), float(lamp.vy[target]))
        self.assertTrue(lamp.handle_interaction("primary", float(lamp.x[target]), float(lamp.y[target]), 1.0))
        self.assertFalse(lamp.handle_interaction("secondary", 8.0, 80.0, 1.0))
        for _ in range(900): lamp._step(.01)
        stats = lamp.get_runtime_stats()
        self.assertNotEqual(velocity, (float(lamp.vx[target]), float(lamp.vy[target])))
        self.assertGreaterEqual(stats["interactions_applied"], 1)
        self.assertLessEqual(stats["blob_count"], lamp.MAX_BLOBS)
        self.assertAlmostEqual(lamp.wax_area, initial_area, places=3)

    def test_scene_timing_and_palette_are_deterministic_at_33_by_138(self) -> None:
        left = LavaLampAnimation(self.controller, {"seed": 314, "heat": .85})
        right = LavaLampAnimation(self.controller, {"seed": 314, "heat": .85})
        left_frames = [left.generate_frame(time, index).pixels.copy() for index, time in enumerate((0., .01, .5, 1., 3.))]
        right_frames = [right.generate_frame(time, index).pixels.copy() for index, time in enumerate((0., .01, .5, 1., 3.))]
        for expected, actual in zip(left_frames, right_frames): np.testing.assert_array_equal(expected, actual)
        self.assertEqual(left_frames[-1].shape, (33 * 138, 3))
        self.assertLessEqual(left.cadence_snapshot()["simulation_hz"], 100.0)

    def test_all_eighteen_presets_are_tracked_local_and_visually_distinct(self) -> None:
        paths = sorted((ROOT / "animation/plugins/lava_lamp/presets").glob("*.json"))
        self.assertEqual([path.stem for path in paths], PRESET_IDS)
        frames = []
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            parameters = LavaLampAnimation._normalized_parameters(payload["params"])
            self.assertSetEqual(set(parameters), set(LavaLampAnimation.DEFAULTS))
            lamp = LavaLampAnimation(self.controller, parameters)
            for index in range(51): rendered = lamp.generate_frame(index / 100.0, index)
            frames.append(rendered.pixels.copy())
        self.assertEqual(len({frame.tobytes() for frame in frames}), 18)

    def test_catalog_preview_live_and_recovery_keep_the_same_lamp_scene(self) -> None:
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        self.assertEqual([choice["preset_id"] for choice in interface.composer_presets.choices("lava_lamp")], PRESET_IDS)
        scene = interface.composer_presets.apply(self._scene(), "stormy-wax")
        self.assertNotIn("brightness", scene["animation"]["parameters"])
        self.assertEqual(client.get("/api/composer/components/lava_lamp/presets").status_code, 200)
        self.assertEqual(client.post("/api/composer/preview", json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-08-31T12:00:00+00:00"}}).status_code, 200)
        self.assertEqual(client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "lava", "client_sequence": 1}).get_json()["state"], "live")
        self.assertEqual(client.post("/api/composer/stop", json={"client_id": "lava"}).get_json()["status"]["state"], "stopped")
        edited = copy.deepcopy(scene); edited["animation"]["parameters"]["heat"] = .33
        self.assertFalse(client.post("/api/composer/scene", json={"origin": "composer", "scene": edited, "client_id": "lava", "client_sequence": 2}).get_json()["published"])
        self.assertEqual(client.get("/api/composer/recovery?client_id=lava").get_json()["recovery"]["scene"]["animation"]["parameters"]["heat"], .33)
        self.assertIs(current_component_catalog().require(provider="python", component_id="lava_lamp", version=1), LavaLampAnimation.component_descriptor())

    def test_control_remix_preserves_hidden_parameters_across_preview_live_and_recovery(self) -> None:
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        scene = interface.composer_presets.apply(self._scene(), "stormy-wax")
        scene["animation"]["parameters"].update({"interaction_radius": 16.0, "interaction_strength": 2.0})
        preset = next(choice for choice in interface.composer_presets.choices("lava_lamp") if choice["preset_id"] == "quiet")
        self.assertEqual(preset["parameters"]["interaction_radius"], 8.0)
        self.assertEqual(preset["parameters"]["interaction_strength"], 1.0)
        selected = copy.deepcopy(scene)
        selected["animation"]["parameters"] = {
            **preset["parameters"],
            **{name: scene["animation"]["parameters"][name] for name in ("interaction_radius", "interaction_strength")},
        }
        self.assertEqual(selected["animation"]["parameters"]["heat"], preset["parameters"]["heat"])
        self.assertEqual(selected["animation"]["parameters"]["interaction_radius"], 16.0)
        self.assertEqual(selected["animation"]["parameters"]["interaction_strength"], 2.0)
        first_preview = client.post("/api/composer/preview", json={"origin": "composer", "scene": selected, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-08-31T12:00:00+00:00"}}).get_json()
        first_live = client.post("/api/composer/scene", json={"origin": "composer", "scene": selected, "client_id": "lava-remix", "client_sequence": 1}).get_json()
        remix = copy.deepcopy(selected); remix["animation"]["parameters"] = {**remix["animation"]["parameters"], "heat": .41}
        remixed_preview = client.post("/api/composer/preview", json={"origin": "composer", "scene": remix, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-08-31T12:00:00+00:00"}}).get_json()
        remixed_live = client.post("/api/composer/scene", json={"origin": "composer", "scene": remix, "client_id": "lava-remix", "client_sequence": 2}).get_json()
        recovered = client.get("/api/composer/recovery?client_id=lava-remix").get_json()["recovery"]["scene"]
        self.assertEqual(first_preview["basis"]["digest"], first_live["current"]["digest"])
        self.assertEqual(first_live["current"]["digest"], first_live["desired"]["digest"])
        self.assertEqual(first_live["current"]["digest"], first_live["observed"]["digest"])
        self.assertEqual(remixed_preview["basis"]["digest"], remixed_live["current"]["digest"])
        self.assertEqual(remixed_live["current"]["digest"], remixed_live["desired"]["digest"])
        self.assertEqual(remixed_live["current"]["digest"], remixed_live["observed"]["digest"])
        self.assertEqual(recovered["animation"]["parameters"]["heat"], .41)
        self.assertEqual(recovered["animation"]["parameters"]["interaction_radius"], 16.0)
        self.assertEqual(recovered["animation"]["parameters"]["interaction_strength"], 2.0)

    def test_existing_safe_primary_interaction_seam_stirs_only_its_preview(self) -> None:
        manager = AnimationManager(self.controller, auto_start=False)
        client = AnimationWebInterface(LocalControlChannel(manager), manager, local_mode=True).app.test_client()
        self.assertEqual(client.get("/api/preview/lava_lamp").status_code, 200)
        self.assertEqual(client.post("/api/preview/lava_lamp/interaction", json={"kind": "primary", "x": 8.0, "y": 90.0, "strength": .75}).status_code, 200)
        manager.current_animation = LavaLampAnimation(self.controller); manager.current_animation_name = "lava_lamp"
        self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 8.0, "y": 90.0, "strength": 1.0}).status_code, 200)
        self.assertEqual(client.post("/api/interaction", json={"kind": "secondary", "x": 8.0, "y": 90.0, "strength": 1.0}).status_code, 400)
        self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 32.999, "y": 137.999, "strength": 1.0}).status_code, 200)
        self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 33.0, "y": 20.0, "strength": 1.0}).status_code, 400)
        self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 8.0, "y": 138.0, "strength": 1.0}).status_code, 400)
        self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 8.0, "y": 90.0, "strength": 1.01}).status_code, 400)

    def test_composer_canvas_uses_only_bounded_primary_interaction_for_live_instruments(self) -> None:
        script = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn("const lavaParameters = (existing = {}) => ({...existing", script)
        self.assertIn("lavaParameters(next.animation.parameters)", script)
        self.assertIn("const lavaInteractionParameters = (parameters = {})", script)
        self.assertIn("next.animation.parameters = {...preset.parameters, ...lavaInteractionParameters(next.animation.parameters)}", script)
        start = script.index("const primaryInstruments = Object.freeze")
        end = script.index("function renderLibrary()", start)
        interaction = script[start:end]
        for token in ("lava_lamp:", "flame_burst:", "fluid_tank:", "componentId", "!trigger", "event.button !== 0", "event.isPrimary === false", "status?.running", "status?.armed", "Math.min(32.999", "Math.min(137.999", "kind: 'primary'", "strength: 1", "body.accepted !== true", "fetch('/api/interaction'"):
            self.assertIn(token, interaction)
        self.assertNotIn("/api/preview/lava_lamp/interaction", interaction)


if __name__ == "__main__":
    unittest.main()
