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
        self.assertEqual(len(PLANT_MODIFIER_IDS), 12)
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
