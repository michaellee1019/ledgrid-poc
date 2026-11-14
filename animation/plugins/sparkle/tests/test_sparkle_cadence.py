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

    def test_fast_scene_drops_excess_steps_without_replaying_them_when_frozen(self):
        wall = PreviewLEDController(33, 138)
        control, fast = SparkleAnimation(wall), SparkleAnimation(wall)
        control.generate_frame(0, 0)
        fast.generate_frame(0, 0)
        control.generate_frame((fast.MAX_CATCH_UP_TICKS + .1) / fast.SIMULATION_HZ, 1)
        fast.generate_frame(250, 1)
        self.assertEqual(fast.cadence_snapshot()["tick"], 30_000)
        self.assertEqual(fast.semantic_snapshot()["spark"], control.semantic_snapshot()["spark"])
        self.assertEqual(fast._rng.bit_generator.state, control._rng.bit_generator.state)
        self.assertFalse(fast.generate_frame(250, 2).changed)



if __name__ == "__main__":
    unittest.main()
