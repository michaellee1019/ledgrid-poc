"""Canopy Cup delegates plant handling to Scene v2 composition."""

import unittest

from animation.core.manager import PreviewLEDController
from animation.plugins.canopy_cup import CanopyCupAnimation


class CanopyCupCompositionBoundaryTests(unittest.TestCase):
    def test_legacy_calibration_and_modifier_inputs_cannot_reenter_component_state(self):
        controller = PreviewLEDController(33, 138)
        for parameters in (
            {"plant_aware": True},
            {"plant_modifiers": {"version": 1, "active": [], "strengths": {}}},
            {"plant_mask_path": "config/plant_pixel_map_32x138.json"},
            {"plant_globe_mask_path": "config/plant_globe_map_32x138.json"},
            {"plant_clearance": 1},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                CanopyCupAnimation(controller, parameters)


if __name__ == "__main__":
    unittest.main()
