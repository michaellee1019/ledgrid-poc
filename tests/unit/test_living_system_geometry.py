"""Qualified installation-geometry continuity for the living systems."""

from __future__ import annotations

import copy
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.core.plant_awareness import InstallationGeometryContact, PlantMaskGeometry
from animation.core.presentation_contracts import ResolvedScene
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.living_ecosystem import LivingEcosystemAnimation
from animation.plugins.physarum_network import PhysarumNetworkAnimation
from animation.plugins.reaction_diffusion_garden import ReactionDiffusionGardenAnimation
from animation.plugins.wind_in_the_reeds import WindInTheReedsAnimation
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


COMPONENTS = (
    LivingEcosystemAnimation,
    PhysarumNetworkAnimation,
    ReactionDiffusionGardenAnimation,
    WindInTheReedsAnimation,
)
ROOT = Path(__file__).resolve().parents[2]


def _contact(identity=("first",), *, core_x=10) -> InstallationGeometryContact:
    shape = (33, 138)
    empty = np.zeros(shape, dtype=bool)
    foliage = empty.copy(); foliage[3:7, 30:38] = True
    cores = empty.copy(); cores[core_x, 52] = True
    clearance = empty.copy(); clearance[core_x - 1:core_x + 2, 51:54] = True
    edge = cores.copy()
    distance = np.zeros(shape, dtype=np.float32)
    geometry = PlantMaskGeometry(
        foliage=foliage, globes=cores, obstacle=cores, clearance=clearance,
        foliage_flat=foliage.reshape(-1), globes_flat=cores.reshape(-1),
        obstacle_flat=cores.reshape(-1), clearance_flat=clearance.reshape(-1),
        foliage_count=int(foliage.sum()), globe_count=1, globe_regions=1,
        foliage_edge=foliage, globe_edge=edge, obstacle_edge=edge,
        distance=distance, normal_x=distance, normal_y=distance,
        globe_region_masks={},
    )
    return InstallationGeometryContact.from_geometry(geometry, identity=identity)


def _context(component, contact=None, strength=None, phase=0.0):
    effects = {
        "version": 1,
        "active": [] if strength is None else ["habitat"],
        "strengths": {} if strength is None else {"habitat": strength},
    }
    return ResolvedScene(
        canonical_scene={"plants": {"effects": effects}},
        canonical_bytes=b"living-system-geometry",
        digest="living-system-geometry",
        descriptor=component.component_descriptor(),
        parameters=component.COMPONENT_DEFAULTS,
        palette={"palette_id": "neutral"},
        phase_time=phase,
        plant_inputs={},
        installation_geometry=contact,
    )


class LivingSystemGeometryTests(unittest.TestCase):
    def test_all_four_are_explicit_contact_consumers_without_local_geometry(self):
        forbidden = {"calibration", "geometry", "mask", "installation_geometry_contact"}
        for component in COMPONENTS:
            with self.subTest(component=component.COMPONENT_ID):
                descriptor = component.component_descriptor()
                self.assertTrue(descriptor.accepts_installation_geometry_contact)
                self.assertEqual(component.PLANT_MODIFIER_SUPPORT, frozenset(("habitat",)))
                self.assertFalse(forbidden & set(component.COMPONENT_DEFAULTS))
                self.assertFalse(forbidden & set(component(_Controller()).get_parameter_schema()))

    def test_composer_final_preview_supplies_the_same_provider_input_to_each(self):
        preview = ComposerFinalPreview(current_component_catalog(), Path("."))
        descriptors = {entry.component_id: entry for entry in current_component_catalog().descriptors}
        contacts = []
        for component in COMPONENTS:
            contact = preview._plant_inputs({}, descriptors[component.COMPONENT_ID])["installation_geometry_contact"]
            self.assertEqual(contact.exact_globe_cores.shape, (33, 138))
            self.assertFalse(contact.exact_globe_cores.flags.writeable)
            contacts.append(contact)
        self.assertIs(contacts[0], contacts[-1])

    def test_manager_final_path_keeps_the_qualified_contact_and_effect_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            library = InstallationProfileLibrary(Path(directory) / "profiles")
            receipt = library.publish((ROOT / "tests/fixtures/installation_profile_v1.bin").read_bytes())
            with contextlib.redirect_stdout(io.StringIO()):
                manager = AnimationManager(
                    PreviewLEDController(33, 138), auto_start=False,
                    installation_profile_library=library,
                )
            manager._launch_animation_loop = lambda: None
            manager.select_installation_profile(receipt.content_digest)
            manager.set_plant_modifiers({
                "version": 1, "active": ["habitat"], "strengths": {"habitat": 1.0},
            })
            animation = LivingEcosystemAnimation(manager.controller, {"seed": 81})
            component = manager._new_scene_component(
                "living_ecosystem", animation, {"seed": 81}, started_at=0.0
            )
            manager._render_scene_component(
                component, now=0.0, resolved_vibe=manager._resolved_vibe,
                operator_tempo=1.0, overlay=False,
            )
            self.assertEqual(animation._geometry_strength, 1.0)
            self.assertTrue(np.any(animation._geometry_cores))

    def test_off_zero_and_missing_contact_are_exact_noops(self):
        for component in COMPONENTS:
            with self.subTest(component=component.COMPONENT_ID):
                ordinary = component(_Controller(), {"seed": 307})
                zero = component(_Controller(), {"seed": 307})
                missing = component(_Controller(), {"seed": 307})
                ordinary.set_presentation_context(_context(component))
                zero.set_presentation_context(_context(component, _contact(), 0.0))
                missing.set_presentation_context(_context(
                    component,
                    InstallationGeometryContact.unavailable(33, 138, status="missing"),
                    1.0,
                ))
                for index, elapsed in enumerate((0.0, .05, .10, .20)):
                    baseline = ordinary.generate_frame(elapsed, index)
                    for candidate in (zero, missing):
                        frame = candidate.generate_frame(elapsed, index)
                        np.testing.assert_array_equal(frame.pixels, baseline.pixels)
                        self.assertEqual(candidate.logical_state(), ordinary.logical_state())
                        self.assertEqual(candidate.rng.bit_generator.state, ordinary.rng.bit_generator.state)
                        self.assertEqual(candidate._last_sim_tick, ordinary._last_sim_tick)

    def test_live_geometry_revision_only_stages_derived_views_until_next_tick(self):
        for component in COMPONENTS:
            with self.subTest(component=component.COMPONENT_ID):
                animation = component(_Controller(), {"seed": 911})
                first = _contact(("first",))
                second = _contact(("second",), core_x=12)
                animation.set_presentation_context(_context(component, first, 1.0))
                initial = animation.generate_frame(0.0, 0)
                state = animation.logical_state()
                rng = copy.deepcopy(animation.rng.bit_generator.state)
                tick = animation._last_sim_tick

                animation.set_presentation_context(_context(component, second, 1.0))
                same_tick = animation.generate_frame(0.0, 1)
                np.testing.assert_array_equal(same_tick.pixels, initial.pixels)
                self.assertEqual(animation.logical_state(), state)
                self.assertEqual(animation.rng.bit_generator.state, rng)
                self.assertEqual(animation._last_sim_tick, tick)

                animation.generate_frame(.10, 2)
                self.assertIsNone(animation._pending_geometry)
                if component is WindInTheReedsAnimation:
                    self.assertTrue(animation._geometry_globe_edge[12, 52])
                else:
                    self.assertTrue(animation._geometry_cores[12, 52])


if __name__ == "__main__":
    unittest.main()
