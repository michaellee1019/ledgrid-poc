"""Rules and frame-contract tests for the autonomous Snake animation."""

from collections import deque
import unittest

import numpy as np

from animation.core.base import RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.snake import RIGHT, SnakeAnimation


class SnakeAnimationTests(unittest.TestCase):
    def make_animation(self, **params):
        return SnakeAnimation(
            PreviewLEDController(strips=12, leds_per_strip=24),
            {"seed": 42, "snake_count": 1, "wall_pattern": "none", **params},
        )

    def test_snake_is_registered(self):
        self.assertIn("snake", AnimationManager.ALLOWED_PLUGINS)

    def test_snake_speed_baseline_is_ten_times_the_configured_rate(self):
        animation = self.make_animation(speed=1.0, moves_per_second=11.0)

        self.assertEqual(animation._effective_moves_per_second(), 110.0)
        self.assertEqual(animation.get_runtime_stats()["speed_baseline"], 10.0)

    def test_frame_contract_and_source_rate_cache(self):
        animation = self.make_animation(render_fps=30.0)
        first = animation.generate_frame(0.0, 0)
        cached = animation.generate_frame(0.01, 1)
        later = animation.generate_frame(0.04, 2)

        self.assertIsInstance(first, RenderedFrame)
        self.assertEqual(first.pixels.shape, (288, 3))
        self.assertEqual(first.pixels.dtype, np.uint8)
        self.assertTrue(first.pixels.flags.c_contiguous)
        self.assertFalse(cached.changed)
        self.assertTrue(later.changed)

    def test_eating_food_grows_without_dropping_tail(self):
        animation = self.make_animation(ruleset="classic", growth_per_food=2)
        snake = animation.snakes[0]
        snake.body = deque([(5, 5), (4, 5), (3, 5)])
        snake.direction = RIGHT
        snake.target_length = 3
        animation.food = {(6, 5)}
        animation._choose_direction = lambda _snake, _occupied: RIGHT

        animation._step_game()

        self.assertEqual(snake.head, (6, 5))
        self.assertEqual(len(snake.body), 4)
        self.assertEqual(snake.target_length, 5)
        self.assertEqual(animation.food_eaten, 1)

    def test_classic_edge_collision_kills_and_schedules_respawn(self):
        animation = self.make_animation(ruleset="classic")
        snake = animation.snakes[0]
        snake.body = deque([(11, 5), (10, 5), (9, 5)])
        snake.direction = RIGHT
        snake.target_length = 3
        animation._choose_direction = lambda _snake, _occupied: RIGHT

        animation._step_game()

        self.assertFalse(snake.body)
        self.assertGreater(snake.respawn_ticks, 0)
        self.assertEqual(animation.deaths, 1)

    def test_wrap_rules_cross_the_board_edge(self):
        animation = self.make_animation(ruleset="wrap")
        self.assertEqual(animation._advance_cell((11, 7), RIGHT), (0, 7))

    def test_portals_relocate_a_head_and_preserve_snake_rules(self):
        animation = self.make_animation(ruleset="portal")
        entrance = next(iter(animation.portals))
        destination = animation.portals[entrance]
        approach = (entrance[0] - 1, entrance[1])
        if approach[0] < 0:
            approach = (entrance[0] + 1, entrance[1])
            direction = (-1, 0)
        else:
            direction = RIGHT
        self.assertEqual(animation._advance_cell(approach, direction), destination)

    def test_schema_exposes_rules_and_visual_varieties(self):
        schema = self.make_animation().get_parameter_schema()
        self.assertSetEqual(set(schema["ruleset"]["options"]), {"classic", "wrap", "portal", "battle"})
        self.assertIn("prism", schema["visual_style"]["options"])
        self.assertIn("zigzag", schema["wall_pattern"]["options"])


if __name__ == "__main__":
    unittest.main()
