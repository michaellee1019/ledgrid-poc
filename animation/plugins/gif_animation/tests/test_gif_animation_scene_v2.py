"""Resolved-Scene coverage for authored GIF media."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import resolve_scene
from animation.plugins.gif_animation import GifAnimation
from web.composer_final_preview import current_component_catalog


def _scene(*, palette="neutral", pace=1.0, parameters=None):
    return {
        "schema": "ledgrid.scene.v2",
        "background": {
            "component_id": "solid_background", "version": 1, "provider": "python",
            "role": "background", "parameters": {"gain": 0.0, "seed": 4201},
        },
        "animation": {
            "component_id": "gif_animation", "version": 1, "provider": "python",
            "role": "animation", "parameters": parameters or {},
        },
        "widgets": [],
        "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": palette, "pace": pace, "presentation_brightness": 1.0},
    }


class GifAnimationSceneV2Tests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)
        self.catalog = current_component_catalog()
        self.parameters = {
            "gif_name": "axolotl-bubble-column.gif", "playback_speed": 1.0,
            "flip_y": True, "fit_mode": "stretch",
        }

    def resolved(self, *, elapsed, pace=1.0, palette="neutral", parameters=None):
        return resolve_scene(
            _scene(palette=palette, pace=pace, parameters=parameters or self.parameters),
            self.catalog,
            monotonic_elapsed=elapsed,
        )

    def test_descriptor_and_resolved_output_preserve_premultiplied_media(self):
        descriptor = GifAnimation.component_descriptor()
        self.assertEqual(
            (
                descriptor.component_id, descriptor.provider.value, descriptor.role.value,
                descriptor.timing_policy.value, descriptor.alpha_behavior.value,
                descriptor.palette_policy.value, descriptor.fidelity_exceptions,
            ),
            (
                "gif_animation", "python", "animation", "scaled_context",
                "premultiplied_rgba", "preserve", ("authored_media_colors",),
            ),
        )
        animation = GifAnimation(self.controller, self.parameters)
        neutral = animation.render_resolved_scene(self.resolved(elapsed=0.0, palette="neutral"))
        authored = animation._frames[0]
        np.testing.assert_array_equal(neutral.pixels, authored)
        self.assertEqual(neutral.pixels.shape, (33 * 138, 4))
        self.assertTrue(neutral.pixels.flags.c_contiguous)
        self.assertTrue(np.all(neutral.pixels[:, :3] <= neutral.pixels[:, 3:4]))
        last_strip = neutral.pixels.reshape(33, 138, 4)[-1]
        self.assertFalse(np.all(last_strip[:, :3] == 0))

        spectrum = animation.render_resolved_scene(self.resolved(elapsed=0.0, palette="spectrum"))
        self.assertFalse(spectrum.changed)
        np.testing.assert_array_equal(spectrum.pixels, neutral.pixels)

    def test_scene_pace_and_local_playback_speed_each_apply_once(self):
        slow = GifAnimation(self.controller, self.parameters)
        fast = GifAnimation(self.controller, self.parameters)
        slow.render_resolved_scene(self.resolved(elapsed=0.2, pace=0.5))
        fast.render_resolved_scene(self.resolved(elapsed=0.2, pace=1.0))
        self.assertEqual(slow._current_frame_index, 0)
        self.assertEqual(fast._current_frame_index, 1)

        doubled = GifAnimation(self.controller, {**self.parameters, "playback_speed": 2.0})
        doubled.render_resolved_scene(
            self.resolved(
                elapsed=0.2, pace=0.5,
                parameters={**self.parameters, "playback_speed": 2.0},
            )
        )
        self.assertEqual(doubled._current_frame_index, fast._current_frame_index)

    def test_opaque_black_tail_is_stretched_across_the_installed_strip(self) -> None:
        padded = Image.new("RGBA", (33, 138), (0, 0, 0, 255))
        for x in range(32):
            for y in range(138):
                padded.putpixel((x, y), (255, 40, 12, 255))
        fitted = GifAnimation._fit_frame(padded, 33, 138, "stretch", 0)
        pixels = np.asarray(fitted)
        self.assertEqual(pixels.shape, (138, 33, 4))
        self.assertTrue(np.all(pixels[:, -1, 0] > 200))
        self.assertTrue(np.all(pixels[:, -1, 3] == 255))

    def test_native_last_strip_content_is_not_treated_as_padding(self) -> None:
        native = Image.new("RGBA", (33, 138), (8, 12, 20, 255))
        for y in range(138):
            native.putpixel((32, y), (0, 255, 90, 255))
        fitted = GifAnimation._fit_frame(native, 33, 138, "stretch", 0)
        pixels = np.asarray(fitted)
        self.assertTrue(np.all(pixels[:, -1, 1] == 255))
        self.assertTrue(np.all(pixels[:, -1, 0] == 0))

    def test_contain_padding_is_transparent_and_authored_alpha_is_premultiplied(self):
        image = Image.new("RGBA", (2, 2), (200, 100, 50, 128))
        fitted = GifAnimation._fit_frame(image, 4, 8, "contain", 200)
        flattened = GifAnimation._flatten_frame(fitted, flip_y=False).reshape(4, 8, 4)
        self.assertTrue(np.all(flattened[:, :2] == 0))
        self.assertTrue(np.all(flattened[:, 6:] == 0))
        covered = flattened[:, 2:6]
        self.assertTrue(np.all(covered[..., 3] == 128))
        self.assertTrue(np.all(covered[..., :3] <= covered[..., 3:4]))

    def test_every_existing_preset_normalizes_without_rewriting_saved_data(self):
        preset_dir = Path(__file__).resolve().parents[1] / "presets"
        for path in sorted(preset_dir.glob("*.json")):
            with self.subTest(preset=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                normalized = GifAnimation.component_descriptor().parameter_normalizer(payload["params"])
                self.assertEqual(set(normalized), set(GifAnimation.DEFAULTS))
                self.assertEqual(normalized["gif_name"], payload["params"]["gif_name"])


if __name__ == "__main__":
    unittest.main()
