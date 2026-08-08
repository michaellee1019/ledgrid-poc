import unittest

import numpy as np

from animation.libraries.spatial import normalized_axis_positions


class NormalizedAxisPositionsTests(unittest.TestCase):
    def test_axes_follow_canonical_flat_layout(self):
        horizontal = normalized_axis_positions(2, 3, "horizontal")
        vertical = normalized_axis_positions(2, 3, "vertical")
        diagonal = normalized_axis_positions(2, 3, "diagonal")

        np.testing.assert_array_equal(horizontal, [0, 0, 0, 1, 1, 1])
        np.testing.assert_array_equal(vertical, [1, 0.5, 0, 1, 0.5, 0])
        np.testing.assert_array_equal(diagonal, (horizontal + vertical) * 0.5)
        self.assertEqual(horizontal.dtype, np.float32)

    def test_cached_fields_are_shared_and_read_only(self):
        first = normalized_axis_positions(4, 5, "vertical")
        second = normalized_axis_positions(4, 5, "vertical")
        self.assertIs(first, second)
        self.assertFalse(first.flags.writeable)
        with self.assertRaises(ValueError):
            first[0] = 0.5

    def test_rejects_invalid_geometry_and_axis(self):
        with self.assertRaises(ValueError):
            normalized_axis_positions(0, 5, "vertical")
        with self.assertRaises(ValueError):
            normalized_axis_positions(4, 5, "radial")


if __name__ == "__main__":
    unittest.main()
