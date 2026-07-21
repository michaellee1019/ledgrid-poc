import unittest

import numpy as np

from animation.core.mask_effects import build_halo_weights, dilate_8, logical_mask


class MaskEffectsTests(unittest.TestCase):
    def test_corner_dilation_does_not_wrap(self):
        mask = logical_mask([0], strip_count=3, leds_per_strip=3)
        dilated = dilate_8(mask)
        expected = np.array(
            [[True, True, False], [True, True, False], [False, False, False]]
        )
        np.testing.assert_array_equal(dilated, expected)

    def test_halo_has_weighted_rings_and_excludes_core(self):
        core, halo = build_halo_weights([12], 5, 5, radius=2, falloff=1.0)
        self.assertTrue(core[12])
        self.assertEqual(float(halo[12]), 0.0)
        self.assertGreater(float(halo[11]), float(halo[10]))
        self.assertGreater(float(halo[10]), 0.0)


if __name__ == "__main__":
    unittest.main()
