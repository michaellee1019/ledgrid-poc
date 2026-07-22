"""Opt-in calibrated plant terrain tests for ASCII Drop."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.ascii_drop import AsciiDropAnimation


class PlantAwareAsciiDropTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        self.foliage_path.write_text(json.dumps({"covered_indices": []}))
        self.globe_path.write_text(json.dumps({"globe_indices": [], "region_count": 0}))

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_animation(self, **params):
        return AsciiDropAnimation(PreviewLEDController(strips=10, leds_per_strip=14), {
            "phrase": "A",
            "drop_speed": 40.0,
            "spawn_rate": 0.1,
            "random_seed": 9,
            "plant_clearance": 0,
            "plant_mask_path": str(self.foliage_path),
            "plant_globe_mask_path": str(self.globe_path),
            **params,
        })

    def test_disabled_mode_preserves_seeded_simulation_and_pixels(self):
        implicit = self.make_animation()
        explicit = self.make_animation(plant_aware=False)

        for frame_count, elapsed in enumerate((0.0, 0.1, 0.25, 0.5, 0.75)):
            left = implicit.generate_frame(elapsed, frame_count).pixels.copy()
            right = explicit.generate_frame(elapsed, frame_count).pixels.copy()
            np.testing.assert_array_equal(left, right)
            self.assertEqual(implicit._pieces, explicit._pieces)
            np.testing.assert_array_equal(implicit._settled, explicit._settled)

    def test_enabled_mode_selects_the_least_occluded_falling_lane(self):
        # Cover the full height of the left half. A five-pixel glyph fits at x=5.
        covered = [x * 14 + led for x in range(5) for led in range(14)]
        self.foliage_path.write_text(json.dumps({"covered_indices": covered}))
        animation = self.make_animation(plant_aware=True)

        animation._spawn_next_character()

        self.assertEqual(animation._pieces[0]["x"], 5)

    def test_falling_glyphs_stack_on_terrain_without_entering_it(self):
        # Physical LED 5 maps to top-down simulation row 8.
        covered = [x * 14 + 5 for x in range(10)]
        self.foliage_path.write_text(json.dumps({"covered_indices": covered}))
        animation = self.make_animation(plant_aware=True)

        animation.generate_frame(0.0, 0)
        for frame_count, elapsed in enumerate(np.arange(0.25, 2.25, 0.25), start=1):
            animation.generate_frame(float(elapsed), frame_count)

        self.assertGreater(np.count_nonzero(animation._settled), 0)
        self.assertFalse(np.any(animation._settled & animation._plant_clearance))
        self.assertFalse(np.any(animation._settled[8:]))

    def test_foliage_and_globes_render_as_distinct_landmarks(self):
        self.foliage_path.write_text(json.dumps({"covered_indices": [2 * 14 + 3]}))
        self.globe_path.write_text(json.dumps({"globe_indices": [7 * 14 + 4], "region_count": 1}))
        animation = self.make_animation(phrase="", plant_aware=True, background_blue=0)

        frame = animation.generate_frame(0.0, 0).pixels
        foliage = frame[2 * 14 + 3]
        globe = frame[7 * 14 + 4]

        self.assertGreater(int(foliage[1]), int(foliage[0]))
        self.assertGreater(int(globe[2]), int(globe[1]))
        self.assertFalse(np.array_equal(foliage, globe))
        self.assertTrue(animation.get_runtime_stats()["plant_aware"])


if __name__ == "__main__":
    unittest.main()
