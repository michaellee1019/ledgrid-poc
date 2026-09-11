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
        self.assertEqual(frame.pixels.shape, (4480, 4))
        self.assertEqual(frame.pixels.dtype, np.uint8)
        self.assertGreater(len(np.unique(frame.pixels, axis=0)), 4)
        self.assertGreater(np.count_nonzero(frame.pixels[:, 3] == 0), 0)

    def test_single_japan_centers_red_disc(self):
        animation = WorldFlagsAnimation(
            _Controller(),
            {"display_mode": "single", "country": "JPN", "map_mode": "off", "flip_vertical": False},
        )
        frame = animation.generate_frame(0.0, 0).pixels.reshape((32, 140, 4))
        center = frame[16, 70]
        self.assertGreater(int(center[0]), 150)
        self.assertLess(int(center[1]), 80)

    def test_unknown_country_is_rejected_at_the_current_contract(self):
        with self.assertRaisesRegex(ValueError, "supported ISO code"):
            WorldFlagsAnimation(
                _Controller(),
                {"display_mode": "single", "country": "???"},
            )


if __name__ == "__main__":
    unittest.main()
