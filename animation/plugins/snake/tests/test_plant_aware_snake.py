"""Opt-in calibrated plant terrain tests for Snake Garden."""

from collections import deque
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.snake import RIGHT, SnakeAnimation


class PlantAwareSnakeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        # Flat mask indices are strip * leds_per_strip + led, or x * 10 + y.
        self.foliage_path.write_text(json.dumps({"covered_indices": [34, 35]}))
        self.globe_path.write_text(json.dumps({"globe_indices": [36], "region_count": 1}))

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_animation(self, **params):
        config = {
            "seed": 41,
            "snake_count": 1,
            "initial_length": 3,
            "food_count": 6,
            "ruleset": "classic",
            "wall_pattern": "none",
            "plant_clearance": 0,
            "plant_mask_path": str(self.foliage_path),
            "plant_globe_mask_path": str(self.globe_path),
            **params,
        }
        return SnakeAnimation(PreviewLEDController(strips=8, leds_per_strip=10), config)

    def test_disabled_mode_preserves_seeded_world_and_frames(self):
        implicit = self.make_animation()
        explicit = self.make_animation(plant_aware=False)

        self.assertEqual([list(s.body) for s in implicit.snakes], [list(s.body) for s in explicit.snakes])
        self.assertEqual(implicit.food, explicit.food)
        for elapsed in (0.0, 0.05, 0.1):
            left = implicit.generate_frame(elapsed, 0).pixels.copy()
            right = explicit.generate_frame(elapsed, 0).pixels.copy()
            np.testing.assert_array_equal(left, right)
        self.assertEqual(implicit.moves, explicit.moves)
        self.assertEqual(implicit.food_eaten, explicit.food_eaten)

    def test_enabled_masks_constrain_spawning_food_and_steering(self):
        animation = self.make_animation(plant_aware=True)
        plant_cells = {(3, 4), (3, 5), (3, 6)}

        self.assertEqual(animation._plant_obstacles, plant_cells)
        self.assertTrue(animation._occupied().isdisjoint(plant_cells))
        self.assertTrue(animation.food.isdisjoint(plant_cells))

        snake = animation.snakes[0]
        snake.body = deque([(2, 4), (1, 4), (0, 4)])
        snake.direction = RIGHT
        snake.target_length = 3
        animation.food = {(4, 4)}

        direction = animation._choose_direction(snake, animation._occupied())
        self.assertNotEqual(direction, RIGHT)
        animation._step_game()
        self.assertNotIn(snake.head, plant_cells)

    def test_enabled_render_marks_foliage_and_globes_as_distinct_landmarks(self):
        animation = self.make_animation(plant_aware=True, background="void", trail_strength=0.0)
        animation.snakes.clear()
        animation.food.clear()
        animation._render(0.0)

        foliage = animation._canvas[4, 3]
        globe = animation._canvas[6, 3]
        self.assertGreater(int(foliage[1]), int(foliage[0]))
        self.assertGreater(int(globe[2]), int(globe[1]))
        self.assertFalse(np.array_equal(foliage, globe))


if __name__ == "__main__":
    unittest.main()
