"""Sparkle unique-frame cadence at Scene v2 phase time."""
import unittest

from animation.core.manager import PreviewLEDController
from animation.plugins.sparkle import SparkleAnimation


class SparkleCadenceTests(unittest.TestCase):
    def test_unique_frames_follow_high_rate_source_ticks(self):
        animation = SparkleAnimation(PreviewLEDController(8, 12))
        hz = SparkleAnimation.SIMULATION_HZ
        self.assertEqual(hz, 120.0)
        first = animation.generate_frame(0.0, 0)
        self.assertTrue(first.changed)
        self.assertFalse(animation.generate_frame(0.5 / hz, 1).changed)
        nxt = animation.generate_frame(1.0 / hz, 2)
        self.assertTrue(nxt.changed)
        self.assertEqual(animation.cadence_snapshot()["simulation_hz"], hz)
        self.assertEqual(animation.cadence_snapshot()["tick"], 1)


if __name__ == "__main__":
    unittest.main()
