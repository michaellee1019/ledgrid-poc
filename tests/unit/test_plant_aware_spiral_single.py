"""Plant-aware traversal tests for Spiral Single."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.plugins.spiral_single import SpiralSingleAnimation


class _Controller:
    strip_count = 5
    leds_per_strip = 6
    total_leds = strip_count * leds_per_strip


class PlantAwareSpiralSingleTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        self.foliage_path.write_text(json.dumps({"covered_indices": [8, 9]}))
        self.globe_path.write_text(
            json.dumps({"globe_indices": [20], "region_count": 1})
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def animation(self, **params):
        return SpiralSingleAnimation(
            _Controller(),
            {
                "pixels_per_second": 10.0,
                "plant_clearance": 0,
                "plant_mask_path": str(self.foliage_path),
                "plant_globe_mask_path": str(self.globe_path),
                **params,
            },
        )

    def test_disabled_mode_preserves_legacy_route_and_frames(self):
        implicit = self.animation()
        explicit = self.animation(plant_aware=False)

        self.assertFalse(implicit.get_parameter_schema()["plant_aware"]["default"])
        self.assertEqual(implicit.spiral_indices, explicit.spiral_indices)
        for step in range(_Controller.total_leds * 2):
            elapsed = step / 10.0
            left = implicit.generate_frame(elapsed, step).pixels.copy()
            right = explicit.generate_frame(elapsed, step).pixels.copy()
            np.testing.assert_array_equal(left, right)

    def test_enabled_route_never_uses_plant_or_clearance_pixels(self):
        animation = self.animation(plant_aware=True, plant_clearance=1)
        masks = animation.get_plant_masks()
        route, _, _ = animation._active_route()

        self.assertTrue(route)
        self.assertTrue(all(not masks.clearance_flat[index] for index in route))
        self.assertEqual(len(route), int(np.count_nonzero(masks.safe_flat)))

        visited = []
        for step in range(len(route)):
            frame = animation.generate_frame(step / 10.0, step).pixels
            lit = np.flatnonzero(np.any(frame != 0, axis=1))
            self.assertEqual(len(lit), 1)
            visited.append(int(lit[0]))
        self.assertEqual(visited, route)

    def test_removed_plant_runs_create_distinct_semantic_boundary_marks(self):
        animation = self.animation(plant_aware=True)
        route, semantics, _ = animation._active_route()

        self.assertIn(1, semantics)
        self.assertIn(2, semantics)
        foliage_step = semantics.index(1)
        globe_step = semantics.index(2)
        foliage_frame = animation.generate_frame(foliage_step / 10.0, foliage_step).pixels
        globe_frame = animation.generate_frame(globe_step / 10.0, globe_step).pixels

        self.assertEqual(tuple(foliage_frame[route[foliage_step]]), (48, 255, 80))
        self.assertEqual(tuple(globe_frame[route[globe_step]]), (255, 80, 220))
        self.assertNotEqual(route[foliage_step], 8)
        self.assertNotEqual(route[globe_step], 20)

        stats = animation.get_runtime_stats()
        self.assertEqual(stats["plant_skipped_pixels"], 3)
        self.assertEqual(stats["plant_globe_regions"], 1)


if __name__ == "__main__":
    unittest.main()
