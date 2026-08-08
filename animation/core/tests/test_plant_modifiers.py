"""Contract tests for composable plant modifier state and geometry."""

import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation import AnimationBase
from animation.core.plant_awareness import (
    GLOBE_REGION_ORDER, PLANT_MODIFIER_IDS, PlantModifierState,
)


class _Controller:
    strip_count = 8
    leds_per_strip = 12
    total_leds = strip_count * leds_per_strip


class _Animation(AnimationBase):
    PLANT_MODIFIER_SUPPORT = frozenset(("illuminate", "obstacle"))

    def generate_frame(self, time_elapsed, frame_count):
        return self.next_frame_buffer()


class PlantModifierStateTests(unittest.TestCase):
    def test_catalog_and_canonical_defaults(self):
        self.assertEqual(len(PLANT_MODIFIER_IDS), 14)
        self.assertIn("hue_shift", PLANT_MODIFIER_IDS)
        self.assertIn("liquid_glass", PLANT_MODIFIER_IDS)
        state = PlantModifierState.from_payload({
            "active": ["obstacle", "illuminate"], "strengths": {}
        })
        self.assertEqual(state.active, ("illuminate", "obstacle"))
        self.assertEqual(state.strength("illuminate"), 0.5)
        self.assertEqual(state.strength("obstacle"), 1.0)
        self.assertEqual(PlantModifierState.from_payload(state.to_dict()), state)

    def test_rejects_invalid_ids_strengths_and_exclusive_groups(self):
        invalid = (
            {"active": ["shadow", "shadow"]},
            {"active": ["unknown"]},
            {"active": ["attractor", "repulsor"]},
            {"active": ["portal", "hazard"]},
            {"active": ["shadow"], "strengths": {"shadow": True}},
            {"active": ["shadow"], "strengths": {"shadow": math.nan}},
            {"active": ["shadow"], "strengths": {"shadow": 1.01}},
            {"active": [], "strengths": {"unknown": 0.5}},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                PlantModifierState.from_payload(payload)

    def test_helpers_require_declared_plugin_support(self):
        animation = _Animation(_Controller(), {"plant_modifiers": {
            "active": ["illuminate", "emitter"],
            "strengths": {"illuminate": 0.75, "emitter": 1.0},
        }})
        self.assertTrue(animation.plant_modifier_enabled("illuminate"))
        self.assertEqual(animation.plant_modifier_strength("illuminate"), 0.75)
        self.assertFalse(animation.plant_modifier_enabled("emitter"))
        self.assertEqual(animation.plant_modifier_strength("emitter"), 0.0)
        self.assertEqual(animation.get_info()["unsupported_plant_modifiers"], ["emitter"])

    def test_framework_visual_modifiers_are_universally_supported(self):
        animation = _Animation(_Controller(), {"plant_modifiers": {
            "active": ["hue_shift", "liquid_glass"],
            "strengths": {"hue_shift": 0.75, "liquid_glass": 0.6},
        }})
        self.assertTrue(animation.plant_modifier_enabled("hue_shift"))
        self.assertTrue(animation.plant_modifier_enabled("liquid_glass"))
        self.assertIn("hue_shift", animation.get_info()["plant_modifier_support"])
        self.assertIn("liquid_glass", animation.get_info()["plant_modifier_support"])
        self.assertFalse(animation.get_info()["unsupported_plant_modifiers"])

    def test_framework_visual_modifier_postprocess_and_zero_strength_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            foliage = Path(directory) / "foliage.json"
            globes = Path(directory) / "globes.json"
            foliage.write_text(json.dumps({"covered_indices": [25, 26, 37]}))
            globes.write_text(json.dumps({"globe_indices": [50]}))
            paths = {
                "plant_mask_path": str(foliage),
                "plant_globe_mask_path": str(globes),
            }
            source = np.zeros((_Controller.total_leds, 3), dtype=np.uint8)
            source[:, 0] = np.arange(_Controller.total_leds, dtype=np.uint8) + 80
            source[:, 1] = 45
            source[:, 2] = 15

            zero = _Animation(_Controller(), {**paths, "plant_modifiers": {
                "active": ["hue_shift", "liquid_glass"],
                "strengths": {"hue_shift": 0.0, "liquid_glass": 0.0},
            }})
            self.assertIs(zero.apply_framework_plant_modifiers(source), source)

            hue = _Animation(_Controller(), {**paths, "plant_modifiers": {
                "active": ["hue_shift"], "strengths": {"hue_shift": 0.8},
            }})
            hue_frame = hue.apply_framework_plant_modifiers(source)
            self.assertFalse(np.array_equal(hue_frame[25], source[25]))
            np.testing.assert_array_equal(hue_frame[0], source[0])
            self.assertFalse(np.shares_memory(hue_frame, source))

            glass = _Animation(_Controller(), {**paths, "plant_modifiers": {
                "active": ["liquid_glass"], "strengths": {"liquid_glass": 0.9},
            }})
            glass_frame = glass.apply_framework_plant_modifiers(source)
            self.assertFalse(np.array_equal(glass_frame, source))
            self.assertIs(
                glass.apply_framework_plant_modifiers(source, changed=False),
                glass_frame,
            )
            glass.update_parameters({"plant_modifiers": {
                "active": ["liquid_glass"], "strengths": {"liquid_glass": 0.2},
            }})
            self.assertTrue(glass.framework_plant_modifier_refresh_pending())
            refreshed = glass.apply_framework_plant_modifiers(source, changed=False)
            self.assertFalse(np.array_equal(refreshed, glass_frame))
            self.assertFalse(glass.framework_plant_modifier_refresh_pending())

    def test_live_updates_refresh_cached_modifier_state(self):
        animation = _Animation(_Controller())

        animation.update_parameters({"plant_modifiers": {
            "active": ["illuminate"], "strengths": {"illuminate": 0.75},
        }})
        self.assertTrue(animation.plant_modifier_enabled("illuminate"))
        self.assertEqual(animation.plant_modifier_strength("illuminate"), 0.75)

        animation.update_parameters({"plant_modifiers": {"active": []}})
        self.assertFalse(animation.plant_modifier_enabled("illuminate"))

    def test_live_updates_refresh_legacy_plant_aware_bridge(self):
        animation = _Animation(_Controller())

        animation.update_parameters({"plant_aware": True})
        self.assertEqual(
            animation.plant_modifier_state().active, ("illuminate", "obstacle")
        )

        animation.update_parameters({"plant_aware": False})
        self.assertFalse(animation.plant_modifier_state().active)

    def test_missing_companion_mask_returns_wholly_empty_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            foliage = Path(directory) / "foliage.json"
            foliage.write_text(json.dumps({"covered_indices": [3, 4]}))
            animation = _Animation(_Controller(), {
                "plant_mask_path": str(foliage),
                "plant_globe_mask_path": str(Path(directory) / "missing.json"),
            })
            geometry = animation.get_plant_masks()
            self.assertTrue(geometry.error)
            self.assertEqual(geometry.foliage_count, 0)
            self.assertEqual(geometry.globe_count, 0)
            self.assertFalse(np.any(geometry.obstacle))

    def test_installed_geometry_exposes_derivatives_and_ordered_regions(self):
        class InstalledController:
            strip_count = 32
            leds_per_strip = 138
            total_leds = strip_count * leds_per_strip
        geometry = _Animation(InstalledController()).get_plant_masks()
        self.assertFalse(geometry.error)
        for value in (geometry.foliage_edge, geometry.globe_edge,
                      geometry.distance, geometry.normal_x, geometry.normal_y):
            self.assertEqual(value.shape, (32, 138))
        self.assertEqual(tuple(geometry.globe_region_masks), GLOBE_REGION_ORDER)
        self.assertTrue(all(np.any(mask) for mask in geometry.globe_region_masks.values()))


if __name__ == "__main__":
    unittest.main()
