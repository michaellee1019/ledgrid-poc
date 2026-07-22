"""Focused composition tests for Gradient's plant modifiers."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.plugins.gradient import GradientAnimation


class _Controller:
    strip_count = 8
    leds_per_strip = 16
    total_leds = strip_count * leds_per_strip
    debug = False


class GradientPlantModifierTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.foliage = 2 * _Controller.leds_per_strip + 8
        self.globe = 5 * _Controller.leds_per_strip + 8
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        self.foliage_path.write_text(
            json.dumps({"covered_indices": [self.foliage]}), encoding="utf-8"
        )
        self.globe_path.write_text(
            json.dumps({"globe_indices": [self.globe], "region_count": 1}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.directory.cleanup()

    def animation(self, active=(), strengths=None, **config):
        return GradientAnimation(_Controller(), {
            "color1_red": 24, "color1_green": 36, "color1_blue": 48,
            "color2_red": 72, "color2_green": 96, "color2_blue": 120,
            "plant_clearance": 1,
            "plant_mask_path": str(self.foliage_path),
            "plant_globe_mask_path": str(self.globe_path),
            "plant_modifiers": {
                "version": 1,
                "active": list(active),
                "strengths": strengths or {},
            },
            **config,
        })

    def test_declares_exact_support_and_unsupported_modifier_is_noop(self):
        self.assertEqual(
            GradientAnimation.PLANT_MODIFIER_SUPPORT,
            frozenset(("illuminate", "shadow", "refract")),
        )
        plain = self.animation().generate_frame(0.0, 0).pixels
        unsupported = self.animation(
            active=("emitter",), strengths={"emitter": 1.0}
        ).generate_frame(0.0, 0).pixels
        np.testing.assert_array_equal(plain, unsupported)

    def test_zero_strengths_are_exact_noops_and_nonzero_strengths_are_visible(self):
        plain = self.animation().generate_frame(0.0, 0).pixels.copy()
        for modifier in GradientAnimation.PLANT_MODIFIER_SUPPORT:
            zero = self.animation(
                active=(modifier,), strengths={modifier: 0.0}
            ).generate_frame(0.0, 0).pixels
            np.testing.assert_array_equal(plain, zero, err_msg=modifier)

        illuminated = self.animation(
            active=("illuminate",), strengths={"illuminate": 1.0}
        ).generate_frame(0.0, 0).pixels
        shadowed = self.animation(
            active=("shadow",), strengths={"shadow": 1.0}
        ).generate_frame(0.0, 0).pixels
        refracted = self.animation(
            active=("refract",), strengths={"refract": 1.0}
        ).generate_frame(0.0, 0).pixels
        self.assertGreater(int(illuminated[self.foliage].sum()), int(plain[self.foliage].sum()))
        self.assertLess(int(shadowed[self.foliage].sum()), int(plain[self.foliage].sum()))
        self.assertFalse(np.array_equal(refracted, plain))

    def test_shadow_then_illuminate_keeps_dark_core_and_lights_boundary(self):
        illuminated = self.animation(
            active=("illuminate",), strengths={"illuminate": 1.0}
        ).generate_frame(0.0, 0).pixels
        combined = self.animation(
            active=("illuminate", "shadow"),
            strengths={"illuminate": 1.0, "shadow": 1.0},
        ).generate_frame(0.0, 0).pixels
        plain = self.animation().generate_frame(0.0, 0).pixels
        self.assertLess(int(combined[self.foliage].sum()), int(illuminated[self.foliage].sum()))
        # The one-cell semantic core is also its boundary, so illuminate leaves
        # a legible outline instead of reducing it to full black.
        self.assertGreater(int(combined[self.foliage].sum()), 0)
        self.assertFalse(np.array_equal(combined, plain))

    def test_static_composition_recaches_once_after_live_strength_change(self):
        animation = self.animation(
            active=("shadow",), strengths={"shadow": 0.25}
        )
        first = animation.generate_frame(0.0, 0)
        cached = animation.generate_frame(1.0, 1)
        self.assertFalse(cached.changed)
        self.assertIs(cached.pixels, first.pixels)

        animation.update_parameters({"plant_modifiers": {
            "version": 1, "active": ["shadow"], "strengths": {"shadow": 0.9}
        }})
        changed = animation.generate_frame(1.0, 2)
        recached = animation.generate_frame(2.0, 3)
        self.assertTrue(changed.changed)
        self.assertFalse(recached.changed)
        self.assertFalse(np.array_equal(first.pixels, changed.pixels))
        self.assertEqual(animation.get_runtime_stats()["plant_modifier_strengths"], {"shadow": 0.9})


if __name__ == "__main__":
    unittest.main()
