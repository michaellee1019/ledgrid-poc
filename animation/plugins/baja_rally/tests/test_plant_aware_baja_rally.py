"""Plant-mask semantics for Baja Rally."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.baja_rally import BajaRallyAnimation


class PlantAwareBajaRallyTests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(20, 48)

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
        globe_path.write_text(
            json.dumps({"globe_indices": list(globes), "region_count": 1}),
            encoding="utf-8",
        )
        return str(foliage_path), str(globe_path)

    def flat_index(self, x, y):
        # Canvas y grows downward from the top of the wall. LED 0 is the bottom.
        return x * 48 + (47 - y)

    def test_off_and_active_zero_have_exact_frame_and_state_parity(self):
        ordinary = BajaRallyAnimation(self.controller, {"seed": 71, "traffic": 0.2})
        zero = BajaRallyAnimation(self.controller, {
            "seed": 71,
            "traffic": 0.2,
            "plant_modifiers": self.modifiers("obstacle", "illuminate",
                                              obstacle=0.0, illuminate=0.0),
        })
        for frame, elapsed in enumerate(np.linspace(0.0, 2.0, 41)):
            left = ordinary.generate_frame(float(elapsed), frame)
            right = zero.generate_frame(float(elapsed), frame)
            np.testing.assert_array_equal(left.pixels, right.pixels)
        self.assertEqual(ordinary.logical_state(), zero.logical_state())

    def test_obstacle_bends_the_road_out_of_a_center_block(self):
        blocked = [
            self.flat_index(x, y)
            for x in range(6, 15)
            for y in range(48)
        ]
        foliage_path, globe_path = self.make_masks(foliage=blocked)
        config = {
            "seed": 4,
            "traffic": 0.0,
            "plant_clearance": 0,
            "plant_mask_path": foliage_path,
            "plant_globe_mask_path": globe_path,
        }
        clear = BajaRallyAnimation(self.controller, config)
        blocked_road = BajaRallyAnimation(self.controller, {
            **config,
            "plant_modifiers": self.modifiers("obstacle", obstacle=1.0),
        })
        clear.course.line[:] = 0.0
        blocked_road.course.line[:] = 0.0
        open_road = clear._road_slice()
        closed_road = blocked_road._road_slice()
        y = 40
        open_center = float(open_road.center[int(open_road.y_index[y])])
        closed_center = float(closed_road.center[int(closed_road.y_index[y])])
        self.assertGreater(open_center, 6.0)
        self.assertLess(open_center, 14.0)
        self.assertTrue(closed_center < 6.0 or closed_center > 14.0)

    def test_exact_core_damages_and_clearance_alone_does_not(self):
        foliage_path, globe_path = self.make_masks()
        animation = BajaRallyAnimation(self.controller, {
            "seed": 9,
            "traffic": 0.0,
            "plant_clearance": 1,
            "plant_mask_path": foliage_path,
            "plant_globe_mask_path": globe_path,
            "plant_modifiers": self.modifiers("obstacle", obstacle=1.0),
        })
        animation.hold = 0.0
        animation.hero.speed = 0.0
        road = animation._road_slice()
        box = animation._truck_box(animation.hero.progress, animation.hero.lane, road)
        self.assertIsNotNone(box)
        x, y, _width, _height = box
        animation._core_canvas[y, x] = True
        animation._fixed_step(animation.SIM_DT)
        self.assertGreater(animation.hero.damage, 0.0)

        missed = BajaRallyAnimation(self.controller, {
            "seed": 9,
            "traffic": 0.0,
            "plant_clearance": 1,
            "plant_mask_path": foliage_path,
            "plant_globe_mask_path": globe_path,
            "plant_modifiers": self.modifiers("obstacle", obstacle=1.0),
        })
        missed.hold = 0.0
        missed.hero.speed = 0.0
        missed._clearance_canvas[y, x] = True
        missed._fixed_step(missed.SIM_DT)
        self.assertEqual(missed.hero.damage, 0.0)

    def test_hazard_slows_without_moving_the_road(self):
        indices = [self.flat_index(x, y) for x in range(8, 12) for y in range(30, 44)]
        foliage_path, globe_path = self.make_masks(foliage=indices)
        plain = BajaRallyAnimation(self.controller, {
            "seed": 6, "traffic": 0.0, "plant_clearance": 0,
            "plant_mask_path": foliage_path, "plant_globe_mask_path": globe_path,
        })
        hazard = BajaRallyAnimation(self.controller, {
            "seed": 6, "traffic": 0.0, "plant_clearance": 0,
            "plant_mask_path": foliage_path, "plant_globe_mask_path": globe_path,
            "plant_modifiers": self.modifiers("hazard", hazard=1.0),
        })
        plain.course.line[:] = 0.0
        hazard.course.line[:] = 0.0
        plain_center = plain._road_slice().center
        hazard_center = hazard._road_slice().center
        np.testing.assert_allclose(plain_center, hazard_center)
        hazard.hold = 0.0
        box = hazard._truck_box(hazard.hero.progress, hazard.hero.lane, hazard._road_slice())
        x, y, width, height = box
        for yy in range(y - height, y + 1):
            for xx in range(x - width // 2, x - width // 2 + width):
                if 0 <= xx < hazard.width and 0 <= yy < hazard.height:
                    hazard._hazard_canvas[yy, xx] = True
        hazard.hero.speed = 22.0
        plain.hold = 0.0
        plain.hero.speed = 22.0
        hazard._fixed_step(hazard.SIM_DT)
        plain._fixed_step(plain.SIM_DT)
        self.assertGreater(hazard.hero.damage, 0.0)
        self.assertLess(hazard.hero.speed, plain.hero.speed)

    def test_illuminate_changes_pixels_without_changing_the_race(self):
        foliage_path, globe_path = self.make_masks(foliage=(self.flat_index(4, 20),), globes=(self.flat_index(8, 30),))
        plain = BajaRallyAnimation(self.controller, {
            "seed": 15, "traffic": 0.0,
            "plant_mask_path": foliage_path, "plant_globe_mask_path": globe_path,
        })
        lit = BajaRallyAnimation(self.controller, {
            "seed": 15, "traffic": 0.0,
            "plant_mask_path": foliage_path, "plant_globe_mask_path": globe_path,
            "plant_modifiers": self.modifiers("illuminate", illuminate=1.0),
        })
        for index in range(20):
            plain._fixed_step(plain.SIM_DT)
            lit._fixed_step(lit.SIM_DT)
        self.assertEqual(plain.logical_state(), lit.logical_state())
        plain._render()
        lit._render()
        self.assertFalse(np.array_equal(plain._canvas, lit._canvas))

    def test_live_modifier_change_preserves_progress_rng_and_time(self):
        foliage_path, globe_path = self.make_masks(foliage=(self.flat_index(4, 20),))
        animation = BajaRallyAnimation(self.controller, {
            "seed": 29, "traffic": 0.3,
            "plant_mask_path": foliage_path, "plant_globe_mask_path": globe_path,
        })
        animation.hold = 0.0
        for _ in range(20):
            animation._fixed_step(animation.SIM_DT)
        before = animation.logical_state()
        animation.update_parameters({
            "plant_modifiers": self.modifiers("illuminate", "obstacle", illuminate=0.7, obstacle=1.0),
        })
        self.assertEqual(before, animation.logical_state())

    def test_legacy_plant_aware_routes_like_an_obstacle(self):
        blocked = [self.flat_index(x, y) for x in range(6, 15) for y in range(48)]
        foliage_path, globe_path = self.make_masks(foliage=blocked)
        animation = BajaRallyAnimation(self.controller, {
            "seed": 4,
            "traffic": 0.0,
            "plant_clearance": 0,
            "plant_aware": True,
            "plant_mask_path": foliage_path,
            "plant_globe_mask_path": globe_path,
        })
        animation.course.line[:] = 0.0
        center = float(animation._road_slice().center[int(animation._road_slice().y_index[40])])
        self.assertTrue(center < 6.0 or center > 14.0)

    def test_real_wall_obstacle_does_not_instantly_retire_the_hero(self):
        animation = self.make_animation_wall(traffic=0.0, plant_modifiers=self.modifiers("obstacle", "illuminate", obstacle=1.0, illuminate=0.6))
        animation.hold = 0.0
        for _ in range(30 * 8):
            animation._fixed_step(animation.SIM_DT)
        self.assertGreater(animation.hero.career_distance, 80.0)
        self.assertLess(animation.hero.damage, 0.35)
        self.assertFalse(animation.hero.dnf)

    def test_missing_masks_fall_back_to_an_open_course(self):
        animation = BajaRallyAnimation(self.controller, {
            "plant_mask_path": "/definitely/missing/foliage.json",
            "plant_globe_mask_path": "/definitely/missing/globes.json",
            "plant_modifiers": self.modifiers(
                "illuminate", "obstacle", illuminate=1.0, obstacle=1.0,
            ),
        })
        self.assertFalse(np.any(animation._core_canvas))
        rendered = animation.generate_frame(0.0, 0)
        self.assertEqual(rendered.pixels.shape, (20 * 48, 3))

    def make_animation_wall(self, **config):
        return BajaRallyAnimation(PreviewLEDController(33, 138), {"seed": 21, **config})


if __name__ == "__main__":
    unittest.main()
