"""Installed-geometry coverage for animation contact-sheet evidence tools."""

import unittest

import numpy as np

from animation.plugins.canopy_cup.render_contact_sheet import (
    frame_to_visual as canopy_frame_to_visual,
    wall_image_size as canopy_wall_image_size,
)
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT
from tools.render_procedural_ideas_contact_sheet import (
    frame_to_visual as procedural_frame_to_visual,
    wall_image_size as procedural_wall_image_size,
)


class InstalledContactSheetGeometryTests(unittest.TestCase):
    def test_renderers_include_the_installed_tail_strip(self):
        pixels = np.zeros(
            (DEFAULT_STRIP_COUNT * DEFAULT_LEDS_PER_STRIP, 3), dtype=np.uint8
        )
        tail_pixel = (DEFAULT_STRIP_COUNT - 1) * DEFAULT_LEDS_PER_STRIP
        pixels[tail_pixel] = (17, 23, 31)

        for renderer in (canopy_frame_to_visual, procedural_frame_to_visual):
            with self.subTest(renderer=renderer.__module__):
                visual = renderer(pixels)
                self.assertEqual(
                    visual.shape,
                    (DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT, 3),
                )
                np.testing.assert_array_equal(
                    visual[-1, DEFAULT_STRIP_COUNT - 1], (17, 23, 31)
                )

    def test_tile_sizes_derive_from_the_installed_layout(self):
        expected = (DEFAULT_STRIP_COUNT * 3, DEFAULT_LEDS_PER_STRIP * 3)
        self.assertEqual(canopy_wall_image_size(3), expected)
        self.assertEqual(procedural_wall_image_size(3), expected)


if __name__ == "__main__":
    unittest.main()
