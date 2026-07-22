"""Validation for the pre-rendered GIF animation asset pack."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageSequence

from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT
from scripts.generate_cute_gif_pack import SCENES, preset_payload, save_gif


def _decoded_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with Image.open(path) as image:
        digest.update(str(image.info.get("loop")).encode())
        for frame in ImageSequence.Iterator(image):
            digest.update(frame.convert("RGB").tobytes())
            digest.update(str(frame.info.get("duration")).encode())
    return digest.hexdigest()


class GifAnimationAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin_dir = Path(__file__).resolve().parents[1]
        cls.asset_dir = cls.plugin_dir / "assets"
        cls.preset_dir = cls.plugin_dir / "presets"
        cls.pack_gifs = sorted(
            path for path in cls.asset_dir.glob("*.gif")
            if path.name != "penguin_top_center.gif"
        )

    def test_pack_has_a_few_dozen_matching_presets(self):
        self.assertGreaterEqual(len(self.pack_gifs), 32)
        presets = sorted(self.preset_dir.glob("*.json"))
        self.assertEqual(len(presets), len(self.pack_gifs))
        self.assertEqual({path.stem for path in presets}, {path.stem for path in self.pack_gifs})

    def test_every_gif_uses_the_installed_native_resolution(self):
        for path in sorted(self.asset_dir.glob("*.gif")):
            with self.subTest(gif=path.name), Image.open(path) as image:
                self.assertEqual(image.size, (DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP))

    def test_every_pack_gif_is_animated_and_infinite(self):
        for path in self.pack_gifs:
            with self.subTest(gif=path.name), Image.open(path) as image:
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
                self.assertEqual(
                    params["gif_directory"],
                    "animation/plugins/gif_animation/assets",
                )
                selected = self.asset_dir / params["gif_name"]
                self.assertTrue(selected.is_file(), selected)

    def test_generator_reproduces_every_decoded_loop_and_preset(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            output_dir = Path(temporary_dir)
            for scene in SCENES:
                with self.subTest(scene=scene.slug):
                    generated = output_dir / f"{scene.slug}.gif"
                    save_gif(scene, generated)

                    self.assertEqual(
                        _decoded_digest(generated),
                        _decoded_digest(self.asset_dir / generated.name),
                    )
                    committed_preset = json.loads(
                        (self.preset_dir / f"{scene.slug}.json").read_text(encoding="utf-8")
                    )
                    self.assertEqual(preset_payload(scene), committed_preset)


if __name__ == "__main__":
    unittest.main()
