"""Provider, role, and rejected-shape boundaries for packet-A scenes."""

from __future__ import annotations

import unittest

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from animation.core.presentation_contracts import SceneValidationError, resolve_scene


def _scene() -> dict:
    return {
        "schema": "ledgrid.scene.v1",
        "background": {
            "component_id": "aurora", "version": 1, "provider": "python",
            "role": "background", "parameters": {"glow_intensity": 0.4},
        },
        "vibe": "neutral", "master_brightness": 1.0,
    }


class ProviderRoleProductSurfaceTests(unittest.TestCase):
    def test_only_a_provider_qualified_python_background_is_available(self) -> None:
        catalog = ComponentCatalog([ComponentDescriptor(
            component_id="aurora", version=1, intensity_parameter="glow_intensity",
        )])
        scene = _scene()
        self.assertEqual(
            resolve_scene(scene, catalog, monotonic_elapsed=1.0).descriptor.component_id,
            "aurora",
        )

        scene["background"] = {**scene["background"], "provider": "receiver_native"}
        with self.assertRaisesRegex(SceneValidationError, "Python"):
            resolve_scene(scene, catalog, monotonic_elapsed=1.0)

    def test_overlay_full_scene_wall_clock_and_legacy_component_controls_are_rejected(self) -> None:
        catalog = ComponentCatalog([ComponentDescriptor(
            component_id="aurora", version=1, intensity_parameter="glow_intensity",
        )])
        for change in (
            {"overlays": []},
            {"background": {**_scene()["background"], "role": "overlay"}},
            {"background": {**_scene()["background"], "parameters": {"speed": 1}}},
            {"wall_clock": 10},
        ):
            scene = _scene()
            scene.update(change)
            with self.assertRaises(SceneValidationError):
                resolve_scene(scene, catalog, monotonic_elapsed=1.0)

    def test_preserve_policy_requires_an_explicit_fidelity_reason(self) -> None:
        with self.assertRaisesRegex(ValueError, "fidelity reason"):
            ComponentDescriptor(component_id="media", version=1, palette_policy="preserve")
        descriptor = ComponentDescriptor(
            component_id="media", version=1, palette_policy="preserve",
            preserve_reason="calibrated diagnostic",
        )
        self.assertEqual(descriptor.preserve_reason, "calibrated diagnostic")

    def test_wall_clock_descriptor_is_rejected_before_scene_activation(self) -> None:
        with self.assertRaisesRegex(ValueError, "scaled_context"):
            ComponentDescriptor(component_id="clock", version=1, timing_policy="wall_clock")
