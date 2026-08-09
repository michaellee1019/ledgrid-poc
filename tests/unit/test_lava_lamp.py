"""Behavior, cadence, mask, preset, and interaction tests for Lava Lamp."""

import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.base import RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.lava_lamp import LavaLampAnimation
from web.app import AnimationWebInterface
from web.local_control import LocalControlChannel


ROOT = Path(__file__).resolve().parents[2]


class LavaLampTests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(32, 138)

    @staticmethod
    def pixels(frame):
        return frame.pixels if isinstance(frame, RenderedFrame) else frame

    def test_frame_contract_and_100fps_cadence_at_200hz(self):
        animation = LavaLampAnimation(self.controller)
        frames = [animation.generate_frame(index / 200.0, index) for index in range(200)]
        changed = sum(frame.changed for frame in frames)
        self.assertEqual(changed, 100)
        pixels = frames[-1].pixels
        self.assertEqual(pixels.shape, (32 * 138, 3))
        self.assertEqual(pixels.dtype, np.uint8)
        self.assertTrue(pixels.flags.c_contiguous)

    def test_speed_scales_semantic_time_without_changing_render_cadence(self):
        results = []
        for speed in (0.1, 1.0, 4.0):
            animation = LavaLampAnimation(self.controller, {"speed": speed})
            changed = 0
            for index in range(200):
                changed += animation.generate_frame(index / 200.0, index).changed
            results.append((animation.simulation_time, changed))
        self.assertEqual([changed for _, changed in results], [100, 100, 100])
        self.assertAlmostEqual(results[1][0] / results[0][0], 10.0, delta=1.2)
        self.assertAlmostEqual(results[2][0] / results[1][0], 4.0, delta=0.25)

    def test_default_thermal_cycle_is_bounded_and_conserves_wax(self):
        animation = LavaLampAnimation(self.controller)
        initial = animation.wax_area
        for _ in range(12000):
            animation._step(0.01)
        stats = animation.get_runtime_stats()
        self.assertGreaterEqual(stats["midline_up_crossings"], 5)
        self.assertGreaterEqual(stats["midline_down_crossings"], 5)
        self.assertLessEqual(stats["blob_count"], animation.MAX_BLOBS)
        self.assertTrue(np.isfinite(animation.x[animation.active]).all())
        self.assertTrue(np.isfinite(animation.y[animation.active]).all())
        self.assertAlmostEqual(animation.wax_area, initial, places=3)

    def test_visual_axes_do_not_change_semantic_state(self):
        left = LavaLampAnimation(self.controller, {
            "seed": 55, "palette": "classic", "background": "black",
        })
        right = LavaLampAnimation(self.controller, {
            "seed": 55, "palette": "candy", "background": "ember",
        })
        for index in range(80):
            left.generate_frame(index / 100.0, index)
            right.generate_frame(index / 100.0, index)
        for name in ("x", "y", "vx", "vy", "radius", "temperature", "active"):
            np.testing.assert_array_equal(getattr(left, name), getattr(right, name))

    def test_zero_strength_modifier_has_exact_frame_state_and_rng_parity(self):
        base = LavaLampAnimation(self.controller, {"seed": 77})
        zero = LavaLampAnimation(self.controller, {
            "seed": 77,
            "plant_modifiers": {
                "version": 1, "active": ["refract"],
                "strengths": {"refract": 0.0},
            },
        })
        for index in range(30):
            expected = base.generate_frame(index / 100.0, index)
            actual = zero.generate_frame(index / 100.0, index)
            np.testing.assert_array_equal(expected.pixels, actual.pixels)
        for name in ("x", "y", "vx", "vy", "radius", "temperature", "active"):
            np.testing.assert_array_equal(getattr(base, name), getattr(zero, name))
        self.assertEqual(base.rng.bit_generator.state, zero.rng.bit_generator.state)

    def test_click_stirs_splits_and_conserves_wax(self):
        animation = LavaLampAnimation(self.controller, {"blob_count": 5})
        target = int(np.flatnonzero(animation.active)[0])
        before_velocity = (float(animation.vx[target]), float(animation.vy[target]))
        before_area = animation.wax_area
        self.assertTrue(animation.handle_interaction(
            "primary", float(animation.x[target]), float(animation.y[target]), 1.0
        ))
        animation._step(0.01)
        self.assertNotEqual(
            before_velocity, (float(animation.vx[target]), float(animation.vy[target]))
        )
        self.assertEqual(animation.get_runtime_stats()["interactions_applied"], 1)
        self.assertAlmostEqual(animation.wax_area, before_area, places=3)

    def test_declares_rich_mask_semantics_and_modifier_updates_preserve_state(self):
        expected = {
            "illuminate", "shadow", "refract", "attractor", "repulsor",
            "slow_zone", "obstacle", "bumper", "portal", "hazard",
            "habitat", "emitter",
        }
        self.assertSetEqual(set(LavaLampAnimation.PLANT_MODIFIER_SUPPORT), expected)
        animation = LavaLampAnimation(self.controller)
        before = (animation.x.copy(), animation.y.copy(), animation.rng.bit_generator.state)
        animation.update_parameters({
            "plant_modifiers": {
                "version": 1, "active": ["portal"], "strengths": {"portal": 1.0}
            }
        })
        np.testing.assert_array_equal(animation.x, before[0])
        np.testing.assert_array_equal(animation.y, before[1])
        self.assertEqual(animation.rng.bit_generator.state, before[2])

    def test_all_eighteen_presets_are_schema_valid_warmed_and_distinct(self):
        paths = sorted((ROOT / "animation/plugins/lava_lamp/presets").glob("*.json"))
        self.assertEqual(len(paths), 18)
        self.assertTrue({"quiet", "night", "showcase"}.issubset({path.stem for path in paths}))
        frames = []
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["preset_id"], path.stem)
            self.assertIs(payload["params"]["plant_aware"], True)
            animation = LavaLampAnimation(self.controller, payload["params"])
            schema = animation.get_parameter_schema()
            self.assertTrue(set(payload["params"]).issubset(schema))
            for index in range(51):
                rendered = animation.generate_frame(index / 100.0, index)
            frames.append(rendered.pixels.copy())
        self.assertEqual(len({frame.tobytes() for frame in frames}), 18)

    def test_manager_persists_and_isolates_interactive_preview(self):
        manager = AnimationManager(self.controller, auto_start=False)
        first = manager.get_animation_preview_with_params("lava_lamp", {"seed": 12})
        session = manager._preview_session
        second = manager.get_animation_preview_with_params("lava_lamp", {"seed": 12})
        self.assertIs(manager._preview_session, session)
        self.assertGreater(second["frame_count"], first["frame_count"])
        self.assertTrue(manager.dispatch_preview_interaction(
            "lava_lamp", "primary", 10.0, 80.0, 1.0, params={"seed": 12}
        ))
        self.assertEqual(len(session["animation"]._interactions), 1)
        manager.set_plant_modifiers({
            "version": 1, "active": ["refract"],
            "strengths": {"refract": 0.5},
        })
        self.assertIs(manager._preview_session, session)
        manager.get_animation_preview_with_params("lava_lamp", {"seed": 13})
        self.assertIsNot(manager._preview_session, session)
        expired = manager._preview_session
        expired["last_access"] -= 301.0
        manager.get_animation_preview_with_params("lava_lamp", {"seed": 13})
        self.assertIsNot(manager._preview_session, expired)

    def test_live_and_preview_http_interactions_are_routed_and_validated(self):
        manager = AnimationManager(self.controller, auto_start=False)
        interface = AnimationWebInterface(
            LocalControlChannel(manager), manager, local_mode=True
        )
        client = interface.app.test_client()

        preview = client.get("/api/preview/lava_lamp")
        self.assertEqual(preview.status_code, 200)
        response = client.post("/api/preview/lava_lamp/interaction", json={
            "kind": "primary", "x": 8.0, "y": 90.0, "strength": 0.75,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["accepted"])

        manager.current_animation = LavaLampAnimation(self.controller)
        manager.current_animation_name = "lava_lamp"
        response = client.post("/api/interaction", json={
            "kind": "primary", "x": 8.0, "y": 90.0, "strength": 1.0,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(manager.current_animation._interactions), 1)
        live_queue_size = len(manager.current_animation._interactions)
        response = client.post("/api/preview/lava_lamp/interaction", json={
            "kind": "primary", "x": 8.0, "y": 90.0, "strength": 1.0,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(manager.current_animation._interactions), live_queue_size)
        rejected = client.post("/api/preview/lava_lamp/interaction", json={
            "kind": "primary", "x": 99.0, "y": 90.0, "strength": 1.0,
        })
        self.assertEqual(rejected.status_code, 400)
        rejected = client.post("/api/interaction", json={
            "kind": "primary", "x": 8.0, "y": 138.0, "strength": 1.0,
        })
        self.assertEqual(rejected.status_code, 400)
        rejected = client.post("/api/interaction", json={
            "kind": "secondary", "x": 8.0, "y": 90.0, "strength": 1.0,
        })
        self.assertEqual(rejected.status_code, 400)


if __name__ == "__main__":
    unittest.main()
