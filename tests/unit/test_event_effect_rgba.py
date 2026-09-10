"""Installed-final Scene v2 acceptance for transparent event instruments."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import OverlayFrame, ResolvedScene
from animation.plugins.fireworks import FireworksAnimation
from animation.plugins.flame_burst import FlameBurstAnimation
from ipc.scene_contract import normalize_composer_scene
from tests.unit.test_composer_slice import _current_scene
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog


class EventEffectRgbaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def test_warmed_effects_are_bounded_transparent_premultiplied_planes(self) -> None:
        for renderer, parameters in (
            (FireworksAnimation, {"seed": 77, "launch_cadence": 3.0}),
            (FlameBurstAnimation, {"seed": 77, "ignition_cadence": 3.0}),
        ):
            with self.subTest(renderer=renderer.COMPONENT_ID):
                effect = renderer(self.controller, parameters)
                frame = None
                for tick in range(1, 73):
                    frame = effect.generate_frame(tick / 24.0, tick)
                assert frame is not None
                self.assertIsInstance(frame, OverlayFrame)
                self.assertEqual(frame.pixels.shape, (33 * 138, 4))
                self.assertTrue(frame.pixels.flags.c_contiguous)
                self.assertTrue(
                    np.all(frame.pixels[:, :3] <= frame.pixels[:, 3:4])
                )
                coverage = int(np.count_nonzero(frame.pixels[:, 3]))
                self.assertGreater(coverage, 0)
                self.assertLess(coverage, 33 * 138)
                cached = effect.generate_frame(72 / 24.0, 100)
                self.assertFalse(cached.changed)
                np.testing.assert_array_equal(cached.pixels, frame.pixels)

    def test_palette_invalidation_is_presentation_only_at_one_source_tick(self) -> None:
        for renderer, parameters in (
            (FireworksAnimation, {"seed": 13, "launch_cadence": 3.0}),
            (FlameBurstAnimation, {"seed": 13, "ignition_cadence": 3.0}),
        ):
            with self.subTest(renderer=renderer.COMPONENT_ID):
                effect = renderer(self.controller, parameters)
                descriptor = renderer.component_descriptor()
                warm = ResolvedScene(
                    {}, b"", "", descriptor, parameters,
                    {"palette_id": "neutral"}, 0.0, {},
                )
                neutral = ResolvedScene(
                    {}, b"", "", descriptor, parameters,
                    {"palette_id": "neutral"}, 0.5, {},
                )
                mist = ResolvedScene(
                    {}, b"", "", descriptor, parameters,
                    {"palette_id": "mist"}, 0.5, {},
                )
                effect.render_resolved_scene(warm)
                first = effect.render_resolved_scene(neutral)
                first_pixels = first.pixels.copy()
                semantic = (
                    effect.semantic_snapshot()
                    if renderer is FireworksAnimation else tuple(effect._ignitions)
                )
                state = semantic, effect.cadence_snapshot()
                changed = effect.render_resolved_scene(mist)
                semantic = (
                    effect.semantic_snapshot()
                    if renderer is FireworksAnimation else tuple(effect._ignitions)
                )
                after = semantic, effect.cadence_snapshot()
                self.assertTrue(changed.changed)
                self.assertFalse(np.array_equal(first_pixels, changed.pixels))
                self.assertEqual(state, after)

    def test_installed_final_preview_reveals_dark_and_bright_native_backgrounds_once(self) -> None:
        catalog = current_component_catalog()
        wall_time = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
        for component_id in ("fireworks", "flame_burst"):
            outputs = []
            for gain in (0.0, 1.0):
                scene = _current_scene()
                scene["background"]["parameters"]["gain"] = gain
                scene["animation"] = {
                    "component_id": component_id, "version": 1,
                    "provider": "python", "role": "animation", "parameters": {},
                }
                canonical = normalize_composer_scene(
                    {"origin": "composer", "scene": scene}, catalog
                )
                preview = ComposerFinalPreview(
                    catalog, Path(__file__).resolve().parents[2]
                )
                for elapsed in (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0):
                    output = preview.render(canonical, elapsed, wall_time)
                self.assertEqual(output.stage_trace.count("plant_optics"), 1)
                self.assertGreater(np.count_nonzero(output.foreground.pixels[:, 3]), 0)
                outputs.append(output.pixels.copy())
            with self.subTest(component=component_id):
                self.assertFalse(np.array_equal(outputs[0], outputs[1]))


if __name__ == "__main__":
    unittest.main()
