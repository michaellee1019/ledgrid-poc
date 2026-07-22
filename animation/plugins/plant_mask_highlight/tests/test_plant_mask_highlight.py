import unittest

import numpy as np

from animation.plugins.plant_mask_highlight import PlantMaskHighlightAnimation


class _Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class PlantMaskHighlightAnimationTests(unittest.TestCase):
    def test_default_masks_render_verified_foliage_and_seven_globes(self):
        animation = PlantMaskHighlightAnimation(
            _Controller(),
            {
                "brightness": 1.0,
                "background_red": 0,
                "background_green": 0,
                "background_blue": 0,
                "plant_red": 255,
                "plant_green": 255,
                "plant_blue": 255,
                "globe_red": 255,
                "globe_green": 0,
                "globe_blue": 220,
                "pulse_speed": 0.0,
                "pulse_depth": 0.0,
            },
        )

        self.assertEqual(len(animation.mask_indices), 504)
        self.assertEqual(len(animation.globe_indices), 356)
        self.assertEqual(animation.globe_region_count, 7)
        self.assertEqual(
            animation.get_runtime_stats()["mask_path"],
            "config/plant_pixel_map_32x138.json",
        )

        frame = animation.generate_frame(0.0, 0)
        lit = set(np.flatnonzero(np.any(frame != 0, axis=1)).tolist())
        self.assertEqual(lit, animation.mask_indices | animation.globe_indices)
        globe_indices = np.fromiter(animation.globe_indices, dtype=np.intp)
        np.testing.assert_array_equal(
            frame[globe_indices],
            np.tile((255, 0, 220), (len(globe_indices), 1)),
        )

    def test_single_globe_outline_and_center_calibration_mode(self):
        animation = PlantMaskHighlightAnimation(
            _Controller(),
            {
                "brightness": 1.0,
                "background_red": 0,
                "background_green": 0,
                "background_blue": 0,
                "show_foliage": False,
                "globe_region": "top_left",
                "globe_outline_only": True,
                "globe_center_marker": True,
                "pulse_depth": 0.0,
            },
        )
        frame = animation.generate_frame(0.0, 0)
        lit = set(np.flatnonzero(np.any(frame != 0, axis=1)).tolist())
        self.assertTrue(animation.globe_region_centers["top_left"] <= lit)
        self.assertTrue(lit < animation.globe_region_indices["top_left"])
        self.assertTrue(lit.isdisjoint(animation.mask_indices))


if __name__ == "__main__":
    unittest.main()
