import unittest

from animation.libraries.color import mix_rgb, parameter_rgb, scale_rgb


class ColorLibraryTests(unittest.TestCase):
    def test_parameter_rgb_defaults_and_channel_policy(self):
        params = {"accent_red": -8, "accent_green": 128, "accent_blue": 300}
        self.assertEqual(parameter_rgb(params, "missing", (1, 2, 3)), (1, 2, 3))
        self.assertEqual(parameter_rgb(params, "accent", (0, 0, 0)), (0, 128, 255))
        self.assertEqual(
            parameter_rgb(params, "accent", (0, 0, 0), clamp_channels=False),
            (-8, 128, 300),
        )

    def test_scale_rgb_supports_both_plugin_policies(self):
        self.assertEqual(scale_rgb((100, 200, 255), 1.5), (150, 255, 255))
        self.assertEqual(
            scale_rgb((-10, 100, 200), -1.0, scale_bounds=(0.0, 2.0), clamp_lower=False),
            (0, 0, 0),
        )

    def test_mix_rgb_clamps_ratio(self):
        self.assertEqual(mix_rgb((0, 10, 20), (100, 110, 120), 0.25), (25, 35, 45))
        self.assertEqual(mix_rgb((0, 10, 20), (100, 110, 120), 2.0), (100, 110, 120))


if __name__ == "__main__":
    unittest.main()
