"""Deterministic coverage for Plant Calibration's opt-in semantic stages."""

import unittest

import numpy as np

from animation.plugins.plant_calibration import PlantCalibrationAnimation


class _PlantWallController:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _BaselineController:
    strip_count = 32
    leds_per_strip = 140
    total_leds = strip_count * leds_per_strip


class PlantAwarePlantCalibrationTests(unittest.TestCase):
    def test_disabled_mode_preserves_the_exact_six_stage_workflow(self):
        common = {"brightness": 0.63, "plant_clearance": 4}
        default = PlantCalibrationAnimation(_PlantWallController(), common)
        explicitly_disabled = PlantCalibrationAnimation(
            _PlantWallController(),
            {
                **common,
                "plant_aware": False,
                "plant_mask_path": "/does/not/exist.json",
                "plant_globe_mask_path": "/also/does/not/exist.json",
            },
        )

        self.assertEqual(
            default._pattern_names,
            PlantCalibrationAnimation.PATTERN_SEQUENCE_LABELS,
        )
        self.assertEqual(default.get_parameter_schema()["manual_pattern_index"]["max"], 5)
        self.assertFalse(default.get_parameter_schema()["plant_aware"]["default"])
        self.assertEqual(default.get_parameter_schema()["plant_clearance"]["default"], 1)
        self.assertEqual(len(default._pattern_frames), 6)
        for baseline, disabled in zip(
            default._pattern_frames, explicitly_disabled._pattern_frames
        ):
            np.testing.assert_array_equal(baseline, disabled)

    def test_enabled_mode_appends_foliage_globe_and_combined_clearance_stages(self):
        animation = PlantCalibrationAnimation(
            _PlantWallController(),
            {"plant_aware": True, "plant_clearance": 1, "brightness": 1.0},
        )

        self.assertEqual(
            animation._pattern_names,
            PlantCalibrationAnimation.PATTERN_SEQUENCE_LABELS
            + PlantCalibrationAnimation.PLANT_PATTERN_LABELS,
        )
        self.assertEqual(animation.get_parameter_schema()["manual_pattern_index"]["max"], 8)

        masks = animation.get_plant_masks()
        foliage, globes, combined = animation._pattern_frames[-3:]
        self.assertEqual(np.count_nonzero(np.any(foliage != 0, axis=1)), 504)
        self.assertEqual(np.count_nonzero(np.any(globes != 0, axis=1)), 356)
        self.assertEqual(np.count_nonzero(np.any(combined != 0, axis=1)), int(masks.clearance_flat.sum()))
        np.testing.assert_array_equal(foliage[masks.foliage_flat][0], (40, 255, 90))
        np.testing.assert_array_equal(globes[masks.globes_flat][0], (255, 48, 220))
        np.testing.assert_array_equal(combined[masks.foliage_flat][0], (40, 255, 90))
        np.testing.assert_array_equal(combined[masks.globes_flat][0], (255, 48, 220))

        clearance_only = masks.clearance_flat & ~masks.obstacle_flat
        self.assertTrue(np.any(clearance_only))
        np.testing.assert_array_equal(combined[clearance_only][0], (255, 140, 24))

    def test_runtime_reports_fixed_wall_and_seven_globe_semantics(self):
        animation = PlantCalibrationAnimation(
            _PlantWallController(),
            {"plant_aware": True, "manual_pattern_index": 7},
        )

        stats = animation.get_runtime_stats()

        self.assertEqual(stats["current_pattern_name"], "globe_verification")
        self.assertTrue(stats["plant_wall_geometry_valid"])
        self.assertEqual(stats["plant_foliage_pixels"], 504)
        self.assertEqual(stats["plant_globe_pixels"], 356)
        self.assertEqual(stats["plant_globe_regions"], 7)
        self.assertTrue(stats["plant_globe_semantics_valid"])
        self.assertTrue(stats["plant_semantics_ready"])
        self.assertEqual(stats["plant_mask_error"], "")

    def test_non_production_height_is_visible_as_not_ready(self):
        animation = PlantCalibrationAnimation(
            _BaselineController(), {"plant_aware": True}
        )

        stats = animation.get_runtime_stats()

        self.assertFalse(stats["plant_wall_geometry_valid"])
        self.assertFalse(stats["plant_semantics_ready"])

    def test_live_toggle_rebuilds_only_the_opt_in_stage_family(self):
        animation = PlantCalibrationAnimation(_PlantWallController())
        original_frames = [frame.copy() for frame in animation._pattern_frames]

        animation.update_parameters({"plant_aware": True})

        self.assertEqual(len(animation._pattern_frames), 9)
        for original, rebuilt in zip(original_frames, animation._pattern_frames[:6]):
            np.testing.assert_array_equal(original, rebuilt)

        animation.update_parameters({"plant_aware": False})
        self.assertEqual(len(animation._pattern_frames), 6)
        self.assertEqual(animation._pattern_names, animation.PATTERN_SEQUENCE_LABELS)


if __name__ == "__main__":
    unittest.main()
