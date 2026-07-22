"""Opt-in calibrated plant terrain tests for Pixel Quest."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.pixel_quest import PixelQuestAnimation


class PlantAwarePixelQuestTests(unittest.TestCase):
    WIDTH = 20
    HEIGHT = 40

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        self.foliage_path.write_text(json.dumps({"covered_indices": []}), encoding="utf-8")
        self.globe_path.write_text(
            json.dumps({"globe_indices": [], "region_count": 0}), encoding="utf-8"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_animation(self, **params):
        return PixelQuestAnimation(
            PreviewLEDController(self.WIDTH, self.HEIGHT),
            {
                "seed": 73,
                "plant_clearance": 0,
                "plant_mask_path": str(self.foliage_path),
                "plant_globe_mask_path": str(self.globe_path),
                **params,
            },
        )

    def canvas_indices(self, cells):
        # Physical flat index is x * height + led; canvas y is vertically flipped.
        return [x * self.HEIGHT + (self.HEIGHT - 1 - y) for x, y in cells]

    def write_masks(self, foliage=(), globes=()):
        self.foliage_path.write_text(
            json.dumps({"covered_indices": self.canvas_indices(foliage)}), encoding="utf-8"
        )
        self.globe_path.write_text(
            json.dumps({"globe_indices": self.canvas_indices(globes), "region_count": 1}),
            encoding="utf-8",
        )

    def test_disabled_mode_preserves_seeded_gameplay_and_frames(self):
        implicit = self.make_animation()
        explicit = self.make_animation(plant_aware=False)

        for elapsed in (0.0, 0.03, 0.08, 0.15, 0.4, 1.0, 2.6, 3.0):
            left = implicit.generate_frame(elapsed, 0).pixels.copy()
            right = explicit.generate_frame(elapsed, 0).pixels.copy()
            np.testing.assert_array_equal(left, right)
            self.assertEqual(implicit.logical_state(), explicit.logical_state())

    def test_enabled_route_moves_hero_sprite_away_from_foliage_lane(self):
        animation_y = self.make_animation()._hero_y()
        foliage = {
            (x, y)
            for x in range(7, 14)
            for y in range(animation_y - 4, animation_y + 5)
        }
        self.write_masks(foliage=foliage)
        animation = self.make_animation(plant_aware=True)

        routed_x = int(animation._nearest_clear_x(10, animation_y, 3, 4))
        _, _, blocked = animation._plant_canvas_layers()

        self.assertNotEqual(routed_x, 10)
        self.assertFalse(np.any(blocked[
            animation_y - 4:animation_y + 5,
            routed_x - 3:routed_x + 4,
        ]))
        animation.hero_x = 10
        self.assertEqual(animation._hero_screen_x(), routed_x)

    def test_enabled_render_exposes_landmarks_and_relocates_occluded_hud(self):
        foliage = {(x, y) for x in range(self.WIDTH) for y in range(9)}
        globe = {(10, 20)}
        self.write_masks(foliage=foliage, globes=globe)
        animation = self.make_animation(plant_aware=True)

        self.assertEqual(animation._least_occluded_hud_y(), 9)
        animation._render()

        np.testing.assert_array_equal(animation._canvas[0, 0], animation.PLANT_FOLIAGE)
        np.testing.assert_array_equal(animation._canvas[20, 10], animation.PLANT_GLOBE)
        self.assertTupleEqual(tuple(animation._canvas[10, 1]), animation.HEALTH)
        self.assertTrue(animation.get_runtime_stats()["plant_aware"])


if __name__ == "__main__":
    unittest.main()
