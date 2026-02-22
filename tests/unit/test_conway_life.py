"""Unit tests for the Conway Game of Life animation plugin."""

import unittest

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.conway_life import ConwayLifeAnimation


class ConwayLifeAnimationTests(unittest.TestCase):
    def test_conway_is_allowed_plugin(self):
        self.assertIn("conway_life", AnimationManager.ALLOWED_PLUGINS)

    def test_preview_frame_generation(self):
        controller = PreviewLEDController(strips=8, leds_per_strip=8)
        manager = AnimationManager(controller)

        preview = manager.get_animation_preview("conway_life")
        self.assertEqual(len(preview["frame_data"]), 64)
        self.assertEqual(preview["current_animation"], "conway_life")
        self.assertNotIn("error", preview)

    def test_seeded_blinker_evolves(self):
        controller = PreviewLEDController(strips=8, leds_per_strip=8)
        animation = ConwayLifeAnimation(
            controller,
            {
                "random_density": 0.0,
                "wrap_edges": False,
                "phase_frames": 1,
                "generations_per_second": 12.0,
                "seed_cells": [(3, 2), (3, 3), (3, 4)],
            },
        )

        # First call initializes step timing.
        animation.generate_frame(0.0, 0)
        # This advances exactly two steps (color->transition->next generation).
        animation.generate_frame(0.09, 1)

        self.assertEqual(animation.generation, 1)

        expected_live_cells = {(2, 3), (3, 3), (4, 3)}
        actual_live_cells = set()
        for y, row in enumerate(animation.grid):
            for x, value in enumerate(row):
                if value > 0:
                    actual_live_cells.add((x, y))

        self.assertSetEqual(actual_live_cells, expected_live_cells)


if __name__ == "__main__":
    unittest.main()
