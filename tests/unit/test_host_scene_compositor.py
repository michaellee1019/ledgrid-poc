"""Acceptance tests for the bounded, host-only scene compositor demo."""

from __future__ import annotations

import unittest

import numpy as np

from animation.core.compositing import BaseFrame, HostSceneCompositor, OverlayFrame, PlacedOverlay


class HostSceneCompositorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.compositor = HostSceneCompositor(2, 4)

    @staticmethod
    def base(color=(12, 34, 56), *, changed=True, dirty_ranges=None) -> BaseFrame:
        pixels = np.full((8, 3), color, dtype=np.uint8)
        return BaseFrame(pixels, changed=changed, dirty_ranges=dirty_ranges)

    @staticmethod
    def overlay(values, *, revision=1, changed=True, dirty_ranges=None) -> OverlayFrame:
        pixels = np.zeros((8, 4), dtype=np.uint8)
        for index, value in values.items():
            pixels[index] = value
        return OverlayFrame(pixels, revision=revision, changed=changed, dirty_ranges=dirty_ranges)

    def test_two_overlays_are_bottom_to_top_and_reversal_changes_pixels(self) -> None:
        base = self.base()
        bottom = self.overlay({0: (80, 20, 0, 128)}, changed=False)
        top = self.overlay({0: (0, 40, 80, 128)}, changed=False)

        ordered = self.compositor.compose(base, (PlacedOverlay(bottom), PlacedOverlay(top)))
        reversed_frame = self.compositor.compose(
            BaseFrame(base.pixels, changed=False), (PlacedOverlay(top), PlacedOverlay(bottom))
        )

        np.testing.assert_array_equal(ordered.pixels[0], (43, 58, 94))
        np.testing.assert_array_equal(reversed_frame.pixels[0], (83, 48, 54))
        self.assertFalse(np.array_equal(ordered.pixels[0], reversed_frame.pixels[0]))
        self.assertEqual(reversed_frame.dirty_ranges, ((0, 1),))

    def test_transparent_and_opaque_black_are_byte_exact(self) -> None:
        result = self.compositor.compose(self.base(), (PlacedOverlay(self.overlay({
            0: (0, 0, 0, 0), 1: (0, 0, 0, 255),
        })),))
        np.testing.assert_array_equal(result.pixels[0], (12, 34, 56))
        np.testing.assert_array_equal(result.pixels[1], (0, 0, 0))

    def test_strip_major_placement_clips_without_cross_strip_wrap(self) -> None:
        pixels = {
            0: (10, 0, 0, 255), 3: (40, 0, 0, 255),
            4: (0, 60, 0, 255), 7: (30, 0, 0, 255),
        }
        result = self.compositor.compose(
            BaseFrame(np.zeros((8, 3), dtype=np.uint8)),
            (PlacedOverlay(self.overlay(pixels, changed=False), strip_offset=1, led_offset=-1),),
        )
        expected = np.zeros((8, 3), dtype=np.uint8)
        expected[6] = (40, 0, 0)
        np.testing.assert_array_equal(result.pixels, expected)

    def test_opacity_scales_premultiplied_channels_once(self) -> None:
        result = self.compositor.compose(
            self.base((0, 0, 0)),
            (PlacedOverlay(self.overlay({0: (100, 50, 25, 128)}, changed=False), opacity=128),),
        )
        np.testing.assert_array_equal(result.pixels[0], (50, 25, 13))

    def test_move_disable_and_remove_repaint_old_coverage_without_stale_pixels(self) -> None:
        base = self.base((2, 3, 4))
        overlay = self.overlay({0: (255, 0, 0, 255)}, changed=False)
        self.compositor.compose(base, (PlacedOverlay(overlay),))

        moved = self.compositor.compose(BaseFrame(base.pixels, changed=False), (PlacedOverlay(overlay, led_offset=1),))
        self.assertEqual(moved.dirty_ranges, ((0, 2),))
        np.testing.assert_array_equal(moved.pixels[:2], ((2, 3, 4), (255, 0, 0)))

        disabled = self.compositor.compose(BaseFrame(base.pixels, changed=False), (PlacedOverlay(overlay, led_offset=1, enabled=False),))
        self.assertEqual(disabled.dirty_ranges, ((1, 2),))
        np.testing.assert_array_equal(disabled.pixels, base.pixels)

        removed = self.compositor.compose(BaseFrame(base.pixels, changed=False), ())
        self.assertEqual(removed.dirty_ranges, ())
        np.testing.assert_array_equal(removed.pixels, base.pixels)

    def test_remove_directly_marks_previous_coverage(self) -> None:
        base = self.base()
        overlay = self.overlay({3: (20, 0, 0, 255)}, changed=False)
        self.compositor.compose(base, (PlacedOverlay(overlay),))
        removed = self.compositor.compose(BaseFrame(base.pixels, changed=False), ())
        self.assertEqual(removed.dirty_ranges, ((3, 4),))
        np.testing.assert_array_equal(removed.pixels, base.pixels)

    def test_invalid_inputs_fail_before_any_reusable_output_mutates(self) -> None:
        base = self.base((9, 8, 7))
        stable = self.overlay({0: (10, 0, 0, 10)}, changed=False)
        before = self.compositor.compose(base, (PlacedOverlay(stable),)).pixels.copy()
        bad_pixels = np.zeros((8, 4), dtype=np.uint8)
        bad_pixels[0] = (2, 0, 0, 1)
        with self.assertRaisesRegex(ValueError, "premultiplied"):
            OverlayFrame(bad_pixels, revision=2)
        with self.assertRaisesRegex(ValueError, "geometry"):
            self.compositor.compose(BaseFrame(np.zeros((2, 3), dtype=np.uint8)), ())
        cached = self.compositor.compose(BaseFrame(base.pixels, changed=False), (PlacedOverlay(stable),))
        np.testing.assert_array_equal(cached.pixels, before)
        self.assertFalse(cached.changed)

    def test_non_contiguous_and_wrong_type_frames_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "C-contiguous"):
            BaseFrame(np.zeros((3, 8), dtype=np.uint8).T)
        with self.assertRaisesRegex(ValueError, "shape"):
            BaseFrame(np.zeros((8, 4), dtype=np.uint8))
        with self.assertRaisesRegex(ValueError, "dtype"):
            OverlayFrame(np.zeros((8, 4), dtype=np.int16), revision=1)
        with self.assertRaises(TypeError):
            BaseFrame([[0, 0, 0]])  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            PlacedOverlay("nope")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            self.compositor.compose(self.base(), (object(),))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
