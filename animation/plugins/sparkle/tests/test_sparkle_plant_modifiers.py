"""Focused field and lifecycle tests for Sparkle's plant modifiers."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.sparkle import SparkleAnimation


class SparklePlantModifierTests(unittest.TestCase):
    STRIPS = 8
    LEDS = 12

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.foliage = 2 * self.LEDS + 5
        self.globe = 6 * self.LEDS + 7
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
        return SparkleAnimation(
            PreviewLEDController(self.STRIPS, self.LEDS),
            {
                "plant_clearance": 1,
                "plant_mask_path": str(self.foliage_path),
                "plant_globe_mask_path": str(self.globe_path),
                "plant_modifiers": {
                    "version": 1,
                    "active": list(active),
                    "strengths": strengths or {},
                },
                **config,
            },
        )

    def test_declares_exact_support_and_unsupported_state_preserves_rng_and_pixels(self):
        self.assertEqual(
            SparkleAnimation.PLANT_MODIFIER_SUPPORT,
            frozenset(("illuminate", "attractor", "repulsor", "habitat", "emitter")),
        )
        frames = []
        tails = []
        for active in ((), ("shadow",)):
            np.random.seed(4567)
            animation = self.animation(active=active, strengths={"shadow": 1.0})
            frames.append(animation.generate_frame(0.0, 0).copy())
            tails.append(np.random.random())
        np.testing.assert_array_equal(frames[0], frames[1])
        self.assertEqual(tails[0], tails[1])

    def test_zero_strength_modifiers_preserve_baseline_pixels_with_same_rng(self):
        for modifier in SparkleAnimation.PLANT_MODIFIER_SUPPORT:
            np.random.seed(817)
            plain = self.animation().generate_frame(0.0, 0).copy()
            np.random.seed(817)
            zero = self.animation(
                active=(modifier,), strengths={modifier: 0.0}
            ).generate_frame(0.0, 0).copy()
            np.testing.assert_array_equal(plain, zero, err_msg=modifier)

    def test_attractor_repulsor_and_foliage_habitat_have_distinct_fields(self):
        random_values = np.ones(self.STRIPS * self.LEDS)
        results = {}
        for modifier in ("attractor", "repulsor", "habitat"):
            animation = self.animation(
                active=(modifier,), strengths={modifier: 1.0}
            )
            with patch("animation.plugins.sparkle.np.random.random", return_value=random_values):
                animation.generate_frame(0.0, 0)
            results[modifier] = animation._plant_spawn_multiplier.copy()

        foliage_neighbor = self.foliage + 1
        globe_neighbor = self.globe + 1
        far = 0
        self.assertGreater(results["attractor"][foliage_neighbor], results["attractor"][far])
        self.assertGreater(results["attractor"][globe_neighbor], results["attractor"][far])
        self.assertLess(results["repulsor"][foliage_neighbor], results["repulsor"][far])
        self.assertGreater(results["habitat"][foliage_neighbor], results["habitat"][far])
        self.assertEqual(results["habitat"][globe_neighbor], results["habitat"][far])

    def test_emitter_is_cadence_limited_bounded_and_observable(self):
        animation = self.animation(
            active=("emitter",), strengths={"emitter": 1.0},
            sparkle_probability=0.1,
        )
        random_values = np.zeros(self.STRIPS * self.LEDS)
        with patch("animation.plugins.sparkle.np.random.random", return_value=random_values):
            animation.generate_frame(0.0, 0)
            first = animation.get_runtime_stats()
            animation.generate_frame(0.01, 1)
            throttled = animation.get_runtime_stats()
            animation.generate_frame(0.1, 2)
            later = animation.get_runtime_stats()

        self.assertGreater(first["plant_emitted_last_frame"], 0)
        self.assertLessEqual(first["plant_emitted_last_frame"], 16)
        self.assertEqual(throttled["plant_emitted_last_frame"], 0)
        self.assertGreater(later["plant_emitted_total"], first["plant_emitted_total"])

    def test_illuminate_preserves_hue_and_composes_with_field_habitat_and_emitter(self):
        animation = self.animation(
            active=("illuminate", "attractor", "habitat", "emitter"),
            strengths={
                "illuminate": 1.0, "attractor": 0.5,
                "habitat": 0.6, "emitter": 0.7,
            },
            base_red=10, base_green=20, base_blue=30,
            sparkle_probability=0.001,
        )
        with patch(
            "animation.plugins.sparkle.np.random.random",
            return_value=np.ones(self.STRIPS * self.LEDS),
        ):
            frame = animation.generate_frame(0.0, 0)
        color = frame[self.foliage]
        self.assertEqual(int(color[1]), int(color[0]) * 2)
        self.assertEqual(int(color[2]), int(color[0]) * 3)
        stats = animation.get_runtime_stats()
        self.assertEqual(
            stats["plant_modifiers"],
            ["illuminate", "attractor", "habitat", "emitter"],
        )


if __name__ == "__main__":
    unittest.main()
