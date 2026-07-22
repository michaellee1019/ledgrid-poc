"""Shared contracts for opt-in calibrated plant geometry."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


class _Controller:
    strip_count = 4
    leds_per_strip = 6
    total_leds = 24


class _Animation(AnimationBase):
    def generate_frame(self, time_elapsed, frame_count):
        return self.next_frame_buffer()


class PlantAwarenessTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        self.foliage_path.write_text(
            json.dumps({"covered_indices": [0, 7, 8, 999, "bad"]}),
            encoding="utf-8",
        )
        self.globe_path.write_text(
            json.dumps({"globe_indices": [8, 22], "region_count": 2}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def animation(self, **config):
        return _Animation(
            _Controller(),
            {
                "plant_mask_path": str(self.foliage_path),
                "plant_globe_mask_path": str(self.globe_path),
                **config,
            },
        )

    def test_common_schema_is_opt_in(self):
        animation = self.animation()
        schema = animation.get_parameter_schema()

        self.assertFalse(animation.plant_aware_enabled())
        self.assertFalse(schema["plant_aware"]["default"])
        self.assertEqual(schema["plant_clearance"]["default"], 1)

    def test_layers_are_disjoint_and_globes_take_priority(self):
        masks = self.animation(plant_clearance=0).get_plant_masks()

        self.assertEqual(masks.foliage_count, 2)
        self.assertEqual(masks.globe_count, 2)
        self.assertEqual(masks.globe_regions, 2)
        self.assertFalse(np.any(masks.foliage & masks.globes))
        self.assertTrue(masks.foliage_flat[7])
        self.assertFalse(masks.foliage_flat[8])
        self.assertTrue(masks.globes_flat[8])

    def test_clearance_dilates_without_wrapping_panel_edges(self):
        masks = self.animation(plant_clearance=1).get_plant_masks()

        self.assertTrue(masks.clearance[0, 0])
        self.assertTrue(masks.clearance[1, 1])
        self.assertFalse(masks.clearance[3, 0])

    def test_live_path_update_invalidates_cached_geometry(self):
        animation = self.animation(plant_clearance=0)
        first = animation.get_plant_masks()
        replacement = Path(self.temp_dir.name) / "replacement.json"
        replacement.write_text(json.dumps({"covered_indices": [5]}), encoding="utf-8")

        animation.update_parameters({"plant_mask_path": str(replacement)})
        second = animation.get_plant_masks()

        self.assertIsNot(first, second)
        self.assertEqual(second.foliage_count, 1)
        self.assertTrue(second.foliage_flat[5])

    def test_every_shipped_plugin_renders_with_real_plant_masks_enabled(self):
        loader = AnimationPluginLoader(allowed_plugins=AnimationManager.ALLOWED_PLUGINS)
        plugins = loader.load_all_plugins()
        controller = PreviewLEDController(DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP)
        expected_shape = (controller.total_leds, 3)

        self.assertEqual(set(plugins), AnimationManager.ALLOWED_PLUGINS)
        for name, animation_class in sorted(plugins.items()):
            with self.subTest(plugin=name):
                animation = animation_class(controller, {"plant_aware": True})
                schema = animation.get_parameter_schema()
                self.assertIn("plant_aware", schema)
                self.assertFalse(schema["plant_aware"]["default"])
                rendered = animation.generate_frame(0.0, 0)
                pixels = rendered.pixels if isinstance(rendered, RenderedFrame) else rendered
                self.assertIsInstance(pixels, np.ndarray)
                self.assertEqual(pixels.shape, expected_shape)
                self.assertEqual(pixels.dtype, np.uint8)


if __name__ == "__main__":
    unittest.main()
