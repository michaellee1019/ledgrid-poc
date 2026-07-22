"""Focused behavior and frame-contract tests for the clock animation."""

from datetime import datetime, timezone
import unittest

import numpy as np

from animation.core.base import RenderedFrame
from animation.plugins.clock import ClockAnimation


class _Controller:
    strip_count = 32
    leds_per_strip = 140
    total_leds = strip_count * leds_per_strip
    debug = False


class _FixedClock(ClockAnimation):
    fixed_now = datetime(2026, 7, 21, 13, 47, 36, tzinfo=timezone.utc)

    def _clock_now(self):
        return self.fixed_now


class ClockAnimationTests(unittest.TestCase):
    def test_schema_exposes_composable_faces_backgrounds_and_palettes(self):
        animation = _FixedClock(_Controller())
        schema = animation.get_parameter_schema()

        self.assertGreaterEqual(len(schema["face"]["options"]), 10)
        self.assertGreaterEqual(len(schema["background"]["options"]), 8)
        self.assertGreaterEqual(len(schema["palette"]["options"]), 10)
        self.assertIn("clock_offset_minutes", schema)
        self.assertIn("format_24h", schema)

    def test_every_face_returns_a_visible_canonical_frame(self):
        fingerprints = set()
        for face in ClockAnimation.FACE_OPTIONS:
            with self.subTest(face=face):
                animation = _FixedClock(_Controller(), {
                    "face": face, "background": "solid", "palette": "ice",
                })
                rendered = animation.generate_frame(1.25, 0)
                self.assertIsInstance(rendered, RenderedFrame)
                self.assertEqual(rendered.pixels.shape, (_Controller.total_leds, 3))
                self.assertEqual(rendered.pixels.dtype, np.uint8)
                self.assertTrue(rendered.pixels.flags.c_contiguous)
                self.assertGreater(int(rendered.pixels.max()), 0)
                fingerprints.add(rendered.pixels.tobytes())

        self.assertEqual(len(fingerprints), len(ClockAnimation.FACE_OPTIONS))

    def test_every_background_returns_a_distinct_visible_frame(self):
        fingerprints = set()
        for background in ClockAnimation.BACKGROUND_OPTIONS:
            with self.subTest(background=background):
                animation = _FixedClock(_Controller(), {
                    "face": "minimal", "background": background,
                    "palette": "neon", "density": 0.72,
                })
                rendered = animation.generate_frame(2.0, 0)
                self.assertGreater(int(rendered.pixels.max()), 0)
                fingerprints.add(rendered.pixels.tobytes())

        self.assertEqual(len(fingerprints), len(ClockAnimation.BACKGROUND_OPTIONS))

    def test_digital_face_is_upright_in_wall_coordinates(self):
        animation = _FixedClock(_Controller(), {
            "face": "digital", "background": "solid", "palette": "mono",
            "format_24h": True, "show_seconds": False, "glow": 0.0,
        })
        rendered = animation.generate_frame(0.0, 0)
        physical = rendered.pixels.reshape(
            _Controller.strip_count, _Controller.leds_per_strip, 3
        )
        visual = physical[:, ::-1, :]

        text = "13:47"
        x = (_Controller.strip_count - animation._text_width(text)) // 2
        y = animation._center_y() - 3
        lit = np.any(visual > 0, axis=2)

        # The first glyph is 1: its top row has only the center pixel, while
        # its bottom row is a three-pixel base. Reversing the wall geometry
        # makes this assertion fail even though the frame remains non-empty.
        np.testing.assert_array_equal(lit[x:x + 3, y], (False, True, False))
        np.testing.assert_array_equal(lit[x:x + 3, y + 4], (True, True, True))

    def test_non_animated_clock_reuses_frame_inside_same_second(self):
        animation = _FixedClock(_Controller(), {
            "face": "digital", "background": "solid", "show_seconds": True,
        })
        first = animation.generate_frame(0.1, 0)
        second = animation.generate_frame(0.8, 1)

        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertIs(first.pixels, second.pixels)

    def test_animated_background_has_a_bounded_twelve_hz_source_rate(self):
        animation = _FixedClock(_Controller(), {"background": "aurora"})
        first = animation.generate_frame(1.001, 0)
        duplicate = animation.generate_frame(1.04, 1)
        advanced = animation.generate_frame(1.09, 2)

        self.assertTrue(first.changed)
        self.assertFalse(duplicate.changed)
        self.assertTrue(advanced.changed)


if __name__ == "__main__":
    unittest.main()
