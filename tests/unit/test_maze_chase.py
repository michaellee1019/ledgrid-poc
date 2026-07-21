"""Tests for the rules-driven Neon Maze Chase animation."""

import unittest
from collections import deque

import numpy as np

from animation import RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.maze_chase import LEFT, MAZE_WIDTH, MazeChaseAnimation


class MazeChaseAnimationTests(unittest.TestCase):
    def make_animation(self, strips=32, leds=140):
        return MazeChaseAnimation(PreviewLEDController(strips, leds), {"seed": 7})

    def test_production_manager_allows_plugin(self):
        self.assertIn("maze_chase", AnimationManager.ALLOWED_PLUGINS)

    def test_default_playback_speed_is_one_and_a_half_times(self):
        animation = self.make_animation()
        self.assertEqual(animation.params["speed"], 1.5)
        self.assertEqual(animation.get_parameter_schema()["speed"]["default"], 1.5)

    def test_maze_is_connected_including_wrap_tunnel(self):
        animation = self.make_animation()
        start = next(iter(animation.walkable))
        queue = deque([start])
        seen = {start}
        while queue:
            cell = queue.popleft()
            for direction in animation._available(cell):
                neighbor = animation._neighbor(cell, direction)
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        self.assertEqual(seen, animation.walkable)
        self.assertEqual(animation._neighbor((11, 0), LEFT), (11, MAZE_WIDTH - 1))
        self.assertIn(animation.PLAYER_SPAWN, animation.walkable)
        self.assertTrue(all(spawn in animation.walkable for spawn in animation.GHOST_SPAWNS))

    def test_renders_canonical_frames_on_supported_layouts(self):
        for strips, leds in ((32, 140), (16, 140), (12, 40)):
            with self.subTest(strips=strips, leds=leds):
                animation = self.make_animation(strips, leds)
                result = animation.generate_frame(0.0, 0)
                self.assertIsInstance(result, RenderedFrame)
                self.assertEqual(result.pixels.shape, (strips * leds, 3))
                self.assertEqual(result.pixels.dtype, np.uint8)
                self.assertGreater(np.count_nonzero(result.pixels), 100)

    def test_render_cap_reuses_unchanged_frame(self):
        animation = self.make_animation()
        first = animation.generate_frame(0.0, 0)
        skipped = animation.generate_frame(0.001, 1)
        advanced = animation.generate_frame(0.02, 2)
        self.assertTrue(first.changed)
        self.assertFalse(skipped.changed)
        self.assertIs(first.pixels, skipped.pixels)
        self.assertTrue(advanced.changed)

    def test_personalities_have_distinct_chase_targets(self):
        animation = self.make_animation()
        animation.mode = "chase"
        animation.player.row, animation.player.col = (17, 5)
        animation.player.direction = LEFT
        targets = [animation._ghost_target(ghost, index)
                   for index, ghost in enumerate(animation.ghosts)]
        self.assertEqual(targets[0], animation.player.cell)
        self.assertEqual(targets[1], (17, 1))
        self.assertEqual(targets[2], (23, -1))
        self.assertNotEqual(targets[3], targets[1])

    def test_energizer_and_ghost_chain_scoring(self):
        animation = self.make_animation()
        energizer = next(iter(animation.energizers))
        animation.player.row, animation.player.col = energizer
        animation._consume_player_cell()
        self.assertEqual(animation.score, 50)
        self.assertGreater(animation.frightened_timer, 0.0)
        self.assertTrue(all(ghost.state == "frightened" for ghost in animation.ghosts))

        ghost = animation.ghosts[0]
        ghost.row, ghost.col = animation.player.cell
        ghost.progress = animation.player.progress = 0.0
        animation._check_collisions()
        self.assertEqual(ghost.state, "eyes")
        self.assertEqual(animation.score, 250)
        self.assertEqual(animation.ghost_chain, 1)

        second = animation.ghosts[1]
        second.row, second.col = animation.player.cell
        second.progress = 0.0
        animation._check_collisions()
        self.assertEqual(animation.score, 650)
        self.assertEqual(animation.ghost_chain, 2)

    def test_mid_corridor_reversal_preserves_the_same_edge(self):
        animation = self.make_animation()
        ghost = animation.ghosts[0]
        ghost.row, ghost.col = (11, 7)
        ghost.direction = LEFT
        ghost.progress = 0.25
        before = animation._actor_position(ghost)
        animation._reverse_actor(ghost)
        after = animation._actor_position(ghost)
        self.assertEqual(ghost.cell, (11, 6))
        self.assertAlmostEqual(ghost.progress, 0.75)
        self.assertEqual(before, after)

    def test_normal_collision_runs_death_and_life_loss_sequence(self):
        animation = self.make_animation()
        ghost = animation.ghosts[0]
        ghost.row, ghost.col = animation.player.cell
        ghost.progress = animation.player.progress = 0.0
        animation._check_collisions()
        self.assertEqual(animation.game_state, "dying")

        animation._update(1.36)
        self.assertEqual(animation.lives, 2)
        self.assertEqual(animation.game_state, "ready")

    def test_runtime_stats_expose_gameplay_state(self):
        animation = self.make_animation()
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["lives"], 3)
        self.assertEqual(stats["level"], 1)
        self.assertEqual(stats["pellets_remaining"],
                         len(animation.initial_pellets) + len(animation.initial_energizers))


if __name__ == "__main__":
    unittest.main()
