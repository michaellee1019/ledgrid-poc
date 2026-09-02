"""Deterministic Scene-v2 lifecycle tests for the Conway animation."""

from __future__ import annotations

from types import MappingProxyType
import unittest

import numpy as np

from animation.core.compositing import BaseFrame, HostSceneCompositor, PlacedOverlay
from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import ResolvedScene
from animation.plugins.conway_life import ConwayLifeAnimation


def _context(*, palette: str, phase_time: float, parameters: dict) -> ResolvedScene:
    descriptor = ConwayLifeAnimation.component_descriptor()
    return ResolvedScene(
        canonical_scene=MappingProxyType({"palette_id": palette}),
        canonical_bytes=b"conway-test",
        digest="0" * 64,
        descriptor=descriptor,
        parameters=MappingProxyType(parameters),
        palette=MappingProxyType({"palette_id": palette}),
        phase_time=phase_time,
        plant_inputs=MappingProxyType({"foliage_density": 0.0, "globe_proximity": 0.0, "occlusion": 0.0}),
    )


class ConwayLifeAnimationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=8, leds_per_strip=8)

    @staticmethod
    def _live(animation: ConwayLifeAnimation) -> set[tuple[int, int]]:
        return set(map(tuple, np.argwhere(animation._grid)))

    def test_current_animation_declaration_and_frame_contract(self) -> None:
        animation = ConwayLifeAnimation(self.controller)
        descriptor = animation.component_descriptor()
        self.assertEqual((descriptor.provider.value, descriptor.role.value), ("python", "animation"))
        self.assertEqual(descriptor.timing_policy.value, "scaled_context")
        self.assertEqual(descriptor.optional_simulation_inputs, ("foliage_density", "globe_proximity", "occlusion"))
        self.assertEqual(animation.FRAME_FORMAT, "rgba_uint8_premultiplied_strip_major")
        self.assertEqual(set(animation.params), {"seed", "rule", "initial_density", "generations_per_second", "seed_cells"})
        frame = animation.generate_frame(0.0, 0)
        self.assertEqual(frame.pixels.shape, (64, 4))
        self.assertEqual(frame.pixels.dtype, np.uint8)
        self.assertTrue(frame.pixels.flags.c_contiguous)
        self.assertTrue(np.all(frame.pixels[:, :3] <= frame.pixels[:, 3:4]))
        with self.assertRaisesRegex(ValueError, "non-local"):
            animation.update_parameters({"brightness": 0.5})

    def test_seeded_blinker_follows_b3_s23_at_local_generation_cadence(self) -> None:
        animation = ConwayLifeAnimation(self.controller, {
            "seed": 7, "initial_density": 0.0, "generations_per_second": 5.0,
            "seed_cells": [[3, 2], [3, 3], [3, 4]],
        })
        animation.generate_frame(0.0, 0)
        animation.generate_frame(0.2, 1)
        self.assertEqual(animation.semantic_snapshot()["generation"], 1)
        self.assertSetEqual(self._live(animation), {(2, 3), (3, 3), (4, 3)})

    def test_palette_context_is_presentation_only_and_preserves_semantics(self) -> None:
        animation = ConwayLifeAnimation(self.controller, {"seed": 811, "initial_density": 0.2})
        parameters = dict(animation.params)
        neutral = animation.render_resolved_scene(_context(palette="neutral", phase_time=1.0, parameters=parameters))
        semantic = animation.semantic_snapshot()
        cadence = animation.cadence_snapshot()
        spectrum = animation.render_resolved_scene(_context(palette="spectrum", phase_time=1.0, parameters=parameters))
        self.assertEqual(semantic, animation.semantic_snapshot())
        self.assertEqual(cadence, animation.cadence_snapshot())
        self.assertFalse(np.array_equal(neutral.pixels, spectrum.pixels))

    def test_semantic_seed_or_rule_change_restarts_only_its_world(self) -> None:
        animation = ConwayLifeAnimation(self.controller, {"seed": 9, "initial_density": 0.2})
        animation.generate_frame(0.0, 0)
        animation.generate_frame(1.0, 1)
        self.assertGreater(animation.semantic_snapshot()["generation"], 0)
        animation.update_parameters({"seed": 10})
        self.assertEqual(animation.semantic_snapshot()["generation"], 0)
        self.assertEqual(animation.semantic_snapshot()["seed"], 10)
        animation.generate_frame(0.0, 2)
        animation.update_parameters({"rule": "B36/S23"})
        self.assertEqual(animation.semantic_snapshot()["generation"], 0)
        self.assertEqual(animation.semantic_snapshot()["rule"], "B36/S23")

    def test_sub_tick_cache_and_compositor_placement_do_not_create_stale_pixels(self) -> None:
        animation = ConwayLifeAnimation(self.controller, {
            "seed": 72, "initial_density": 0.2, "generations_per_second": 5.0,
        })
        first = animation.generate_frame(1.0, 0)
        cached = animation.generate_frame(1.01, 1)
        self.assertTrue(first.changed)
        self.assertFalse(cached.changed)
        self.assertIs(first.pixels, cached.pixels)

        base = BaseFrame(np.full((64, 3), 4, dtype=np.uint8))
        compositor = HostSceneCompositor(8, 8)
        compositor.compose(base, (PlacedOverlay(first),))
        moved = compositor.compose(BaseFrame(base.pixels, changed=False), (PlacedOverlay(first, led_offset=1),))
        disabled = compositor.compose(BaseFrame(base.pixels, changed=False), (PlacedOverlay(first, led_offset=1, enabled=False),))
        self.assertTrue(moved.changed)
        np.testing.assert_array_equal(disabled.pixels, base.pixels)


if __name__ == "__main__":
    unittest.main()
