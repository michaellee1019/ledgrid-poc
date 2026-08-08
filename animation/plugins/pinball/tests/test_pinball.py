"""Tests for the CPU-light portrait pinball animation."""

import unittest

import numpy as np

from animation import RenderedFrame
from animation.core.manager import PreviewLEDController
from animation.plugins.pinball import PinballAnimation


class PinballAnimationTests(unittest.TestCase):
    def test_renders_canonical_frame_for_actual_grid(self):
        controller = PreviewLEDController(strips=32, leds_per_strip=140)
        animation = PinballAnimation(controller)

        result = animation.generate_frame(0.0, 0)

        self.assertIsInstance(result, RenderedFrame)
        self.assertEqual(result.pixels.shape, (4480, 3))
        self.assertEqual(result.pixels.dtype, np.uint8)
        self.assertTrue(result.changed)
        self.assertGreater(np.count_nonzero(result.pixels), 300)

    def test_adapts_to_hat_and_small_preview_layouts(self):
        for strips, leds in ((16, 140), (12, 40), (6, 20)):
            with self.subTest(strips=strips, leds=leds):
                animation = PinballAnimation(PreviewLEDController(strips, leds))
                result = animation.generate_frame(0.1, 1)
                self.assertEqual(result.pixels.shape, (strips * leds, 3))

    def test_render_cap_returns_cached_unchanged_frame(self):
        animation = PinballAnimation(
            PreviewLEDController(32, 140), {"render_fps": 100.0}
        )
        first = animation.generate_frame(0.0, 0)
        skipped = animation.generate_frame(0.005, 1)
        advanced = animation.generate_frame(0.02, 2)

        self.assertTrue(first.changed)
        self.assertFalse(skipped.changed)
        self.assertIs(skipped.pixels, first.pixels)
        self.assertTrue(advanced.changed)

    def test_defaults_to_100_fps_simulation(self):
        animation = PinballAnimation(PreviewLEDController(32, 140))

        self.assertEqual(animation.params["render_fps"], 100.0)
        self.assertEqual(
            animation.get_parameter_schema()["render_fps"]["default"],
            100.0,
        )

    def test_exact_100_hz_timestamps_are_not_dropped(self):
        animation = PinballAnimation(PreviewLEDController(32, 140))

        results = [animation.generate_frame(index / 100.0, index) for index in range(101)]

        self.assertTrue(all(result.changed for result in results))

    def test_bumper_hit_increments_score_and_streak(self):
        animation = PinballAnimation(PreviewLEDController(32, 140))
        bx, by = animation._bumper_positions()[0]
        animation.ball_x = bx + 0.2
        animation.ball_y = by
        animation.ball_vx = -5.0
        animation.ball_vy = 0.0
        original_score = animation.score

        animation.generate_frame(0.0, 0)
        animation.generate_frame(0.02, 1)

        self.assertGreater(animation.score, original_score)
        self.assertEqual(animation.streak, 1)
        self.assertGreater(len(animation.bursts), 0)

    def test_minigame_and_failure_have_visible_state(self):
        animation = PinballAnimation(PreviewLEDController(32, 140))
        animation._start_minigame()
        self.assertNotEqual(animation.mode, "READY")
        self.assertGreater(animation.multiplier, 1)

        animation._drain()
        self.assertGreater(animation.drain_time, 0)
        self.assertGreater(animation._failure_flash, 0)
        self.assertEqual(animation.streak, 0)


if __name__ == "__main__":
    unittest.main()
