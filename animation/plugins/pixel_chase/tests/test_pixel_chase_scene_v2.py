"""Focused resolved-Scene regressions for the current Pixel Chase plane."""

from __future__ import annotations

import unittest

import numpy as np

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from animation.core.compositing import BaseFrame
from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import resolve_scene
from animation.core.scene_runtime import CanonicalSceneRuntime
from animation.plugins.pixel_chase import PixelChaseAnimation
from ipc.scene_contract import normalize_composer_scene


def scene(*, palette="neutral", pace=1.0, parameters=None):
    return {
        "schema": "ledgrid.scene.v2",
        "background": {"component_id": "native", "version": 1, "provider": "receiver_native", "role": "background", "parameters": {"bundle_digest": "a" * 64}, "bundle_digest": "a" * 64},
        "animation": {"component_id": "pixel_chase", "version": 1, "provider": "python", "role": "animation", "parameters": parameters or {}},
        "widgets": [], "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": palette, "pace": pace, "presentation_brightness": 1.0},
    }


class PixelChaseSceneV2Tests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)
        native = ComponentDescriptor("native", 1, "receiver_native", "background", "scaled_context", "none", "preserve", ("final_optics",), ("native_preview",), defaults={"bundle_digest": "a" * 64})
        self.catalog = ComponentCatalog([native, PixelChaseAnimation.component_descriptor()])

    def test_descriptor_and_resolved_scene_emit_transparent_premultiplied_chase(self):
        descriptor = PixelChaseAnimation.component_descriptor()
        self.assertEqual((descriptor.component_id, descriptor.provider.value, descriptor.role.value, descriptor.timing_policy.value, descriptor.alpha_behavior.value, descriptor.palette_policy.value), ("pixel_chase", "python", "animation", "scaled_context", "premultiplied_rgba", "semantic"))
        resolved = resolve_scene(scene(palette="mist", pace=.5, parameters={"pixels_per_second": 10., "pixel_count": 1, "tail_style": "fade", "tail_length": 2, "color_cycle_speed": 0.}), self.catalog, monotonic_elapsed=.2)
        chase = PixelChaseAnimation(self.controller)
        frame = chase.render_resolved_scene(resolved)
        self.assertEqual(resolved.phase_time, .1)
        self.assertEqual(frame.pixels.shape, (33 * 138, 4)); self.assertTrue(frame.pixels.flags.c_contiguous)
        self.assertTrue(np.all(frame.pixels[:, :3] <= frame.pixels[:, 3:4]))
        self.assertGreater(np.count_nonzero(frame.pixels[:, 3]), 0); self.assertGreater(np.count_nonzero(frame.pixels[:, 3] == 0), 0)
        self.assertEqual(int(chase._last_head_pixels[0]), 136)

    def test_tail_follows_previous_head_positions_including_path_wrap(self):
        for tail_style, expected_alpha in (("fade", (255, 170, 85)), ("solid", (255, 255, 255))):
            parameters = {"pixels_per_second": 10., "pixel_count": 1, "tail_style": tail_style, "tail_length": 2, "color_cycle_speed": 0.}
            chase = PixelChaseAnimation(self.controller)
            for step in (2, self.controller.total_leds):
                with self.subTest(tail_style=tail_style, step=step):
                    previous_heads = []
                    for past_step in (step - 2, step - 1, step):
                        frame = chase.render_resolved_scene(resolve_scene(scene(parameters=parameters), self.catalog, monotonic_elapsed=past_step / 10.))
                        previous_heads.append(chase.semantic_snapshot()["heads"][0])
                    np.testing.assert_array_equal(frame.pixels[list(reversed(previous_heads)), 3], expected_alpha)
                    self.assertEqual(np.count_nonzero(frame.pixels[:, 3]), 3)

    def test_palette_repaints_without_changing_head_state_and_cache_is_exact(self):
        parameters = {"pixels_per_second": 20., "pixel_count": 4, "tail_style": "solid", "tail_length": 2, "color_cycle_speed": 0.}
        chase = PixelChaseAnimation(self.controller)
        mist = chase.render_resolved_scene(resolve_scene(scene(palette="mist", parameters=parameters), self.catalog, monotonic_elapsed=.4))
        heads = chase.semantic_snapshot(); cached = chase.render_resolved_scene(resolve_scene(scene(palette="mist", parameters=parameters), self.catalog, monotonic_elapsed=.4))
        ember = chase.render_resolved_scene(resolve_scene(scene(palette="ember", parameters=parameters), self.catalog, monotonic_elapsed=.4))
        self.assertFalse(cached.changed); self.assertEqual(heads, chase.semantic_snapshot())
        self.assertTrue(ember.changed); self.assertEqual(heads, chase.semantic_snapshot())
        self.assertTrue(np.array_equal(mist.pixels[:, 3], ember.pixels[:, 3])); self.assertFalse(np.array_equal(mist.pixels[:, :3], ember.pixels[:, :3]))

    def test_final_runtime_reveals_native_background_through_unlit_pixels(self):
        canonical = normalize_composer_scene({"origin": "composer", "scene": scene(parameters={"pixels_per_second": 12., "pixel_count": 2, "tail_style": "none", "tail_length": 0, "color_cycle_speed": 0.})}, self.catalog)
        native = np.full((33 * 138, 3), (7, 11, 19), dtype=np.uint8)
        runtime = CanonicalSceneRuntime(self.controller, self.catalog, background_renderer=lambda _context, _count: BaseFrame(native, changed=True), animation_factory=lambda _descriptor, controller, parameters: PixelChaseAnimation(controller, parameters))
        runtime.activate(canonical); rendered = runtime.render(.25)
        self.assertEqual(rendered.foreground.pixels.shape, (33 * 138, 4))
        transparent = rendered.foreground.pixels[:, 3] == 0
        self.assertGreater(np.count_nonzero(transparent), 0)
        np.testing.assert_array_equal(rendered.pixels[transparent], native[transparent])

    def test_rejects_retired_rgb_brightness_and_plant_authority(self):
        for values in ({"red": 255}, {"brightness": .5}, {"plant_aware": True}, {"color_mode": "rainbow"}):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, "non-local"):
                PixelChaseAnimation(self.controller, values)


if __name__ == "__main__": unittest.main()
