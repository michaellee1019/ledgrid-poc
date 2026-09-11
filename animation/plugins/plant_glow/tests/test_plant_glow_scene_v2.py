"""Focused resolved-Scene coverage for the current Plant Glow Animation."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from animation.core.compositing import BaseFrame
from animation.core.manager import PreviewLEDController
from animation.core.plant_awareness import (
    INSTALLATION_GEOMETRY_CONTACT_INPUT,
    InstallationGeometryContact,
    PlantMaskGeometry,
)
from animation.core.presentation_contracts import resolve_scene
from animation.core.scene_runtime import CanonicalSceneRuntime
from animation.plugins.plant_glow import PlantGlowAnimation
from ipc.scene_contract import normalize_composer_scene


def scene(*, palette="neutral", pace=1.0, brightness=1.0, parameters=None):
    return {
        "schema": "ledgrid.scene.v2",
        "background": {
            "component_id": "native", "version": 1, "provider": "receiver_native",
            "role": "background", "parameters": {"bundle_digest": "a" * 64},
            "bundle_digest": "a" * 64,
        },
        "animation": {
            "component_id": "plant_glow", "version": 1, "provider": "python",
            "role": "animation", "parameters": parameters or {},
        },
        "widgets": [],
        "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {
            "palette_id": palette, "pace": pace,
            "presentation_brightness": brightness,
        },
    }


def contact(identity=("installed",), *, foliage_x=4, globe_x=21):
    shape = (33, 138)
    foliage = np.zeros(shape, dtype=np.bool_)
    foliage[foliage_x:foliage_x + 3, 30:38] = True
    globes = np.zeros(shape, dtype=np.bool_)
    globes[globe_x:globe_x + 2, 78:81] = True
    obstacle = globes.copy()
    clearance = globes.copy()
    distance = np.zeros(shape, dtype=np.float32)
    geometry = PlantMaskGeometry(
        foliage=foliage, globes=globes, obstacle=obstacle, clearance=clearance,
        foliage_flat=foliage.reshape(-1), globes_flat=globes.reshape(-1),
        obstacle_flat=obstacle.reshape(-1), clearance_flat=clearance.reshape(-1),
        foliage_count=int(foliage.sum()), globe_count=int(globes.sum()), globe_regions=1,
        foliage_edge=foliage, globe_edge=globes, obstacle_edge=globes,
        distance=distance, normal_x=distance, normal_y=distance,
        globe_region_masks={},
    )
    return InstallationGeometryContact.from_geometry(geometry, identity=identity)


class PlantGlowSceneV2Tests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)
        native = ComponentDescriptor(
            "native", 1, "receiver_native", "background", "scaled_context", "none",
            "preserve", ("final_optics",), ("native_preview",),
            defaults={"bundle_digest": "a" * 64},
        )
        self.catalog = ComponentCatalog([native, PlantGlowAnimation.component_descriptor()])
        self.contact = contact()

    def resolved(self, *, elapsed=1.0, contact_value=None, **kwargs):
        resolved = resolve_scene(scene(**kwargs), self.catalog, monotonic_elapsed=elapsed)
        return replace(
            resolved,
            installation_geometry=self.contact if contact_value is None else contact_value,
        )

    def test_descriptor_and_schema_expose_only_local_current_controls(self):
        descriptor = PlantGlowAnimation.component_descriptor()
        self.assertEqual(
            (
                descriptor.component_id, descriptor.provider.value, descriptor.role.value,
                descriptor.timing_policy.value, descriptor.alpha_behavior.value,
                descriptor.palette_policy.value,
            ),
            (
                "plant_glow", "python", "animation", "scaled_context",
                "premultiplied_rgba", "semantic",
            ),
        )
        self.assertTrue(descriptor.accepts_installation_geometry_contact)
        self.assertEqual(
            set(PlantGlowAnimation(self.controller).get_parameter_schema()),
            set(PlantGlowAnimation.DEFAULTS),
        )
        for retired in (
            "brightness", "speed", "background_source", "foliage_red",
            "mask_path", "plant_aware",
        ):
            with self.subTest(retired=retired), self.assertRaisesRegex(ValueError, "non-local"):
                PlantGlowAnimation(self.controller, {retired: 1})

    def test_resolved_scene_emits_transparent_premultiplied_cores_and_halos(self):
        glow = PlantGlowAnimation(self.controller)
        rendered = glow.render_resolved_scene(
            self.resolved(elapsed=0.0, parameters={"breath_depth": 0.0, "shimmer": 0.0})
        )
        self.assertEqual(rendered.pixels.shape, (33 * 138, 4))
        self.assertEqual(rendered.pixels.dtype, np.uint8)
        self.assertTrue(rendered.pixels.flags.c_contiguous)
        self.assertTrue(np.all(rendered.pixels[:, :3] <= rendered.pixels[:, 3:4]))
        self.assertTrue(np.all(rendered.pixels[glow._foliage_core, 3] == 255))
        self.assertTrue(np.all(rendered.pixels[glow._globe_core, 3] == 255))
        halos = (glow._foliage_halo > 0) | (glow._globe_halo > 0)
        self.assertGreater(np.count_nonzero(halos), 0)
        self.assertTrue(np.all(rendered.pixels[halos, 3] > 0))
        self.assertGreater(np.count_nonzero(rendered.pixels[:, 3] == 0), 0)

    def test_scene_palette_and_pace_change_presentation_without_semantic_state(self):
        parameters = {"breath_speed": 0.5, "breath_depth": 0.7, "shimmer": 0.2}
        neutral = PlantGlowAnimation(self.controller)
        neutral_frame = neutral.render_resolved_scene(
            self.resolved(elapsed=0.5, palette="neutral", parameters=parameters)
        )
        state = neutral.semantic_snapshot()
        cached = neutral.render_resolved_scene(
            self.resolved(elapsed=0.5, palette="neutral", parameters=parameters)
        )
        mist_frame = neutral.render_resolved_scene(
            self.resolved(elapsed=0.5, palette="mist", parameters=parameters)
        )
        fast = PlantGlowAnimation(self.controller).render_resolved_scene(
            self.resolved(elapsed=0.5, palette="neutral", pace=2.0, parameters=parameters)
        )
        self.assertFalse(cached.changed)
        self.assertEqual(state, neutral.semantic_snapshot())
        np.testing.assert_array_equal(neutral_frame.pixels[:, 3], mist_frame.pixels[:, 3])
        self.assertFalse(np.array_equal(neutral_frame.pixels[:, :3], mist_frame.pixels[:, :3]))
        self.assertFalse(np.array_equal(neutral_frame.pixels, fast.pixels))

    def test_missing_geometry_and_zero_layer_intensities_are_exact_no_paint(self):
        missing = PlantGlowAnimation(self.controller).render_resolved_scene(
            self.resolved(
                contact_value=InstallationGeometryContact.unavailable(
                    33, 138, status="missing"
                )
            )
        )
        zero = PlantGlowAnimation(self.controller).render_resolved_scene(
            self.resolved(parameters={"foliage_intensity": 0.0, "globe_intensity": 0.0})
        )
        self.assertFalse(np.any(missing.pixels))
        self.assertFalse(np.any(zero.pixels))

    def test_live_geometry_revision_rebuilds_only_presentation(self):
        glow = PlantGlowAnimation(self.controller)
        first = glow.render_resolved_scene(self.resolved(elapsed=0.75)).pixels.copy()
        state = glow.semantic_snapshot()
        revised = replace(
            self.resolved(elapsed=0.75),
            installation_geometry=contact(("revised",), foliage_x=12, globe_x=27),
        )
        second = glow.render_resolved_scene(revised)
        self.assertTrue(second.changed)
        self.assertFalse(np.array_equal(first, second.pixels))
        self.assertEqual(state, glow.semantic_snapshot())
        self.assertFalse(glow.render_resolved_scene(revised).changed)

    def test_canonical_final_composes_over_native_and_applies_scene_brightness(self):
        canonical = normalize_composer_scene(
            {
                "origin": "composer",
                "scene": scene(brightness=0.5, parameters={"breath_depth": 0.0}),
            },
            self.catalog,
        )
        native = np.full((33 * 138, 3), (20, 40, 60), dtype=np.uint8)
        runtime = CanonicalSceneRuntime(
            self.controller,
            self.catalog,
            background_renderer=lambda _context, _count: BaseFrame(native, changed=True),
            animation_factory=lambda _descriptor, controller, parameters: (
                PlantGlowAnimation(controller, parameters)
            ),
            plant_input_resolver=lambda _plants, _descriptor: {
                INSTALLATION_GEOMETRY_CONTACT_INPUT: self.contact
            },
        )
        runtime.activate(canonical)
        rendered = runtime.render(0.0)
        expected_source = PlantGlowAnimation(
            self.controller, {"breath_depth": 0.0}
        ).render_resolved_scene(
            replace(
                resolve_scene(
                    scene(brightness=0.5, parameters={"breath_depth": 0.0}),
                    self.catalog,
                    monotonic_elapsed=0.0,
                ),
                installation_geometry=self.contact,
            )
        )
        np.testing.assert_array_equal(rendered.foreground.pixels, expected_source.pixels)
        transparent = rendered.foreground.pixels[:, 3] == 0
        np.testing.assert_array_equal(
            rendered.pixels[transparent],
            np.broadcast_to(
                np.array((10, 20, 30), dtype=np.uint8),
                rendered.pixels[transparent].shape,
            ),
        )
        self.assertTrue(
            np.any(
                rendered.pixels[~transparent]
                != np.array((10, 20, 30), dtype=np.uint8)
            )
        )

    def test_four_curated_presets_are_normalized_and_visibly_distinct(self):
        preset_dir = Path(__file__).resolve().parents[1] / "presets"
        paths = sorted(preset_dir.glob("*.json"))
        self.assertEqual(
            [path.stem for path in paths],
            ["canopy-pulse", "fine-veins", "globe-constellation", "soft-moss"],
        )
        frames = []
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["animation"], "plant_glow")
            parameters = PlantGlowAnimation._normalized_parameters(payload["params"])
            frames.append(
                PlantGlowAnimation(self.controller, parameters)
                .render_resolved_scene(self.resolved(elapsed=1.25, parameters=parameters))
                .pixels.copy()
            )
        self.assertEqual(len({frame.tobytes() for frame in frames}), 4)


if __name__ == "__main__":
    unittest.main()
