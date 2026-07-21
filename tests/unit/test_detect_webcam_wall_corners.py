import unittest

import cv2
import numpy as np

from scripts.detect_webcam_wall_corners import detect_panel_corners


class DetectWebcamWallCornersTests(unittest.TestCase):
    def test_detects_tall_illuminated_quadrilateral(self):
        off = np.full((600, 900, 3), 24, dtype=np.uint8)
        on = off.copy()
        expected = np.asarray(((470, 35), (610, 42), (590, 565), (450, 552)), dtype=np.int32)
        cv2.fillConvexPoly(on, expected, (180, 210, 245))
        cv2.circle(on, (525, 220), 28, (24, 24, 24), -1)
        corners, diagnostics = detect_panel_corners(on, off)
        np.testing.assert_allclose(corners, expected, atol=8)
        self.assertGreater(diagnostics["confidence"], 0.60)


if __name__ == "__main__":
    unittest.main()
