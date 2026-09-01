"""Focused Scene v2 Widget placement and lifecycle contracts."""

from __future__ import annotations

import unittest

import numpy as np

from animation.core.compositing import OverlayFrame
from animation.core.widget_placement import resolve_widget_placement


def _frame(*points: tuple[int, int], strips: int = 5, leds: int = 8) -> OverlayFrame:
    pixels = np.zeros((strips * leds, 4), dtype=np.uint8)
    for strip, led in points:
        pixels[strip * leds + led] = (30, 40, 50, 255)
    return OverlayFrame(pixels, revision=1)


class WidgetPlacementTests(unittest.TestCase):
    def test_auto_uses_clearance_safe_candidate_with_deterministic_tie_break(self) -> None:
        frame = _frame((2, 3), (2, 4))
        safe = np.ones(5 * 8, dtype=np.bool_)
        # The unshifted clock footprint is obstructed. Several nearest cells
        # are clear; the stable score picks the lower strip translation first.
        safe[2 * 8 + 3:2 * 8 + 5] = False
        resolved = resolve_widget_placement(
            {"mode": "auto"}, frame, strip_count=5, leds_per_strip=8, safe_flat=safe,
        )
        self.assertEqual((resolved.strip_translation, resolved.led_translation), (-1, 0))
        self.assertFalse(resolved.used_fallback)
        self.assertIsNone(resolved.warning)

    def test_auto_falls_back_to_least_overlap_with_a_perceivable_warning(self) -> None:
        frame = _frame((2, 3), (2, 4))
        safe = np.zeros(5 * 8, dtype=np.bool_)
        first = resolve_widget_placement(
            {"mode": "auto"}, frame, strip_count=5, leds_per_strip=8, safe_flat=safe,
        )
        second = resolve_widget_placement(
            {"mode": "auto"}, frame, strip_count=5, leds_per_strip=8, safe_flat=safe,
        )
        self.assertEqual(first, second)
        self.assertTrue(first.used_fallback)
        self.assertIn("least-overlapping", first.warning or "")

    def test_auto_respects_reserved_widgets_and_warns_when_no_space_remains(self) -> None:
        frame = _frame((2, 3))
        safe = np.ones(5 * 8, dtype=np.bool_)
        reserved = np.zeros(5 * 8, dtype=np.bool_)
        reserved[2 * 8 + 3] = True
        moved = resolve_widget_placement(
            {"mode": "auto"}, frame, strip_count=5, leds_per_strip=8,
            safe_flat=safe, reserved_flat=reserved,
        )
        self.assertEqual((moved.strip_translation, moved.led_translation), (-1, 0))
        self.assertFalse(moved.used_fallback)

        fully_reserved = np.ones(5 * 8, dtype=np.bool_)
        first = resolve_widget_placement(
            {"mode": "auto"}, frame, strip_count=5, leds_per_strip=8,
            safe_flat=safe, reserved_flat=fully_reserved,
        )
        second = resolve_widget_placement(
            {"mode": "auto"}, frame, strip_count=5, leds_per_strip=8,
            safe_flat=safe, reserved_flat=fully_reserved,
        )
        self.assertEqual(first, second)
        self.assertTrue(first.used_fallback)
        self.assertEqual(first.widget_overlap_pixels, 1)
        self.assertIn("earlier Widget", first.warning or "")

    def test_direct_nudge_is_retained_or_clamped_with_a_warning(self) -> None:
        frame = _frame((0, 0), (1, 1))
        safe = np.ones(5 * 8, dtype=np.bool_)
        retained = resolve_widget_placement(
            {"mode": "manual", "strip_translation": 2, "led_translation": 3},
            frame, strip_count=5, leds_per_strip=8, safe_flat=safe,
        )
        self.assertEqual((retained.strip_translation, retained.led_translation), (2, 3))
        self.assertFalse(retained.clamped)
        clamped = resolve_widget_placement(
            {"mode": "manual", "strip_translation": 99, "led_translation": -99},
            frame, strip_count=5, leds_per_strip=8, safe_flat=safe,
        )
        self.assertEqual((clamped.strip_translation, clamped.led_translation), (3, 0))
        self.assertTrue(clamped.clamped)
        self.assertIn("clamped", clamped.warning or "")

        safe[3 * 8 + 3] = False
        warned = resolve_widget_placement(
            {"mode": "manual", "strip_translation": 3, "led_translation": 3},
            frame, strip_count=5, leds_per_strip=8, safe_flat=safe,
        )
        self.assertEqual(warned.overlap_pixels, 1)
        self.assertIn("clearance", warned.warning or "")

        reserved = np.zeros(5 * 8, dtype=np.bool_)
        reserved[3 * 8 + 3] = True
        widget_warned = resolve_widget_placement(
            {"mode": "manual", "strip_translation": 3, "led_translation": 3},
            frame, strip_count=5, leds_per_strip=8, safe_flat=np.ones(5 * 8, dtype=np.bool_),
            reserved_flat=reserved,
        )
        self.assertEqual(widget_warned.widget_overlap_pixels, 1)
        self.assertIn("earlier Widget", widget_warned.warning or "")


if __name__ == "__main__":
    unittest.main()
