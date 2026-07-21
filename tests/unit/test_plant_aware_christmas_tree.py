"""Opt-in calibrated plant integration tests for Christmas Tree."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.plugins.christmas_tree import ChristmasTreeAnimation


class _Controller:
    strip_count = 16
    leds_per_strip = 32
    total_leds = strip_count * leds_per_strip


class PlantAwareChristmasTreeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"

        # Fixtures are described in the plugin's visual coordinates and then
        # converted to the canonical x * height + physical_led index.
        foliage = [(8, 6), (10, 15), (11, 16)]
        globes = [(9, 18)]
        self.foliage_path.write_text(json.dumps({
            "covered_indices": [self._index(x, y) for x, y in foliage],
        }))
        self.globe_path.write_text(json.dumps({
            "globe_indices": [self._index(x, y) for x, y in globes],
            "region_count": 1,
        }))

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def _index(x, logical_y):
        return x * _Controller.leds_per_strip + (_Controller.leds_per_strip - 1 - logical_y)

    def make_animation(self, **params):
        return ChristmasTreeAnimation(_Controller(), {
            "brightness": 1.0,
            "snowfall_density": 0.0,
            "snow_layer_depth": 4,
            "tree_height": 20,
            "light_count": 18,
            "seed": 91,
            "plant_clearance": 0,
            "plant_mask_path": str(self.foliage_path),
            "plant_globe_mask_path": str(self.globe_path),
            **params,
        })

    def test_disabled_mode_preserves_legacy_layout_and_pixels(self):
        implicit = self.make_animation()
        explicit = self.make_animation(plant_aware=False)

        for elapsed in (0.0, 0.1, 0.3):
            np.testing.assert_array_equal(
                implicit.generate_frame(elapsed, 0),
                explicit.generate_frame(elapsed, 0),
            )
        self.assertEqual(implicit._tree_center, _Controller.strip_count // 2)
        self.assertEqual(implicit._tree_pixels, explicit._tree_pixels)
        self.assertEqual(implicit._light_nodes, explicit._light_nodes)

    def test_enabled_layout_protects_star_and_useful_decorations(self):
        animation = self.make_animation(plant_aware=True)
        animation._build_static_elements()

        self.assertNotEqual(animation._tree_center, _Controller.strip_count // 2)
        self.assertTrue(all(
            not animation._plant_clearance[x, y]
            for x, y, _ in animation._star_pixels
        ))
        self.assertTrue(all(
            not animation._plant_clearance[node["x"], node["y"]]
            for node in animation._light_nodes
        ))

    def test_enabled_render_uses_foliage_and_globes_as_distinct_decor(self):
        animation = self.make_animation(plant_aware=True)
        frame = animation.generate_frame(0.25, 0)
        physical = frame.reshape(_Controller.strip_count, _Controller.leds_per_strip, 3)

        foliage = physical[10, _Controller.leds_per_strip - 1 - 15]
        globe = physical[9, _Controller.leds_per_strip - 1 - 18]
        self.assertGreater(int(foliage[1]), int(foliage[0]))
        self.assertGreater(int(globe[2]), int(globe[1]))
        self.assertFalse(np.array_equal(foliage, globe))

        stats = animation.get_runtime_stats()
        self.assertTrue(stats["plant_aware"])
        self.assertEqual(stats["plant_foliage_pixels"], 3)
        self.assertEqual(stats["plant_globe_pixels"], 1)

    def test_enabled_layout_and_frame_are_deterministic(self):
        first = self.make_animation(plant_aware=True)
        second = self.make_animation(plant_aware=True)

        left = first.generate_frame(0.5, 0)
        right = second.generate_frame(0.5, 0)

        self.assertEqual(first._tree_center, second._tree_center)
        self.assertEqual(first._light_nodes, second._light_nodes)
        np.testing.assert_array_equal(left, right)

    def test_schema_exposes_common_plant_controls(self):
        schema = self.make_animation().get_parameter_schema()
        self.assertFalse(schema["plant_aware"]["default"])
        self.assertIn("plant_clearance", schema)
        self.assertIn("plant_mask_path", schema)
        self.assertIn("plant_globe_mask_path", schema)


if __name__ == "__main__":
    unittest.main()
