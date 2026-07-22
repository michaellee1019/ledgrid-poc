"""Deterministic plant-aware behavior tests for Conway Life."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.conway_life import ConwayLifeAnimation


class PlantAwareConwayLifeTests(unittest.TestCase):
    WIDTH = 8
    HEIGHT = 8

    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.mask_directory = Path(self._temporary_directory.name)

    def tearDown(self):
        self._temporary_directory.cleanup()

    def _physical_index(self, x, y):
        return x * self.HEIGHT + (self.HEIGHT - 1 - y)

    def _write_masks(self, foliage=(), globes=()):
        foliage_path = self.mask_directory / "foliage.json"
        globe_path = self.mask_directory / "globes.json"
        foliage_path.write_text(
            json.dumps(
                {"covered_indices": [self._physical_index(x, y) for x, y in foliage]}
            ),
            encoding="utf-8",
        )
        globe_path.write_text(
            json.dumps(
                {
                    "globe_indices": [self._physical_index(x, y) for x, y in globes],
                    "region_count": 1 if globes else 0,
                }
            ),
            encoding="utf-8",
        )
        return str(foliage_path), str(globe_path)

    def _animation(self, **config):
        return ConwayLifeAnimation(
            PreviewLEDController(strips=self.WIDTH, leds_per_strip=self.HEIGHT),
            {
                "random_density": 0,
                "wrap_edges": False,
                "glider_interval": 0,
                "stagnation_generations": 0,
                "destruct_on_loop": False,
                "random_seed": 73,
                **config,
            },
        )

    def test_disabled_mode_preserves_simulation_and_render_exactly(self):
        foliage_path, globe_path = self._write_masks(
            foliage=((3, 3),), globes=((5, 5),)
        )
        common = {
            "seed_cells": [(2, 3), (3, 3), (4, 3)],
            "palette": "aurora",
            "background": "twilight",
            "background_animation": False,
        }
        baseline = self._animation(**common)
        disabled = self._animation(
            **common,
            plant_aware=False,
            plant_clearance=4,
            plant_mask_path=foliage_path,
            plant_globe_mask_path=globe_path,
        )

        for _ in range(7):
            baseline._advance_phase()
            disabled._advance_phase()

        self.assertEqual(baseline.grid, disabled.grid)
        self.assertEqual(baseline.next_grid, disabled.next_grid)
        self.assertTrue(
            np.array_equal(
                baseline.generate_frame(0.0, 0).pixels,
                disabled.generate_frame(0.0, 0).pixels,
            )
        )

    def test_masks_are_boundaries_and_globe_shore_is_a_nursery(self):
        foliage_path, globe_path = self._write_masks(
            foliage=((6, 6),), globes=((4, 4),)
        )
        common = {
            "seed_cells": [(2, 3), (3, 2), (4, 4), (6, 6)],
            "plant_aware": True,
            "plant_clearance": 0,
            "plant_mask_path": foliage_path,
            "plant_globe_mask_path": globe_path,
        }
        nursery = self._animation(**common, plant_nursery=True)
        ordinary = self._animation(**common, plant_nursery=False)

        # Explicit useful cells behind foliage/glass are removed at initialization.
        self.assertEqual(nursery.grid[4][4], 0)
        self.assertEqual(nursery.grid[6][6], 0)
        # (3, 3) touches the globe and has only two live neighbors: the globe
        # shoreline creates a birth that ordinary B3/S23 does not.
        self.assertTrue(nursery._plant_fertile[3, 3])
        self.assertEqual(nursery.next_grid[3][3], 1)
        self.assertEqual(ordinary.next_grid[3][3], 0)
        self.assertEqual(nursery.next_natural_grid[3][3], (255, 176, 48))

    def test_render_distinguishes_foliage_globes_and_nursery_ring(self):
        foliage = (6, 6)
        globe = (4, 4)
        nursery = (3, 3)
        foliage_path, globe_path = self._write_masks(
            foliage=(foliage,), globes=(globe,)
        )
        animation = self._animation(
            seed_cells=[],
            plant_aware=True,
            plant_clearance=0,
            plant_mask_path=foliage_path,
            plant_globe_mask_path=globe_path,
            background="void",
        )

        frame = animation.generate_frame(0.0, 0).pixels
        colors = {
            "foliage": tuple(frame[self._physical_index(*foliage)]),
            "globe": tuple(frame[self._physical_index(*globe)]),
            "nursery": tuple(frame[self._physical_index(*nursery)]),
        }

        self.assertEqual(len(set(colors.values())), 3)
        self.assertTrue(all(any(channel > 0 for channel in color) for color in colors.values()))
        self.assertEqual(animation.alive_cells, 0)
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["plant_foliage_pixels"], 1)
        self.assertEqual(stats["plant_globe_pixels"], 1)
        self.assertGreater(stats["plant_nursery_pixels"], 0)


if __name__ == "__main__":
    unittest.main()
