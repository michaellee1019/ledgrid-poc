"""Acceptance tests for the first five full-height procedural atmospheres."""

import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.base import RenderedFrame
from animation.core.manager import PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader


PLUGIN_IDS = (
    "rain_on_glass", "aurora_curtains", "cloud_canyon",
    "waterfall_veil", "tidal_bioluminescence",
)


class ProceduralAtmosphereTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = AnimationPluginLoader(allowed_plugins=PLUGIN_IDS)
        cls.plugins = cls.loader.load_all_plugins()
        cls.controller = PreviewLEDController(strips=32, leds_per_strip=138)

    @staticmethod
    def pixels(rendered):
        return rendered.pixels if isinstance(rendered, RenderedFrame) else rendered

    def test_discovery_manifest_concrete_class_and_frame_contract(self):
        self.assertEqual(set(self.plugins), set(PLUGIN_IDS))
        for plugin_id, plugin_class in self.plugins.items():
            with self.subTest(plugin=plugin_id):
                self.assertEqual(plugin_class.__module__, f"animation.plugins.{plugin_id}")
                animation = plugin_class(self.controller)
                rendered = animation.generate_frame(0.0, 0)
                frame = self.pixels(rendered)
                self.assertEqual(frame.shape, (32 * 138, 3))
                self.assertEqual(frame.dtype, np.uint8)
                self.assertTrue(frame.flags.c_contiguous)
                logical = frame.reshape(32, 138, 3)
                self.assertGreater(int(logical[:, :6].max()), 0)
                self.assertGreater(int(logical[:, -6:].max()), 0)

    def test_source_cadence_returns_cached_unchanged_frame(self):
        for plugin_id, plugin_class in self.plugins.items():
            with self.subTest(plugin=plugin_id):
                animation = plugin_class(self.controller, {"source_fps": 20.0})
                first = animation.generate_frame(1.0, 0)
                cached = animation.generate_frame(1.02, 1)
                advanced = animation.generate_frame(1.051, 2)
                self.assertTrue(first.changed)
                self.assertFalse(cached.changed)
                self.assertIs(first.pixels, cached.pixels)
                self.assertTrue(advanced.changed)
                self.assertFalse(np.array_equal(first.pixels, advanced.pixels))

    def test_seed_and_call_sequence_are_deterministic(self):
        for plugin_class in self.plugins.values():
            left = plugin_class(self.controller, {"seed": 8181})
            right = plugin_class(self.controller, {"seed": 8181})
            for index, elapsed in enumerate((0.0, .04, .09, .17)):
                np.testing.assert_array_equal(
                    self.pixels(left.generate_frame(elapsed, index)),
                    self.pixels(right.generate_frame(elapsed, index)),
                )

    def test_scenes_and_density_have_distinct_visual_structure(self):
        fingerprints = set()
        for plugin_class in self.plugins.values():
            animation = plugin_class(self.controller, {"seed": 919, "brightness": .55})
            animation.generate_frame(0.0, 0)
            frame = self.pixels(animation.generate_frame(.12, 1))
            fingerprints.add(hash(frame.tobytes()))
            sparse = plugin_class(self.controller, {"seed": 919, "density": .05})
            dense = plugin_class(self.controller, {"seed": 919, "density": .95})
            self.assertFalse(np.array_equal(
                self.pixels(sparse.generate_frame(0.0, 0)),
                self.pixels(dense.generate_frame(0.0, 0)),
            ))
        self.assertEqual(len(fingerprints), len(PLUGIN_IDS))

    def test_schema_and_live_visual_parameters(self):
        for plugin_class in self.plugins.values():
            animation = plugin_class(self.controller, {"seed": 17})
            schema = animation.get_parameter_schema()
            for key in ("motion", "density", "mood", "brightness", "source_fps", "seed"):
                self.assertIn(key, schema)
            before = self.pixels(animation.generate_frame(0.0, 0)).copy()
            animation.update_parameters({"mood": "ember", "density": .94})
            after = self.pixels(animation.generate_frame(0.0, 1))
            self.assertFalse(np.array_equal(before, after))

    def test_modifier_off_and_zero_strength_are_exact_noops(self):
        for plugin_class in self.plugins.values():
            support = sorted(plugin_class.PLANT_MODIFIER_SUPPORT)
            self.assertTrue(support)
            modifier = support[0]
            base_config = {"seed": 551, "plant_aware": False}
            empty = plugin_class(self.controller, base_config)
            zero = plugin_class(self.controller, {
                **base_config,
                "plant_modifiers": {"version": 1, "active": [modifier],
                                    "strengths": {modifier: 0.0}},
            })
            unsupported = plugin_class(self.controller, {
                **base_config,
                "plant_modifiers": {"version": 1, "active": ["hazard"],
                                    "strengths": {"hazard": 1.0}},
            })
            for index, elapsed in enumerate((0.0, .04, .1)):
                expected = self.pixels(empty.generate_frame(elapsed, index))
                np.testing.assert_array_equal(expected, self.pixels(zero.generate_frame(elapsed, index)))
                np.testing.assert_array_equal(expected, self.pixels(unsupported.generate_frame(elapsed, index)))
                self.assertEqual(empty.get_runtime_stats()["simulation_time"],
                                 zero.get_runtime_stats()["simulation_time"])

    def test_three_curated_presets_per_plugin_are_valid_and_distinct(self):
        for plugin_id, plugin_class in self.plugins.items():
            paths = list(self.loader.iter_curated_preset_files(plugin_id))
            self.assertEqual([path.stem for path in paths], ["night", "quiet", "showcase"])
            frames = []
            for path in paths:
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertIs(payload["params"]["plant_aware"], True)
                self.assertIn("plant_modifiers", payload["params"])
                animation = plugin_class(self.controller, payload["params"])
                schema = animation.get_parameter_schema()
                self.assertTrue(set(payload["params"]).issubset(schema))
                frames.append(self.pixels(animation.generate_frame(0.0, 0)).copy())
            self.assertEqual(len({frame.tobytes() for frame in frames}), 3)

    def test_key_scene_semantics(self):
        tidal = self.plugins["tidal_bioluminescence"](self.controller, {"density": .7})
        tidal_frame = self.pixels(tidal.generate_frame(0.0, 0)).reshape(32, 138, 3)
        self.assertGreater(tidal_frame[:, 78:].mean(), tidal_frame[:, :45].mean())

        aurora = self.plugins["aurora_curtains"](self.controller, {"density": .8})
        aurora_frame = self.pixels(aurora.generate_frame(0.0, 0)).reshape(32, 138, 3)
        # Curtains leave deep gaps instead of filling the wall uniformly.
        column_energy = aurora_frame.mean(axis=(1, 2))
        self.assertGreater(column_energy.max(), column_energy.min() * 2.0)

        rain = self.plugins["rain_on_glass"](self.controller, {"density": .8})
        frame = self.pixels(rain.generate_frame(0.0, 0)).reshape(32, 138, 3)
        # Droplet tracks are narrow relative to the broad background field.
        strip_energy = frame.mean(axis=(1, 2))
        self.assertGreater(strip_energy.max(), np.median(strip_energy) * 1.25)


if __name__ == "__main__":
    unittest.main()
