import unittest

import numpy as np

from animation.plugins.spiral_single import SpiralSingleAnimation


class _Controller:
    strip_count = 4
    leds_per_strip = 5
    total_leds = strip_count * leds_per_strip


class SpiralSingleAnimationTests(unittest.TestCase):
    def test_generates_uint8_frames_with_one_lit_pixel(self):
        animation = SpiralSingleAnimation(_Controller())

        for frame_count in range(8):
            frame = animation.generate_frame(frame_count / 60.0, frame_count)

            self.assertIsInstance(frame, np.ndarray)
            self.assertEqual(frame.dtype, np.uint8)
            self.assertEqual(frame.shape, (_Controller.total_leds, 3))
            self.assertEqual(np.count_nonzero(np.any(frame != 0, axis=1)), 1)

    def test_alternates_reusable_buffers_without_mutating_current_frame(self):
        animation = SpiralSingleAnimation(_Controller())

        first = animation.generate_frame(0.0, 0)
        first_snapshot = first.copy()
        second = animation.generate_frame(1.0 / 60.0, 1)

        self.assertIsNot(first, second)
        np.testing.assert_array_equal(first, first_snapshot)

        third = animation.generate_frame(2.0 / 60.0, 2)
        self.assertIs(first, third)
        self.assertEqual(np.count_nonzero(np.any(third != 0, axis=1)), 1)


if __name__ == "__main__":
    unittest.main()
