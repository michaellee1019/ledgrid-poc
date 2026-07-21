import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.plugins.world_flags import WorldFlagsAnimation


class _Controller:
    strip_count = 32
    leds_per_strip = 80
    total_leds = strip_count * leds_per_strip


class PlantAwareWorldFlagsTests(unittest.TestCase):
    def _mask_file(self, covered_indices, **extra):
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        json.dump({"covered_indices": list(covered_indices), **extra}, handle)
        handle.close()
        return handle.name

    def test_default_and_explicit_false_have_identical_pixels(self):
        common = {
            "map_mode": "off",
            "flip_vertical": False,
            "display_mode": "parade",
        }
        default = WorldFlagsAnimation(_Controller(), common)
        disabled = WorldFlagsAnimation(_Controller(), {**common, "plant_aware": False})

        np.testing.assert_array_equal(
            default.generate_frame(3.25, 17),
            disabled.generate_frame(3.25, 17),
        )

    def test_single_flag_moves_away_from_foliage_and_globe_landmarks(self):
        # The legacy 21-pixel flag is centered at rows 29..49. Cover that entire
        # location so an information-aware placement has an unambiguous safe bay.
        center = {
            strip * _Controller.leds_per_strip + led
            for strip in range(_Controller.strip_count)
            for led in range(29, 50)
        }
        globe = {
            strip * _Controller.leds_per_strip + led
            for strip in range(12, 20)
            for led in range(34, 42)
        }
        foliage_path = self._mask_file(center - globe)
        globe_path = self._mask_file(globe, region_count=1)
        common = {
            "display_mode": "single",
            "country": "JPN",
            "map_mode": "off",
            "flip_vertical": False,
            "plant_clearance": 0,
            "plant_mask_path": foliage_path,
            "plant_globe_mask_path": globe_path,
        }
        legacy = WorldFlagsAnimation(_Controller(), common)
        aware = WorldFlagsAnimation(_Controller(), {**common, "plant_aware": True})

        legacy_frame = legacy.generate_frame(0.0, 0).reshape((32, 80, 3))
        aware_frame = aware.generate_frame(0.0, 0).reshape((32, 80, 3))
        mask = np.zeros((32, 80), dtype=bool)
        for index in center:
            mask[index // 80, index % 80] = True

        self.assertGreater(np.count_nonzero(np.any(legacy_frame[mask] != 0, axis=1)), 600)
        self.assertEqual(np.count_nonzero(aware_frame[mask]), 0)
        self.assertGreater(np.count_nonzero(aware_frame), 0)
        stats = aware.get_runtime_stats()
        self.assertEqual(stats["plant_banner_overlap"], 0)
        self.assertEqual(stats["plant_globe_pixels"], len(globe))

    def test_plant_controls_are_exposed_in_schema(self):
        schema = WorldFlagsAnimation(_Controller(), {"map_mode": "off"}).get_parameter_schema()
        self.assertFalse(schema["plant_aware"]["default"])
        self.assertIn("plant_clearance", schema)
        self.assertIn("plant_mask_path", schema)
        self.assertIn("plant_globe_mask_path", schema)

    def test_parade_shrinks_and_shifts_a_banner_out_of_a_blocked_slot(self):
        blocked = {
            strip * _Controller.leds_per_strip + led
            for strip in range(_Controller.strip_count)
            for led in range(0, 11)
        }
        foliage_path = self._mask_file(blocked)
        globe_path = self._mask_file(())
        common = {
            "display_mode": "parade",
            "speed": 0.0,
            "gap": 3,
            "map_mode": "off",
            "flip_vertical": False,
            "plant_clearance": 0,
            "plant_mask_path": foliage_path,
            "plant_globe_mask_path": globe_path,
        }
        legacy = WorldFlagsAnimation(_Controller(), common)
        aware = WorldFlagsAnimation(_Controller(), {**common, "plant_aware": True})

        legacy_frame = legacy.generate_frame(0.0, 0).reshape((32, 80, 3))
        aware_frame = aware.generate_frame(0.0, 0).reshape((32, 80, 3))
        blocked_mask = np.zeros((32, 80), dtype=bool)
        blocked_mask[:, :11] = True
        legacy_overlap = np.count_nonzero(np.any(legacy_frame[blocked_mask] != 0, axis=1))
        aware_overlap = np.count_nonzero(np.any(aware_frame[blocked_mask] != 0, axis=1))

        self.assertGreater(legacy_overlap, 300)
        self.assertLess(aware_overlap, legacy_overlap)
        self.assertLess(aware.get_runtime_stats()["plant_banner_overlap"], legacy_overlap)


if __name__ == "__main__":
    unittest.main()
