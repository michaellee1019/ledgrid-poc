"""Focused proof for the current-only packet-A scene pipeline."""

from __future__ import annotations

import unittest

import numpy as np

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from animation.core.presentation_contracts import (
    PIPELINE_TRACE, SceneValidationError, execute_scene, resolve_scene,
)


def _catalog(*, policy: str = "semantic") -> ComponentCatalog:
    return ComponentCatalog([ComponentDescriptor(
        component_id="aurora", version=1, palette_policy=policy,
        intensity_parameter="glow_intensity",
        optional_simulation_inputs=("foliage_density", "unknown_optional"),
        preserve_reason="calibrated diagnostic" if policy == "preserve" else None,
    )])


def _scene(**changes: object) -> dict:
    scene = {
        "schema": "ledgrid.scene.v1",
        "background": {
            "component_id": "aurora", "version": 1, "provider": "python",
            "role": "background", "parameters": {"glow_intensity": 0.75},
        },
        "vibe": "quiet",
        "master_brightness": 0.5,
    }
    scene.update(changes)
    return scene


class AnimationPipelineContractTests(unittest.TestCase):
    def test_vibe_scene_is_canonical_and_resolves_scaled_context(self) -> None:
        scene = _scene()
        resolved = resolve_scene(scene, _catalog(), monotonic_elapsed=4.0)
        reordered = {
            "master_brightness": 0.5, "vibe": "quiet", "background": scene["background"],
            "schema": "ledgrid.scene.v1",
        }
        equivalent = resolve_scene(reordered, _catalog(), monotonic_elapsed=4.0)

        self.assertEqual(resolved.canonical_bytes, equivalent.canonical_bytes)
        self.assertEqual(resolved.digest, equivalent.digest)
        self.assertEqual(resolved.canonical_scene["vibe_source"], "quiet")
        self.assertEqual(resolved.canonical_scene["palette_id"], "mist")
        self.assertAlmostEqual(resolved.phase_time, 2.8)
        self.assertEqual(resolved.parameters["glow_intensity"], 0.75)

    def test_custom_values_replace_vibe_and_preserve_bypasses_palette(self) -> None:
        scene = _scene(
            vibe=None,
            custom={
                "palette_id": "ember", "wall_pace": 1.5,
                "presentation_luminance": 0.4,
            },
        )
        resolved = resolve_scene(scene, _catalog(policy="preserve"), monotonic_elapsed=2.0)

        self.assertEqual(resolved.canonical_scene["vibe_source"], "custom")
        self.assertEqual(resolved.canonical_scene["palette_id"], "ember")
        self.assertEqual(resolved.phase_time, 3.0)
        self.assertIsNone(resolved.palette)

    def test_exact_ten_stage_order_and_single_luminance_then_master_output(self) -> None:
        observed: list[tuple[float, object, object]] = []

        def renderer(resolved):
            observed.append((
                resolved.phase_time, resolved.parameters["glow_intensity"], resolved.palette,
            ))
            return np.full((2, 3), 100, dtype=np.uint8)

        result = execute_scene(_scene(), _catalog(), monotonic_elapsed=4.0, renderer=renderer)

        self.assertEqual(result.trace, PIPELINE_TRACE)
        self.assertEqual(len(result.trace), 10)
        self.assertEqual(result.frame.tolist(), [[41, 41, 41], [41, 41, 41]])
        self.assertEqual(observed[0][0], 2.8)
        self.assertEqual(observed[0][1], 0.75)
        self.assertEqual(observed[0][2]["palette_id"], "mist")

    def test_headless_plant_inputs_are_neutral_and_optics_is_identity(self) -> None:
        resolved = resolve_scene(_scene(), _catalog(), monotonic_elapsed=0.0)
        result = execute_scene(
            _scene(master_brightness=1.0), _catalog(), monotonic_elapsed=0.0,
            renderer=lambda _resolved: np.array([[3, 4, 5]], dtype=np.uint8),
        )

        self.assertEqual(dict(resolved.plant_inputs), {"foliage_density": 0.0, "unknown_optional": 0.0})
        self.assertEqual(result.frame.tolist(), [[2, 3, 4]])  # quiet luminance only
        self.assertEqual(result.trace[-2], "apply_plant_optics")

    def test_legacy_aliases_fail_before_a_renderer_can_run(self) -> None:
        calls: list[object] = []
        scene = _scene()
        scene["speed"] = 2

        with self.assertRaisesRegex(SceneValidationError, "legacy"):
            execute_scene(
                scene, _catalog(), monotonic_elapsed=1.0,
                renderer=lambda _resolved: calls.append(True),
            )
        self.assertEqual(calls, [])
