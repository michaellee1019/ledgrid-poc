"""Focused acceptance coverage for the reusable host scene compositor."""

from __future__ import annotations

import unittest

import numpy as np

from animation.core.compositing import (
    HostForegroundCompositor,
    HostSceneCompositor,
    PlacedOverlay,
    fold_overlays,
    scale_premultiplied_rgba,
    source_over_rgb,
)
from animation.core.presentation_contracts import BaseFrame, OverlayFrame


class HostSceneCompositorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.strip_count = 2
        self.leds_per_strip = 4
        self.pixel_count = self.strip_count * self.leds_per_strip
        self.compositor = HostSceneCompositor(self.strip_count, self.leds_per_strip)

    def base(
        self,
        rgb=(12, 34, 56),
        *,
        changed: bool = True,
        dirty_ranges=None,
    ) -> BaseFrame:
        pixels = np.empty((self.pixel_count, 3), dtype=np.uint8)
        pixels[:] = rgb
        return BaseFrame(pixels, changed=changed, dirty_ranges=dirty_ranges)

    def overlay(
        self,
        values: dict[int, tuple[int, int, int, int]],
        *,
        revision: int = 1,
        changed: bool = True,
        dirty_ranges=None,
        pixels: np.ndarray | None = None,
    ) -> OverlayFrame:
        if pixels is None:
            pixels = np.zeros((self.pixel_count, 4), dtype=np.uint8)
        for index, value in values.items():
            pixels[index] = value
        return OverlayFrame(
            pixels,
            revision=revision,
            changed=changed,
            dirty_ranges=dirty_ranges,
        )

    def test_transparent_black_opaque_black_and_alpha_endpoints(self):
        overlay = self.overlay(
            {
                0: (0, 0, 0, 0),
                1: (0, 0, 0, 255),
                2: (64, 32, 16, 128),
                3: (12, 34, 56, 255),
            }
        )

        result = self.compositor.compose(self.base(), (PlacedOverlay(overlay),))

        np.testing.assert_array_equal(result.pixels[0], (12, 34, 56))
        np.testing.assert_array_equal(result.pixels[1], (0, 0, 0))
        np.testing.assert_array_equal(result.pixels[2], (70, 49, 44))
        np.testing.assert_array_equal(result.pixels[3], (12, 34, 56))

    def test_scene_opacity_scales_every_premultiplied_channel(self):
        black = self.base((0, 0, 0))
        overlay = self.overlay({0: (100, 50, 25, 128)}, changed=False)

        half = self.compositor.compose(
            black, (PlacedOverlay(overlay, opacity=128),)
        )
        np.testing.assert_array_equal(half.pixels[0], (50, 25, 13))

        full = self.compositor.compose(
            BaseFrame(black.pixels, changed=False),
            (PlacedOverlay(overlay, opacity=255),),
        )
        np.testing.assert_array_equal(full.pixels[0], (100, 50, 25))
        self.assertEqual(full.dirty_ranges, ((0, 1),))

        invisible = self.compositor.compose(
            BaseFrame(black.pixels, changed=False),
            (PlacedOverlay(overlay, opacity=0),),
        )
        np.testing.assert_array_equal(invisible.pixels, black.pixels)
        self.assertEqual(invisible.dirty_ranges, ((0, 1),))

    def test_two_overlays_fold_bottom_to_top_at_each_rounding_point(self):
        base = self.base()
        bottom = self.overlay({0: (80, 20, 0, 128)}, revision=7, changed=False)
        top = self.overlay({0: (0, 40, 80, 128)}, revision=7, changed=False)

        ordered = self.compositor.compose(
            base, (PlacedOverlay(bottom), PlacedOverlay(top))
        )
        np.testing.assert_array_equal(ordered.pixels[0], (43, 58, 94))

        reversed_result = self.compositor.compose(
            BaseFrame(base.pixels, changed=False),
            (PlacedOverlay(top), PlacedOverlay(bottom)),
        )
        np.testing.assert_array_equal(reversed_result.pixels[0], (83, 48, 54))
        self.assertEqual(reversed_result.dirty_ranges, ((0, 1),))

    def test_translation_is_strip_major_clipped_and_never_wraps(self):
        compositor = HostSceneCompositor(3, 4)
        base_pixels = np.zeros((12, 3), dtype=np.uint8)
        overlay_pixels = np.zeros((12, 4), dtype=np.uint8)
        overlay_pixels[0] = (10, 0, 0, 255)   # clipped by negative LED offset
        overlay_pixels[3] = (40, 0, 0, 255)   # (strip 0, LED 3) -> (1, 2)
        overlay_pixels[5] = (20, 0, 0, 255)   # (strip 1, LED 1) -> (2, 0)
        overlay_pixels[11] = (30, 0, 0, 255)  # clipped by positive strip offset
        no_wrap_pixels = np.zeros((12, 4), dtype=np.uint8)
        no_wrap_pixels[3] = (0, 50, 0, 255)  # clipped, never wraps to strip 1
        no_wrap_pixels[4] = (0, 60, 0, 255)  # (strip 1, LED 0) -> (1, 1)

        result = compositor.compose(
            BaseFrame(base_pixels),
            (
                PlacedOverlay(
                    OverlayFrame(overlay_pixels, revision=1, changed=False),
                    strip_offset=1,
                    led_offset=-1,
                ),
                PlacedOverlay(
                    OverlayFrame(no_wrap_pixels, revision=1, changed=False),
                    led_offset=1,
                ),
            ),
        )

        expected = np.zeros_like(base_pixels)
        expected[5] = (0, 60, 0)
        expected[6] = (40, 0, 0)
        expected[8] = (20, 0, 0)
        np.testing.assert_array_equal(result.pixels, expected)

    def test_vectorized_composition_matches_reference_math_for_full_scene(self):
        strip_count, leds_per_strip = 3, 5
        pixel_count = strip_count * leds_per_strip
        rng = np.random.default_rng(42)
        base_pixels = rng.integers(0, 256, (pixel_count, 3), dtype=np.uint8)
        placements = []
        for revision, (strip_offset, led_offset, opacity) in enumerate(
            ((0, 0, 255), (1, -2, 173), (-1, 1, 89)), start=1
        ):
            alpha = rng.integers(0, 256, pixel_count, dtype=np.uint8)
            pixels = np.empty((pixel_count, 4), dtype=np.uint8)
            pixels[:, 3] = alpha
            for channel in range(3):
                pixels[:, channel] = np.array(
                    [rng.integers(0, int(value) + 1) for value in alpha],
                    dtype=np.uint8,
                )
            placements.append(
                PlacedOverlay(
                    OverlayFrame(pixels, revision=revision, changed=False),
                    strip_offset=strip_offset,
                    led_offset=led_offset,
                    opacity=opacity,
                )
            )

        result = HostSceneCompositor(strip_count, leds_per_strip).compose(
            BaseFrame(base_pixels), tuple(placements)
        )
        expected = np.empty_like(base_pixels)
        for strip in range(strip_count):
            for led in range(leds_per_strip):
                contributions = []
                for placement in placements:
                    source_strip = strip - placement.strip_offset
                    source_led = led - placement.led_offset
                    if 0 <= source_strip < strip_count and 0 <= source_led < leds_per_strip:
                        source_index = source_strip * leds_per_strip + source_led
                        contributions.append(
                            scale_premultiplied_rgba(
                                tuple(
                                    int(value)
                                    for value in placement.frame.pixels[source_index]
                                ),
                                placement.opacity,
                            )
                        )
                index = strip * leds_per_strip + led
                expected[index] = source_over_rgb(
                    tuple(int(value) for value in base_pixels[index]),
                    fold_overlays(contributions),
                )

        np.testing.assert_array_equal(result.pixels, expected)

    def test_movement_disable_reenable_and_removal_clear_previous_coverage(self):
        base = self.base((2, 3, 4))
        overlay = self.overlay({0: (255, 0, 0, 255)}, changed=False)

        first = self.compositor.compose(base, (PlacedOverlay(overlay),))
        np.testing.assert_array_equal(first.pixels[0], (255, 0, 0))

        moved = self.compositor.compose(
            BaseFrame(base.pixels, changed=False),
            (PlacedOverlay(overlay, led_offset=1),),
        )
        self.assertEqual(moved.dirty_ranges, ((0, 2),))
        np.testing.assert_array_equal(moved.pixels[0], (2, 3, 4))
        np.testing.assert_array_equal(moved.pixels[1], (255, 0, 0))

        disabled = self.compositor.compose(
            BaseFrame(base.pixels, changed=False),
            (PlacedOverlay(overlay, led_offset=1, enabled=False),),
        )
        self.assertEqual(disabled.dirty_ranges, ((1, 2),))
        np.testing.assert_array_equal(disabled.pixels, base.pixels)

        enabled = self.compositor.compose(
            BaseFrame(base.pixels, changed=False),
            (PlacedOverlay(overlay, led_offset=1),),
        )
        self.assertEqual(enabled.dirty_ranges, ((1, 2),))
        np.testing.assert_array_equal(enabled.pixels[1], (255, 0, 0))

        removed = self.compositor.compose(BaseFrame(base.pixels, changed=False), ())
        self.assertEqual(removed.dirty_ranges, ((1, 2),))
        np.testing.assert_array_equal(removed.pixels, base.pixels)

    def test_revision_change_recomposes_even_when_frame_says_unchanged(self):
        base = self.base((0, 0, 0))
        pixels = np.zeros((self.pixel_count, 4), dtype=np.uint8)
        pixels[0] = (255, 0, 0, 255)
        first_overlay = OverlayFrame(pixels, revision=10, changed=False)
        self.compositor.compose(base, (PlacedOverlay(first_overlay),))

        pixels[0] = (0, 255, 0, 255)
        next_overlay = OverlayFrame(pixels, revision=11, changed=False)
        result = self.compositor.compose(
            BaseFrame(base.pixels, changed=False), (PlacedOverlay(next_overlay),)
        )

        self.assertTrue(result.changed)
        self.assertEqual(result.dirty_ranges, ((0, 1),))
        np.testing.assert_array_equal(result.pixels[0], (0, 255, 0))

    def test_changing_base_recomposes_stable_overlay_coverage(self):
        first_base = self.base((100, 0, 0))
        overlay = self.overlay({0: (0, 0, 128, 128)}, changed=False)
        self.compositor.compose(first_base, (PlacedOverlay(overlay),))

        second_pixels = first_base.pixels.copy()
        second_pixels[0] = (0, 100, 0)
        result = self.compositor.compose(
            BaseFrame(second_pixels, dirty_ranges=((0, 1),)),
            (PlacedOverlay(overlay),),
        )

        self.assertEqual(result.dirty_ranges, ((0, 1),))
        np.testing.assert_array_equal(result.pixels[0], (0, 50, 128))
        np.testing.assert_array_equal(result.pixels[1], (100, 0, 0))

    def test_known_overlay_dirty_ranges_translate_and_clip_exactly(self):
        base = self.base((0, 0, 0))
        pixels = np.zeros((self.pixel_count, 4), dtype=np.uint8)
        pixels[:] = (1, 1, 1, 255)
        stable = OverlayFrame(pixels, revision=1, changed=False)
        self.compositor.compose(base, (PlacedOverlay(stable, led_offset=1),))

        pixels[2:6] = (2, 2, 2, 255)
        changed = OverlayFrame(
            pixels, revision=2, changed=True, dirty_ranges=((2, 6),)
        )
        result = self.compositor.compose(
            BaseFrame(base.pixels, changed=False),
            (PlacedOverlay(changed, led_offset=1),),
        )

        self.assertEqual(result.dirty_ranges, ((3, 4), (5, 7)))

    def test_unknown_dirty_metadata_propagates_conservatively(self):
        base = self.base((0, 0, 0))
        pixels = np.zeros((self.pixel_count, 4), dtype=np.uint8)
        pixels[0] = (10, 0, 0, 10)
        stable = OverlayFrame(pixels, revision=1, changed=False)
        self.compositor.compose(base, (PlacedOverlay(stable),))

        pixels[0] = (20, 0, 0, 20)
        unknown_overlay = OverlayFrame(pixels, revision=2, changed=True)
        overlay_result = self.compositor.compose(
            BaseFrame(base.pixels, changed=False), (PlacedOverlay(unknown_overlay),)
        )
        self.assertIsNone(overlay_result.dirty_ranges)

        unknown_base = self.base((1, 2, 3), changed=True, dirty_ranges=None)
        base_result = self.compositor.compose(
            unknown_base,
            (PlacedOverlay(OverlayFrame(pixels, revision=2, changed=False)),),
        )
        self.assertIsNone(base_result.dirty_ranges)

    def test_base_only_is_byte_exact_and_output_pool_is_reused_without_overwrite(self):
        base = self.base((9, 8, 7))
        base_before = base.pixels.copy()
        first = self.compositor.compose(base)

        np.testing.assert_array_equal(first.pixels, base_before)
        self.assertTrue(first.pixels.flags.c_contiguous)
        self.assertFalse(np.shares_memory(first.pixels, base.pixels))
        first_output_identity = id(first.pixels)

        cached = self.compositor.compose(BaseFrame(base.pixels, changed=False))
        self.assertFalse(cached.changed)
        self.assertEqual(cached.dirty_ranges, ())
        self.assertEqual(id(cached.pixels), first_output_identity)

        first_bytes = first.pixels.copy()
        next_pixels = base.pixels.copy()
        next_pixels[:] = (1, 2, 3)
        changed = self.compositor.compose(
            BaseFrame(next_pixels, changed=True, dirty_ranges=())
        )
        self.assertTrue(changed.changed)
        self.assertEqual(changed.dirty_ranges, ())
        self.assertNotEqual(id(changed.pixels), first_output_identity)
        np.testing.assert_array_equal(first.pixels, first_bytes)
        np.testing.assert_array_equal(changed.pixels, next_pixels)

        returned = self.compositor.compose(
            BaseFrame(base.pixels, changed=True, dirty_ranges=())
        )
        self.assertEqual(id(returned.pixels), first_output_identity)
        np.testing.assert_array_equal(base.pixels, base_before)

    def test_component_inputs_are_never_mutated(self):
        base = self.base((11, 22, 33))
        overlay = self.overlay({0: (70, 20, 10, 100)})
        base_before = base.pixels.copy()
        overlay_before = overlay.pixels.copy()

        self.compositor.compose(
            base, (PlacedOverlay(overlay, opacity=127, strip_offset=-1),)
        )

        np.testing.assert_array_equal(base.pixels, base_before)
        np.testing.assert_array_equal(overlay.pixels, overlay_before)

    def test_invalid_geometry_types_and_placements_fail_at_the_boundary(self):
        with self.assertRaises(TypeError):
            HostSceneCompositor(True, 4)
        with self.assertRaises(ValueError):
            HostSceneCompositor(0, 4)

        overlay = self.overlay({}, changed=False)
        with self.assertRaises(TypeError):
            PlacedOverlay("not a frame")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            PlacedOverlay(overlay, strip_offset=True)
        with self.assertRaises(TypeError):
            PlacedOverlay(overlay, led_offset=1.5)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            PlacedOverlay(overlay, opacity=256)
        with self.assertRaises(TypeError):
            PlacedOverlay(overlay, enabled=1)  # type: ignore[arg-type]

        with self.assertRaises(TypeError):
            self.compositor.compose("not a base")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "geometry"):
            self.compositor.compose(BaseFrame(np.zeros((2, 3), dtype=np.uint8)))
        with self.assertRaisesRegex(ValueError, "geometry"):
            short_overlay = OverlayFrame(np.zeros((2, 4), dtype=np.uint8), revision=1)
            self.compositor.compose(self.base(), (PlacedOverlay(short_overlay),))
        with self.assertRaises(TypeError):
            self.compositor.compose(self.base(), (item for item in ()))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            self.compositor.compose(self.base(), (object(),))  # type: ignore[arg-type]


class HostForegroundCompositorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.compositor = HostForegroundCompositor(2, 4)

    @staticmethod
    def frame(
        values: dict[int, tuple[int, int, int, int]],
        *,
        revision: int = 1,
        changed: bool = True,
        dirty_ranges=None,
    ) -> OverlayFrame:
        pixels = np.zeros((8, 4), dtype=np.uint8)
        for index, value in values.items():
            pixels[index] = value
        return OverlayFrame(
            pixels,
            revision=revision,
            changed=changed,
            dirty_ranges=dirty_ranges,
        )

    def test_aggregate_is_premultiplied_and_scales_opacity_once(self):
        source = self.frame({0: (100, 50, 25, 128)})
        result = self.compositor.compose((PlacedOverlay(source, opacity=128),))

        np.testing.assert_array_equal(result.pixels[0], (50, 25, 13, 64))
        self.assertTrue(result.changed)
        self.assertIsNone(result.dirty_ranges)
        np.testing.assert_array_equal(source.pixels[0], (100, 50, 25, 128))

    def test_move_disable_and_remove_publish_exact_old_and_new_coverage(self):
        source = self.frame(
            {1: (40, 0, 0, 80)}, changed=False, dirty_ranges=()
        )
        self.compositor.compose((PlacedOverlay(source),))

        moved = self.compositor.compose((PlacedOverlay(
            self.frame(
                {1: (40, 0, 0, 80)},
                revision=1,
                changed=False,
                dirty_ranges=(),
            ),
            led_offset=1,
        ),))
        self.assertEqual(moved.dirty_ranges, ((1, 3),))
        np.testing.assert_array_equal(moved.pixels[1], (0, 0, 0, 0))
        np.testing.assert_array_equal(moved.pixels[2], (40, 0, 0, 80))

        disabled = self.compositor.compose((PlacedOverlay(
            self.frame(
                {1: (40, 0, 0, 80)},
                revision=1,
                changed=False,
                dirty_ranges=(),
            ),
            led_offset=1,
            enabled=False,
        ),))
        self.assertEqual(disabled.dirty_ranges, ((2, 3),))
        self.assertFalse(np.any(disabled.pixels))

        removed = self.compositor.compose(())
        self.assertTrue(removed.changed)
        self.assertEqual(removed.dirty_ranges, ())

    def test_translated_dirty_ranges_are_strip_major_and_clipped(self):
        first = self.frame({3: (10, 0, 0, 10)}, changed=False)
        self.compositor.compose((PlacedOverlay(first),))
        changed = self.frame(
            {3: (20, 0, 0, 20)},
            revision=2,
            changed=True,
            dirty_ranges=((3, 4),),
        )

        result = self.compositor.compose((PlacedOverlay(changed, led_offset=-1),))

        self.assertEqual(result.dirty_ranges, ((2, 4),))
        np.testing.assert_array_equal(result.pixels[2], (20, 0, 0, 20))

    def test_unchanged_frame_reuses_output_without_mutation(self):
        source = self.frame({0: (1, 2, 3, 4)}, changed=False)
        first = self.compositor.compose((PlacedOverlay(source),))
        before = first.pixels.copy()
        cached = self.compositor.compose((PlacedOverlay(
            OverlayFrame(
                source.pixels,
                revision=source.revision,
                changed=False,
                dirty_ranges=(),
            ),
        ),))

        self.assertFalse(cached.changed)
        self.assertEqual(cached.dirty_ranges, ())
        self.assertIs(cached.pixels, first.pixels)
        np.testing.assert_array_equal(first.pixels, before)

    def test_version_one_rejects_multiple_planes_at_the_boundary(self):
        source = self.frame({}, changed=False)
        with self.assertRaisesRegex(ValueError, "supports one overlay"):
            self.compositor.compose((PlacedOverlay(source), PlacedOverlay(source)))


if __name__ == "__main__":
    unittest.main()
