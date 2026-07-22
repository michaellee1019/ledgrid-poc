"""Focused behavior tests for the mathematical light-sculpture plugin family."""
import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.base import RenderedFrame
from animation.core.manager import PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader
from animation.plugins.cellular_tapestry import CellularTapestryAnimation
from animation.plugins.flow_field_silk import FlowFieldSilkAnimation
from animation.plugins.frostwork import FrostworkAnimation
from animation.plugins.living_stained_glass import LivingStainedGlassAnimation
from animation.plugins.quasicrystal_bloom import QuasicrystalBloomAnimation


CLASSES = (FrostworkAnimation, FlowFieldSilkAnimation, LivingStainedGlassAnimation,
           QuasicrystalBloomAnimation, CellularTapestryAnimation)
IDS = ("frostwork", "flow_field_silk", "living_stained_glass",
       "quasicrystal_bloom", "cellular_tapestry")


class ProceduralLightSculptureTests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(strips=32, leds_per_strip=138)

    @staticmethod
    def pixels(frame):
        return frame.pixels if isinstance(frame, RenderedFrame) else frame

    def test_discovery_frame_contract_and_source_cache(self):
        loader = AnimationPluginLoader(); loader.scan_plugins()
        for plugin_id in IDS:
            with self.subTest(plugin=plugin_id):
                cls = loader.load_plugin(plugin_id)
                self.assertIsNotNone(cls)
                animation = cls(self.controller)
                first = animation.generate_frame(0.0, 0)
                cached = animation.generate_frame(0.01, 1)
                self.assertEqual(first.pixels.shape, (32 * 138, 3))
                self.assertEqual(first.pixels.dtype, np.uint8)
                self.assertTrue(first.pixels.flags.c_contiguous)
                self.assertTrue(first.changed); self.assertFalse(cached.changed)

    def test_seeded_determinism_schema_axes_and_live_updates(self):
        for cls in CLASSES:
            with self.subTest(plugin=cls.__name__):
                a = cls(self.controller, {"seed": 123}); b = cls(self.controller, {"seed": 123})
                for t in (0.0, .2, .8):
                    np.testing.assert_array_equal(self.pixels(a.generate_frame(t, 0)), self.pixels(b.generate_frame(t, 0)))
                schema = a.get_parameter_schema()
                for key in ("motion", "density", "mood", "brightness", "seed"):
                    self.assertIn(key, schema)
                before = self.pixels(a.generate_frame(1.0, 0)).copy()
                a.update_parameters({"mood": "showcase"})
                after = self.pixels(a.generate_frame(1.0, 0))
                self.assertFalse(np.array_equal(before, after))

    def test_modifier_off_and_zero_strength_have_exact_frame_and_state_parity(self):
        modifier = {FrostworkAnimation:"obstacle", FlowFieldSilkAnimation:"refract",
                    LivingStainedGlassAnimation:"refract", QuasicrystalBloomAnimation:"refract",
                    CellularTapestryAnimation:"obstacle"}
        for cls in CLASSES:
            with self.subTest(plugin=cls.__name__):
                base = cls(self.controller, {"seed": 77, "plant_aware": False})
                zero = cls(self.controller, {"seed": 77, "plant_aware": False,
                    "plant_modifiers":{"version":1,"active":[modifier[cls]],"strengths":{modifier[cls]:0.0}}})
                for t in (0, .3, .9):
                    np.testing.assert_array_equal(self.pixels(base.generate_frame(t, 0)), self.pixels(zero.generate_frame(t, 0)))
                self.assertEqual(base.rng.bit_generator.state, zero.rng.bit_generator.state)

    def test_frost_grows_then_a_warm_cycle_sublimates_old_crystal(self):
        frost = FrostworkAnimation(self.controller, {"seed":3,"density":1,"motion":1,"melt_cycle":1})
        initial = int(frost.occupied.sum()); frost.generate_frame(1.0, 0)
        self.assertGreater(int(frost.occupied.sum()), initial)
        frost.occupied[:] = True; frost.age[:] = 100
        before = int(frost.occupied.sum()); frost._step(40)
        self.assertLess(int(frost.occupied.sum()), before)

    def test_silk_filaments_are_advected_and_leave_multiple_age_samples(self):
        silk = FlowFieldSilkAnimation(self.controller, {"seed":4,"motion":1})
        start = silk.filaments.copy(); silk.generate_frame(.5, 0)
        self.assertFalse(np.array_equal(start[:,0], silk.filaments[:,0]))
        self.assertGreater(np.unique(silk.filaments.reshape(-1,2), axis=0).shape[0], silk.filaments.shape[0])

    def test_stained_glass_topology_evolves_slowly_without_reseeding(self):
        glass = LivingStainedGlassAnimation(self.controller, {"seed":5,"motion":1})
        seeds = glass.seeds.copy(); glass.generate_frame(0,0); first=glass.pane_ids.copy()
        glass.generate_frame(80,0); second=glass.pane_ids.copy()
        np.testing.assert_array_equal(seeds, glass.seeds)
        changed=np.count_nonzero(first!=second)
        self.assertGreater(changed,0); self.assertLess(changed, first.size//3)

    def test_quasicrystal_symmetry_order_changes_nonperiodic_structure(self):
        five=QuasicrystalBloomAnimation(self.controller,{"symmetry":5,"seed":2})
        twelve=QuasicrystalBloomAnimation(self.controller,{"symmetry":12,"seed":2})
        a=self.pixels(five.generate_frame(.5,0)); b=self.pixels(twelve.generate_frame(.5,0))
        self.assertFalse(np.array_equal(a,b)); self.assertGreater(np.unique(a,axis=0).shape[0],20)

    def test_cellular_tapestry_is_a_scrolling_one_dimensional_row_history(self):
        ca=CellularTapestryAnimation(self.controller,{"rule":90,"mutation":0,"motion":1,"row_interval":.1})
        for t in np.linspace(0,1,11): ca.generate_frame(float(t),0)
        self.assertGreater(ca.rows_written,2)
        for row in ca.history:
            self.assertEqual(row.shape,(32,))
        self.assertTrue(np.any(ca.history[0] != ca.history[1]))

    def test_each_plugin_has_three_distinct_schema_valid_curated_presets(self):
        root=Path(__file__).resolve().parents[2]/"animation"/"plugins"
        for plugin_id,cls in zip(IDS,CLASSES):
            frames=[]
            paths=sorted((root/plugin_id/"presets").glob("*.json"))
            self.assertEqual({p.stem for p in paths},{"quiet","showcase","night"})
            for path in paths:
                payload=json.loads(path.read_text()); self.assertIs(payload["params"]["plant_aware"],True)
                animation=cls(self.controller,payload["params"]); schema=animation.get_parameter_schema()
                for key,value in payload["params"].items():
                    self.assertIn(key,schema)
                    if "options" in schema[key]: self.assertIn(value,schema[key]["options"])
                frames.append(self.pixels(animation.generate_frame(.8,0)).copy())
            self.assertEqual(len({frame.tobytes() for frame in frames}),3)


if __name__ == "__main__": unittest.main()
