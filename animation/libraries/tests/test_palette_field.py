import unittest

import numpy as np

from animation.libraries.palette_field import AnimatedPaletteField


class AnimatedPaletteFieldTests(unittest.TestCase):
    def setUp(self):
        values = np.arange(256, dtype=np.uint8)
        self.palette = np.column_stack((values, values, values))

    def test_render_is_deterministic_and_supports_reused_output(self):
        field = AnimatedPaletteField(4, 6, self.palette)
        first = field.render(0.25).copy()
        out = np.empty((6, 4, 3), dtype=np.uint8)
        returned = field.render(0.25, out=out)

        self.assertIs(returned, out)
        np.testing.assert_array_equal(first, out)
        self.assertEqual(out.dtype, np.uint8)

    def test_time_advances_palette_indices(self):
        field = AnimatedPaletteField(3, 5, self.palette)
        first = field.render(0.0).copy()
        second = field.render(0.01).copy()
        self.assertFalse(np.array_equal(first, second))

    def test_validates_palette_and_output_contracts(self):
        with self.assertRaises(ValueError):
            AnimatedPaletteField(3, 5, np.zeros((16, 3), dtype=np.uint8))

        field = AnimatedPaletteField(3, 5, self.palette)
        with self.assertRaises(ValueError):
            field.render(0.0, out=np.empty((5, 3, 3), dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
