"""Opt-in calibrated plant layout tests for Emoji Arranger."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.emoji_arranger import EmojiArrangerAnimation


class PlantAwareEmojiArrangerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        self.foliage_path.write_text(
            json.dumps({"covered_indices": [1]}), encoding="utf-8"
        )
        self.globe_path.write_text(
            json.dumps({"globe_indices": [20], "region_count": 1}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_animation(self, **params):
        config = {
            "text": "👀",
            "active_columns": 12,
            "pulse_speed": 0.0,
            "plant_clearance": 0,
            "plant_mask_path": str(self.foliage_path),
            "plant_globe_mask_path": str(self.globe_path),
            **params,
        }
        controller = PreviewLEDController(strips=10, leds_per_strip=20)
        return EmojiArrangerAnimation(controller, config)

    def test_disabled_mode_has_exact_legacy_layout_and_frame_parity(self):
        implicit = self.make_animation(text="A", active_columns=8)
        explicit = self.make_animation(text="A", active_columns=8, plant_aware=False)

        self.assertFalse(implicit.get_parameter_schema()["plant_aware"]["default"])
        self.assertEqual(implicit._arrange_text_with_wrapping("AA", 8, 1), [["A"], ["A"]])
        for elapsed in (0.0, 0.4, 1.25):
            np.testing.assert_array_equal(
                implicit.generate_frame(elapsed, 0).copy(),
                explicit.generate_frame(elapsed, 0).copy(),
            )

        frame = implicit.generate_frame(0.0, 0)
        background = np.array((2, 6, 12), dtype=np.uint8)
        self.assertTrue(np.array_equal(frame[0], background))
        self.assertFalse(np.array_equal(frame[2], background))
        self.assertIsNone(implicit._plant_layout_key)

    def test_enabled_layout_moves_important_features_off_masks(self):
        animation = self.make_animation(plant_aware=True)
        lines = animation._arrange_text_with_wrapping("👀", 12, 1)
        masks = animation.get_plant_masks()

        origins = animation._plant_aware_layout(lines, 0, 0, 1, 1, 10, 20, masks)
        self.assertNotEqual(origins[0], (0, 0))

        cells, _, _ = animation._line_pattern_cells(lines[0], 1)
        origin_x, origin_y = origins[0]
        for row, column, _ in cells:
            self.assertFalse(masks.clearance[origin_y + row, origin_x + column])

    def test_foliage_and_globes_remain_visible_as_distinct_landmarks(self):
        animation = self.make_animation(plant_aware=True)
        frame = animation.generate_frame(0.0, 0)

        np.testing.assert_array_equal(frame[1], animation.PLANT_FOLIAGE_COLOR)
        np.testing.assert_array_equal(frame[20], animation.PLANT_GLOBE_COLOR)
        self.assertFalse(np.array_equal(frame[1], frame[20]))

    def test_clearance_and_prior_lines_are_layout_obstacles(self):
        # One foliage pixel expands into the preferred eye cells at radius one.
        animation = EmojiArrangerAnimation(
            PreviewLEDController(strips=16, leds_per_strip=20),
            {
                "text": "👀👀",
                "active_columns": 12,
                "plant_aware": True,
                "plant_clearance": 1,
                "plant_mask_path": str(self.foliage_path),
                "plant_globe_mask_path": str(self.globe_path),
            },
        )
        lines = animation._arrange_text_with_wrapping("👀👀", 12, 1)
        masks = animation.get_plant_masks()
        origins = animation._plant_aware_layout(lines, 0, 0, 1, 1, 16, 20, masks)

        occupied = []
        for line, (origin_x, origin_y) in zip(lines, origins):
            cells, _, _ = animation._line_pattern_cells(line, 1)
            visible = {
                (origin_y + row, origin_x + column)
                for row, column, _ in cells
                if not masks.clearance[origin_y + row, origin_x + column]
            }
            occupied.append(visible)
            self.assertTrue(visible)
        self.assertTrue(occupied[0].isdisjoint(occupied[1]))

    def test_unavoidable_plant_pixels_clip_glyph_instead_of_hiding_landmark(self):
        total_pixels = 7 * 12
        self.foliage_path.write_text(
            json.dumps({"covered_indices": list(range(total_pixels))}),
            encoding="utf-8",
        )
        self.globe_path.write_text(
            json.dumps({"globe_indices": [], "region_count": 0}), encoding="utf-8"
        )
        animation = EmojiArrangerAnimation(
            PreviewLEDController(strips=7, leds_per_strip=12),
            {
                "text": "👀",
                "active_columns": 12,
                "pulse_speed": 0.0,
                "plant_aware": True,
                "plant_clearance": 0,
                "plant_mask_path": str(self.foliage_path),
                "plant_globe_mask_path": str(self.globe_path),
            },
        )

        frame = animation.generate_frame(0.0, 0)
        expected = np.tile(np.array(animation.PLANT_FOLIAGE_COLOR), (total_pixels, 1))
        np.testing.assert_array_equal(frame, expected)


if __name__ == "__main__":
    unittest.main()
