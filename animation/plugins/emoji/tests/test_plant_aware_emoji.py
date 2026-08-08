"""Deterministic coverage for opt-in plant-aware emoji placement."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.plugins.emoji import EmojiAnimation


class _Controller:
    strip_count = 14
    leds_per_strip = 30
    total_leds = strip_count * leds_per_strip
    debug = False


class PlantAwareEmojiTests(unittest.TestCase):
    def _mask_files(self, root: Path, foliage=(), globes=()):
        foliage_path = root / "foliage.json"
        globe_path = root / "globes.json"
        foliage_path.write_text(
            json.dumps({"covered_indices": sorted(foliage)}), encoding="utf-8"
        )
        globe_path.write_text(
            json.dumps({"globe_indices": sorted(globes)}), encoding="utf-8"
        )
        return foliage_path, globe_path

    @staticmethod
    def _glyph_indices(pattern, start_strip, start_led):
        return {
            (start_strip + row) * _Controller.leds_per_strip + start_led + col
            for row, line in enumerate(pattern)
            for col, cell in enumerate(line)
            if cell != "."
        }

    def test_schema_exposes_standard_disabled_controls(self):
        schema = EmojiAnimation(_Controller()).get_parameter_schema()

        self.assertFalse(schema["plant_aware"]["default"])
        self.assertEqual(schema["plant_clearance"]["default"], 1)
        self.assertIn("plant_mask_path", schema)
        self.assertIn("plant_globe_mask_path", schema)

    def test_explicitly_disabled_mode_has_exact_default_render_parity(self):
        config = {"emoji": "smile", "pulse_speed": 0.7, "brightness": 0.8}
        default = EmojiAnimation(_Controller(), config).generate_frame(1.25, 4)
        disabled = EmojiAnimation(
            _Controller(), {**config, "plant_aware": False}
        ).generate_frame(1.25, 4)

        np.testing.assert_array_equal(default, disabled)

    def test_synthetic_mask_relocates_expression_to_clear_pixels(self):
        pattern = EmojiAnimation.EMOJI_PATTERNS["smile"]
        preferred_strip = (_Controller.strip_count - len(pattern)) // 2
        preferred_led = (_Controller.leds_per_strip - len(pattern[0])) // 2
        covered = self._glyph_indices(pattern, preferred_strip, preferred_led)

        with tempfile.TemporaryDirectory() as directory:
            foliage_path, globe_path = self._mask_files(
                Path(directory), covered, sorted(covered)[:4]
            )
            animation = EmojiAnimation(_Controller(), {
                "plant_aware": True,
                "plant_clearance": 0,
                "plant_mask_path": str(foliage_path),
                "plant_globe_mask_path": str(globe_path),
            })
            animation.generate_frame(0.0, 0)
            start_strip, start_led = animation._plant_layout_origin
            relocated = self._glyph_indices(pattern, start_strip, start_led)

            self.assertNotEqual(
                animation._plant_layout_origin, (preferred_strip, preferred_led)
            )
            self.assertTrue(relocated.isdisjoint(covered))
            self.assertGreater(animation.get_runtime_stats()["plant_avoided_weight"], 0)

    def test_enabled_render_uses_distinct_foliage_and_globe_accents(self):
        foliage_index = 1
        globe_index = _Controller.total_leds - 2
        with tempfile.TemporaryDirectory() as directory:
            foliage_path, globe_path = self._mask_files(
                Path(directory), {foliage_index}, {globe_index}
            )
            animation = EmojiAnimation(_Controller(), {
                "plant_aware": True,
                "plant_clearance": 0,
                "plant_mask_path": str(foliage_path),
                "plant_globe_mask_path": str(globe_path),
            })
            frame = animation.generate_frame(0.5, 0)

            foliage = frame[foliage_index]
            globe = frame[globe_index]
            self.assertGreater(int(foliage[1]), int(foliage[0]))
            self.assertGreater(int(globe[2]), int(globe[1]))
            self.assertFalse(np.array_equal(foliage, globe))


if __name__ == "__main__":
    unittest.main()
