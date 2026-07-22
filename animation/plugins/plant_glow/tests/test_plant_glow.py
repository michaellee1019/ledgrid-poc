import unittest

import numpy as np

from animation.plugins.plant_glow import PlantGlowAnimation


class _Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class PlantGlowAnimationTests(unittest.TestCase):
    def test_verified_masks_render_with_exterior_halos(self):
        animation = PlantGlowAnimation(
            _Controller(),
            {
                "brightness": 1.0,
                "background_red": 0,
                "background_green": 0,
                "background_blue": 0,
                "breath_depth": 0.0,
                "shimmer": 0.0,
                "glow_radius": 1,
            },
        )
        self.assertEqual(len(animation.foliage_indices), 504)
        self.assertEqual(len(animation.globe_indices), 356)
        self.assertTrue(animation.foliage_indices.isdisjoint(animation.globe_indices))

        frame = animation.generate_frame(0.0, 0)
        self.assertEqual(frame.shape, (4416, 3))
        self.assertEqual(frame.dtype, np.uint8)
        cores = animation._foliage_core | animation._globe_core
        halos = (animation._foliage_halo > 0) | (animation._globe_halo > 0)
        self.assertTrue(np.all(np.any(frame[cores] != 0, axis=1)))
        self.assertTrue(np.any(halos & ~cores))
        self.assertTrue(np.all(np.any(frame[halos & ~cores] != 0, axis=1)))
        self.assertEqual(animation.get_runtime_stats()["globe_regions"], 7)

    def test_live_radius_update_rebuilds_halo(self):
        animation = PlantGlowAnimation(_Controller(), {"glow_radius": 1})
        one_ring = np.count_nonzero(animation._foliage_halo)
        animation.update_parameters({"glow_radius": 3})
        self.assertGreater(np.count_nonzero(animation._foliage_halo), one_ring)

    def test_schema_exposes_conway_and_pinball_backgrounds(self):
        schema = PlantGlowAnimation(_Controller()).get_parameter_schema()
        self.assertEqual(
            schema["background_source"]["options"], ["color", "conway", "pinball"]
        )
        self.assertIn("earth", schema["background_style"]["options"])
        self.assertIn("arcade", schema["background_style"]["options"])

    def test_borrowed_backgrounds_render_beneath_mask_cores(self):
        common = {
            "brightness": 1.0,
            "breath_depth": 0.0,
            "shimmer": 0.0,
            "glow_strength": 0.0,
            "background_strength": 0.5,
        }
        conway = PlantGlowAnimation(
            _Controller(), {**common, "background_source": "conway", "background_style": "earth"}
        )
        pinball = PlantGlowAnimation(
            _Controller(), {**common, "background_source": "pinball", "background_seed": 42}
        )

        conway_frame = conway.generate_frame(0.0, 0)
        pinball_frame = pinball.generate_frame(0.0, 0)
        outside = ~(conway._foliage_core | conway._globe_core)

        self.assertGreater(np.count_nonzero(conway_frame[outside]), 100)
        self.assertGreater(np.count_nonzero(pinball_frame[outside]), 100)
        self.assertFalse(np.array_equal(conway_frame[outside], pinball_frame[outside]))
        np.testing.assert_array_equal(
            conway_frame[conway._foliage_core][0], np.array([54, 255, 132], dtype=np.uint8)
        )

    def test_live_background_change_rebuilds_borrowed_animation(self):
        animation = PlantGlowAnimation(
            _Controller(), {"background_source": "conway", "background_style": "aurora"}
        )
        animation.generate_frame(0.0, 0)
        original = animation._background_animation
        animation.update_parameters({"background_style": "starfield"})
        animation.generate_frame(0.1, 1)
        self.assertIsNot(animation._background_animation, original)


if __name__ == "__main__":
    unittest.main()
