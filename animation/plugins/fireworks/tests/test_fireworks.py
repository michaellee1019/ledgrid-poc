import unittest

import numpy as np

from animation.plugins.fireworks import FireworksAnimation


class _Controller:
    strip_count = 8
    leds_per_strip = 24
    total_leds = strip_count * leds_per_strip


class FireworksAnimationTests(unittest.TestCase):
    def test_generates_canonical_double_buffered_frames(self):
        animation = FireworksAnimation(_Controller(), {"random_seed": 7, "star_density": 0})
        first = animation.generate_frame(0.0, 0)
        snapshot = first.copy()
        second = animation.generate_frame(1 / 60, 1)

        self.assertEqual(first.shape, (_Controller.total_leds, 3))
        self.assertEqual(first.dtype, np.uint8)
        self.assertIsNot(first, second)
        np.testing.assert_array_equal(first, snapshot)
        self.assertIs(first, animation.generate_frame(2 / 60, 2))

    def test_rocket_bursts_and_emits_sparks(self):
        animation = FireworksAnimation(_Controller(), {
            "random_seed": 3,
            "launch_speed": 1.5,
            "burst_height_min": 0.2,
            "burst_height_max": 0.2,
            "particles_per_burst": 20,
            "star_density": 0,
        })
        for frame in range(180):
            animation.generate_frame(frame / 60, frame)

        stats = animation.get_runtime_stats()
        self.assertGreaterEqual(stats["bursts"], 1)
        self.assertGreater(stats["sparks"], 0)

    def test_seed_makes_show_repeatable(self):
        config = {"random_seed": 41, "star_density": 0.02}
        left = FireworksAnimation(_Controller(), config)
        right = FireworksAnimation(_Controller(), config)
        for frame in range(75):
            left_frame = left.generate_frame(frame / 60, frame)
            right_frame = right.generate_frame(frame / 60, frame)
            np.testing.assert_array_equal(left_frame, right_frame)

    def test_schema_exposes_shape_color_and_physics_controls(self):
        schema = FireworksAnimation(_Controller()).get_parameter_schema()
        for parameter in (
            "launch_rate", "particles_per_burst", "burst_style", "palette",
            "gravity", "air_drag", "trail_persistence", "twinkle", "random_seed",
        ):
            self.assertIn(parameter, schema)


if __name__ == "__main__":
    unittest.main()
