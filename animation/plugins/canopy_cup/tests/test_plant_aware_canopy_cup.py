"""Plant-mask semantics for Canopy Cup."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.canopy_cup import CanopyCupAnimation


class PlantAwareCanopyCupTests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(8, 40)

    @staticmethod
    def modifiers(*active, **strengths):
        return {"version": 1, "active": list(active), "strengths": strengths}

    def make_masks(self, *, foliage=(), globes=()):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        foliage_path = root / "foliage.json"
        globe_path = root / "globes.json"
        foliage_path.write_text(json.dumps({"covered_indices": list(foliage)}), encoding="utf-8")
        globe_path.write_text(json.dumps({"globe_indices": list(globes), "region_count": 1}), encoding="utf-8")
        return str(foliage_path), str(globe_path)

    def test_off_and_active_zero_have_exact_frame_and_state_parity(self):
        ordinary = CanopyCupAnimation(self.controller, {"seed": 71})
        zero = CanopyCupAnimation(self.controller, {
            "seed": 71,
            "plant_modifiers": self.modifiers("obstacle", "emitter", obstacle=0.0, emitter=0.0),
        })
        for frame, elapsed in enumerate(np.linspace(0.0, 4.0, 121)):
            left = ordinary.generate_frame(float(elapsed), frame)
            right = zero.generate_frame(float(elapsed), frame)
            np.testing.assert_array_equal(left.pixels, right.pixels)
        self.assertEqual(ordinary.logical_state(), zero.logical_state())

    def test_exact_core_collides_while_clearance_only_guides_planning(self):
        # Canvas (x=4, y=20) maps to physical strip 4, LED 19.
        foliage_path, globe_path = self.make_masks(foliage=(4 * 40 + 19,))
        animation = CanopyCupAnimation(self.controller, {
            "plant_mask_path": foliage_path,
            "plant_globe_mask_path": globe_path,
            "plant_clearance": 1,
            "plant_modifiers": self.modifiers("obstacle", obstacle=1.0),
        })
        self.assertEqual(animation._plant_overlap(4.0, 20.0), 1)
        self.assertEqual(animation._plant_overlap(2.0, 20.0), 0)
        self.assertTrue(animation._plant_clearance_canvas[20, 3])

    def test_emitter_spawns_from_plant_edge_on_semantic_tick(self):
        indices = [4 * 40 + led for led in (18, 19, 20)]
        foliage_path, globe_path = self.make_masks(foliage=indices)
        animation = CanopyCupAnimation(self.controller, {
            "seed": 7,
            "enemy_density": 0.0,
            "plant_mask_path": foliage_path,
            "plant_globe_mask_path": globe_path,
            "plant_modifiers": self.modifiers("emitter", emitter=1.0),
        })
        animation._start_heat(0)
        animation._procedural_emitters = []
        animation.next_enemy_time = 0.0
        animation._spawn_enemies_if_due()
        self.assertEqual(len(animation.enemies), 1)
        self.assertEqual(animation.plant_enemy_spawns, 1)

    def test_render_rate_does_not_multiply_emission(self):
        foliage_path, globe_path = self.make_masks(foliage=(4 * 40 + 19,))
        config = {
            "seed": 17,
            "plant_mask_path": foliage_path,
            "plant_globe_mask_path": globe_path,
            "plant_modifiers": self.modifiers("emitter", emitter=.8),
        }
        low = CanopyCupAnimation(self.controller, {**config, "render_fps": 30})
        high = CanopyCupAnimation(self.controller, {**config, "render_fps": 90})
        for index in range(401):
            elapsed = index / 100.0
            low.generate_frame(elapsed, index)
            high.generate_frame(elapsed, index)
        self.assertEqual(low.enemy_spawns, high.enemy_spawns)
        self.assertEqual(low.logical_state(), high.logical_state())

    def test_live_modifier_change_preserves_racer_state_rng_and_time(self):
        foliage_path, globe_path = self.make_masks(foliage=(4 * 40 + 19,))
        animation = CanopyCupAnimation(self.controller, {
            "seed": 29,
            "plant_mask_path": foliage_path,
            "plant_globe_mask_path": globe_path,
        })
        animation._start_heat(0)
        for _ in range(120):
            animation._fixed_step(animation.PHYSICS_DT)
        before_racers = [(r.x, r.y, r.vx, r.vy, r.power) for r in animation.racers]
        before_rng = animation.game_rng.getstate()
        before_time = (animation.simulation_time, animation.heat_time, animation.fixed_steps)
        animation.update_parameters({
            "plant_modifiers": self.modifiers("illuminate", "obstacle", illuminate=.7, obstacle=1.0)
        })
        self.assertEqual(before_racers, [(r.x, r.y, r.vx, r.vy, r.power) for r in animation.racers])
        self.assertEqual(before_rng, animation.game_rng.getstate())
        self.assertEqual(before_time, (animation.simulation_time, animation.heat_time, animation.fixed_steps))

    def test_missing_masks_fall_back_to_empty_geometry(self):
        animation = CanopyCupAnimation(self.controller, {
            "plant_mask_path": "/definitely/missing/foliage.json",
            "plant_globe_mask_path": "/definitely/missing/globes.json",
            "plant_modifiers": self.modifiers("illuminate", "obstacle", "emitter",
                                               illuminate=1.0, obstacle=1.0, emitter=1.0),
        })
        self.assertFalse(np.any(animation._plant_obstacle_canvas))
        self.assertFalse(np.any(animation._plant_edge_canvas))
        rendered = animation.generate_frame(0.0, 0)
        self.assertEqual(rendered.pixels.shape, (320, 3))


if __name__ == "__main__":
    unittest.main()
