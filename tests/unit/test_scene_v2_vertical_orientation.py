"""Directional Scene v2 renderers honor the wall's bottom-origin frame."""

import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.libraries.pixel_art import EMOJI_PATTERNS
from animation.plugins.ascii_drop import AsciiDropAnimation
from animation.plugins.christmas_tree import ChristmasTreeAnimation
from animation.plugins.emoji import EmojiAnimation
from animation.plugins.firefly_synchrony import FireflySynchronyAnimation
from animation.plugins.fireworks import FireworksAnimation
from animation.plugins.fluid_tank import FluidTankAnimation
from animation.plugins.night_train_windows import NightTrainWindowsAnimation


class SceneV2VerticalOrientationTests(unittest.TestCase):
    WIDTH = 33
    HEIGHT = 138

    def setUp(self) -> None:
        self.controller = PreviewLEDController(
            strips=self.WIDTH, leds_per_strip=self.HEIGHT
        )

    def wall(self, rendered) -> np.ndarray:
        return rendered.pixels.reshape(self.WIDTH, self.HEIGHT, 3)

    def test_fireworks_launch_from_physical_bottom(self) -> None:
        animation = FireworksAnimation(
            self.controller, {"seed": 808, "launch_cadence": 1.7}
        )
        animation.generate_frame(0.0, 0)
        rendered = None
        for frame in range(1, 7):
            rendered = animation.generate_frame(frame / 24.0, frame)
        self.assertIsNotNone(rendered)
        wall = self.wall(rendered)
        background = np.asarray((4, 9, 18), dtype=np.uint8)
        active = np.any(wall != background, axis=2)
        _, leds = np.nonzero(active)
        self.assertGreater(leds.size, 0)
        self.assertLess(int(leds.max()), self.HEIGHT // 2)

    def test_tree_trunk_is_below_its_star(self) -> None:
        wall = self.wall(
            ChristmasTreeAnimation(
                self.controller, {"snowfall": 0.0}
            ).generate_frame(0.0, 0)
        )
        trunk_leds = np.nonzero(np.all(wall == (112, 57, 20), axis=2))[1]
        star_leds = np.nonzero(np.all(wall == (255, 225, 100), axis=2))[1]
        self.assertGreater(trunk_leds.size, 0)
        self.assertGreater(star_leds.size, 0)
        self.assertLess(int(trunk_leds.max()), int(star_leds.min()))

    def test_train_landscape_is_below_the_sky(self) -> None:
        wall = self.wall(
            NightTrainWindowsAnimation(self.controller).generate_frame(0.0, 0)
        )
        np.testing.assert_array_equal(wall[1, 0], (35, 56, 105))
        np.testing.assert_array_equal(wall[1, -1], (2, 5, 18))

    def test_meadow_and_water_are_brightest_at_the_bottom(self) -> None:
        meadow = self.wall(
            FireflySynchronyAnimation(
                self.controller,
                {"population": 20, "meadow_glow": 0.5, "seed": 7319},
            ).generate_frame(0.0, 0)
        )
        tank = self.wall(
            FluidTankAnimation(self.controller).generate_frame(0.0, 0)
        )
        for wall in (meadow, tank):
            bottom = float(wall[:, :20].astype(np.float32).mean())
            top = float(wall[:, -20:].astype(np.float32).mean())
            self.assertGreater(bottom, top)

    def test_ascii_glyph_rows_map_from_top_down_to_bottom_origin(self) -> None:
        params = {
            "phrase": "HELLO",
            "story": "terminal",
            "fall_speed": 13.0,
            "density": 0.08,
            "seed": 4,
        }
        wall = self.wall(
            AsciiDropAnimation(self.controller, params).generate_frame(0.0, 0)
        )
        background = np.asarray((5, 2, 0), dtype=np.uint8)
        actual = set(zip(*np.nonzero(np.any(wall != background, axis=2))))
        rng = np.random.default_rng(params["seed"])
        count = max(4, int(self.WIDTH * 8 * params["density"]))
        xs = rng.integers(0, self.WIDTH, count)
        offsets = rng.integers(0, self.HEIGHT, count)
        expected = set()
        for index, raw_x in enumerate(xs):
            glyph = ord(params["phrase"][index % len(params["phrase"])])
            x = (int(raw_x) + glyph + index) % self.WIDTH
            y = int((offsets[index] + glyph * 3) % self.HEIGHT)
            for dx, dy in ((0, 0), (0, 1), (1, 1), (-1, 1), (0, 2)):
                if 0 <= x + dx < self.WIDTH:
                    expected.add(
                        (x + dx, self.HEIGHT - 1 - ((y + dy) % self.HEIGHT))
                    )
        self.assertEqual(actual, expected)

    def test_emoji_pattern_rows_are_not_vertically_mirrored(self) -> None:
        wall = self.wall(
            EmojiAnimation(
                self.controller, {"face": "smile", "scale": 1.0}
            ).generate_frame(0.0, 0)
        )
        background = np.asarray((3, 7, 18), dtype=np.uint8)
        actual = set(zip(*np.nonzero(np.any(wall != background, axis=2))))
        pattern = EMOJI_PATTERNS["smile"]
        height, width = len(pattern), len(pattern[0])
        unit = max(
            1,
            int(min(self.WIDTH / (width + 3), self.HEIGHT / (height + 10))),
        )
        left = (self.WIDTH - width * unit) // 2
        top = (self.HEIGHT - height * unit) // 2
        expected = set()
        for row, line in enumerate(pattern):
            for column, cell in enumerate(line):
                if cell == ".":
                    continue
                for strip in range(
                    max(0, left + column * unit),
                    min(self.WIDTH, left + (column + 1) * unit),
                ):
                    for top_down_led in range(
                        max(0, top + row * unit),
                        min(self.HEIGHT, top + (row + 1) * unit),
                    ):
                        expected.add((strip, self.HEIGHT - 1 - top_down_led))
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
