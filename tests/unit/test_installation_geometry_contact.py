"""Provider-qualified installation geometry/contact contract coverage."""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

import numpy as np

from animation.component_parameters import validate_component_parameters
from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from animation.core.compositing import BaseFrame
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.installation_profile_runtime import InstallationProfileSelection
from animation.core.installation_profile_topology import (
    INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
)
from animation.core.plant_awareness import (
    INSTALLATION_GEOMETRY_CONTACT_INPUT,
    InstallationGeometryContact,
)
from animation.core.scene_runtime import CanonicalSceneRuntime, ScenePresentationContext
from ipc.scene_contract import SCENE_V2_SCHEMA, normalize_composer_scene
from web.composer_final_preview import ComposerFinalPreview


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "tests" / "fixtures" / "installation_profile_v1.bin"


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


def _descriptor(component_id: str, inputs=()) -> ComponentDescriptor:
    capabilities = ("simulation_inputs",) if inputs else ("none",)
    return ComponentDescriptor(
        component_id, 1, "python", "animation", "scaled_context",
        "opaque", "semantic", capabilities, (), optional_simulation_inputs=inputs,
    )


def _scene(component_id: str) -> dict:
    return {
        "schema": SCENE_V2_SCHEMA,
        "background": {
            "component_id": "native", "version": 1,
            "provider": "receiver_native", "role": "background",
            "parameters": {"bundle_digest": "a" * 64},
            "bundle_digest": "a" * 64,
        },
        "animation": {
            "component_id": component_id, "version": 1, "provider": "python",
            "role": "animation", "parameters": {},
        },
        "widgets": [],
        "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": "neutral", "pace": 1.0, "presentation_brightness": 1.0},
    }


class _Consumer:
    def __init__(self, controller):
        self.controller = controller
        self.context = None

    def render_resolved_scene(self, context):
        self.context = context
        return np.zeros((self.controller.total_leds, 3), dtype=np.uint8)


class InstallationGeometryContactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        library = InstallationProfileLibrary(Path(self.temporary.name) / "profiles")
        receipt = library.publish(PROFILE.read_bytes())
        selection = InstallationProfileSelection(
            library, INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
        )
        self.assertTrue(selection.select(receipt.content_digest))
        self.view = selection.view

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runtime(self, descriptor: ComponentDescriptor, resolver):
        catalog = ComponentCatalog([
            ComponentDescriptor(
                "native", 1, "receiver_native", "background", "scaled_context",
                "none", "semantic", ("final_optics",), (),
                defaults={"bundle_digest": "a" * 64},
            ),
            descriptor,
        ])
        consumer = _Consumer(_Controller())
        runtime = CanonicalSceneRuntime(
            _Controller(), catalog,
            background_renderer=lambda context, frame: BaseFrame(
                np.zeros((33 * 138, 3), dtype=np.uint8), changed=False
            ),
            animation_factory=lambda descriptor, controller, parameters: consumer,
            plant_input_resolver=resolver,
        )
        return runtime, catalog, consumer

    def test_runtime_view_exposes_immutable_distinct_contact_layers(self) -> None:
        contact = self.view.geometry_contact
        self.assertTrue(contact.available)
        self.assertEqual(contact.status, "ready")
        self.assertEqual(contact.exact_globe_cores.shape, (33, 138))
        self.assertFalse(contact.exact_globe_cores.flags.writeable)
        self.assertFalse(contact.clearance.flags.writeable)
        self.assertFalse(contact.safe.flags.writeable)
        self.assertGreater(np.count_nonzero(contact.clearance), np.count_nonzero(contact.exact_globe_cores))
        self.assertEqual(tuple(contact.named_globe_regions), (
            "top_left", "top_right", "upper_middle", "middle_left", "middle_right",
            "lower_left", "lower_right",
        ))
        with self.assertRaises(ValueError):
            contact.exact_globe_cores[0, 0] = True

    def test_only_an_explicit_descriptor_receives_contact(self) -> None:
        calls = []

        def resolver(_plants, descriptor):
            calls.append(descriptor.component_id)
            return {INSTALLATION_GEOMETRY_CONTACT_INPUT: self.view.geometry_contact}

        qualified = _descriptor("qualified", (INSTALLATION_GEOMETRY_CONTACT_INPUT,))
        runtime, catalog, consumer = self._runtime(qualified, resolver)
        canonical = normalize_composer_scene({"origin": "composer", "scene": _scene("qualified")}, catalog)
        runtime.activate(canonical)
        runtime.render_presentation(ScenePresentationContext(canonical, 1.0))
        self.assertEqual(calls, ["qualified"])
        self.assertIs(consumer.context.installation_geometry, self.view.geometry_contact)
        self.assertEqual(dict(consumer.context.plant_inputs), {})

        unqualified = _descriptor("unqualified")
        runtime, catalog, consumer = self._runtime(
            unqualified,
            lambda *_args: self.fail("unsupported animation resolved installation geometry"),
        )
        canonical = normalize_composer_scene({"origin": "composer", "scene": _scene("unqualified")}, catalog)
        runtime.activate(canonical)
        runtime.render_presentation(ScenePresentationContext(canonical, 1.0))
        self.assertIsNone(consumer.context.installation_geometry)

    def test_missing_contact_is_an_all_safe_statusful_fallback(self) -> None:
        qualified = _descriptor("qualified", (INSTALLATION_GEOMETRY_CONTACT_INPUT,))
        runtime, catalog, consumer = self._runtime(qualified, lambda *_args: {})
        canonical = normalize_composer_scene({"origin": "composer", "scene": _scene("qualified")}, catalog)
        runtime.activate(canonical)
        runtime.render_presentation(ScenePresentationContext(canonical, 1.0))
        contact = consumer.context.installation_geometry
        self.assertFalse(contact.available)
        self.assertIn("unavailable", contact.status)
        self.assertFalse(np.any(contact.exact_globe_cores))
        self.assertTrue(np.all(contact.safe))

    def test_final_preview_reuses_the_canonical_cache_for_a_qualified_contact(self) -> None:
        descriptor = _descriptor("qualified", (INSTALLATION_GEOMETRY_CONTACT_INPUT,))
        preview = ComposerFinalPreview(ComponentCatalog([]), ROOT)
        first = preview._plant_inputs({}, descriptor)[INSTALLATION_GEOMETRY_CONTACT_INPUT]
        second = preview._plant_inputs({}, descriptor)[INSTALLATION_GEOMETRY_CONTACT_INPUT]
        self.assertIs(first, second)
        self.assertEqual(first.exact_globe_cores.shape, (33, 138))
        self.assertFalse(first.exact_globe_cores.flags.writeable)

    def test_installed_final_and_composer_preview_accept_the_same_live_view(self) -> None:
        first = ComposerFinalPreview(ComponentCatalog([]), ROOT)
        second = ComposerFinalPreview(ComponentCatalog([]), ROOT)
        first.set_installation_profile(self.view)
        second.set_installation_profile(self.view)
        self.assertIs(first._installation_geometry(), self.view.plant_masks)
        self.assertIs(second._installation_geometry(), self.view.plant_masks)
        self.assertIs(first._plant_inputs({}, _descriptor("qualified", (INSTALLATION_GEOMETRY_CONTACT_INPUT,)))[INSTALLATION_GEOMETRY_CONTACT_INPUT], self.view.geometry_contact)

    def test_contact_is_limited_to_python_animations(self) -> None:
        with self.assertRaisesRegex(ValueError, "limited to Python Animation"):
            ComponentDescriptor(
                "native_contact", 1, "receiver_native", "background",
                "scaled_context", "none", "semantic", ("simulation_inputs",), (),
                optional_simulation_inputs=(INSTALLATION_GEOMETRY_CONTACT_INPUT,),
            )

    def test_calibration_and_contact_names_are_never_component_parameters(self) -> None:
        for name in (
            "plant_clearance", "plant_mask_path", "plant_globe_mask_path",
            INSTALLATION_GEOMETRY_CONTACT_INPUT,
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "legacy parameter alias"):
                    validate_component_parameters({name: "not-local"}, intensity_parameter=None)


if __name__ == "__main__":
    unittest.main()
