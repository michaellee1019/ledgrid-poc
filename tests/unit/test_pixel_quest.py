"""Tests for the original Pixel Quest overworld animation."""

import unittest

import numpy as np

from animation import RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.pixel_quest import PixelQuestAnimation


class PixelQuestAnimationTests(unittest.TestCase):
    def make_animation(self, strips=32, leds=140):
        return PixelQuestAnimation(PreviewLEDController(strips, leds))

    def test_animation_is_shipped(self):
        self.assertIn("pixel_quest", AnimationManager.ALLOWED_PLUGINS)

    def test_renders_canonical_frames_on_supported_layouts(self):
        for strips, leds in ((32, 140), (16, 140), (12, 40)):
            with self.subTest(strips=strips, leds=leds):
                result = self.make_animation(strips, leds).generate_frame(12.0, 0)
                self.assertIsInstance(result, RenderedFrame)
                self.assertEqual(result.pixels.shape, (strips * leds, 3))
                self.assertEqual(result.pixels.dtype, np.uint8)
                self.assertGreater(np.count_nonzero(result.pixels), strips * leds)

    def test_story_visits_three_biomes_and_four_battles(self):
        animation = self.make_animation()
        expected = {
            5.0: ("meadow", None),
            9.0: ("meadow battle", "mossling"),
            14.0: ("forest", None),
            19.0: ("forest battle", "nightwing"),
            24.0: ("desert", None),
            29.0: ("desert battle", "sandclaw"),
            37.0: ("guardian battle", "stoneguard"),
        }
        for timestamp, (scene, enemy) in expected.items():
            with self.subTest(timestamp=timestamp):
                _x, _y, actual_scene, actual_enemy, _progress = animation._quest_state(timestamp)
                self.assertEqual((actual_scene, actual_enemy), (scene, enemy))

    def test_defeats_accumulate_before_relic_finale(self):
        animation = self.make_animation()
        animation.generate_frame(39.5, 0)
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["scene"], "relic found")
        self.assertEqual(stats["enemies_defeated"], 4)

    def test_render_cap_reuses_unchanged_frame(self):
        animation = self.make_animation()
        first = animation.generate_frame(5.0, 0)
        skipped = animation.generate_frame(5.001, 1)
        advanced = animation.generate_frame(5.03, 2)
        self.assertTrue(first.changed)
        self.assertFalse(skipped.changed)
        self.assertIs(first.pixels, skipped.pixels)
        self.assertTrue(advanced.changed)


if __name__ == "__main__":
    unittest.main()
