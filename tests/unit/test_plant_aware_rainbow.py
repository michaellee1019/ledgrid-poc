"""Deterministic coverage for Rainbow's opt-in calibrated plant contours."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.rainbow import RainbowAnimation


class PlantAwareRainbowTests(unittest.TestCase):
    STRIPS = 8
    LEDS = 14

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        self.foliage_index = 3 * self.LEDS + 6
        self.globe_index = 6 * self.LEDS + 9
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
        controller = PreviewLEDController(
            strips=self.STRIPS, leds_per_strip=self.LEDS
        )
        return RainbowAnimation(controller, config)

    def test_disabled_mode_has_exact_legacy_frame_fingerprints(self):
        cases = (
            ({}, "7db967c9b17f6c9bb5258cd39f70325fac92bce2e5d92cf7345cd05a54687612"),
            (
                {
                    "speed": 1.7,
                    "span_ratio": 0.7,
                    "direction": -1,
                    "brightness": 0.73,
                    "color_saturation": 0.61,
                    "color_value": 0.82,
                },
                "a324a3c52916e2ce6522d7991bd8f38fd927520cc1b27508bb8d47f41ccf88f8",
            ),
        )
        for config, expected in cases:
            controller = PreviewLEDController(strips=4, leds_per_strip=9)
            animation = RainbowAnimation(controller, {**config, "plant_aware": False})
            payload = b"".join(
                animation.generate_frame(elapsed, frame_count).tobytes()
                for frame_count, elapsed in enumerate((0.0, 0.1, 1.0, 2.0))
            )
            self.assertEqual(hashlib.sha256(payload).hexdigest(), expected)

    def test_implicit_and_explicit_disabled_modes_are_byte_identical(self):
        implicit = self.make_animation()
        explicit = self.make_animation(plant_aware=False)
        for frame_count, elapsed in enumerate((0.0, 0.2, 0.7, 1.4)):
            np.testing.assert_array_equal(
                implicit.generate_frame(elapsed, frame_count),
                explicit.generate_frame(elapsed, frame_count),
            )

    def test_enabled_mode_bends_bands_in_opposite_directions_around_layers(self):
        ordinary = self.make_animation(plant_aware=False)
        aware = self.make_animation(plant_aware=True)
        ordinary_frame = ordinary.generate_frame(0.0, 0).copy()
        aware_frame = aware.generate_frame(0.0, 0).copy()

        foliage_neighbor = 2 * self.LEDS + 6
        globe_neighbor = 5 * self.LEDS + 9
        far_pixel = 0
        self.assertGreater(aware._plant_phase_offsets[foliage_neighbor], 0)
        self.assertLess(aware._plant_phase_offsets[globe_neighbor], 0)
        self.assertFalse(
            np.array_equal(aware_frame[foliage_neighbor], ordinary_frame[foliage_neighbor])
        )
        self.assertFalse(
            np.array_equal(aware_frame[globe_neighbor], ordinary_frame[globe_neighbor])
        )
        np.testing.assert_array_equal(aware_frame[far_pixel], ordinary_frame[far_pixel])

    def test_enabled_mode_marks_occluded_cores_with_distinct_semantic_accents(self):
        animation = self.make_animation(plant_aware=True)
        frame = animation.generate_frame(0.0, 0)

        np.testing.assert_array_equal(frame[self.foliage_index], (22, 150, 65))
        np.testing.assert_array_equal(frame[self.globe_index], (220, 55, 190))
        self.assertGreater(int(frame[self.foliage_index, 1]), int(frame[self.foliage_index, 0]))
        self.assertGreater(int(frame[self.globe_index, 2]), int(frame[self.globe_index, 1]))
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["plant_foliage_pixels"], 1)
        self.assertEqual(stats["plant_globe_pixels"], 1)
        self.assertEqual(stats["plant_globe_regions"], 1)


if __name__ == "__main__":
    unittest.main()
