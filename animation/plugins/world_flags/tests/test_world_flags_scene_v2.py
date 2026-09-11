"""Resolved-Scene coverage for authored flag identity media."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

import numpy as np

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from animation.core.compositing import BaseFrame
from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import resolve_scene
from animation.core.scene_runtime import CanonicalSceneRuntime
from animation.plugins.world_flags import WorldFlagsAnimation
from ipc.scene_contract import normalize_composer_scene


def _scene(*, palette="neutral", pace=1.0, parameters=None):
    return {
        "schema": "ledgrid.scene.v2",
        "background": {
            "component_id": "native", "version": 1, "provider": "receiver_native",
            "role": "background", "parameters": {"bundle_digest": "a" * 64},
            "bundle_digest": "a" * 64,
        },
        "animation": {
            "component_id": "world_flags", "version": 1, "provider": "python",
            "role": "animation", "parameters": parameters or {},
        },
        "widgets": [],
        "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": palette, "pace": pace, "presentation_brightness": 1.0},
    }


class WorldFlagsSceneV2Tests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)
        native = ComponentDescriptor(
            "native", 1, "receiver_native", "background", "scaled_context", "none",
            "preserve", ("final_optics",), ("native_preview",),
            defaults={"bundle_digest": "a" * 64},
        )
        self.catalog = ComponentCatalog([native, WorldFlagsAnimation.component_descriptor()])
        self.parameters = dict(WorldFlagsAnimation.DEFAULTS)
        self.parameters["flip_vertical"] = False

    def resolved(self, *, elapsed, pace=1.0, palette="neutral", parameters=None):
        return resolve_scene(
            _scene(palette=palette, pace=pace, parameters=parameters or self.parameters),
            self.catalog,
            monotonic_elapsed=elapsed,
        )

    def test_descriptor_and_palette_independent_premultiplied_output(self):
        descriptor = WorldFlagsAnimation.component_descriptor()
        self.assertEqual(
            (
                descriptor.component_id, descriptor.provider.value, descriptor.role.value,
                descriptor.timing_policy.value, descriptor.alpha_behavior.value,
                descriptor.palette_policy.value, descriptor.fidelity_exceptions,
            ),
            (
                "world_flags", "python", "animation", "scaled_context",
                "premultiplied_rgba", "preserve", ("flag_identity_colors",),
            ),
        )
        animation = WorldFlagsAnimation(self.controller, self.parameters)
        neutral = animation.render_resolved_scene(self.resolved(elapsed=0.0, palette="neutral"))
        spectrum = animation.render_resolved_scene(self.resolved(elapsed=0.0, palette="spectrum"))
        self.assertFalse(spectrum.changed)
        np.testing.assert_array_equal(neutral.pixels, spectrum.pixels)
        self.assertEqual(neutral.pixels.shape, (33 * 138, 4))
        self.assertTrue(neutral.pixels.flags.c_contiguous)
        self.assertTrue(np.all(neutral.pixels[:, :3] <= neutral.pixels[:, 3:4]))

    def test_scene_pace_scales_local_scroll_exactly_once(self):
        slow = WorldFlagsAnimation(self.controller, self.parameters)
        fast = WorldFlagsAnimation(self.controller, self.parameters)
        slow_frame = slow.render_resolved_scene(self.resolved(elapsed=0.2, pace=0.5))
        fast_frame = fast.render_resolved_scene(self.resolved(elapsed=0.2, pace=1.0))
        self.assertEqual(slow._last_key[2], 0)
        self.assertEqual(fast._last_key[2], 1)
        self.assertFalse(np.array_equal(slow_frame.pixels, fast_frame.pixels))

    def test_black_flag_pixels_remain_covered_while_gaps_reveal_background(self):
        parameters = {**self.parameters, "display_mode": "single", "country": "DEU"}
        frame = WorldFlagsAnimation(self.controller, parameters).render_resolved_scene(
            self.resolved(elapsed=0.0, parameters=parameters)
        ).pixels.reshape(33, 138, 4)
        covered_black = np.all(frame[..., :3] == 0, axis=2) & (frame[..., 3] == 255)
        transparent = frame[..., 3] == 0
        self.assertGreater(np.count_nonzero(covered_black), 0)
        self.assertGreater(np.count_nonzero(transparent), 0)

    def test_canonical_composition_reveals_native_background_only_through_gaps(self):
        canonical = normalize_composer_scene(
            {"origin": "composer", "scene": _scene(parameters=self.parameters)},
            self.catalog,
        )
        native_pixels = np.full((33 * 138, 3), (5, 9, 17), dtype=np.uint8)
        runtime = CanonicalSceneRuntime(
            self.controller,
            self.catalog,
            background_renderer=lambda _context, _count: BaseFrame(native_pixels),
            animation_factory=lambda _descriptor, controller, parameters: WorldFlagsAnimation(controller, parameters),
        )
        runtime.activate(canonical)
        rendered = runtime.render(0.0)
        alpha = rendered.foreground.pixels[:, 3]
        self.assertGreater(np.count_nonzero(alpha == 0), 0)
        self.assertGreater(np.count_nonzero(alpha == 255), 0)
        np.testing.assert_array_equal(rendered.pixels[alpha == 0], native_pixels[alpha == 0])
        np.testing.assert_array_equal(
            rendered.pixels[alpha == 255], rendered.foreground.pixels[alpha == 255, :3]
        )

    def test_every_existing_preset_uses_only_current_local_parameters(self):
        preset_dir = Path(__file__).resolve().parents[1] / "presets"
        for path in sorted(preset_dir.glob("*.json")):
            with self.subTest(preset=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                normalized = WorldFlagsAnimation.component_descriptor().parameter_normalizer(payload["params"])
                self.assertEqual(set(normalized), set(WorldFlagsAnimation.DEFAULTS))
                self.assertEqual(normalized, payload["params"])


if __name__ == "__main__":
    unittest.main()
