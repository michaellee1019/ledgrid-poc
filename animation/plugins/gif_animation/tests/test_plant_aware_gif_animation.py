"""Plant-aware composition tests for pre-rendered GIF media."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from animation.plugins.gif_animation import GifAnimation


class _Controller:
    strip_count = 8
    leds_per_strip = 8
    total_leds = 64


class PlantAwareGifAnimationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.gif_path = root / "subject.gif"
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"

        frames = []
        for color in ((240, 20, 10), (255, 35, 15)):
            pixels = np.zeros((8, 8, 3), dtype=np.uint8)
            pixels[3:5, 3:5] = color
            frames.append(Image.fromarray(pixels, mode="RGB"))
        frames[0].save(
            self.gif_path,
            save_all=True,
            append_images=frames[1:],
            duration=100,
            loop=0,
            optimize=False,
        )
        subject = [x * 8 + y for x in range(3, 5) for y in range(3, 5)]
        self.foliage_path.write_text(json.dumps({"covered_indices": subject[:2]}))
        self.globe_path.write_text(
            json.dumps({"globe_indices": subject[2:], "region_count": 1})
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def animation(self, **params):
        return GifAnimation(
            _Controller(),
            {
                "gif_directory": str(self.gif_path.parent),
                "gif_name": self.gif_path.name,
                "fit_mode": "stretch",
                "flip_y": False,
                "plant_clearance": 0,
                "plant_mask_path": str(self.foliage_path),
                "plant_globe_mask_path": str(self.globe_path),
                **params,
            },
        )

    def test_disabled_mode_is_the_unmodified_decoded_frame(self):
        implicit = self.animation()
        explicit = self.animation(plant_aware=False)

        left = implicit.generate_frame(0.0, 0).pixels
        right = explicit.generate_frame(0.0, 0).pixels

        np.testing.assert_array_equal(left, implicit._frames[0])
        np.testing.assert_array_equal(left, right)
        self.assertEqual(implicit.get_runtime_stats()["plant_content_offset"], (0, 0))

    def test_enabled_mode_moves_salient_subject_off_the_masks(self):
        baseline = self.animation(plant_aware=False)
        aware = self.animation(
            plant_aware=True,
            plant_gif_offset_radius=2,
            plant_accent_strength=0.0,
            plant_foliage_dim=0.0,
            plant_globe_dim=0.0,
        )
        masks = aware.get_plant_masks()

        before = baseline.generate_frame(0.0, 0).pixels.reshape(8, 8, 3)
        after = aware.generate_frame(0.0, 0).pixels.reshape(8, 8, 3)
        before_subject = np.max(before, axis=2) > 100
        after_subject = np.max(after, axis=2) > 100

        self.assertGreater(np.count_nonzero(before_subject & masks.obstacle), 0)
        self.assertEqual(np.count_nonzero(after_subject & masks.obstacle), 0)
        self.assertNotEqual(aware.get_runtime_stats()["plant_content_offset"], (0, 0))

    def test_foliage_and_globes_get_distinct_subtle_accents(self):
        aware = self.animation(
            plant_aware=True,
            plant_gif_offset_radius=2,
            plant_accent_strength=0.5,
        )

        frame = aware.generate_frame(0.0, 0).pixels
        foliage = frame[3 * 8 + 3]
        globe = frame[4 * 8 + 3]

        self.assertGreater(int(foliage[1]), int(foliage[0]))
        self.assertGreater(int(globe[2]), int(globe[1]))
        self.assertFalse(np.array_equal(foliage, globe))
        self.assertTrue(aware.get_runtime_stats()["plant_aware"])

    def test_live_plant_configuration_invalidates_cached_composition(self):
        aware = self.animation(plant_aware=True, plant_accent_strength=0.0)
        first = aware.generate_frame(0.0, 0).pixels.copy()

        aware.update_parameters({"plant_gif_offset_radius": 0})
        second = aware.generate_frame(0.0, 1).pixels.copy()

        self.assertFalse(np.array_equal(first, second))
        self.assertEqual(aware.get_runtime_stats()["plant_content_offset"], (0, 0))


if __name__ == "__main__":
    unittest.main()
