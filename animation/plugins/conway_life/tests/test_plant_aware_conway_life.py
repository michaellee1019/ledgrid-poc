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

    @staticmethod
    def _modifiers(*active, **strengths):
        return {"version": 1, "active": list(active), "strengths": strengths}

    def test_declares_exact_sprint_support(self):
        self.assertEqual(
            ConwayLifeAnimation.PLANT_MODIFIER_SUPPORT,
            frozenset(("obstacle", "habitat", "hazard", "emitter")),
        )

    def test_explicit_empty_modifier_state_has_full_parity(self):
        common = {
            "seed_cells": [(2, 3), (3, 3), (4, 3)],
            "palette": "aurora",
            "background": "void",
        }
        baseline = self._animation(**common)
        empty = self._animation(
            **common, plant_modifiers=self._modifiers()
        )
        for _ in range(5):
            baseline._advance_phase()
            empty._advance_phase()
        self.assertEqual(baseline.grid, empty.grid)
        self.assertEqual(baseline.random.getstate(), empty.random.getstate())
        np.testing.assert_array_equal(
            baseline.generate_frame(1.0, 1).pixels,
            empty.generate_frame(1.0, 1).pixels,
        )

    def test_modifier_only_live_change_preserves_world_phase_and_rng(self):
        foliage_path, globe_path = self._write_masks(foliage=((3, 3),))
        animation = self._animation(
            seed_cells=[(2, 3), (3, 3), (4, 3)],
            plant_mask_path=foliage_path,
            plant_globe_mask_path=globe_path,
        )
        before = (
            [row[:] for row in animation.grid], animation.generation,
            animation.phase, animation.phase_frame, animation.random.getstate(),
        )
        animation.update_parameters({
            "plant_modifiers": self._modifiers("obstacle", obstacle=1.0)
        })
        after = (
            animation.grid, animation.generation, animation.phase,
            animation.phase_frame, animation.random.getstate(),
        )
        self.assertEqual(before, after)

    def test_constructed_obstacle_habitat_and_hazard_rules(self):
        foliage_path, globe_path = self._write_masks(foliage=((4, 4),))
        paths = {
            "plant_clearance": 0,
            "plant_mask_path": foliage_path,
            "plant_globe_mask_path": globe_path,
        }
        obstacle = self._animation(
            **paths, seed_cells=[(4, 4), (2, 3), (3, 3), (4, 3)],
            plant_modifiers=self._modifiers("obstacle", obstacle=1.0),
        )
        self.assertEqual(obstacle.grid[4][4], 0)

        habitat = self._animation(
            **paths, seed_cells=[(2, 3), (3, 2)],
            plant_modifiers=self._modifiers("habitat", habitat=1.0),
        )
        ordinary = self._animation(**paths, seed_cells=[(2, 3), (3, 2)])
        self.assertTrue(habitat._plant_fertile[3, 3])
        self.assertEqual(habitat.next_grid[3][3], 1)
        self.assertEqual(ordinary.next_grid[3][3], 0)

        hazard = self._animation(
            **paths, seed_cells=[(3, 4), (4, 4), (5, 4)],
            plant_modifiers=self._modifiers("hazard", hazard=1.0),
        )
        self.assertEqual(hazard.next_grid[4][4], 0)
        self.assertEqual(hazard.plant_hazard_deaths, 0)
        hazard.phase = "transition"
        hazard.phase_frame = int(hazard.params["phase_frames"]) - 1
        hazard._advance_phase()
        self.assertEqual(hazard.plant_hazard_deaths, 1)

    def test_emitter_is_bounded_and_only_fires_on_generation_boundaries(self):
        foliage_path, globe_path = self._write_masks(foliage=((4, 4),))
        animation = self._animation(
            seed_cells=[], plant_clearance=0,
            plant_mask_path=foliage_path,
            plant_globe_mask_path=globe_path,
            plant_modifiers=self._modifiers("emitter", emitter=0.5),
        )
        for _ in range(7):
            animation._advance_phase()
        self.assertEqual(animation.plant_emitter_events, 0)
        animation.phase = "transition"
        animation.phase_frame = int(animation.params["phase_frames"]) - 1
        animation._advance_phase()
        self.assertEqual(animation.plant_emitter_events, 1)
        self.assertEqual(animation.plant_emitted_cells, 4)

        for _ in range(19):
            animation.phase = "transition"
            animation.phase_frame = int(animation.params["phase_frames"]) - 1
            animation._advance_phase()
        self.assertLessEqual(animation.plant_emitted_cells, 20 * 4)
        self.assertGreater(animation.get_runtime_stats()["plant_emitter_events"], 1)

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
