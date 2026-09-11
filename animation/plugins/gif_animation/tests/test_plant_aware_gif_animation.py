"""Authority regressions for the current GIF media component."""

import unittest

from animation.plugins.gif_animation import GifAnimation


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class GifAnimationAuthorityTests(unittest.TestCase):
    def test_legacy_installation_and_presentation_values_are_not_current_controls(self):
        legacy = {
            "gif_directory": "animation/plugins/gif_animation/assets",
            "gif_index": 7,
            "brightness": 0.6,
            "brightness_mode": "rgb",
            "brightness_floor": 0.1,
            "gamma": 1.4,
            "contain_background": 12,
            "plant_aware": True,
            "plant_clearance": 2,
            "plant_mask_path": "legacy-foliage.json",
            "plant_globe_mask_path": "legacy-globes.json",
        }
        normalized = GifAnimation.component_descriptor().parameter_normalizer(legacy)
        self.assertTrue(set(legacy).isdisjoint(normalized))

        animation = GifAnimation(_Controller(), legacy)
        schema = animation.get_parameter_schema()
        self.assertEqual(set(schema), set(GifAnimation.DEFAULTS))
        self.assertTrue(set(legacy).isdisjoint(schema))


if __name__ == "__main__":
    unittest.main()
