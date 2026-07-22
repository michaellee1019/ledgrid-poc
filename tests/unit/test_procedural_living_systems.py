"""Behavioral contract for the five emergent living-system plugins."""

import importlib
import json
from pathlib import Path
import unittest

import numpy as np

from animation.core.base import RenderedFrame


PLUGINS = {
    "wind_in_the_reeds": "WindInTheReedsAnimation",
    "reaction_diffusion_garden": "ReactionDiffusionGardenAnimation",
    "physarum_network": "PhysarumNetworkAnimation",
    "firefly_synchrony": "FireflySynchronyAnimation",
    "cyclic_reef": "CyclicReefAnimation",
}


class Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


def animation(plugin, params=None):
    module = importlib.import_module(f"animation.plugins.{plugin}")
    return getattr(module, PLUGINS[plugin])(Controller(), params or {})


def pixels(output):
    return output.pixels if isinstance(output, RenderedFrame) else output


def advance(item, count=16, step=.08):
    for i in range(count + 1):
        item.generate_frame(i * step, i)


class ProceduralLivingSystemsTests(unittest.TestCase):
    def test_discovery_manifest_frame_shape_and_dtype(self):
        for plugin, class_name in PLUGINS.items():
            with self.subTest(plugin=plugin):
                manifest = json.loads(Path(f"animation/plugins/{plugin}/manifest.json").read_text())
                self.assertEqual(manifest["plugin_id"], plugin)
                self.assertEqual(manifest["class"], class_name)
                output = animation(plugin).generate_frame(0, 0)
                self.assertIsInstance(output, RenderedFrame)
                self.assertEqual(pixels(output).shape, (4416, 3))
                self.assertEqual(pixels(output).dtype, np.uint8)
                self.assertTrue(pixels(output).flags.c_contiguous)

    def test_source_rate_cache_returns_unchanged(self):
        for plugin in PLUGINS:
            with self.subTest(plugin=plugin):
                item = animation(plugin)
                first = item.generate_frame(0, 0)
                cached = item.generate_frame(.001, 1)
                self.assertTrue(first.changed)
                self.assertFalse(cached.changed)
                self.assertIs(first.pixels, cached.pixels)

    def test_dedicated_seeds_are_deterministic(self):
        for plugin in PLUGINS:
            with self.subTest(plugin=plugin):
                a, b = animation(plugin, {"seed": 404}), animation(plugin, {"seed": 404})
                advance(a); advance(b)
                self.assertEqual(a.logical_state(), b.logical_state())

    def test_schema_and_live_presentation_parameters(self):
        for plugin in PLUGINS:
            with self.subTest(plugin=plugin):
                item = animation(plugin)
                schema = item.get_parameter_schema()
                for key in ("motion", "density", "mood", "brightness", "seed", "simulation_hz", "render_fps", "plant_aware"):
                    self.assertIn(key, schema)
                item.generate_frame(0, 0)
                before = item.logical_state()
                item.update_parameters({"mood": "ember", "brightness": .2})
                self.assertEqual(before, item.logical_state())
                self.assertTrue(item.generate_frame(.001, 1).changed)

    def test_visual_controls_do_not_change_logical_simulation(self):
        visual = {
            "wind_in_the_reeds": {"mood":"ember", "brightness":.2, "motes":1.5},
            "reaction_diffusion_garden": {"mood":"dusk", "brightness":.2, "edge_glow":1.4, "color_by_age":0},
            "physarum_network": {"mood":"violet", "brightness":.2, "pulse_visibility":1},
            "firefly_synchrony": {"mood":"ember", "brightness":.2, "pulse_softness":1, "meadow_glow":.4},
            "cyclic_reef": {"mood":"violet", "brightness":.2, "edge_glow":1.4},
        }
        for plugin, params in visual.items():
            with self.subTest(plugin=plugin):
                a = animation(plugin, {"seed": 51})
                b = animation(plugin, {"seed": 51, **params})
                advance(a, 25); advance(b, 25)
                self.assertEqual(a.logical_state(), b.logical_state())

    def test_modifier_off_and_zero_strength_have_exact_parity(self):
        modifiers = {
            "wind_in_the_reeds":"slow_zone", "reaction_diffusion_garden":"hazard",
            "physarum_network":"attractor", "firefly_synchrony":"emitter", "cyclic_reef":"hazard",
        }
        for plugin, modifier in modifiers.items():
            with self.subTest(plugin=plugin):
                base = animation(plugin, {"seed": 88, "plant_aware":False})
                zero = animation(plugin, {"seed":88, "plant_aware":False,
                    "plant_modifiers":{"version":1,"active":[modifier],"strengths":{modifier:0.0}}})
                for i in range(18):
                    pa = pixels(base.generate_frame(i*.08, i)).copy()
                    pb = pixels(zero.generate_frame(i*.08, i)).copy()
                    self.assertTrue(np.array_equal(pa, pb))
                self.assertEqual(base.logical_state(), zero.logical_state())
                self.assertEqual(base.rng.bit_generator.state, zero.rng.bit_generator.state)

    def test_core_semantics_are_observable_and_bounded(self):
        reeds = animation("wind_in_the_reeds", {"wind":1.3,"gustiness":1.2})
        advance(reeds, 40)
        self.assertGreater(float(np.max(np.abs(reeds.bend))), .05)
        self.assertLessEqual(reeds.base_x.size, 96)

        garden = animation("reaction_diffusion_garden")
        initial = garden.v.copy(); advance(garden, 80)
        self.assertGreater(np.count_nonzero(np.abs(garden.v-initial) > 1e-4), 8)

        mold = animation("physarum_network", {"agent_count":99999})
        advance(mold, 30)
        self.assertLessEqual(mold.x.size, mold.MAX_AGENTS)
        self.assertGreater(float(mold.trail.max()), .05)

        flies = animation("firefly_synchrony", {"population":180})
        flies.phase.fill(0); flies.generate_frame(0, 0)
        self.assertLessEqual(flies._last_peak_count, max(1, int(flies.x.size * flies.MAX_PEAK_FRACTION)))
        self.assertLessEqual(flies.x.size, flies.MAX_FIREFLIES)

        reef = animation("cyclic_reef", {"grazer_density":1.5})
        before = reef.state.copy(); advance(reef, 25)
        self.assertGreater(np.count_nonzero(reef.state != before), 30)
        self.assertLessEqual(reef.gx.size, 40)

    def test_three_curated_presets_are_schema_valid_and_distinct(self):
        for plugin in PLUGINS:
            with self.subTest(plugin=plugin):
                fingerprints = set()
                for preset_id in ("quiet", "showcase", "night"):
                    path = Path(f"animation/plugins/{plugin}/presets/{preset_id}.json")
                    payload = json.loads(path.read_text())
                    self.assertEqual(payload["preset_id"], preset_id)
                    self.assertEqual(payload["animation"], plugin)
                    self.assertIs(payload["params"]["plant_aware"], True)
                    item = animation(plugin, payload["params"])
                    schema = item.get_parameter_schema()
                    self.assertTrue(set(payload["params"]).issubset(schema))
                    item.generate_frame(0, 0)
                    item.generate_frame(.2, 1)
                    frame = pixels(item.generate_frame(.4, 2))
                    fingerprints.add(hash(frame.tobytes()))
                self.assertEqual(len(fingerprints), 3)


if __name__ == "__main__":
    unittest.main()
