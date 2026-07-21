"""Deterministic coverage for Sparkle's opt-in calibrated plant behavior."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.sparkle import SparkleAnimation


class PlantAwareSparkleTests(unittest.TestCase):
    STRIPS = 8
    LEDS = 12

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        self.foliage_index = 2 * self.LEDS + 5
        self.globe_index = 5 * self.LEDS + 7
        self.foliage_path.write_text(
            json.dumps({"covered_indices": [self.foliage_index]}), encoding="utf-8"
        )
        self.globe_path.write_text(
            json.dumps({"globe_indices": [self.globe_index], "region_count": 1}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_animation(self, **params):
        config = {
            "plant_clearance": 0,
            "plant_mask_path": str(self.foliage_path),
            "plant_globe_mask_path": str(self.globe_path),
            **params,
        }
        return SparkleAnimation(
            PreviewLEDController(strips=self.STRIPS, leds_per_strip=self.LEDS),
            config,
        )

    def test_disabled_mode_has_exact_legacy_pixels_and_rng_consumption(self):
        cases = (
            ({}, "665072989ade7e1898d83bf10a8dc2010da495af4b423d6e70be3e5217858ae9"),
            (
                {
                    "base_red": 1,
                    "base_green": 2,
                    "base_blue": 3,
                    "sparkle_red": 100,
                    "sparkle_green": 120,
                    "sparkle_blue": 140,
                    "sparkle_probability": 0.075,
                    "fade_speed": 0.73,
                    "brightness": 0.61,
                },
                "1fb6b3336c2dcbdbedc7288388dee4a434faa8f99af0612919607e4da7f49344",
            ),
        )
        for config, expected in cases:
            np.random.seed(90210)
            animation = SparkleAnimation(
                PreviewLEDController(strips=4, leds_per_strip=9),
                {**config, "plant_aware": False},
            )
            payload = b"".join(
                animation.generate_frame(elapsed, frame_count).tobytes()
                for frame_count, elapsed in enumerate((0.0, 0.1, 0.4, 1.2, 2.0))
            )
            self.assertEqual(hashlib.sha256(payload).hexdigest(), expected)
            self.assertEqual(np.random.random(), 0.35025257301266577)

    def test_implicit_and_explicit_disabled_modes_are_byte_identical(self):
        frames = []
        for config in ({}, {"plant_aware": False}):
            np.random.seed(741)
            animation = self.make_animation(**config)
            frames.append(
                [animation.generate_frame(t, i).copy() for i, t in enumerate((0.0, 0.2, 0.8))]
            )
        for implicit, explicit in zip(*frames):
            np.testing.assert_array_equal(implicit, explicit)

    def test_enabled_mode_repels_from_foliage_and_attracts_to_globe_rims(self):
        animation = self.make_animation(
            plant_aware=True,
            sparkle_probability=0.1,
            fade_speed=0.5,
        )
        random_values = np.full(self.STRIPS * self.LEDS, 0.11)
        with patch("animation.plugins.sparkle.np.random.random", return_value=random_values):
            animation.generate_frame(0.0, 0)

        foliage_neighbor = 1 * self.LEDS + 5
        globe_neighbor = 4 * self.LEDS + 7
        far_pixel = 0
        self.assertEqual(animation._plant_spawn_multiplier[self.foliage_index], 0.0)
        self.assertEqual(animation._plant_spawn_multiplier[self.globe_index], 0.0)
        self.assertLess(
            animation._plant_spawn_multiplier[foliage_neighbor],
            animation._plant_spawn_multiplier[far_pixel],
        )
        self.assertGreater(
            animation._plant_spawn_multiplier[globe_neighbor],
            animation._plant_spawn_multiplier[far_pixel],
        )
        self.assertEqual(animation.sparkle_brightness[foliage_neighbor], 0.0)
        self.assertEqual(animation.sparkle_brightness[far_pixel], 0.0)
        self.assertEqual(animation.sparkle_brightness[globe_neighbor], 1.0)
        self.assertEqual(animation.sparkle_brightness[self.foliage_index], 0.0)
        self.assertEqual(animation.sparkle_brightness[self.globe_index], 0.0)

    def test_enabled_mode_marks_plant_cores_with_distinct_subdued_glows(self):
        animation = self.make_animation(plant_aware=True, brightness=0.5)
        animation.sparkle_brightness[[self.foliage_index, self.globe_index]] = 1.0
        with patch(
            "animation.plugins.sparkle.np.random.random",
            return_value=np.ones(self.STRIPS * self.LEDS),
        ):
            frame = animation.generate_frame(0.0, 0)

        np.testing.assert_array_equal(frame[self.foliage_index], (9, 56, 19))
        np.testing.assert_array_equal(frame[self.globe_index], (88, 24, 75))
        self.assertGreater(int(frame[self.foliage_index, 1]), int(frame[self.foliage_index, 0]))
        self.assertGreater(int(frame[self.globe_index, 2]), int(frame[self.globe_index, 1]))
        self.assertFalse(np.array_equal(frame[self.foliage_index], frame[self.globe_index]))
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["plant_foliage_pixels"], 1)
        self.assertEqual(stats["plant_globe_pixels"], 1)
        self.assertEqual(stats["plant_globe_regions"], 1)


if __name__ == "__main__":
    unittest.main()
