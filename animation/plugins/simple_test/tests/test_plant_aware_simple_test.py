"""Deterministic coverage for Simple Test's opt-in plant diagnostics."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.plugins.simple_test import SimpleTestAnimation


class _Controller:
    strip_count = 6
    leds_per_strip = 8
    total_leds = strip_count * leds_per_strip
    debug = False


class PlantAwareSimpleTestTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        self.foliage = 2 * _Controller.leds_per_strip + 3
        self.globe = 4 * _Controller.leds_per_strip + 4
        self.foliage_path.write_text(
            json.dumps({"covered_indices": [self.foliage]}), encoding="utf-8"
        )
        self.globe_path.write_text(
            json.dumps({"globe_indices": [self.globe], "region_count": 1}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def animation(self, **params):
        return SimpleTestAnimation(
            _Controller(),
            {
                "plant_clearance": 1,
                "plant_mask_path": str(self.foliage_path),
                "plant_globe_mask_path": str(self.globe_path),
                **params,
            },
        )

    def test_schema_exposes_disabled_standard_mode_and_diagnostic_level(self):
        schema = self.animation().get_parameter_schema()

        self.assertFalse(schema["plant_aware"]["default"])
        self.assertEqual(schema["plant_occlusion_brightness"]["default"], 0.35)
        self.assertEqual(schema["plant_clearance"]["max"], 4)

    def test_disabled_mode_preserves_exact_original_frames_and_cache_behavior(self):
        implicit = self.animation()
        explicit = self.animation(
            plant_aware=False,
            plant_mask_path="/does/not/exist.json",
            plant_globe_mask_path="/also/does/not/exist.json",
        )

        implicit_first = implicit.generate_frame(0.0, 0)
        explicit_first = explicit.generate_frame(0.0, 0)
        implicit_repeat = implicit.generate_frame(0.25, 1)
        explicit_repeat = explicit.generate_frame(0.25, 1)

        np.testing.assert_array_equal(implicit_first.pixels, explicit_first.pixels)
        np.testing.assert_array_equal(implicit_first.pixels, np.full((48, 3), (255, 0, 0)))
        self.assertIs(implicit_first.pixels, implicit_repeat.pixels)
        self.assertIs(explicit_first.pixels, explicit_repeat.pixels)
        self.assertFalse(implicit_repeat.changed)
        self.assertFalse(explicit_repeat.changed)

    def test_enabled_mode_dims_clearance_and_marks_semantic_cores(self):
        animation = self.animation(plant_aware=True)
        frame = animation.generate_frame(0.0, 0).pixels
        clearance_only = self.foliage + 1

        np.testing.assert_array_equal(frame[0], (255, 0, 0))
        np.testing.assert_array_equal(frame[clearance_only], (89, 0, 0))
        self.assertGreater(int(frame[self.foliage, 1]), int(frame[self.foliage, 0]))
        self.assertGreater(int(frame[self.globe, 2]), int(frame[self.globe, 1]))
        self.assertFalse(np.array_equal(frame[self.foliage], frame[self.globe]))

        stats = animation.get_runtime_stats()
        self.assertEqual(stats["plant_foliage_pixels"], 1)
        self.assertEqual(stats["plant_globe_pixels"], 1)
        self.assertEqual(stats["plant_globe_regions"], 1)

    def test_runtime_toggle_changes_frame_without_advancing_color_stage(self):
        animation = self.animation()
        ordinary = animation.generate_frame(0.0, 0)
        animation.update_parameters({"plant_aware": True})
        aware = animation.generate_frame(0.1, 1)

        self.assertTrue(aware.changed)
        self.assertFalse(np.array_equal(ordinary.pixels, aware.pixels))
        self.assertEqual(animation.color_index, 0)
        self.assertFalse(animation.generate_frame(0.2, 2).changed)


if __name__ == "__main__":
    unittest.main()
