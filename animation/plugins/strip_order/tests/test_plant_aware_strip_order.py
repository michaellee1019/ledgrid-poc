"""Deterministic coverage for Strip Order's plant-mask diagnostic."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.base import RenderedFrame
from animation.plugins.strip_order import StripOrderAnimation


class _Controller:
    strip_count = 3
    leds_per_strip = 6
    total_leds = strip_count * leds_per_strip
    debug = False


class PlantAwareStripOrderTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage = _Controller.leds_per_strip + 2
        self.globe = _Controller.leds_per_strip + 4
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
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
        return StripOrderAnimation(
            _Controller(),
            {
                "hold_seconds": 3.0,
                "pause_seconds": 1.0,
                "brightness": 1.0,
                "plant_clearance": 0,
                "plant_mask_path": str(self.foliage_path),
                "plant_globe_mask_path": str(self.globe_path),
                **params,
            },
        )

    def test_schema_is_opt_in_and_exposes_diagnostic_pass_controls(self):
        schema = self.make_animation().get_parameter_schema()

        self.assertFalse(schema["plant_aware"]["default"])
        self.assertEqual(schema["plant_diagnostic_style"]["default"], "focus_passes")
        self.assertEqual(
            schema["plant_diagnostic_style"]["options"],
            ["focus_passes", "semantic"],
        )
        self.assertEqual(schema["plant_foliage_green"]["default"], 255)
        self.assertEqual(schema["plant_globe_blue"]["default"], 220)

    def test_disabled_mode_preserves_exact_default_pixels_and_cache(self):
        implicit = StripOrderAnimation(_Controller(), {"brightness": 0.5})
        explicit = StripOrderAnimation(
            _Controller(),
            {
                "brightness": 0.5,
                "plant_aware": False,
                "plant_mask_path": "/does/not/exist.json",
                "plant_globe_mask_path": "/also/does/not/exist.json",
            },
        )

        for elapsed, active_strip in (
            (0.0, 0),
            (0.5, 0),
            (1.1, None),
            (2.1, 1),
        ):
            left = implicit.generate_frame(elapsed, 0)
            right = explicit.generate_frame(elapsed, 0)
            self.assertEqual(left.changed, right.changed)
            self.assertEqual(left.dirty_ranges, right.dirty_ranges)
            np.testing.assert_array_equal(left.pixels, right.pixels)
            expected = np.zeros((_Controller.total_leds, 3), dtype=np.uint8)
            if active_strip is not None:
                start = active_strip * _Controller.leds_per_strip
                expected[start:start + _Controller.leds_per_strip] = 127
            np.testing.assert_array_equal(left.pixels, expected)

        cached = explicit.generate_frame(2.2, 1)
        self.assertFalse(cached.changed)

    def test_focus_passes_distinguish_foliage_and_globes(self):
        animation = self.make_animation(plant_aware=True)

        overview = animation.generate_frame(4.1, 0).pixels.copy()
        foliage_pass = animation.generate_frame(5.1, 1).pixels.copy()
        globe_pass = animation.generate_frame(6.1, 2).pixels.copy()

        np.testing.assert_array_equal(overview[self.foliage], (42, 255, 96))
        np.testing.assert_array_equal(overview[self.globe], (255, 48, 220))
        np.testing.assert_array_equal(foliage_pass[self.foliage], (42, 255, 96))
        np.testing.assert_array_equal(foliage_pass[self.globe], (45, 8, 39))
        np.testing.assert_array_equal(globe_pass[self.foliage], (7, 45, 17))
        np.testing.assert_array_equal(globe_pass[self.globe], (255, 48, 220))

        active = slice(_Controller.leds_per_strip, 2 * _Controller.leds_per_strip)
        self.assertTrue(np.all(np.any(overview[active] != 0, axis=1)))
        self.assertTrue(np.all(np.any(foliage_pass[active] != 0, axis=1)))
        self.assertTrue(np.all(np.any(globe_pass[active] != 0, axis=1)))

    def test_every_strip_is_fully_visited_and_pause_is_dark(self):
        animation = self.make_animation(plant_aware=True)

        for strip in range(_Controller.strip_count):
            frame = animation.generate_frame(strip * 4.0 + 0.1, strip).pixels.copy()
            active = slice(
                strip * _Controller.leds_per_strip,
                (strip + 1) * _Controller.leds_per_strip,
            )
            self.assertTrue(np.all(np.any(frame[active] != 0, axis=1)))
            self.assertEqual(int(np.count_nonzero(frame[:active.start])), 0)
            self.assertEqual(int(np.count_nonzero(frame[active.stop:])), 0)

            pause = animation.generate_frame(strip * 4.0 + 3.1, strip).pixels
            self.assertEqual(int(np.count_nonzero(pause)), 0)

    def test_semantic_style_marks_clearance_halo_without_hiding_strip(self):
        animation = self.make_animation(
            plant_aware=True,
            plant_clearance=1,
            plant_diagnostic_style="semantic",
        )

        frame = animation.generate_frame(4.1, 0).pixels
        clearance_pixel = self.foliage + 1

        np.testing.assert_array_equal(frame[clearance_pixel], (255, 144, 24))
        np.testing.assert_array_equal(frame[self.foliage], (42, 255, 96))
        np.testing.assert_array_equal(frame[self.globe], (255, 48, 220))

    def test_phase_cache_and_runtime_stats_are_stable(self):
        animation = self.make_animation(plant_aware=True)
        first = animation.generate_frame(4.1, 0)
        cached = animation.generate_frame(4.2, 1)
        next_phase = animation.generate_frame(5.1, 2)

        self.assertIsInstance(first, RenderedFrame)
        self.assertFalse(cached.changed)
        self.assertIs(first.pixels, cached.pixels)
        self.assertTrue(next_phase.changed)
        self.assertEqual(next_phase.dirty_ranges, ((6, 12),))

        stats = animation.get_runtime_stats()
        self.assertEqual(stats["plant_diagnostic_phase"], "foliage")
        self.assertEqual(stats["plant_foliage_pixels"], 1)
        self.assertEqual(stats["plant_globe_pixels"], 1)
        self.assertEqual(stats["plant_globe_regions"], 1)

    def test_disabling_live_restores_plain_white_strip(self):
        animation = self.make_animation(plant_aware=True)
        aware = animation.generate_frame(4.1, 0)
        self.assertFalse(
            np.array_equal(aware.pixels[self.foliage], (255, 255, 255))
        )

        animation.update_parameters({"plant_aware": False})
        plain = animation.generate_frame(4.2, 1)

        self.assertTrue(plain.changed)
        np.testing.assert_array_equal(plain.pixels[self.foliage], (255, 255, 255))
        self.assertEqual(
            animation.get_runtime_stats()["plant_diagnostic_phase"], "disabled"
        )


if __name__ == "__main__":
    unittest.main()
