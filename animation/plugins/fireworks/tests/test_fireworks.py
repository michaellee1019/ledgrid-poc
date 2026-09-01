"""Focused Scene v2 behavior checks for the Fireworks instrument."""

import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.fireworks import FireworksAnimation


class FireworksAnimationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def test_semantic_renderer_is_deterministic_bounded_and_caches_subticks(self) -> None:
        params = {"seed": 44, "launch_cadence": 3.5, "shell_population": 84}
        left, right = FireworksAnimation(self.controller, params), FireworksAnimation(self.controller, params)
        for frame in range(90):
            a, b = left.generate_frame(frame / 24, frame), right.generate_frame(frame / 24, frame)
            self.assertEqual(a.pixels.shape, (33 * 138, 3)); self.assertEqual(a.pixels.dtype, np.uint8)
            np.testing.assert_array_equal(a.pixels, b.pixels)
        self.assertGreater(left.cadence_snapshot()["bursts"], 1)
        self.assertLessEqual(left.semantic_snapshot()["sparks"], FireworksAnimation.MAX_SPARKS)
        self.assertFalse(left.generate_frame(89 / 24, 100).changed)

    def test_controls_take_effect_without_output_or_legacy_aliases(self) -> None:
        animation = FireworksAnimation(self.controller, {"seed": 7})
        before = animation.generate_frame(2.0, 0).pixels.copy()
        animation.update_parameters({"launch_cadence": 4.0, "shell_population": 120, "burst_size": .65, "burst_style": "ring", "gravity": 1.1, "trails": .2, "crackle": 1.0, "twinkle": 1.0})
        after = animation.generate_frame(3.0, 1).pixels.copy()
        self.assertFalse(np.array_equal(before, after))
        for alias in ("brightness", "speed", "plant_aware", "launch_rate", "output_power"):
            with self.assertRaisesRegex(ValueError, "non-local parameters"):
                FireworksAnimation(self.controller, {alias: 1})


if __name__ == "__main__":
    unittest.main()
