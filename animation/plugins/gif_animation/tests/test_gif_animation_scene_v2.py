"""Resolved-Scene coverage for authored GIF media."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import resolve_scene
from animation.plugins.gif_animation import GifAnimation


def _scene(*, palette="neutral", pace=1.0, parameters=None):
    return {
        "schema": "ledgrid.scene.v2",
        "background": {
            "component_id": "native", "version": 1, "provider": "receiver_native",
            "role": "background", "parameters": {"bundle_digest": "a" * 64},
            "bundle_digest": "a" * 64,
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
        native = ComponentDescriptor(
            "native", 1, "receiver_native", "background", "scaled_context", "none",
            "preserve", ("final_optics",), ("native_preview",),
            defaults={"bundle_digest": "a" * 64},
        )
        self.catalog = ComponentCatalog([native, GifAnimation.component_descriptor()])
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
