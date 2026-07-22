"""Plant-aware information-placement tests for the clock animation."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.plugins.clock import ClockAnimation


class _Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip
    debug = False


class _ShortController:
    strip_count = 32
    leds_per_strip = 40
    total_leds = strip_count * leds_per_strip
    debug = False


class _FixedClock(ClockAnimation):
    def _clock_now(self):
        return datetime(2026, 7, 21, 13, 47, 36, tzinfo=timezone.utc)


def _digital_marks(animation):
    marks = np.zeros((animation.width, animation.height, 3), dtype=np.float32)
    animation._draw_digital(
        marks,
        animation._clock_now(),
        animation.PALETTES["ice"],
        0.0,
    )
    return marks


class PlantAwareClockTests(unittest.TestCase):
    def test_schema_exposes_standard_opt_in_controls(self):
        schema = _FixedClock(_Controller()).get_parameter_schema()

        self.assertFalse(schema["plant_aware"]["default"])
        self.assertEqual(schema["plant_clearance"]["default"], 1)
        self.assertIn("plant_mask_path", schema)
        self.assertIn("plant_globe_mask_path", schema)

    def test_disabled_mode_has_exact_default_render_parity(self):
        config = {"face": "digital", "background": "solid", "palette": "ice"}
        default = _FixedClock(_Controller(), config).generate_frame(1.0, 0)
        disabled = _FixedClock(
            _Controller(), {**config, "plant_aware": False}
        ).generate_frame(1.0, 0)

        np.testing.assert_array_equal(default.pixels, disabled.pixels)

    def test_synthetic_masks_move_the_whole_face_out_of_occlusion(self):
        with tempfile.TemporaryDirectory() as directory:
            foliage_path = Path(directory) / "foliage.json"
            globe_path = Path(directory) / "globes.json"
            probe = _FixedClock(_ShortController(), {"show_seconds": True})
            original = _digital_marks(probe)
            indices = np.flatnonzero(np.any(original > 0, axis=2).ravel()).tolist()
            foliage_path.write_text(json.dumps({"covered_indices": indices}))
            globe_path.write_text(json.dumps({"globe_indices": indices[:8]}))

            animation = _FixedClock(_ShortController(), {
                "plant_aware": True,
                "plant_clearance": 1,
                "plant_mask_path": str(foliage_path),
                "plant_globe_mask_path": str(globe_path),
                "show_seconds": True,
            })
            placed = animation._place_away_from_plants(_digital_marks(animation))
            masks = animation.get_plant_masks()

            self.assertNotEqual(animation._plant_layout_offset, (0, 0))
            self.assertEqual(
                int(np.count_nonzero(np.any(placed > 0, axis=2) & masks.obstacle)),
                0,
            )
            self.assertEqual(np.count_nonzero(placed), np.count_nonzero(original))

    def test_real_calibrated_masks_never_increase_face_overlap(self):
        animation = _FixedClock(_Controller(), {
            "plant_aware": True,
            "plant_clearance": 2,
            "show_seconds": True,
        })
        original = _digital_marks(animation)
        masks = animation.get_plant_masks()
        before = int(np.count_nonzero(np.any(original > 0, axis=2) & masks.clearance))

        placed = animation._place_away_from_plants(original)
        after = int(np.count_nonzero(np.any(placed > 0, axis=2) & masks.clearance))

        self.assertFalse(masks.error)
        self.assertGreater(masks.foliage_count, 0)
        self.assertGreater(masks.globe_count, 0)
        self.assertLessEqual(after, before)


if __name__ == "__main__":
    unittest.main()
