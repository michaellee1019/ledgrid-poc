"""Deterministic coverage for Plant Glow's opt-in background routing."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.conway_life import ConwayLifeAnimation
from animation.plugins.pinball import PinballAnimation
from animation.plugins.plant_glow import PlantGlowAnimation


class PlantAwarePlantGlowTests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(strips=8, leds_per_strip=40)
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        # Physical (strip=4, led=19) maps to child playfield (x=4, y=20).
        obstacle_index = 4 * 40 + 19
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        self.foliage_path.write_text(
            json.dumps({"covered_indices": [obstacle_index]}), encoding="utf-8"
        )
        self.globe_path.write_text(
            json.dumps({"globe_indices": [obstacle_index + 40], "region_count": 1}),
            encoding="utf-8",
        )
        self.mask_config = {
            "mask_path": str(self.foliage_path),
            "globe_mask_path": str(self.globe_path),
            "plant_mask_path": str(self.foliage_path),
            "plant_globe_mask_path": str(self.globe_path),
        }

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _animation(self, **config):
        return PlantGlowAnimation(
            self.controller,
            {
                **self.mask_config,
                "brightness": 1.0,
                "breath_depth": 0.0,
                "shimmer": 0.0,
                "background_seed": 317,
                **config,
            },
        )

    def test_omitted_and_false_preserve_current_visuals_for_every_background(self):
        for source in ("color", "conway", "pinball"):
            with self.subTest(source=source):
                ordinary = self._animation(background_source=source)
                disabled = self._animation(background_source=source, plant_aware=False)
                for frame_count, elapsed in enumerate((0.0, 0.02, 0.08)):
                    np.testing.assert_array_equal(
                        ordinary.generate_frame(elapsed, frame_count),
                        disabled.generate_frame(elapsed, frame_count),
                    )

    def test_aware_conway_routes_life_around_the_masks(self):
        animation = self._animation(
            background_source="conway", plant_aware=True, plant_clearance=0
        )
        animation.generate_frame(0.0, 0)
        background = animation._background_animation

        self.assertIsInstance(background, ConwayLifeAnimation)
        self.assertTrue(background.plant_aware_enabled())
        self.assertTrue(background._plant_blocked[20, 4])
        self.assertEqual(background.grid[20][4], 0)
        self.assertGreater(background.alive_cells, 0)
        self.assertTrue(animation.get_runtime_stats()["background_plant_routing"])

    def test_aware_pinball_uses_plants_as_scoring_deflectors(self):
        animation = self._animation(
            background_source="pinball", plant_aware=True, plant_clearance=0
        )
        animation.generate_frame(0.0, 0)
        background = animation._background_animation
        self.assertIsInstance(background, PinballAnimation)
        self.assertTrue(background._plant_clearance[20, 4])

        background.ball_x = 2.0
        background.ball_y = 20.0
        background.ball_vx = 30.0
        background.ball_vy = 0.0
        starting_score = background.score
        background._update(0.1)

        self.assertLess(background.ball_vx, 0.0)
        self.assertEqual(background._plant_hits, 1)
        self.assertGreater(background.score, starting_score)

    def test_live_opt_in_rebuilds_the_borrowed_world(self):
        animation = self._animation(background_source="pinball")
        animation.generate_frame(0.0, 0)
        ordinary = animation._background_animation

        animation.update_parameters({"plant_aware": True, "plant_clearance": 0})
        animation.generate_frame(0.1, 1)

        self.assertIsNot(animation._background_animation, ordinary)
        self.assertTrue(animation._background_animation.plant_aware_enabled())


if __name__ == "__main__":
    unittest.main()
