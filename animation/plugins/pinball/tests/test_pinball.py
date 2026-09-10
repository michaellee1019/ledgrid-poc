"""Tests for the CPU-light portrait pinball animation."""

import hashlib
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

    def test_primary_gesture_is_queued_bounded_and_applied_once_on_a_source_tick(self):
        animation = PinballAnimation(PreviewLEDController(33, 138), {"seed": 95})
        animation.ball_x, animation.ball_y = 16.0, 60.0
        animation.ball_vx, animation.ball_vy = 3.0, -20.0
        animation.generate_frame(0.0, 0)
        original_velocity = (animation.ball_vx, animation.ball_vy)
        original_rng = animation.random.getstate()
        original_score = animation.score

        self.assertTrue(animation.handle_interaction("primary", 0.0, 60.0, 1.0))
        self.assertFalse(animation.handle_interaction("secondary", 0.0, 60.0, 1.0))
        self.assertFalse(animation.handle_interaction("primary", -1.0, 60.0, 1.0))
        self.assertFalse(animation.handle_interaction("primary", 0.0, 138.0, 1.0))
        self.assertFalse(animation.handle_interaction("primary", 0.0, 60.0, 0.0))
        self.assertTrue(animation.get_runtime_stats()["primary_interaction_pending"])

        # Cache invalidation creates a fresh frame without simulating a second
        # tick or consuming the queued flipper input early.
        invalidated = animation.generate_frame(0.0, 1)
        self.assertTrue(invalidated.changed)
        self.assertEqual((animation.ball_vx, animation.ball_vy), original_velocity)
        self.assertEqual(animation.random.getstate(), original_rng)
        self.assertEqual(animation.score, original_score)

        animation.generate_frame(0.02, 2)
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["primary_interactions_received"], 1)
        self.assertEqual(stats["primary_interactions_applied"], 1)
        self.assertFalse(stats["primary_interaction_pending"])
        self.assertLessEqual(np.hypot(animation.ball_vx, animation.ball_vy), animation.MAX_PRIMARY_SPEED)
        self.assertNotEqual((animation.ball_vx, animation.ball_vy), original_velocity)
        self.assertEqual(animation.random.getstate(), original_rng)
        self.assertEqual(animation.score, original_score)

    def test_primary_gesture_discards_a_burst_without_deferred_work(self):
        animation = PinballAnimation(PreviewLEDController(33, 138), {"seed": 95})
        animation.ball_x, animation.ball_y = 16.0, 60.0
        animation.ball_vx, animation.ball_vy = 0.0, -12.0
        animation.generate_frame(0.0, 0)
        for index in range(100):
            self.assertTrue(animation.handle_interaction("primary", float(index % 33), 60.0, 1.0))
        animation.generate_frame(0.02, 1)
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["primary_interactions_received"], 100)
        self.assertEqual(stats["primary_interactions_applied"], 1)
        self.assertFalse(stats["primary_interaction_pending"])
        self.assertLessEqual(np.hypot(animation.ball_vx, animation.ball_vy), animation.MAX_PRIMARY_SPEED)

    def test_no_input_sequence_preserves_frame_state_rng_and_cache_contract(self):
        animation = PinballAnimation(PreviewLEDController(33, 138), {"seed": 95})
        sequence = [animation.generate_frame(time, index) for index, time in enumerate((0.0, 0.005, 0.01, 0.02, 0.04, 0.08))]
        self.assertEqual([frame.changed for frame in sequence], [True, False, True, True, True, True])
        self.assertIs(sequence[1].pixels, sequence[0].pixels)
        digest = hashlib.sha256(b"".join(frame.pixels.tobytes() for frame in sequence)).hexdigest()
        self.assertEqual(digest, "7183645735087e899eef42604ce4605c0c72bf9f5b562b747abd84708e1d07a0")
        self.assertEqual(
            (
                animation.ball_x, animation.ball_y, animation.ball_vx,
                animation.ball_vy, animation.score, animation.streak,
                animation.multiplier, animation.balls, animation.mode,
                animation._sim_time, animation.last_elapsed,
                animation.last_render_elapsed,
            ),
            (28.244, 112.03493925, -7.0, -72.652, 95000, 0, 1, 3,
             "READY", 0.10800000000000001, 0.08, 0.08),
        )
        self.assertEqual(animation.score, 95000)
        self.assertEqual(animation.random.getstate(), PinballAnimation(PreviewLEDController(33, 138), {"seed": 95}).random.getstate())
        self.assertEqual(animation.get_runtime_stats()["primary_interactions_applied"], 0)

    def test_declares_the_narrow_local_primary_capability(self):
        self.assertEqual(PinballAnimation.INTERACTION_TYPES, frozenset(("primary",)))
        self.assertEqual(PinballAnimation.COMPOSER_INTERACTIONS["point"]["kind"], "primary")


if __name__ == "__main__":
    unittest.main()
