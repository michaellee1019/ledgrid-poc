import unittest

import numpy as np

from animation.plugins.world_flags import WorldFlagsAnimation


class _Controller:
    strip_count = 32
    leds_per_strip = 140
    total_leds = strip_count * leds_per_strip


class WorldFlagsTests(unittest.TestCase):
    def test_parade_frame_has_expected_geometry_and_colors(self):
        animation = WorldFlagsAnimation(_Controller(), {"map_mode": "off", "flip_vertical": False})
        frame = animation.generate_frame(0.0, 0)
        self.assertEqual(frame.shape, (4480, 3))
        self.assertEqual(frame.dtype, np.uint8)
        self.assertGreater(len(np.unique(frame, axis=0)), 4)

    def test_single_japan_centers_red_disc(self):
        animation = WorldFlagsAnimation(
            _Controller(),
            {"display_mode": "single", "country": "JPN", "map_mode": "off", "flip_vertical": False},
        )
        frame = animation.generate_frame(0.0, 0).reshape((32, 140, 3))
        center = frame[16, 70]
        self.assertGreater(int(center[0]), 150)
        self.assertLess(int(center[1]), 80)

    def test_unknown_country_falls_back_to_first_flag(self):
        animation = WorldFlagsAnimation(
            _Controller(),
            {"display_mode": "single", "country": "???", "map_mode": "off", "flip_vertical": False},
        )
        frame = animation.generate_frame(0.0, 0)
        self.assertTrue(np.any(frame != 0))


if __name__ == "__main__":
    unittest.main()
