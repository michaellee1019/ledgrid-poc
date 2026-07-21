import unittest

import numpy as np
from PIL import Image

from scripts.build_webcam_pixel_map import build_map


class WebcamPixelMapTests(unittest.TestCase):
    def test_wall_off_subtraction_marks_blocked_cell(self):
        off = np.full((40, 40, 3), 80, dtype=np.uint8)
        on = np.full((40, 40, 3), 200, dtype=np.uint8)
        on[20:40, 20:40] = off[20:40, 20:40]

        payload = build_map(
            Image.fromarray(on),
            ((0, 0), (40, 0), (40, 40), (0, 40)),
            strips=2,
            leds_per_strip=2,
            visibility_threshold=0.52,
            off_image=Image.fromarray(off),
        )

        self.assertEqual(payload["observed_count"], 4)
        self.assertEqual(payload["occluded_count"], 1)
        self.assertEqual(payload["covered_indices"], payload["occluded_indices"])
        self.assertTrue(payload["pixels"][3]["occluded"])

    def test_wall_off_image_must_match_capture_size(self):
        with self.assertRaisesRegex(ValueError, "matching dimensions"):
            build_map(
                Image.new("RGB", (40, 40)),
                ((0, 0), (40, 0), (40, 40), (0, 40)),
                strips=2,
                leds_per_strip=2,
                visibility_threshold=0.52,
                off_image=Image.new("RGB", (20, 20)),
            )

    def test_flip_y_maps_led_zero_to_camera_bottom(self):
        payload = build_map(
            Image.new("RGB", (40, 40), "white"),
            ((0, 0), (40, 0), (40, 40), (0, 40)),
            strips=2,
            leds_per_strip=2,
            visibility_threshold=0.52,
            flip_y=True,
        )

        self.assertGreater(payload["pixels"][0]["camera_y"], payload["pixels"][1]["camera_y"])
        self.assertTrue(payload["geometry"]["camera_y_flipped"])


if __name__ == "__main__":
    unittest.main()
