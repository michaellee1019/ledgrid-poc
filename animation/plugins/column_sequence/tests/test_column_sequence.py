"""Column Sequence mapping diagnostic cadence."""
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.column_sequence import ColumnSequenceAnimation


class ColumnSequenceTests(unittest.TestCase):
    def test_one_column_white_then_black_then_next_column(self):
        animation = ColumnSequenceAnimation(PreviewLEDController(4, 6), {"white_level": 1.0})
        on_first = animation.generate_frame(0.5, 0)
        self.assertTrue(on_first.changed)
        self.assertTrue(np.all(on_first.pixels[0:6] == 255))
        self.assertTrue(np.all(on_first.pixels[6:] == 0))
        self.assertFalse(animation.generate_frame(0.9, 1).changed)

        black = animation.generate_frame(1.5, 2)
        self.assertTrue(black.changed)
        self.assertTrue(np.all(black.pixels == 0))

        on_second = animation.generate_frame(2.5, 3)
        self.assertTrue(on_second.changed)
        self.assertTrue(np.all(on_second.pixels[6:12] == 255))
        self.assertTrue(np.all(on_second.pixels[0:6] == 0))
        self.assertTrue(np.all(on_second.pixels[12:] == 0))
        self.assertEqual(animation.cadence_snapshot()["active_column"], 1)
