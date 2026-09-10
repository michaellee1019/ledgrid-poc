"""Production-path RGBA acceptance for the wet atmospheric Animations."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from itertools import combinations
from pathlib import Path
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import OverlayFrame, resolve_scene
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
from animation.plugins.cloud_canyon import CloudCanyonAnimation
from animation.plugins.rain_on_glass import RainOnGlassAnimation
from animation.plugins.waterfall_veil import WaterfallVeilAnimation
from ipc.scene_contract import normalize_composer_scene
from animation.libraries.procedural_atmospheres import MOOD_PALETTES, SEMANTIC_PALETTES
from tests.unit.test_composer_slice import _current_scene
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog


RENDERERS = (RainOnGlassAnimation, WaterfallVeilAnimation)
OPAQUE_SCENE_FINGERPRINTS = {
    "cloud_canyon": "7ab68b9ace37d75bd9a93b16ae2d8b6ea750f71fdaffc6f8cfbd01b881fd5cef",
    "aurora_curtains": "0b6c011452653eafeff00fa48d2d6c376baebaf6b21418b8986a4fb40f7b1d83",
}


class RainWaterfallRgbaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = current_component_catalog()
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    @staticmethod
    def scene(renderer, *, gain: float) -> dict:
        scene = _current_scene()
        scene["background"]["parameters"]["gain"] = gain
        defaults = renderer.COMPONENT_DEFAULTS if hasattr(renderer, "COMPONENT_DEFAULTS") else renderer.DEFAULTS
        scene["animation"] = {
            "component_id": renderer.COMPONENT_ID,
            "version": 1,
            "provider": "python",
            "role": "animation",
            "parameters": dict(defaults),
        }
        scene["look"].update({"palette_id": "mist", "pace": .73})
        return scene

    def test_wet_planes_are_premultiplied_transparent_and_source_cached(self) -> None:
        for renderer in RENDERERS:
            with self.subTest(component=renderer.COMPONENT_ID):
                descriptor = renderer.component_descriptor()
                self.assertEqual(descriptor.alpha_behavior.value, "premultiplied_rgba")
                scene = self.scene(renderer, gain=.62)
                resolved = resolve_scene(scene, self.catalog, monotonic_elapsed=2.0)
                animation = renderer(self.controller, renderer.COMPONENT_DEFAULTS)
                first = animation.render_resolved_scene(resolved)
                before = animation.semantic_snapshot()
                cached = animation.render_resolved_scene(resolved)
                self.assertEqual(before, animation.semantic_snapshot())
                advanced = animation.render_resolved_scene(
                    resolve_scene(scene, self.catalog, monotonic_elapsed=2.04)
                )
                self.assertIsInstance(first, OverlayFrame)
                self.assertEqual(first.pixels.shape, (33 * 138, 4))
                self.assertTrue(np.all(first.pixels[:, :3] <= first.pixels[:, 3:4]))
                self.assertTrue(np.any(first.pixels[:, 3] == 0))
                coverage = float(np.count_nonzero(first.pixels[:, 3])) / first.pixels.shape[0]
                self.assertGreater(coverage, .02)
                self.assertLess(coverage, .90)
                self.assertTrue(np.all(first.pixels[first.pixels[:, 3] == 0, :3] == 0))
                self.assertFalse(cached.changed)
                self.assertIs(first.pixels, cached.pixels)
                self.assertTrue(advanced.changed)
                self.assertGreater(animation.semantic_snapshot()["simulation_time"], before["simulation_time"])

    def test_native_dark_and_bright_backgrounds_remain_visible_after_one_final_optics_pass(self) -> None:
        for renderer in RENDERERS:
            with self.subTest(component=renderer.COMPONENT_ID):
                dark_scene = self.scene(renderer, gain=.16)
                bright_scene = self.scene(renderer, gain=.94)
                dark = ComposerFinalPreview(self.catalog, Path(__file__).resolve().parents[2]).render(
                    normalize_composer_scene({"origin": "composer", "scene": dark_scene}, self.catalog),
                    2.0, datetime(2026, 9, 1, tzinfo=timezone.utc),
                )
                bright = ComposerFinalPreview(self.catalog, Path(__file__).resolve().parents[2]).render(
                    normalize_composer_scene({"origin": "composer", "scene": bright_scene}, self.catalog),
                    2.0, datetime(2026, 9, 1, tzinfo=timezone.utc),
                )
                self.assertEqual(dark.stage_trace.count("plant_optics"), 1)
                self.assertTrue(np.array_equal(dark.foreground.pixels, bright.foreground.pixels))
                transparent = dark.foreground.pixels[:, 3] == 0
                self.assertTrue(np.any(transparent))
                self.assertGreater(
                    float(np.abs(dark.pixels[transparent].astype(np.int16) - bright.pixels[transparent].astype(np.int16)).mean()),
                    3.0,
                )
                self.assertFalse(np.array_equal(dark.pixels, bright.pixels))

    def test_every_exposed_local_mood_pair_remains_distinct_after_final_composition(self) -> None:
        minimum_mean = {
            "rain_on_glass": .50,
            "waterfall_veil": .85,
        }
        for renderer in RENDERERS:
            with self.subTest(component=renderer.COMPONENT_ID):
                for palette_id, gain in (
                    (palette, native_gain)
                    for palette in SEMANTIC_PALETTES
                    for native_gain in (.16, .94)
                ):
                    frames = {}
                    for mood in MOOD_PALETTES:
                        candidate = self.scene(renderer, gain=gain)
                        candidate["look"]["palette_id"] = palette_id
                        candidate["animation"]["parameters"]["mood"] = mood
                        frames[mood] = ComposerFinalPreview(
                            self.catalog, Path(__file__).resolve().parents[2],
                        ).render(
                            normalize_composer_scene({"origin": "composer", "scene": candidate}, self.catalog),
                            2.0, datetime(2026, 9, 1, tzinfo=timezone.utc),
                        ).pixels.copy()
                    for left, right in combinations(MOOD_PALETTES, 2):
                        delta = np.abs(frames[left].astype(np.int16) - frames[right].astype(np.int16))
                        self.assertGreaterEqual(
                            float(delta.mean()), minimum_mean[renderer.COMPONENT_ID],
                            f"{palette_id}/{gain}/{left}/{right}",
                        )
                        self.assertGreaterEqual(int(delta.max()), 16, f"{palette_id}/{gain}/{left}/{right}")

    def test_cloud_and_aurora_keep_their_opaque_resolved_scene_fingerprints(self) -> None:
        for renderer in (CloudCanyonAnimation, AuroraCurtainsAnimation):
            with self.subTest(component=renderer.COMPONENT_ID):
                self.assertEqual(renderer.component_descriptor().alpha_behavior.value, "opaque")
                defaults = renderer.COMPONENT_DEFAULTS if hasattr(renderer, "COMPONENT_DEFAULTS") else renderer.DEFAULTS
                scene = self.scene(renderer, gain=.62)
                scene["look"].update({"palette_id": "ember", "pace": .73})
                scene["animation"]["parameters"] = dict(defaults)
                resolved = resolve_scene(scene, self.catalog, monotonic_elapsed=2.0)
                rendered = renderer(self.controller, defaults).render_resolved_scene(resolved)
                frame = rendered.pixels if hasattr(rendered, "pixels") else rendered
                self.assertEqual(frame.shape, (33 * 138, 3))
                self.assertEqual(
                    hashlib.sha256(frame.tobytes()).hexdigest(),
                    OPAQUE_SCENE_FINGERPRINTS[renderer.COMPONENT_ID],
                )


if __name__ == "__main__":
    unittest.main()
