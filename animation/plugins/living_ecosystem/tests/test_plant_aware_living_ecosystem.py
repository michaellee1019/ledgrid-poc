"""Opt-in calibrated plant habitat tests for Living Ecosystem."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.living_ecosystem import LivingEcosystemAnimation


class PlantAwareLivingEcosystemTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        # Flat mask coordinates are x * height + physical_y.  Foliage makes a
        # vertical habitat barrier; the globe is a separate watering landmark.
        self.foliage_path.write_text(
            json.dumps({"covered_indices": list(range(30, 40))}), encoding="utf-8"
        )
        self.globe_path.write_text(
            json.dumps({"globe_indices": [56], "region_count": 1}), encoding="utf-8"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_animation(self, **params):
        return LivingEcosystemAnimation(
            PreviewLEDController(strips=8, leds_per_strip=10),
            {
                "seed": 81,
                "plant_clearance": 0,
                "plant_mask_path": str(self.foliage_path),
                "plant_globe_mask_path": str(self.globe_path),
                **params,
            },
        )

    @staticmethod
    def logical_state(animation):
        return (
            animation.grass.copy(),
            [(c.species, c.pack, c.x, c.y, c.vx, c.vy, c.energy) for c in animation.creatures],
            [(tree.x, tree.y, tree.age) for tree in animation.trees],
            animation.get_runtime_stats()["births"],
            animation.get_runtime_stats()["kills"],
        )

    def test_disabled_mode_is_deterministically_identical(self):
        implicit = self.make_animation()
        explicit = self.make_animation(plant_aware=False)

        for elapsed in (0.0, 0.25, 1.0, 2.0):
            left = implicit.generate_frame(elapsed, 0).pixels.copy()
            right = explicit.generate_frame(elapsed, 0).pixels.copy()
            np.testing.assert_array_equal(left, right)

        left_state = self.logical_state(implicit)
        right_state = self.logical_state(explicit)
        np.testing.assert_array_equal(left_state[0], right_state[0])
        self.assertEqual(left_state[1:], right_state[1:])

    def test_enabled_habitat_constrains_spawning_and_movement(self):
        animation = self.make_animation(plant_aware=True)

        self.assertTrue(all(not animation._plant_blocked(c.x, c.y) for c in animation.creatures))
        self.assertTrue(all(not animation._plant_blocked(t.x, t.y) for t in animation.trees))

        creature = animation.creatures[0]
        creature.x, creature.y = 2.6, 4.0
        creature.vx, creature.vy = 3.0, 0.0
        animation._integrate(creature, 0.0, 0.0, 0.5, base_speed=3.0)
        self.assertFalse(animation._plant_blocked(creature.x, creature.y))
        self.assertLess(creature.x, 3.0)

        for _ in range(40):
            animation._simulate(0.125)
        self.assertTrue(all(not animation._plant_blocked(c.x, c.y) for c in animation.creatures))

    def test_enabled_render_marks_foliage_and_globes_as_distinct_landmarks(self):
        animation = self.make_animation(plant_aware=True, glow_strength=0.0)
        animation.trees.clear()
        animation.creatures.clear()
        animation._render(45.0)

        # Physical y=5 maps to canvas y=4; physical y=6 maps to canvas y=3.
        foliage = animation._canvas[4, 3]
        globe = animation._canvas[3, 5]
        self.assertGreaterEqual(int(foliage[1]), 72)
        self.assertGreaterEqual(int(globe[2]), 112)
        self.assertFalse(np.array_equal(foliage, globe))
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["plant_foliage_pixels"], 10)
        self.assertEqual(stats["plant_globe_pixels"], 1)


if __name__ == "__main__":
    unittest.main()
