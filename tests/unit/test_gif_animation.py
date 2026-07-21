"""Validation for the pre-rendered GIF animation asset pack."""

import json
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageSequence


class GifAnimationAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]
        cls.asset_dir = cls.root / "assets" / "gifs"
        cls.preset_dir = cls.root / "presets" / "animations" / "gif_animation"
        cls.pack_gifs = sorted(
            path for path in cls.asset_dir.glob("*.gif")
            if path.name != "penguin_top_center.gif"
        )

    def test_pack_has_a_few_dozen_matching_presets(self):
        self.assertGreaterEqual(len(self.pack_gifs), 32)
        presets = sorted(self.preset_dir.glob("*.json"))
        self.assertEqual(len(presets), len(self.pack_gifs))
        self.assertEqual({path.stem for path in presets}, {path.stem for path in self.pack_gifs})

    def test_every_gif_is_native_resolution_animated_and_infinite(self):
        for path in self.pack_gifs:
            with self.subTest(gif=path.name), Image.open(path) as image:
                self.assertEqual(image.size, (32, 140))
                self.assertEqual(image.info.get("loop"), 0)
                frames = [frame.convert("RGB") for frame in ImageSequence.Iterator(image)]
                self.assertEqual(len(frames), 8)
                self.assertTrue(all(frame.info.get("duration", 0) >= 100 for frame in ImageSequence.Iterator(image)))
                self.assertTrue(
                    any(ImageChops.difference(frames[0], frame).getbbox() for frame in frames[1:]),
                    f"{path.name} has no visible animation",
                )

    def test_presets_select_existing_gifs_with_pixel_safe_fit(self):
        for path in sorted(self.preset_dir.glob("*.json")):
            with self.subTest(preset=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                params = payload["params"]
                self.assertEqual(payload["animation"], "gif_animation")
                self.assertEqual(payload["preset_id"], path.stem)
                self.assertEqual(params["fit_mode"], "stretch")
                selected = self.root / params["gif_directory"] / params["gif_name"]
                self.assertTrue(selected.is_file(), selected)


if __name__ == "__main__":
    unittest.main()
