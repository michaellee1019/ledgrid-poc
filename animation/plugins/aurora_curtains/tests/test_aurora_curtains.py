"""Focused semantic-context tests for Aurora Curtains."""

from __future__ import annotations

import unittest

import numpy as np

from animation.core.component_catalog import ComponentCatalog
from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import execute_scene, resolve_scene
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation


def scene(*, vibe: str = "quiet", parameters: dict | None = None) -> dict:
    return {
        "schema": "ledgrid.scene.v1",
        "background": {
            "component_id": "aurora_curtains", "version": 1,
            "provider": "python", "role": "background",
            "parameters": parameters or {"glow_intensity": 0.68},
        },
        "vibe": vibe,
        "master_brightness": 1.0,
    }


class AuroraCurtainsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)
        self.catalog = ComponentCatalog([AuroraCurtainsAnimation.component_descriptor()])

    def test_declaration_and_frame_contract_are_context_native(self) -> None:
        animation = AuroraCurtainsAnimation(self.controller)
        descriptor = animation.component_descriptor()
        self.assertEqual((descriptor.provider.value, descriptor.role.value), ("python", "background"))
        self.assertEqual(animation.FRAME_FORMAT, "rgb_uint8_strip_major")
        self.assertEqual(animation.TIMING_POLICY, "scaled_context")
        self.assertEqual(animation.PALETTE_POLICY, "semantic")
        self.assertEqual(set(animation.PALETTE_ROLES), {"background_low", "primary", "accent"})
        schema = animation.get_parameter_schema()
        self.assertEqual(set(schema), {"curtain_density", "fold_depth", "glow_intensity", "source_fps", "seed"})
        self.assertNotIn("brightness", animation.params)
        with self.assertRaisesRegex(ValueError, "non-local"):
            animation.update_parameters({"brightness": .5})
        frame = animation.render_resolved_scene(resolve_scene(scene(), self.catalog, monotonic_elapsed=1.0))
        self.assertEqual(frame.shape, (33 * 138, 3))
        self.assertEqual(frame.dtype, np.uint8)
        self.assertTrue(frame.flags.c_contiguous)

    def test_palette_vibe_and_luminance_are_presentation_only(self) -> None:
        animation = AuroraCurtainsAnimation(self.controller, {"seed": 731, "source_fps": 20.0})
        quiet = resolve_scene(scene(vibe="quiet"), self.catalog, monotonic_elapsed=2.0)
        vivid = resolve_scene(scene(vibe="vivid"), self.catalog, monotonic_elapsed=2.0)
        quiet_frame = animation.render_resolved_scene(quiet).copy()
        snapshot = animation.semantic_snapshot()
        cadence = animation.cadence_snapshot()
        vivid_frame = animation.render_resolved_scene(vivid).copy()

        self.assertEqual(snapshot, animation.semantic_snapshot())
        self.assertEqual(cadence, animation.cadence_snapshot())
        self.assertEqual(cadence["source_fps"], 30.0)
        self.assertFalse(np.array_equal(quiet_frame, vivid_frame))

        dim = scene(vibe="quiet")
        dim["master_brightness"] = 0.3
        raw = animation.render_resolved_scene(quiet).copy()
        result = execute_scene(
            dim, self.catalog, monotonic_elapsed=2.0,
            renderer=animation.render_resolved_scene,
        )
        expected = execute_scene(
            dim, self.catalog, monotonic_elapsed=2.0,
            renderer=lambda _context: raw,
        )
        np.testing.assert_array_equal(result.frame, expected.frame)
        self.assertEqual(snapshot, animation.semantic_snapshot())

    def test_wall_pace_is_consumed_once_and_source_cadence_is_independent(self) -> None:
        animation = AuroraCurtainsAnimation(self.controller, {"seed": 811, "source_fps": 20.0})
        slow = {
            "schema": "ledgrid.scene.v1",
            "background": scene()["background"],
            "custom": {"palette_id": "mist", "wall_pace": .5, "presentation_luminance": 1.0},
            "master_brightness": 1.0,
        }
        fast = {
            **slow,
            "custom": {"palette_id": "mist", "wall_pace": 1.5, "presentation_luminance": 1.0},
        }
        slow_context = resolve_scene(slow, self.catalog, monotonic_elapsed=2.0)
        fast_context = resolve_scene(fast, self.catalog, monotonic_elapsed=2.0)
        self.assertEqual((slow_context.phase_time, fast_context.phase_time), (1.0, 3.0))
        first = animation.render_resolved_scene(slow_context).copy()
        cached = animation.generate_frame(999.0, 1)
        self.assertFalse(cached.changed)
        changed = animation.render_resolved_scene(fast_context)
        self.assertTrue(changed.flags.c_contiguous)
        self.assertFalse(np.array_equal(first, changed))
        self.assertEqual(animation.cadence_snapshot()["source_fps"], 30.0)

    def test_resolved_seed_owns_semantic_state_not_constructor_config(self) -> None:
        parameters = {
            "curtain_density": .74, "fold_depth": .66, "glow_intensity": .81,
            "source_fps": 22.0, "seed": 614,
        }
        resolved = resolve_scene(
            scene(vibe="quiet", parameters=parameters), self.catalog, monotonic_elapsed=2.0,
        )
        left = AuroraCurtainsAnimation(self.controller, {"seed": 1, "source_fps": 40.0})
        right = AuroraCurtainsAnimation(self.controller, {"seed": 999999, "source_fps": 20.0})
        left_frame = left.render_resolved_scene(resolved).copy()
        right_frame = right.render_resolved_scene(resolved).copy()
        np.testing.assert_array_equal(left_frame, right_frame)
        self.assertEqual(left.semantic_snapshot(), right.semantic_snapshot())
        self.assertEqual(left.semantic_snapshot()["seed"], 614)
        self.assertEqual(left.cadence_snapshot()["source_fps"], 22.0)

        changed_parameters = {**parameters, "seed": 615}
        changed_seed = resolve_scene(
            scene(vibe="quiet", parameters=changed_parameters), self.catalog, monotonic_elapsed=2.0,
        )
        before = left.semantic_snapshot()
        changed_frame = left.render_resolved_scene(changed_seed).copy()
        after_seed_change = left.semantic_snapshot()
        self.assertFalse(np.array_equal(left_frame, changed_frame))
        self.assertNotEqual(before, after_seed_change)
        self.assertEqual(after_seed_change["seed"], 615)
        left.render_resolved_scene(changed_seed)
        self.assertEqual(after_seed_change, left.semantic_snapshot())

        presentation_only = resolve_scene(
            scene(vibe="vivid", parameters=changed_parameters), self.catalog, monotonic_elapsed=2.0,
        )
        cadence = left.cadence_snapshot()
        left.render_resolved_scene(presentation_only)
        self.assertEqual(after_seed_change, left.semantic_snapshot())
        self.assertEqual(cadence, left.cadence_snapshot())

    def test_sub_tick_calls_reuse_the_source_frame_without_events(self) -> None:
        animation = AuroraCurtainsAnimation(self.controller, {"seed": 12, "source_fps": 20.0})
        first = animation.generate_frame(1.0, 0)
        cached = animation.generate_frame(1.02, 1)
        advanced = animation.generate_frame(1.051, 2)
        self.assertTrue(first.changed)
        self.assertFalse(cached.changed)
        self.assertIs(first.pixels, cached.pixels)
        self.assertTrue(advanced.changed)
        self.assertEqual(animation.get_runtime_stats()["events"], 0)


if __name__ == "__main__":
    unittest.main()
