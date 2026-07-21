import unittest

import numpy as np

from animation.core.base import RenderedFrame
from animation.plugins.ascii_drop import AsciiDropAnimation
from animation.plugins.gradient import GradientAnimation
from animation.plugins.pixel_chase import PixelChaseAnimation
from animation.plugins.wave import WaveAnimation


class _Controller:
    strip_count = 8
    leds_per_strip = 14
    total_leds = strip_count * leds_per_strip


def _pixels(output):
    return output.pixels if isinstance(output, RenderedFrame) else output


class AsciiDropAnimationTests(unittest.TestCase):
    def test_uses_wall_orientation_and_skips_identical_render_steps(self):
        animation = AsciiDropAnimation(_Controller(), {
            "phrase": "A",
            "drop_speed": 10.0,
            "spawn_rate": 0.1,
            "random_seed": 4,
            "character_red": 255,
            "character_green": 0,
            "character_blue": 0,
            "background_blue": 0,
        })

        first = animation.generate_frame(0.0, 0)
        frame = _pixels(first)
        self.assertEqual(frame.shape, (_Controller.total_leds, 3))
        self.assertEqual(frame.dtype, np.uint8)

        # A starts at the visible top edge. Physical LED height-1 is the top.
        wall = frame.reshape(_Controller.strip_count, _Controller.leds_per_strip, 3)
        self.assertGreater(np.count_nonzero(wall[:, -1]), 0)
        self.assertEqual(np.count_nonzero(wall[:, 0]), 0)

        unchanged = animation.generate_frame(0.01, 1)
        self.assertFalse(unchanged.changed)
        self.assertIs(unchanged.pixels, frame)

    def test_character_eventually_settles(self):
        animation = AsciiDropAnimation(_Controller(), {
            "phrase": "A",
            "drop_speed": 40.0,
            "spawn_rate": 0.1,
            "random_seed": 1,
        })
        animation.generate_frame(0.0, 0)
        for index, timestamp in enumerate(np.arange(0.25, 2.25, 0.25), start=1):
            animation.generate_frame(float(timestamp), index)
        self.assertGreater(animation.get_runtime_stats()["settled_pixels"], 0)

    def test_clears_when_a_blocked_character_cannot_settle_any_pixels(self):
        animation = AsciiDropAnimation(_Controller(), {
            "phrase": "A",
            "drop_speed": 40.0,
            "spawn_rate": 0.1,
            "random_seed": 1,
        })
        animation._settled[:6, :] = True
        animation._settled_revision += 1

        animation.generate_frame(0.0, 0)
        animation.generate_frame(0.25, 1)

        self.assertEqual(animation.get_runtime_stats()["settled_pixels"], 0)
        self.assertEqual(animation.get_runtime_stats()["falling_characters"], 0)


class GradientAnimationTests(unittest.TestCase):
    def test_static_vertical_gradient_maps_top_to_first_color(self):
        animation = GradientAnimation(_Controller(), {
            "color1_red": 255, "color1_green": 0, "color1_blue": 0,
            "color2_red": 0, "color2_green": 0, "color2_blue": 255,
            "direction": "vertical",
        })
        first = animation.generate_frame(0.0, 0)
        wall = first.pixels.reshape(_Controller.strip_count, _Controller.leds_per_strip, 3)
        np.testing.assert_array_equal(wall[:, -1], np.tile((255, 0, 0), (_Controller.strip_count, 1)))
        np.testing.assert_array_equal(wall[:, 0], np.tile((0, 0, 255), (_Controller.strip_count, 1)))

        second = animation.generate_frame(1.0, 1)
        self.assertFalse(second.changed)
        self.assertIs(second.pixels, first.pixels)

    def test_animated_gradient_changes_over_time(self):
        animation = GradientAnimation(_Controller(), {"animated": True, "speed": 0.5})
        first = animation.generate_frame(0.0, 0).pixels.copy()
        second = animation.generate_frame(0.5, 1).pixels
        self.assertFalse(np.array_equal(first, second))


class PixelChaseAnimationTests(unittest.TestCase):
    def test_visits_pixels_in_visible_top_to_bottom_order(self):
        rate = 10.0
        animation = PixelChaseAnimation(_Controller(), {"pixels_per_second": rate})
        first = animation.generate_frame(0.0, 0)
        second = animation.generate_frame(1.0 / rate, 1)

        self.assertEqual(np.flatnonzero(np.any(first.pixels != 0, axis=1)).tolist(), [_Controller.leds_per_strip - 1])
        self.assertEqual(np.flatnonzero(np.any(second.pixels != 0, axis=1)).tolist(), [_Controller.leds_per_strip - 2])
        self.assertEqual(second.dirty_ranges, ((_Controller.leds_per_strip - 2, _Controller.leds_per_strip - 1), (_Controller.leds_per_strip - 1, _Controller.leds_per_strip)))

        unchanged = animation.generate_frame(1.0 / rate, 2)
        self.assertFalse(unchanged.changed)


class WaveAnimationTests(unittest.TestCase):
    def test_generates_bounded_reusable_frames_that_move_over_time(self):
        animation = WaveAnimation(_Controller(), {"axis": "diagonal", "speed": 0.5})
        first = animation.generate_frame(0.0, 0)
        first_snapshot = first.copy()
        second = animation.generate_frame(0.5, 1)

        self.assertEqual(first.dtype, np.uint8)
        self.assertEqual(first.shape, (_Controller.total_leds, 3))
        self.assertFalse(np.array_equal(first, second))
        np.testing.assert_array_equal(first, first_snapshot)
        self.assertGreaterEqual(int(second.min()), 0)
        self.assertLessEqual(int(second.max()), 255)


if __name__ == "__main__":
    unittest.main()
