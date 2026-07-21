"""Deterministic coverage for opt-in plant-aware gradients."""

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


class PlantAwareGradientTests(unittest.TestCase):
    @staticmethod
    def _mask_files(root: Path, foliage=(), globes=()):
        foliage_path = root / "foliage.json"
        globe_path = root / "globes.json"
        foliage_path.write_text(
            json.dumps({"covered_indices": list(foliage)}), encoding="utf-8"
        )
        globe_path.write_text(
            json.dumps({"globe_indices": list(globes), "region_count": 1}),
            encoding="utf-8",
        )
        return foliage_path, globe_path

    def test_schema_exposes_disabled_standard_controls_and_contour_strength(self):
        schema = GradientAnimation(_Controller()).get_parameter_schema()

        self.assertFalse(schema["plant_aware"]["default"])
        self.assertEqual(schema["plant_contour_strength"]["default"], 0.85)
        self.assertEqual(schema["plant_contour_strength"]["min"], 0.0)
        self.assertEqual(schema["plant_contour_strength"]["max"], 1.0)

    def test_explicitly_disabled_mode_has_exact_default_byte_parity(self):
        config = {
            "animated": True,
            "speed": 0.4,
            "direction": "diagonal",
            "brightness": 0.73,
        }
        default = GradientAnimation(_Controller(), config).generate_frame(1.25, 7)
        disabled = GradientAnimation(
            _Controller(),
            {
                **config,
                "plant_aware": False,
                "plant_mask_path": "/not/a/real/foliage-mask.json",
                "plant_globe_mask_path": "/not/a/real/globe-mask.json",
            },
        ).generate_frame(1.25, 7)

        self.assertEqual(default.pixels.tobytes(), disabled.pixels.tobytes())

    def test_semantic_layers_anchor_distinct_phases_and_bend_neighbors(self):
        foliage = 2 * _Controller.leds_per_strip + 8
        globe = 5 * _Controller.leds_per_strip + 8
        with tempfile.TemporaryDirectory() as directory:
            foliage_path, globe_path = self._mask_files(
                Path(directory), [foliage], [globe]
            )
            config = {
                "direction": "vertical",
                "color1_red": 255, "color1_green": 0, "color1_blue": 0,
                "color2_red": 0, "color2_green": 0, "color2_blue": 255,
                "plant_aware": True,
                "plant_clearance": 1,
                "plant_contour_strength": 1.0,
                "plant_mask_path": str(foliage_path),
                "plant_globe_mask_path": str(globe_path),
            }
            aware = GradientAnimation(_Controller(), config)
            plain = GradientAnimation(
                _Controller(), {**config, "plant_aware": False}
            )
            aware_frame = aware.generate_frame(0.0, 0).pixels
            plain_frame = plain.generate_frame(0.0, 0).pixels

            self.assertGreater(int(aware_frame[foliage, 0]), int(aware_frame[foliage, 2]))
            self.assertGreater(int(aware_frame[globe, 2]), int(aware_frame[globe, 0]))
            self.assertFalse(np.array_equal(aware_frame[foliage], aware_frame[globe]))
            neighbor = foliage + 1
            self.assertFalse(np.array_equal(aware_frame[neighbor], plain_frame[neighbor]))

            # Identical inputs produce identical contour pixels across instances.
            repeat = GradientAnimation(_Controller(), config).generate_frame(0.0, 0)
            np.testing.assert_array_equal(aware_frame, repeat.pixels)

    def test_static_plant_frame_is_cached_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            foliage_path, globe_path = self._mask_files(Path(directory), [20], [90])
            animation = GradientAnimation(_Controller(), {
                "plant_aware": True,
                "plant_mask_path": str(foliage_path),
                "plant_globe_mask_path": str(globe_path),
            })
            first = animation.generate_frame(0.0, 0)
            second = animation.generate_frame(2.0, 1)

            self.assertFalse(second.changed)
            self.assertIs(second.pixels, first.pixels)

    def test_animated_semantic_anchors_remain_visually_separated(self):
        foliage, globe = 20, 90
        with tempfile.TemporaryDirectory() as directory:
            foliage_path, globe_path = self._mask_files(
                Path(directory), [foliage], [globe]
            )
            animation = GradientAnimation(_Controller(), {
                "animated": True,
                "plant_aware": True,
                "plant_clearance": 0,
                "plant_mask_path": str(foliage_path),
                "plant_globe_mask_path": str(globe_path),
            })

            for elapsed in (0.0, 0.35, 1.4):
                frame = animation.generate_frame(elapsed, 0).pixels
                self.assertFalse(np.array_equal(frame[foliage], frame[globe]))


if __name__ == "__main__":
    unittest.main()
