"""Exact-provider edge paint coverage for the two accepted sculptures."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.installation_profile_runtime import InstallationProfileSelection
from animation.core.installation_profile_topology import INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import resolve_scene
from animation.plugins.flow_field_silk import FlowFieldSilkAnimation
from animation.plugins.living_stained_glass import LivingStainedGlassAnimation
from animation.core.plant_awareness import INSTALLATION_GEOMETRY_CONTACT_INPUT
from tests.unit.test_composer_slice import _current_scene
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "tests" / "fixtures" / "installation_profile_v1.bin"
RENDERERS = (FlowFieldSilkAnimation, LivingStainedGlassAnimation)


class SculptureEdgePresentationTests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)
        self.temporary = tempfile.TemporaryDirectory()
        library = InstallationProfileLibrary(Path(self.temporary.name) / "profiles")
        receipt = library.publish(PROFILE.read_bytes())
        selection = InstallationProfileSelection(library, INSTALLED_INSTALLATION_PROFILE_TOPOLOGY)
        self.assertTrue(selection.select(receipt.content_digest))
        self.view = selection.view
        self.contact = selection.view.geometry_contact

    def tearDown(self):
        self.temporary.cleanup()

    def _context(self, renderer, strength):
        scene = _current_scene()
        scene["animation"] = {
            "component_id": renderer.COMPONENT_ID, "version": 1,
            "provider": "python", "role": "animation", "parameters": {},
        }
        effects = {"version": 1, "active": [], "strengths": {}}
        if strength is not None:
            effects = {"version": 1, "active": ["refract"], "strengths": {"refract": strength}}
        scene["plants"]["effects"] = effects
        resolved = resolve_scene(scene, current_component_catalog(), monotonic_elapsed=1.0)
        return replace(resolved, installation_geometry=self.contact)

    @staticmethod
    def _state(renderer):
        semantic = renderer.filaments.copy() if hasattr(renderer, "filaments") else renderer.seeds.copy()
        return semantic, copy.deepcopy(renderer.rng.bit_generator.state), renderer._last_sim_tick

    def _assert_same_state(self, left, right):
        np.testing.assert_array_equal(left[0], right[0])
        self.assertEqual(left[1:], right[1:])

    def test_only_the_two_qualified_renderers_accept_provider_contact(self):
        for renderer in RENDERERS:
            with self.subTest(renderer=renderer.COMPONENT_ID):
                descriptor = renderer.component_descriptor()
                self.assertTrue(descriptor.accepts_installation_geometry_contact)
                self.assertEqual(renderer.PLANT_MODIFIER_SUPPORT, frozenset(("refract",)))

    def test_final_preview_uses_the_same_provider_contact_for_both_renderers(self):
        preview = ComposerFinalPreview(current_component_catalog(), ROOT)
        preview.set_installation_profile(self.view)
        for renderer in RENDERERS:
            with self.subTest(renderer=renderer.COMPONENT_ID):
                inputs = preview._plant_inputs({}, renderer.component_descriptor())
                self.assertIs(inputs[INSTALLATION_GEOMETRY_CONTACT_INPUT], self.contact)

    def test_canonical_edges_paint_without_changing_semantic_state(self):
        for renderer in RENDERERS:
            with self.subTest(renderer=renderer.COMPONENT_ID):
                off = renderer(self.controller, {"seed": 41})
                active = renderer(self.controller, {"seed": 41})
                off_frame = off.render_resolved_scene(self._context(renderer, None)).pixels.copy()
                active_frame = active.render_resolved_scene(self._context(renderer, 0.8)).pixels.copy()
                self.assertFalse(np.array_equal(off_frame, active_frame))
                self._assert_same_state(self._state(off), self._state(active))
                changed = np.any(off_frame.reshape(33, 138, 3) != active_frame.reshape(33, 138, 3), axis=2)
                if renderer is FlowFieldSilkAnimation:
                    self.assertTrue(np.all(changed <= (self.contact.geometry.distance <= 2.0)))
                else:
                    self.assertTrue(np.all(changed <= (self.contact.geometry.foliage_edge | self.contact.geometry.globe_edge)))

    def test_off_and_enabled_zero_are_exact_and_geometry_revision_is_presentation_only(self):
        for renderer in RENDERERS:
            with self.subTest(renderer=renderer.COMPONENT_ID):
                off = renderer(self.controller, {"seed": 53})
                zero = renderer(self.controller, {"seed": 53})
                off_frame = off.render_resolved_scene(self._context(renderer, None)).pixels.copy()
                zero_frame = zero.render_resolved_scene(self._context(renderer, 0.0)).pixels.copy()
                np.testing.assert_array_equal(off_frame, zero_frame)
                self._assert_same_state(self._state(off), self._state(zero))

                missing = renderer(self.controller, {"seed": 53})
                missing_context = replace(self._context(renderer, 0.8), installation_geometry=None)
                np.testing.assert_array_equal(off_frame, missing.render_resolved_scene(missing_context).pixels)

                for strength in (None, 0.0):
                    with self.subTest(inactive_strength=strength):
                        inactive = renderer(self.controller, {"seed": 53})
                        inactive_context = self._context(renderer, strength)
                        inactive.render_resolved_scene(inactive_context)
                        before_inactive = self._state(inactive)
                        revised_inactive = replace(
                            inactive_context,
                            installation_geometry=replace(
                                self.contact, identity=("inactive-geometry-revision", strength)
                            ),
                        )
                        self.assertFalse(inactive.render_resolved_scene(revised_inactive).changed)
                        self._assert_same_state(before_inactive, self._state(inactive))

                active = renderer(self.controller, {"seed": 53})
                context = self._context(renderer, 0.8)
                active.render_resolved_scene(context)
                before = self._state(active)
                revision = replace(self.contact, identity=("test-geometry-revision",))
                refreshed = replace(context, installation_geometry=revision)
                frame = active.render_resolved_scene(refreshed)
                self.assertTrue(frame.changed)
                self._assert_same_state(before, self._state(active))
                self.assertFalse(active.render_resolved_scene(refreshed).changed)


if __name__ == "__main__":
    unittest.main()
