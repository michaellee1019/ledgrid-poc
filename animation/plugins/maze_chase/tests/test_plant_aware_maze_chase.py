"""Deterministic plant-awareness coverage for Neon Maze Chase."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.maze_chase import MazeChaseAnimation


class PlantAwareMazeChaseTests(unittest.TestCase):
    def make_animation(self, config=None):
        return MazeChaseAnimation(PreviewLEDController(32, 140), config or {})

    def test_disabled_mode_is_exactly_the_default_even_with_bad_mask_paths(self):
        baseline = self.make_animation({"seed": 37})
        disabled = self.make_animation({
            "seed": 37,
            "plant_aware": False,
            "plant_mask_path": "/definitely/not/a/plant-mask.json",
            "plant_globe_mask_path": "/definitely/not/a/globe-mask.json",
        })

        self.assertEqual(disabled.walkable, baseline.walkable)
        self.assertEqual(disabled.initial_pellets, baseline.initial_pellets)
        self.assertEqual(disabled.initial_energizers, baseline.initial_energizers)
        self.assertEqual(disabled.player.direction, baseline.player.direction)
        self.assertEqual(
            [ghost.direction for ghost in disabled.ghosts],
            [ghost.direction for ghost in baseline.ghosts],
        )
        np.testing.assert_array_equal(
            disabled.generate_frame(0.0, 0).pixels,
            baseline.generate_frame(0.0, 0).pixels,
        )

    def test_enabled_mask_becomes_connected_obstacle_and_hides_collectible(self):
        baseline = self.make_animation({"seed": 37})
        protected = {baseline.PLAYER_SPAWN, *baseline.GHOST_SPAWNS}
        target = next(
            cell for cell in sorted(baseline.initial_pellets)
            if cell not in protected and baseline._is_connected(baseline.walkable - {cell})
        )
        scale_x, scale_y, left, top = baseline._layout()
        row, col = target
        indices = []
        for y in range(top + row * scale_y, top + (row + 1) * scale_y):
            for x in range(left + col * scale_x, left + (col + 1) * scale_x):
                indices.append(x * baseline.height + (baseline.height - 1 - y))

        with tempfile.TemporaryDirectory() as directory:
            foliage_path = Path(directory) / "foliage.json"
            globe_path = Path(directory) / "globes.json"
            foliage_path.write_text(json.dumps({"covered_indices": indices}), encoding="utf-8")
            globe_path.write_text(json.dumps({"globe_indices": [], "region_count": 0}), encoding="utf-8")
            aware = self.make_animation({
                "seed": 37,
                "plant_aware": True,
                "plant_clearance": 0,
                "plant_mask_path": str(foliage_path),
                "plant_globe_mask_path": str(globe_path),
            })

        self.assertIn(target, aware._plant_occluded_cells)
        self.assertIn(target, aware._plant_blocked_cells)
        self.assertNotIn(target, aware.walkable)
        self.assertNotIn(target, aware.initial_pellets)
        self.assertTrue(aware._is_connected(aware.walkable))
        self.assertTrue({aware.PLAYER_SPAWN, *aware.GHOST_SPAWNS} <= aware.walkable)
        self.assertNotEqual(
            aware.generate_frame(0.0, 0).pixels.tobytes(),
            baseline.generate_frame(0.0, 0).pixels.tobytes(),
        )
        self.assertEqual(aware.get_runtime_stats()["plant_obstacle_cells"], 1)


if __name__ == "__main__":
    unittest.main()
