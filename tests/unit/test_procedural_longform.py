"""Behavior and render-contract coverage for long-form procedural scenes."""

import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.base import RenderedFrame
from animation.core.plugin_loader import AnimationPluginLoader


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_IDS = (
    "moonlit_fog_banks", "desert_wind", "circadian_window", "night_train_windows",
)


class Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip
    debug = False


def pixels(rendered):
    return rendered.pixels if isinstance(rendered, RenderedFrame) else rendered


class ProceduralLongformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = AnimationPluginLoader(allowed_plugins=PLUGIN_IDS)
        cls.plugins = cls.loader.load_all_plugins()

    def test_plugins_discover_and_render_canonical_frames(self):
        self.assertEqual(set(self.plugins), set(PLUGIN_IDS))
        fingerprints = set()
        for plugin_id, animation_class in self.plugins.items():
            with self.subTest(plugin=plugin_id):
                animation = animation_class(Controller(), {"seed": 91})
                rendered = animation.generate_frame(11.0, 2200)
                frame = pixels(rendered)
                self.assertEqual(frame.shape, (4416, 3))
                self.assertEqual(frame.dtype, np.uint8)
                self.assertTrue(frame.flags.c_contiguous)
                self.assertGreater(int(frame.max()), 0)
                fingerprints.add(frame.tobytes())
        self.assertEqual(len(fingerprints), len(PLUGIN_IDS))

    def test_source_rate_cache_and_live_parameter_invalidation(self):
        for plugin_id, animation_class in self.plugins.items():
            with self.subTest(plugin=plugin_id):
                animation = animation_class(Controller(), {"render_fps": 20})
                first = animation.generate_frame(1.0, 200)
                cached = animation.generate_frame(1.01, 202)
                self.assertTrue(first.changed)
                self.assertFalse(cached.changed)
                animation.update_parameters({"mood": animation.MOODS[-1]})
                refreshed = animation.generate_frame(1.01, 203)
                self.assertTrue(refreshed.changed)

    def test_seeded_scenes_are_deterministic(self):
        for plugin_id, animation_class in self.plugins.items():
            config = {"seed": 12345, "hour": 8.0} if plugin_id == "circadian_window" else {"seed": 12345}
            left = pixels(animation_class(Controller(), config).generate_frame(19.5, 3900)).copy()
            right = pixels(animation_class(Controller(), config).generate_frame(19.5, 3900)).copy()
            with self.subTest(plugin=plugin_id):
                np.testing.assert_array_equal(left, right)

    def test_zero_strength_modifier_is_exact_no_op(self):
        zero = {"version": 1, "active": ["shadow"], "strengths": {"shadow": 0.0}}
        for plugin_id, animation_class in self.plugins.items():
            config = {"seed": 66, "hour": 12.0} if plugin_id == "circadian_window" else {"seed": 66}
            off = pixels(animation_class(Controller(), config).generate_frame(4.0, 800)).copy()
            on = pixels(animation_class(Controller(), {**config, "plant_modifiers": zero}).generate_frame(4.0, 800)).copy()
            with self.subTest(plugin=plugin_id):
                np.testing.assert_array_equal(off, on)

    def test_motion_density_and_mood_are_orthogonal_schema_controls(self):
        for plugin_id, animation_class in self.plugins.items():
            with self.subTest(plugin=plugin_id):
                animation = animation_class(Controller())
                schema = animation.get_parameter_schema()
                self.assertIn("motion", schema)
                self.assertIn("density", schema)
                self.assertGreaterEqual(len(schema["mood"]["options"]), 3)
                low = pixels(animation_class(Controller(), {"seed": 7, "density": 0.1}).generate_frame(7.0, 0)).copy()
                high = pixels(animation_class(Controller(), {"seed": 7, "density": 0.9}).generate_frame(7.0, 0)).copy()
                self.assertFalse(np.array_equal(low, high))

    def test_circadian_fixed_hour_isolated_from_wall_clock(self):
        cls = self.plugins["circadian_window"]
        midnight = pixels(cls(Controller(), {"hour": 0.0, "time_scale": 0.0}).generate_frame(0.0, 0)).copy()
        noon = pixels(cls(Controller(), {"hour": 12.0, "time_scale": 0.0}).generate_frame(0.0, 0)).copy()
        self.assertGreater(float(noon.mean()), float(midnight.mean()) * 2.0)

    def test_three_curated_presets_per_plugin_are_valid_and_distinct(self):
        for plugin_id, animation_class in self.plugins.items():
            paths = sorted((ROOT / "animation" / "plugins" / plugin_id / "presets").glob("*.json"))
            with self.subTest(plugin=plugin_id):
                self.assertEqual(len(paths), 3)
                fingerprints = set()
                for path in paths:
                    payload = json.loads(path.read_text())
                    self.assertEqual(payload["animation"], plugin_id)
                    self.assertTrue(payload["params"]["plant_aware"])
                    config = dict(payload["params"])
                    if plugin_id == "circadian_window" and config.get("hour", -1) < 0:
                        config["hour"] = 12.0
                    frame = pixels(animation_class(Controller(), config).generate_frame(3.0, 0))
                    fingerprints.add(frame.tobytes())
                self.assertEqual(len(fingerprints), 3)


if __name__ == "__main__":
    unittest.main()
