"""Opt-in calibrated plant structure tests for Fluid Tank."""

import json
import random
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.fluid_tank import FluidTankAnimation


class PlantAwareFluidTankTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        # Canvas (x=3, y=4/5) maps to physical LED 5/4 on a ten-LED strip.
        self.foliage_path.write_text(json.dumps({"covered_indices": [35]}))
        self.globe_path.write_text(
            json.dumps({"globe_indices": [34], "region_count": 1})
        )
        self.controller = PreviewLEDController(strips=8, leds_per_strip=10)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_animation(self, **params):
        return FluidTankAnimation(self.controller, {
            "auto_hole": False,
            "plant_clearance": 0,
            "plant_mask_path": str(self.foliage_path),
            "plant_globe_mask_path": str(self.globe_path),
            **params,
        })

    @staticmethod
    def _run(animation):
        frames = []
        animation.start()
        for frame_count, elapsed in enumerate((0.0, 0.04, 0.08, 0.12, 0.2)):
            frames.append(animation.generate_frame(elapsed, frame_count).copy())
        return frames, animation.volume_cells, animation.total_inflow_cells

    def test_disabled_mode_has_exact_frame_and_simulation_parity(self):
        random.seed(731)
        implicit = self._run(self.make_animation())
        random.seed(731)
        explicit = self._run(self.make_animation(plant_aware=False))

        for left, right in zip(implicit[0], explicit[0]):
            np.testing.assert_array_equal(left, right)
        self.assertEqual(implicit[1:], explicit[1:])

    def test_bubble_routes_around_masked_structure(self):
        animation = self.make_animation(plant_aware=True)
        animation.start()
        animation._refresh_plant_geometry()
        animation.volume_cells = animation.capacity_cells
        animation.bubbles = [{
            "x": 3.0, "origin_y": 8.5, "y": 5.25, "radius": 0.6,
            "vy": -1.0, "phase": 0.0, "age": 0.0,
        }]

        animation._update_bubbles(0.1, 0.1)

        self.assertEqual(len(animation.bubbles), 1)
        bubble = animation.bubbles[0]
        self.assertNotEqual(round(bubble["x"]), 3)
        self.assertFalse(animation._plant_clearance[
            int(round(bubble["y"])), int(round(bubble["x"]))
        ])
        self.assertGreater(animation._plant_flow_deflections, 0)

    def test_foliage_and_globe_are_distinct_visible_tank_landmarks(self):
        animation = self.make_animation(plant_aware=True, caustic_strength=0.0)
        animation.start()
        animation.volume_cells = animation.capacity_cells

        frame = animation.generate_frame(0.0, 0)
        # Canonical flat coordinates use the physical mask indices directly.
        foliage = frame[35]
        globe = frame[34]
        self.assertGreater(int(foliage[1]), int(foliage[0]))
        self.assertGreater(int(globe[0]), int(globe[2]))
        self.assertFalse(np.array_equal(foliage, globe))
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["plant_foliage_pixels"], 1)
        self.assertEqual(stats["plant_globe_pixels"], 1)

    def test_plant_controls_are_schema_valid_and_live_reload_geometry(self):
        animation = self.make_animation(plant_aware=True)
        schema = animation.get_parameter_schema()
        self.assertIn("plant_aware", schema)
        self.assertIn("plant_flow_deflection", schema)
        animation._refresh_plant_geometry()
        self.assertTrue(animation._plant_foliage[4, 3])

        self.foliage_path.write_text(json.dumps({"covered_indices": [25]}))
        animation.update_parameters({"plant_mask_path": str(self.foliage_path)})
        animation._refresh_plant_geometry()
        self.assertTrue(animation._plant_foliage[4, 2])
        self.assertFalse(animation._plant_foliage[4, 3])


if __name__ == "__main__":
    unittest.main()
