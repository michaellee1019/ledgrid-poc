"""Focused coverage for Solid Color's opt-in plant-aware ambient mode."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.base import RenderedFrame
from animation.plugins.solid import SolidColorAnimation


class _Controller:
    strip_count = 6
    leds_per_strip = 8
    total_leds = strip_count * leds_per_strip
    debug = False


class PlantAwareSolidTests(unittest.TestCase):
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

    def make_animation(self, **params):
        return SolidColorAnimation(
            _Controller(),
            {
                "red": 90,
                "green": 110,
                "blue": 130,
                "plant_clearance": 1,
                "plant_mask_path": str(self.foliage_path),
                "plant_globe_mask_path": str(self.globe_path),
                **params,
            },
        )

    def test_schema_keeps_mode_disabled_and_exposes_ambient_controls(self):
        schema = self.make_animation().get_parameter_schema()

        self.assertFalse(schema["plant_aware"]["default"])
        self.assertEqual(schema["plant_foliage_strength"]["default"], 0.62)
        self.assertEqual(schema["plant_globe_strength"]["default"], 0.72)
        self.assertEqual(schema["plant_negative_space"]["default"], 0.58)
        self.assertEqual(schema["plant_render_fps"]["max"], 60.0)

    def test_disabled_mode_preserves_exact_pixels_and_unchanged_frame_cache(self):
        implicit = self.make_animation()
        explicit = SolidColorAnimation(
            _Controller(),
            {
                "red": 90,
                "green": 110,
                "blue": 130,
                "plant_aware": False,
                "plant_mask_path": "/does/not/exist.json",
                "plant_globe_mask_path": "/also/does/not/exist.json",
            },
        )

        implicit_first = implicit.generate_frame(0.0, 0)
        explicit_first = explicit.generate_frame(0.0, 0)
        implicit_second = implicit.generate_frame(4.0, 1)
        explicit_second = explicit.generate_frame(4.0, 1)

        np.testing.assert_array_equal(implicit_first.pixels, explicit_first.pixels)
        self.assertFalse(implicit_second.changed)
        self.assertFalse(explicit_second.changed)
        self.assertIs(implicit_first.pixels, implicit_second.pixels)
        self.assertIs(explicit_first.pixels, explicit_second.pixels)

    def test_enabled_mode_uses_negative_space_and_distinct_semantic_cores(self):
        animation = self.make_animation(plant_aware=True)
        rendered = animation.generate_frame(0.0, 0)
        frame = rendered.pixels
        foliage_neighbor = self.foliage + 1
        far_pixel = 0

        self.assertIsInstance(rendered, RenderedFrame)
        np.testing.assert_array_equal(frame[far_pixel], (90, 110, 130))
        self.assertLess(int(frame[foliage_neighbor].sum()), 90 + 110 + 130)
        self.assertGreater(int(frame[self.foliage, 1]), int(frame[self.foliage, 0]))
        self.assertGreater(int(frame[self.globe, 2]), int(frame[self.globe, 1]))
        self.assertFalse(np.array_equal(frame[self.foliage], frame[self.globe]))

        stats = animation.get_runtime_stats()
        self.assertEqual(stats["plant_foliage_pixels"], 1)
        self.assertEqual(stats["plant_globe_pixels"], 1)
        self.assertEqual(stats["plant_globe_regions"], 1)

    def test_landmarks_breathe_oppositely_and_source_rate_reuses_frames(self):
        animation = self.make_animation(plant_aware=True, plant_render_fps=20.0)
        first = animation.generate_frame(0.0, 0)
        within_tick = animation.generate_frame(0.02, 1)
        self.assertFalse(within_tick.changed)
        self.assertIs(first.pixels, within_tick.pixels)

        first_pixels = first.pixels.copy()
        quarter_cycle = animation.generate_frame(1.55, 2)
        self.assertTrue(quarter_cycle.changed)
        self.assertFalse(
            np.array_equal(first_pixels[self.foliage], quarter_cycle.pixels[self.foliage])
        )
        self.assertFalse(
            np.array_equal(first_pixels[self.globe], quarter_cycle.pixels[self.globe])
        )

        foliage_target = np.asarray((28, 190, 78), dtype=int)
        globe_target = np.asarray((220, 62, 196), dtype=int)
        self.assertLess(
            np.linalg.norm(quarter_cycle.pixels[self.foliage].astype(int) - foliage_target),
            np.linalg.norm(first_pixels[self.foliage].astype(int) - foliage_target),
        )
        self.assertGreater(
            np.linalg.norm(quarter_cycle.pixels[self.globe].astype(int) - globe_target),
            np.linalg.norm(first_pixels[self.globe].astype(int) - globe_target),
        )

    def test_zero_breath_depth_makes_plant_ambient_static(self):
        animation = self.make_animation(
            plant_aware=True, plant_breath_depth=0.0, plant_render_fps=60.0
        )
        first = animation.generate_frame(0.0, 0)
        later = animation.generate_frame(20.0, 1)

        self.assertFalse(later.changed)
        self.assertIs(first.pixels, later.pixels)


if __name__ == "__main__":
    unittest.main()
