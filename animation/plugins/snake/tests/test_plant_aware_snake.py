"""Opt-in calibrated plant terrain tests for Snake Garden."""

from collections import deque
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.core.plant_awareness import GLOBE_REGION_ORDER
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

    @staticmethod
    def modifier_state(modifier, strength=1.0):
        return {"version": 1, "active": [modifier], "strengths": {modifier: strength}}

    def install_seven_globes(self):
        indices = [x * 10 + 4 for x in range(7)]
        self.globe_path.write_text(json.dumps({
            "globe_indices": indices,
            "region_count": 7,
            "pixels": [
                {"index": index, "region": name}
                for index, name in zip(indices, GLOBE_REGION_ORDER)
            ],
        }))

    def test_disabled_mode_preserves_seeded_world_and_frames(self):
        implicit = self.make_animation()
        explicit = self.make_animation(
            plant_aware=False,
            plant_modifiers={"version": 1, "active": [], "strengths": {}},
        )

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

    def test_obstacle_uses_clearance_for_planning_but_exact_core_for_contact(self):
        animation = self.make_animation(
            plant_modifiers=self.modifier_state("obstacle"), plant_clearance=1
        )

        self.assertTrue(animation._terrain_blocked((2, 4), planning=True))
        self.assertFalse(animation._terrain_blocked((2, 4), planning=False))
        self.assertTrue(animation._terrain_blocked((3, 4), planning=False))

        exact_only = self.make_animation(
            plant_modifiers=self.modifier_state("obstacle", 0.0), plant_clearance=1
        )
        self.assertFalse(exact_only._terrain_blocked((2, 4), planning=True))
        self.assertTrue(exact_only._terrain_blocked((3, 4), planning=True))

    def test_live_modifier_change_invalidates_geometry_without_advancing_state_or_rng(self):
        animation = self.make_animation()
        before = (
            animation.random.getstate(),
            [list(snake.body) for snake in animation.snakes],
            set(animation.food),
            animation.moves,
        )

        animation.update_parameters({
            "plant_modifiers": self.modifier_state("obstacle", 0.6)
        })

        after = (
            animation.random.getstate(),
            [list(snake.body) for snake in animation.snakes],
            set(animation.food),
            animation.moves,
        )
        self.assertEqual(after, before)
        self.assertTrue(animation._plant_obstacles)

    def test_portal_cycles_regions_preserves_direction_and_recovers_cooldown(self):
        self.install_seven_globes()
        animation = self.make_animation(
            plant_modifiers=self.modifier_state("portal", 0.75)
        )
        snake = animation.snakes[0]
        snake.body = deque([(7, 4), (7, 5), (7, 6)])
        snake.direction = RIGHT
        snake.target_length = 3

        for index, source in enumerate(GLOBE_REGION_ORDER):
            destination, target = animation._portal_destination((index, 4))
            self.assertEqual(target, GLOBE_REGION_ORDER[(index + 1) % 7])
            self.assertEqual(destination, ((index + 1) % 7, 4))

        snake.body = deque([(7, 4), (7, 5), (7, 6)])
        snake.portal_exit_region = None
        destination, target = animation._portal_destination((0, 4), snake)
        self.assertEqual((destination, target), ((1, 4), "top_right"))
        snake.portal_exit_region = target
        snake.portal_cooldown_ticks = 1
        self.assertEqual(animation._portal_destination((1, 4), snake), ((1, 4), None))
        self.assertEqual(snake.direction, RIGHT)

    def test_hazard_contact_deterministically_kills_and_counts(self):
        animation = self.make_animation(
            plant_modifiers=self.modifier_state("hazard", 1.0)
        )
        snake = animation.snakes[0]
        snake.body = deque([(2, 4), (1, 4), (0, 4)])
        snake.direction = RIGHT
        snake.target_length = 3
        animation.food.clear()
        animation._choose_direction = lambda _snake, _occupied: RIGHT

        animation._step_game()

        self.assertFalse(snake.body)
        self.assertEqual(animation.plant_hazard_deaths, 1)
        self.assertEqual(animation.plant_contacts, 1)
        self.assertGreaterEqual(snake.respawn_ticks, 14)

    def test_seeded_obstacle_run_never_places_snakes_or_food_in_clearance(self):
        animation = self.make_animation(
            ruleset="wrap", plant_clearance=1,
            plant_modifiers=self.modifier_state("obstacle"),
        )
        for _ in range(200):
            animation._step_game()
            self.assertTrue(animation._occupied().isdisjoint(animation._plant_obstacles))
            self.assertTrue(animation.food.isdisjoint(animation._plant_clearance))
        self.assertEqual(animation.moves, 200)


if __name__ == "__main__":
    unittest.main()
