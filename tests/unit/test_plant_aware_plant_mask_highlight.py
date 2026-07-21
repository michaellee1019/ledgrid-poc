"""Deterministic plant-aware acceptance views for Plant Mask Highlight."""

import unittest

import numpy as np

from animation.core.mask_effects import mask_boundary
from animation.plugins.plant_mask_highlight import PlantMaskHighlightAnimation


class _Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


def _config(**overrides):
    return {
        "brightness": 1.0,
        "background_red": 0,
        "background_green": 0,
        "background_blue": 0,
        "pulse_speed": 0.0,
        "pulse_depth": 0.0,
        **overrides,
    }


class PlantAwarePlantMaskHighlightTests(unittest.TestCase):
    def test_disabled_mode_preserves_the_existing_calibration_frame_exactly(self):
        baseline = PlantMaskHighlightAnimation(_Controller(), _config())
        disabled = PlantMaskHighlightAnimation(
            _Controller(),
            _config(
                plant_aware=False,
                plant_clearance=4,
                plant_mask_path="/does/not/exist.json",
                plant_globe_mask_path="/also/does/not/exist.json",
            ),
        )

        self.assertFalse(baseline.get_parameter_schema()["plant_aware"]["default"])
        np.testing.assert_array_equal(
            baseline.generate_frame(0.0, 0),
            disabled.generate_frame(0.0, 0),
        )

    def test_clearance_view_marks_only_the_exterior_safety_halo(self):
        animation = PlantMaskHighlightAnimation(
            _Controller(), _config(plant_aware=True, plant_clearance=1)
        )
        masks = animation.get_plant_masks()
        frame = animation.generate_frame(0.0, 0)

        clearance_only = masks.clearance_flat & ~masks.obstacle_flat
        self.assertTrue(np.any(clearance_only))
        np.testing.assert_array_equal(frame[clearance_only][0], (255, 140, 24))
        np.testing.assert_array_equal(frame[masks.foliage_flat][0], (255, 64, 24))
        np.testing.assert_array_equal(frame[masks.globes_flat][0], (255, 0, 220))
        self.assertFalse(np.any(masks.foliage_flat & masks.globes_flat))

    def test_boundary_view_keeps_seven_globe_centers_and_hides_interiors(self):
        animation = PlantMaskHighlightAnimation(
            _Controller(),
            _config(plant_aware=True, plant_verification_view="boundaries"),
        )
        masks = animation.get_plant_masks()
        frame = animation.generate_frame(0.0, 0)
        lit = set(np.flatnonzero(np.any(frame != 0, axis=1)).tolist())

        foliage_boundary = set(
            np.flatnonzero(mask_boundary(masks.foliage).ravel()).tolist()
        )
        globe_boundary = set(
            np.flatnonzero(mask_boundary(masks.globes).ravel()).tolist()
        )
        centers = set().union(*animation.globe_region_centers.values())
        expected = foliage_boundary | globe_boundary | (centers & animation.globe_indices)

        self.assertEqual(len(animation.globe_region_centers), 7)
        self.assertEqual(lit, expected)
        self.assertTrue((set(animation.mask_indices) - foliage_boundary).isdisjoint(lit))

    def test_runtime_exposes_semantic_acceptance_gates(self):
        animation = PlantMaskHighlightAnimation(
            _Controller(), _config(plant_aware=True)
        )

        stats = animation.get_runtime_stats()

        self.assertTrue(stats["plant_aware"])
        self.assertEqual(stats["plant_foliage_pixels"], 504)
        self.assertEqual(stats["plant_globe_pixels"], 356)
        self.assertEqual(stats["plant_globe_regions"], 7)
        self.assertEqual(stats["plant_input_overlap_pixels"], 0)
        self.assertTrue(stats["plant_wall_geometry_valid"])
        self.assertTrue(stats["plant_globe_semantics_valid"])
        self.assertTrue(stats["plant_semantics_ready"])
        self.assertEqual(stats["plant_mask_error"], "")


if __name__ == "__main__":
    unittest.main()
