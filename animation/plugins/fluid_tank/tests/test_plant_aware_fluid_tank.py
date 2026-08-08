"""Opt-in calibrated plant structure tests for Fluid Tank."""

import json
import random
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.fluid_tank import FluidTankAnimation


class PlantAwareFluidTankTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.foliage_path = root / "foliage.json"
        self.globe_path = root / "globes.json"
        # Canvas (x=3, y=4/5) maps to physical LED 5/4 on a ten-LED strip.
        self.foliage_path.write_text(json.dumps({"covered_indices": [35]}))
        self.globe_path.write_text(
            json.dumps({"globe_indices": [34], "region_count": 1})
        )
        self.controller = PreviewLEDController(strips=8, leds_per_strip=10)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_animation(self, **params):
        return FluidTankAnimation(self.controller, {
            "auto_hole": False,
            "plant_clearance": 0,
            "plant_mask_path": str(self.foliage_path),
            "plant_globe_mask_path": str(self.globe_path),
            **params,
        })

    @staticmethod
    def modifiers(*active, **strengths):
        return {"version": 1, "active": list(active), "strengths": strengths}

    def test_declares_exact_sprint_support(self):
        self.assertEqual(
            FluidTankAnimation.PLANT_MODIFIER_SUPPORT,
            frozenset(("obstacle", "refract", "slow_zone")),
        )

    def test_explicit_empty_state_and_live_changes_preserve_semantics_and_rng(self):
        random.seed(912)
        baseline = self._run(self.make_animation())
        random.seed(912)
        empty = self._run(self.make_animation(plant_modifiers=self.modifiers()))
        for left, right in zip(baseline[0], empty[0]):
            np.testing.assert_array_equal(left, right)
        self.assertEqual(baseline[1:], empty[1:])

        animation = self.make_animation()
        animation.start()
        animation.volume_cells = 17.5
        animation.surface_velocity[2] = 0.75
        before_random = random.getstate()
        before = (
            animation.volume_cells, animation.surface_offset.copy(),
            animation.surface_velocity.copy(), animation.last_time,
        )
        animation.update_parameters({
            "plant_modifiers": self.modifiers("refract", refract=1.0)
        })
        self.assertEqual(animation.volume_cells, before[0])
        np.testing.assert_array_equal(animation.surface_offset, before[1])
        np.testing.assert_array_equal(animation.surface_velocity, before[2])
        self.assertEqual(animation.last_time, before[3])
        self.assertEqual(random.getstate(), before_random)

    def test_slow_zone_reduces_local_motion_without_changing_volume(self):
        common_bubble = {
            "x": 3.0, "origin_y": 8.5, "y": 5.0, "radius": 0.6,
            "vy": -4.0, "phase": 0.0, "age": 0.0,
        }
        ordinary = self.make_animation()
        slowed = self.make_animation(
            plant_modifiers=self.modifiers("slow_zone", slow_zone=1.0)
        )
        for animation in (ordinary, slowed):
            animation.start()
            animation.volume_cells = animation.capacity_cells
            animation.bubbles = [dict(common_bubble)]
        slowed._refresh_plant_geometry()
        ordinary._update_bubbles(0.1, 0.1)
        slowed._update_bubbles(0.1, 0.1)

        self.assertLess(ordinary.bubbles[0]["y"], slowed.bubbles[0]["y"])
        self.assertEqual(ordinary.volume_cells, slowed.volume_cells)
        self.assertGreater(slowed._plant_slow_zone_steps, 0)

    def test_refract_changes_only_presentation(self):
        animation = self.make_animation(
            caustic_strength=0.4,
            plant_modifiers=self.modifiers("refract", refract=1.0),
        )
        animation.start()
        animation.volume_cells = animation.capacity_cells * 0.62
        animation.surface_offset[:] = np.linspace(-1.0, 1.0, animation.width)
        animation._refresh_plant_geometry()
        coverage, surface = animation._coverage_and_surface()
        before = (
            animation.volume_cells, animation.surface_offset.copy(),
            animation.surface_velocity.copy(), random.getstate(),
        )
        refracted = animation._render_frame(0.7, coverage, surface).copy()

        animation.update_parameters({"plant_modifiers": self.modifiers()})
        plain = animation._render_frame(0.7, coverage, surface).copy()
        self.assertFalse(np.array_equal(refracted, plain))
        self.assertEqual(animation.volume_cells, before[0])
        np.testing.assert_array_equal(animation.surface_offset, before[1])
        np.testing.assert_array_equal(animation.surface_velocity, before[2])
        self.assertEqual(random.getstate(), before[3])

    def test_combined_modifiers_conserve_supplied_water_over_long_run(self):
        random.seed(331)
        animation = self.make_animation(
            target_fill_time=120.0,
            bubble_interval=0.3,
            plant_modifiers=self.modifiers(
                "refract", "slow_zone", "obstacle",
                refract=1.0, slow_zone=1.0, obstacle=1.0,
            ),
        )
        animation.start()
        for frame in range(1, 301):
            animation.generate_frame(frame * 0.02, frame)

        system_volume = (
            animation.volume_cells
            + animation.inlet_reservoir_cells
            + sum(p["volume_cells"] for p in animation.inlet_particles)
        )
        self.assertAlmostEqual(system_volume, animation.total_inflow_cells, places=6)
        stats = animation.get_runtime_stats()
        self.assertGreater(stats["plant_slow_zone_steps"], 0)
        self.assertGreater(stats["plant_refracted_pixels"], 0)

    @staticmethod
    def _run(animation):
        frames = []
        animation.start()
        for frame_count, elapsed in enumerate((0.0, 0.04, 0.08, 0.12, 0.2)):
            frames.append(animation.generate_frame(elapsed, frame_count).copy())
        return frames, animation.volume_cells, animation.total_inflow_cells

    def test_disabled_mode_has_exact_frame_and_simulation_parity(self):
        random.seed(731)
        implicit = self._run(self.make_animation())
        random.seed(731)
        explicit = self._run(self.make_animation(plant_aware=False))

        for left, right in zip(implicit[0], explicit[0]):
            np.testing.assert_array_equal(left, right)
        self.assertEqual(implicit[1:], explicit[1:])

    def test_bubble_routes_around_masked_structure(self):
        animation = self.make_animation(plant_aware=True)
        animation.start()
        animation._refresh_plant_geometry()
        animation.volume_cells = animation.capacity_cells
        animation.bubbles = [{
            "x": 3.0, "origin_y": 8.5, "y": 5.25, "radius": 0.6,
            "vy": -1.0, "phase": 0.0, "age": 0.0,
        }]

        animation._update_bubbles(0.1, 0.1)

        self.assertEqual(len(animation.bubbles), 1)
        bubble = animation.bubbles[0]
        self.assertNotEqual(round(bubble["x"]), 3)
        self.assertFalse(animation._plant_clearance[
            int(round(bubble["y"])), int(round(bubble["x"]))
        ])
        self.assertGreater(animation._plant_flow_deflections, 0)

    def test_foliage_and_globe_are_distinct_visible_tank_landmarks(self):
        animation = self.make_animation(plant_aware=True, caustic_strength=0.0)
        animation.start()
        animation.volume_cells = animation.capacity_cells

        frame = animation.generate_frame(0.0, 0)
        # Canonical flat coordinates use the physical mask indices directly.
        foliage = frame[35]
        globe = frame[34]
        self.assertGreater(int(foliage[1]), int(foliage[0]))
        self.assertGreater(int(globe[0]), int(globe[2]))
        self.assertFalse(np.array_equal(foliage, globe))
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["plant_foliage_pixels"], 1)
        self.assertEqual(stats["plant_globe_pixels"], 1)

    def test_plant_controls_are_schema_valid_and_live_reload_geometry(self):
        animation = self.make_animation(plant_aware=True)
        schema = animation.get_parameter_schema()
        self.assertIn("plant_aware", schema)
        self.assertIn("plant_flow_deflection", schema)
        animation._refresh_plant_geometry()
        self.assertTrue(animation._plant_foliage[4, 3])

        self.foliage_path.write_text(json.dumps({"covered_indices": [25]}))
        animation.update_parameters({"plant_mask_path": str(self.foliage_path)})
        animation._refresh_plant_geometry()
        self.assertTrue(animation._plant_foliage[4, 2])
        self.assertFalse(animation._plant_foliage[4, 3])


if __name__ == "__main__":
    unittest.main()
