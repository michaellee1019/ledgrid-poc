"""Plant-aware information-placement tests for the clock animation."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.plant_awareness import PLANT_MODIFIER_IDS
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


def _face_marks(animation, face):
    marks = np.zeros((animation.width, animation.height, 3), dtype=np.float32)
    getattr(animation, f"_draw_{face}")(
        marks,
        animation._clock_now(),
        animation.PALETTES["ice"],
        1.25,
    )
    return marks


def _physical_indices_from_visual(mask):
    return np.flatnonzero(mask[:, ::-1].ravel()).tolist()


class PlantAwareClockTests(unittest.TestCase):
    def test_schema_exposes_standard_opt_in_controls(self):
        schema = _FixedClock(_Controller()).get_parameter_schema()

        self.assertFalse(schema["plant_aware"]["default"])
        self.assertEqual(schema["plant_clearance"]["default"], 1)
        self.assertIn("plant_mask_path", schema)
        self.assertIn("plant_globe_mask_path", schema)

    def test_every_global_plant_mode_protects_the_clock_hud(self):
        self.assertEqual(
            _FixedClock.PLANT_MODIFIER_SUPPORT,
            frozenset(PLANT_MODIFIER_IDS),
        )
        for modifier in PLANT_MODIFIER_IDS:
            with self.subTest(modifier=modifier):
                animation = _FixedClock(_Controller(), {
                    "plant_modifiers": {
                        "active": [modifier],
                        "strengths": {modifier: 0.5},
                    },
                })
                self.assertTrue(animation._plant_placement_enabled())

    def test_disabled_mode_has_exact_default_render_parity(self):
        config = {"face": "digital", "background": "solid", "palette": "ice"}
        default = _FixedClock(_Controller(), config).generate_frame(1.0, 0)
        disabled = _FixedClock(
            _Controller(), {**config, "plant_aware": False}
        ).generate_frame(1.0, 0)

        np.testing.assert_array_equal(default.pixels, disabled.pixels)

    def test_zero_strength_modifier_has_exact_off_parity(self):
        config = {
            "face": "digital", "background": "aurora", "palette": "ice",
        }
        baseline = _FixedClock(_Controller(), config)
        zero = _FixedClock(_Controller(), {
            **config,
            "plant_modifiers": {
                "active": ["obstacle"],
                "strengths": {"obstacle": 0.0},
            },
        })

        left = baseline.generate_frame(1.25, 0)
        right = zero.generate_frame(1.25, 0)

        np.testing.assert_array_equal(left.pixels, right.pixels)
        self.assertFalse(zero._plant_placement_enabled())
        self.assertEqual(zero.get_runtime_stats(), {})

    def test_synthetic_masks_move_the_whole_face_out_of_occlusion(self):
        with tempfile.TemporaryDirectory() as directory:
            foliage_path = Path(directory) / "foliage.json"
            globe_path = Path(directory) / "globes.json"
            probe = _FixedClock(_ShortController(), {"show_seconds": True})
            original = _digital_marks(probe)
            indices = _physical_indices_from_visual(np.any(original > 0, axis=2))
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
            visual_obstacle = masks.obstacle[:, ::-1]
            self.assertEqual(int(np.count_nonzero(
                np.any(placed > 0, axis=2) & visual_obstacle
            )), 0)
            self.assertEqual(np.count_nonzero(placed), np.count_nonzero(original))

    def test_every_face_clears_real_calibrated_masks(self):
        for face in ClockAnimation.FACE_OPTIONS:
            with self.subTest(face=face):
                animation = _FixedClock(_Controller(), {
                    "face": face,
                    "plant_modifiers": {
                        "active": ["obstacle"],
                        "strengths": {"obstacle": 1.0},
                    },
                    "plant_clearance": 2,
                    "show_seconds": True,
                })
                original = _face_marks(animation, face)
                masks = animation.get_plant_masks()
                visual_clearance = masks.clearance[:, ::-1]
                before = int(np.count_nonzero(
                    np.any(original > 0, axis=2) & visual_clearance
                ))

                placed = animation._place_away_from_plants(original)
                after = int(np.count_nonzero(
                    np.any(placed > 0, axis=2) & visual_clearance
                ))

                self.assertFalse(masks.error)
                self.assertGreater(masks.foliage_count, 0)
                self.assertGreater(masks.globe_count, 0)
                self.assertGreater(before, 0)
                self.assertEqual(after, 0)
                self.assertEqual(animation._plant_obstacle_overlap, 0)
                self.assertEqual(animation._plant_clearance_overlap, 0)

    def test_live_modifier_update_repositions_without_sticking(self):
        config = {"face": "digital", "background": "solid", "palette": "ice"}
        animation = _FixedClock(_Controller(), config)
        baseline = animation.generate_frame(1.0, 0).pixels.copy()

        animation.update_parameters({
            "plant_modifiers": {
                "active": ["obstacle"],
                "strengths": {"obstacle": 1.0},
            },
        })
        protected = animation.generate_frame(1.0, 1).pixels.copy()
        self.assertFalse(np.array_equal(protected, baseline))
        self.assertNotEqual(animation._plant_layout_offset, (0, 0))

        animation.update_parameters({"plant_modifiers": {
            "active": ["obstacle"],
            "strengths": {"obstacle": 0.0},
        }})
        restored = animation.generate_frame(1.0, 2).pixels
        np.testing.assert_array_equal(restored, baseline)
        self.assertEqual(animation._plant_layout_offset, (0, 0))


if __name__ == "__main__":
    unittest.main()
