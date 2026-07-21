"""Unit tests for the Conway Game of Life animation plugin."""

import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.conway_life import ConwayLifeAnimation


class ConwayLifeAnimationTests(unittest.TestCase):
    @staticmethod
    def live_cells(animation):
        return {
            (x, y)
            for y, row in enumerate(animation.grid)
            for x, value in enumerate(row)
            if value > 0
        }

    @staticmethod
    def advance_generation(animation):
        phase_frames = int(animation.params.get("phase_frames", 1))
        for _ in range(phase_frames * 2):
            animation._advance_phase()

    def test_conway_is_allowed_plugin(self):
        self.assertIn("conway_life", AnimationManager.ALLOWED_PLUGINS)

    def test_preview_frame_generation(self):
        controller = PreviewLEDController(strips=8, leds_per_strip=8)
        manager = AnimationManager(controller)

        preview = manager.get_animation_preview("conway_life")
        self.assertEqual(len(preview["frame_data"]), 64)
        self.assertEqual(preview["current_animation"], "conway_life")
        self.assertNotIn("error", preview)

    def test_seeded_blinker_evolves(self):
        controller = PreviewLEDController(strips=8, leds_per_strip=8)
        animation = ConwayLifeAnimation(
            controller,
            {
                "random_density": 0.0,
                "wrap_edges": False,
                "phase_frames": 1,
                "generations_per_second": 12.0,
                "seed_cells": [(3, 2), (3, 3), (3, 4)],
            },
        )

        # First call initializes step timing.
        animation.generate_frame(0.0, 0)
        # This advances exactly two steps (color->transition->next generation).
        animation.generate_frame(0.09, 1)

        self.assertEqual(animation.generation, 1)

        expected_live_cells = {(2, 3), (3, 3), (4, 3)}
        self.assertSetEqual(self.live_cells(animation), expected_live_cells)

    def test_visual_atmosphere_does_not_change_conway_rules(self):
        controller = PreviewLEDController(strips=16, leds_per_strip=16)
        common = {
            "seed_pattern": "r_pentomino",
            "wrap_edges": False,
            "glider_interval": 0,
            "stagnation_generations": 0,
            "random_seed": 42,
        }
        plain = ConwayLifeAnimation(controller, {**common, "palette": "monochrome", "background": "void"})
        scenic = ConwayLifeAnimation(controller, {**common, "palette": "neon", "background": "earth"})

        for _ in range(20):
            plain._advance_phase()
            scenic._advance_phase()

        self.assertEqual(plain.generation, scenic.generation)
        self.assertSetEqual(self.live_cells(plain), self.live_cells(scenic))

    def test_named_pattern_and_atmospheric_background_render(self):
        controller = PreviewLEDController(strips=16, leds_per_strip=16)
        animation = ConwayLifeAnimation(
            controller,
            {
                "seed_pattern": "pulsar",
                "random_density": 0,
                "background": "earth",
                "background_brightness": 0.3,
                "brightness": 1.0,
            },
        )
        self.assertEqual(animation.alive_cells, 48)

        frame = animation.generate_frame(0.0, 0).pixels
        self.assertTrue(any(tuple(pixel) != (0, 0, 0) for pixel in frame))

    def test_schema_exposes_visual_and_seed_choices(self):
        animation = ConwayLifeAnimation(PreviewLEDController(strips=8, leds_per_strip=8))
        schema = animation.get_parameter_schema()
        self.assertIn("earth", schema["background"]["options"])
        self.assertIn("arcade", schema["background"]["options"])
        self.assertIn("gosper_glider_gun", schema["seed_pattern"]["options"])
        self.assertIn("bioluminescent", schema["palette"]["options"])
        self.assertIn("background_animation", schema)
        self.assertEqual(schema["background_fps"]["max"], 30.0)
        self.assertIn("tile_installation", schema)
        self.assertEqual(schema["tile_rows"]["max"], 16)

    def test_animated_background_has_independent_bounded_cadence(self):
        animation = ConwayLifeAnimation(
            PreviewLEDController(strips=16, leds_per_strip=16),
            {
                "seed_cells": [(7, 7), (7, 8), (8, 7), (8, 8)],
                "random_density": 0,
                "speed": 0.1,
                "phase_frames": 30,
                "generations_per_second": 0.5,
                "glider_interval": 0,
                "stagnation_generations": 0,
                "destruct_on_loop": False,
                "background": "arcade",
                "background_brightness": 0.3,
                "background_animation": True,
                "background_speed": 1.0,
                "background_fps": 10.0,
            },
        )

        first = animation.generate_frame(0.0, 0)
        before_tick = animation.generate_frame(0.05, 1)
        after_tick = animation.generate_frame(0.11, 2)

        self.assertTrue(first.changed)
        self.assertFalse(before_tick.changed)
        self.assertTrue(after_tick.changed)
        self.assertEqual(animation.generation, 0)
        self.assertFalse(np.array_equal(first.pixels, after_tick.pixels))

    def test_background_animation_can_be_frozen(self):
        animation = ConwayLifeAnimation(
            PreviewLEDController(strips=8, leds_per_strip=8),
            {
                "random_density": 0,
                "speed": 0.1,
                "phase_frames": 30,
                "generations_per_second": 0.5,
                "background": "arcade",
                "background_brightness": 0.3,
                "background_animation": False,
            },
        )

        animation.generate_frame(0.0, 0)
        later = animation.generate_frame(0.11, 1)

        self.assertFalse(later.changed)

    def test_tiled_pulsars_repeat_into_isolated_regions(self):
        animation = ConwayLifeAnimation(
            PreviewLEDController(strips=32, leds_per_strip=32),
            {
                "seed_pattern": "pulsar",
                "random_density": 0,
                "wrap_edges": False,
                "tile_installation": True,
                "tile_columns": 2,
                "tile_rows": 2,
                "tile_gutter": 1,
                "destruct_on_loop": False,
                "glider_interval": 0,
            },
        )

        self.assertEqual(len(animation._tile_regions), 4)
        self.assertEqual(animation.alive_cells, 4 * 48)
        self.assertTrue(
            all(animation._tile_ids[y][x] >= 0 for x, y in self.live_cells(animation))
        )

    def test_tiled_regions_ignore_neighbors_across_shared_boundary(self):
        common = {
            "seed_cells": [(2, 2), (2, 3), (4, 2)],
            "random_density": 0,
            "wrap_edges": False,
            "destruct_on_loop": False,
            "glider_interval": 0,
        }
        plain = ConwayLifeAnimation(
            PreviewLEDController(strips=8, leds_per_strip=8), common
        )
        tiled = ConwayLifeAnimation(
            PreviewLEDController(strips=8, leds_per_strip=8),
            {
                **common,
                "tile_installation": True,
                "tile_columns": 2,
                "tile_rows": 1,
                "tile_gutter": 0,
            },
        )

        self.assertEqual(plain.next_grid[2][3], 1)
        self.assertEqual(tiled.next_grid[2][3], 0)

    def test_destruct_on_loop_restarts_exact_period_two_oscillator(self):
        animation = ConwayLifeAnimation(
            PreviewLEDController(strips=12, leds_per_strip=12),
            {
                "seed_cells": [(5, 4), (5, 5), (5, 6)],
                "wrap_edges": False,
                "phase_frames": 1,
                "glider_interval": 0,
                "stagnation_generations": 0,
                "destruct_on_loop": True,
                "destruct_on_loop_action": "restart",
            },
        )

        self.advance_generation(animation)
        self.assertEqual(animation.destruct_on_loop_recoveries, 0)
        self.advance_generation(animation)

        self.assertEqual(animation.destruct_on_loop_recoveries, 1)
        self.assertEqual(animation.last_detected_loop_period, 2)
        self.assertEqual(animation.last_detected_loop_generation, 2)
        self.assertEqual(animation.last_destruct_on_loop_action, "restart")
        self.assertSetEqual(self.live_cells(animation), {(5, 4), (5, 5), (5, 6)})

    def test_destruct_on_loop_glider_storm_breaks_oscillator(self):
        animation = ConwayLifeAnimation(
            PreviewLEDController(strips=20, leds_per_strip=20),
            {
                "seed_cells": [(9, 8), (9, 9), (9, 10)],
                "wrap_edges": False,
                "phase_frames": 1,
                "glider_interval": 0,
                "stagnation_generations": 0,
                "destruct_on_loop": True,
                "destruct_on_loop_action": "glider_storm",
                "destruct_on_loop_gliders": 6,
                "random_seed": 77,
            },
        )

        self.advance_generation(animation)
        self.advance_generation(animation)

        self.assertEqual(animation.destruct_on_loop_recoveries, 1)
        self.assertEqual(animation.last_detected_loop_period, 2)
        self.assertEqual(animation.last_destruct_on_loop_action, "glider_storm")
        self.assertGreater(animation.alive_cells, 3)
        self.assertLessEqual(len(animation._loop_history), 1)

    def test_destruct_on_loop_history_is_strictly_bounded(self):
        animation = ConwayLifeAnimation(
            PreviewLEDController(strips=8, leds_per_strip=8),
            {
                "destruct_on_loop": True,
                "destruct_on_loop_history": 16,
                "glider_interval": 0,
            },
        )
        animation._reset_loop_monitor()

        for generation in range(100):
            animation._remember_loop_fingerprint((generation, generation + 1), generation)

        self.assertEqual(len(animation._loop_history), 16)
        self.assertEqual(len(animation._loop_order), 16)

    def test_destruct_on_loop_can_be_disabled(self):
        animation = ConwayLifeAnimation(
            PreviewLEDController(strips=12, leds_per_strip=12),
            {
                "seed_cells": [(5, 4), (5, 5), (5, 6)],
                "wrap_edges": False,
                "phase_frames": 1,
                "glider_interval": 0,
                "stagnation_generations": 0,
                "destruct_on_loop": False,
            },
        )

        for _ in range(6):
            self.advance_generation(animation)

        self.assertEqual(animation.generation, 6)
        self.assertEqual(animation.destruct_on_loop_recoveries, 0)
        self.assertEqual(len(animation._loop_history), 0)

    def test_checked_in_preset_library_is_discoverable_and_valid(self):
        preset_dir = Path(__file__).parents[2] / "presets" / "animations" / "conway_life"
        preset_paths = sorted(preset_dir.glob("*.json"))
        self.assertGreaterEqual(len(preset_paths), 12)
        for path in preset_paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["animation"], "conway_life")
            self.assertEqual(payload["preset_id"], path.stem)
            self.assertIsInstance(payload["params"], dict)

    def test_curated_presets_apply_destruct_on_loop_by_intent(self):
        preset_dir = Path(__file__).parents[2] / "presets" / "animations" / "conway_life"
        expected_actions = {
            "arcade-afterlife": "glider_storm",
            "aurora-garden": "glider_storm",
            "bioluminescent-tide": "reseed",
            "classic-green": "reseed",
            "deep-space-acorn": "restart",
            "earth-cities": "glider_storm",
            "ice-crystal": "reseed",
            "r-pentomino-laboratory": "restart",
            "solar-embers": "glider_storm",
            "synthwave-sunset": "glider_storm",
        }
        intentional_or_continuously_disrupted = {
            "gosper-foundry",
            "maximum-chaos",
            "neon-glider-storm",
            "oscillator-orchard",
            "pulsar-observatory",
        }

        for preset_id, action in expected_actions.items():
            payload = json.loads(
                (preset_dir / f"{preset_id}.json").read_text(encoding="utf-8")
            )
            self.assertTrue(payload["params"]["destruct_on_loop"], preset_id)
            self.assertEqual(payload["params"]["destruct_on_loop_action"], action)

        for preset_id in intentional_or_continuously_disrupted:
            payload = json.loads(
                (preset_dir / f"{preset_id}.json").read_text(encoding="utf-8")
            )
            self.assertFalse(payload["params"]["destruct_on_loop"], preset_id)

        expected_tiling = {
            "deep-space-acorn": (2, 4),
            "gosper-foundry": (2, 3),
            "oscillator-orchard": (4, 10),
            "pulsar-observatory": (2, 4),
            "r-pentomino-laboratory": (2, 4),
        }
        for preset_id, (columns, rows) in expected_tiling.items():
            payload = json.loads(
                (preset_dir / f"{preset_id}.json").read_text(encoding="utf-8")
            )
            self.assertTrue(payload["params"]["tile_installation"], preset_id)
            self.assertEqual(payload["params"]["tile_columns"], columns)
            self.assertEqual(payload["params"]["tile_rows"], rows)


if __name__ == "__main__":
    unittest.main()
