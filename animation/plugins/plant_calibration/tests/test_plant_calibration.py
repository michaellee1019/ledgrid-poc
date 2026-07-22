import unittest

import numpy as np

from animation.plugins.plant_calibration import PlantCalibrationAnimation


class DummyController:
    strip_count = 32
    leds_per_strip = 140
    total_leds = strip_count * leds_per_strip


class PlantCalibrationAnimationTests(unittest.TestCase):
    def test_dimension_probe_marks_all_claimed_edge_rows(self):
        animation = PlantCalibrationAnimation(
            DummyController(),
            {"brightness": 1.0, "manual_pattern_index": 5},
        )

        rendered = animation.generate_frame(0.0, 0)
        frame = np.asarray(rendered.pixels).reshape(32, 140, 3)

        expected = {
            0: (255, 36, 36),
            1: (36, 255, 36),
            2: (36, 80, 255),
            3: (255, 210, 36),
            136: (36, 255, 230),
            137: (230, 36, 255),
            138: (255, 110, 24),
            139: (255, 255, 255),
        }
        for led, color in expected.items():
            np.testing.assert_array_equal(frame[:, led], np.tile(color, (32, 1)))

    def test_runtime_stats_names_dimension_probe(self):
        animation = PlantCalibrationAnimation(
            DummyController(), {"manual_pattern_index": 5}
        )

        stats = animation.get_runtime_stats()

        self.assertEqual(stats["current_pattern_index"], 5)
        self.assertEqual(stats["current_pattern_name"], "dimension_probe")
        self.assertEqual(
            animation.get_parameter_schema()["manual_pattern_index"]["max"], 5
        )


if __name__ == "__main__":
    unittest.main()
