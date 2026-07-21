"""Deterministic coverage for Wave's opt-in plant refraction field."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.plugins.wave import WaveAnimation


class _Controller:
    strip_count = 9
    leds_per_strip = 15
    total_leds = strip_count * leds_per_strip


class PlantAwareWaveTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        self.foliage_index = self._index(3, 6)
        self.globe_index = self._index(7, 11)
        self.foliage_path.write_text(
            json.dumps({"covered_indices": [self.foliage_index]}), encoding="utf-8"
        )
        self.globe_path.write_text(
            json.dumps({"globe_indices": [self.globe_index], "region_count": 1}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def _index(strip, led):
        return strip * _Controller.leds_per_strip + led

    def make_animation(self, **params):
        return WaveAnimation(_Controller(), {
            "plant_clearance": 0,
            "plant_mask_path": str(self.foliage_path),
            "plant_globe_mask_path": str(self.globe_path),
            **params,
        })

    def test_disabled_mode_retains_legacy_frame_fingerprints(self):
        cases = (
            ({}, "ea2f528d035a83e5f9adbecd1d9faa6951652dca4cbd657330f4e760e13668d6"),
            (
                {
                    "axis": "diagonal",
                    "speed": 1.7,
                    "frequency": 3.2,
                    "amplitude": 0.73,
                    "direction": -1,
                    "brightness": 0.62,
                    "wave_red": 220,
                    "wave_green": 30,
                    "wave_blue": 170,
                },
                "bc06ce4c24e9c07956c1ac6c1741aa100150319ac9638c774b89670bdeb2ceb9",
            ),
        )
        for config, expected in cases:
            class FingerprintController:
                strip_count = 4
                leds_per_strip = 9
                total_leds = 36

            animation = WaveAnimation(
                FingerprintController(), {**config, "plant_aware": False}
            )
            payload = b"".join(
                animation.generate_frame(elapsed, frame_count).tobytes()
                for frame_count, elapsed in enumerate((0.0, 0.1, 1.0, 2.0))
            )
            self.assertEqual(hashlib.sha256(payload).hexdigest(), expected)

    def test_implicit_and_explicit_disabled_modes_are_byte_identical(self):
        implicit = self.make_animation(axis="horizontal", frequency=2.7)
        explicit = self.make_animation(
            axis="horizontal", frequency=2.7, plant_aware=False
        )
        for frame_count, elapsed in enumerate((0.0, 0.25, 0.8, 1.7)):
            np.testing.assert_array_equal(
                implicit.generate_frame(elapsed, frame_count),
                explicit.generate_frame(elapsed, frame_count),
            )

    def test_enabled_mode_refracts_around_layers_in_opposite_directions(self):
        ordinary = self.make_animation(plant_aware=False, axis="horizontal")
        aware = self.make_animation(plant_aware=True, axis="horizontal")
        ordinary_frame = ordinary.generate_frame(0.0, 0).copy()
        aware_frame = aware.generate_frame(0.0, 0).copy()

        foliage_neighbor = self._index(2, 6)
        globe_neighbor = self._index(8, 11)
        far_pixel = self._index(0, 14)
        self.assertGreater(aware._plant_phase_offsets[foliage_neighbor], 0.0)
        self.assertLess(aware._plant_phase_offsets[globe_neighbor], 0.0)
        self.assertFalse(
            np.array_equal(aware_frame[foliage_neighbor], ordinary_frame[foliage_neighbor])
        )
        self.assertFalse(
            np.array_equal(aware_frame[globe_neighbor], ordinary_frame[globe_neighbor])
        )
        np.testing.assert_array_equal(aware_frame[far_pixel], ordinary_frame[far_pixel])

    def test_enabled_mode_uses_distinct_low_brightness_plant_landmarks(self):
        animation = self.make_animation(plant_aware=True, brightness=0.5)
        frame = animation.generate_frame(0.4, 7)

        np.testing.assert_array_equal(frame[self.foliage_index], (10, 63, 24))
        np.testing.assert_array_equal(frame[self.globe_index], (90, 21, 79))
        self.assertFalse(
            np.array_equal(frame[self.foliage_index], frame[self.globe_index])
        )
        stats = animation.get_runtime_stats()
        self.assertTrue(stats["plant_aware"])
        self.assertEqual(stats["plant_foliage_pixels"], 1)
        self.assertEqual(stats["plant_globe_pixels"], 1)
        self.assertEqual(stats["plant_globe_regions"], 1)


if __name__ == "__main__":
    unittest.main()
