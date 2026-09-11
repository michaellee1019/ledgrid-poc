"""Authority regressions for the current World Flags component."""

import unittest

from animation.plugins.world_flags import WorldFlagsAnimation


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class WorldFlagsAuthorityTests(unittest.TestCase):
    def test_legacy_installation_and_presentation_values_are_not_current_controls(self):
        legacy = {
            "speed": 8.0,
            "brightness": 0.6,
            "map_path": "config/webcam_pixel_map.json",
            "map_mode": "compensate",
            "visibility_boost": 0.4,
            "plant_aware": True,
            "plant_clearance": 2,
            "plant_mask_path": "legacy-foliage.json",
            "plant_globe_mask_path": "legacy-globes.json",
        }
        normalized = WorldFlagsAnimation.component_descriptor().parameter_normalizer(legacy)
        self.assertEqual(normalized["scroll_pixels_per_second"], 8.0)
        self.assertTrue((set(legacy) - {"speed"}).isdisjoint(normalized))

        animation = WorldFlagsAnimation(_Controller(), legacy)
        schema = animation.get_parameter_schema()
        self.assertEqual(set(schema), set(WorldFlagsAnimation.DEFAULTS))
        self.assertFalse(set(legacy) - {"speed"} & set(schema))


if __name__ == "__main__":
    unittest.main()
