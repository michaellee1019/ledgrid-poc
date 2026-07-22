"""Deterministic coverage for opt-in plant-aware pinball behavior."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.core.plant_awareness import GLOBE_REGION_ORDER
from animation.plugins.pinball import PinballAnimation


class PlantAwarePinballTests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(strips=8, leds_per_strip=40)

    @staticmethod
    def _state(animation):
        return (
            round(animation.ball_x, 8),
            round(animation.ball_y, 8),
            round(animation.ball_vx, 8),
            round(animation.ball_vy, 8),
            animation.score,
            animation.streak,
            animation.balls,
            animation.mode,
            animation._plant_hits,
        )

    def test_disabled_mode_preserves_frames_and_simulation(self):
        ordinary = PinballAnimation(self.controller, {"seed": 317})
        disabled = PinballAnimation(
            self.controller, {
                "seed": 317, "plant_aware": False,
                "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            }
        )

        for frame_count, elapsed in enumerate((0.0, 0.02, 0.04, 0.08, 0.12, 0.18)):
            ordinary_frame = ordinary.generate_frame(elapsed, frame_count)
            disabled_frame = disabled.generate_frame(elapsed, frame_count)
            np.testing.assert_array_equal(ordinary_frame.pixels, disabled_frame.pixels)
            self.assertEqual(self._state(ordinary), self._state(disabled))

    def test_globe_is_a_visible_scoring_bumper_and_routing_constraint(self):
        # Canvas (x=4, y=20) maps to strip 4, physical LED 19.
        globe_index = 4 * 40 + 19
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foliage_path = root / "foliage.json"
            globe_path = root / "globes.json"
            foliage_path.write_text(json.dumps({"covered_indices": []}), encoding="utf-8")
            globe_path.write_text(
                json.dumps({"globe_indices": [globe_index], "region_count": 1}),
                encoding="utf-8",
            )
            animation = PinballAnimation(
                self.controller,
                {
                    "seed": 11,
                    "plant_aware": True,
                    "plant_clearance": 1,
                    "plant_mask_path": str(foliage_path),
                    "plant_globe_mask_path": str(globe_path),
                },
            )
            animation.ball_x = 2.0
            animation.ball_y = 20.0
            animation.ball_vx = 30.0
            animation.ball_vy = 0.0
            starting_score = animation.score

            animation._update(0.1)

            self.assertLess(animation.ball_vx, 0.0)
            self.assertFalse(animation._plant_clearance[
                int(round(animation.ball_y)), int(round(animation.ball_x))
            ])
            self.assertEqual(animation._plant_hits, 1)
            self.assertGreaterEqual(animation.score - starting_score, 2500)
            self.assertTrue(any(burst.color == animation.YELLOW for burst in animation.bursts))

            animation._render()
            self.assertGreater(int(animation._canvas[20, 4, 0]), 100)
            self.assertGreater(int(animation._canvas[20, 4, 0]),
                               int(animation._canvas[20, 4, 2]))

    @staticmethod
    def _modifier_state(modifier, strength=1.0):
        return {"version": 1, "active": [modifier], "strengths": {modifier: strength}}

    def _make_region_animation(self, modifier, strength=1.0):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        foliage_path = root / "foliage.json"
        globe_path = root / "globes.json"
        foliage_index = 10  # canvas (0, 29)
        # Canvas y=20 maps to physical LED 19 on a 40 LED strip.
        indices = [x * 40 + 19 for x in range(1, 8)]
        foliage_path.write_text(json.dumps({"covered_indices": [foliage_index]}))
        globe_path.write_text(json.dumps({
            "globe_indices": indices,
            "region_count": 7,
            "pixels": [
                {"index": index, "region": name}
                for index, name in zip(indices, GLOBE_REGION_ORDER)
            ],
        }))
        return PinballAnimation(self.controller, {
            "seed": 29,
            "plant_clearance": 1,
            "plant_mask_path": str(foliage_path),
            "plant_globe_mask_path": str(globe_path),
            "plant_modifiers": self._modifier_state(modifier, strength),
        })

    def test_bumper_reflects_on_exact_globe_not_clearance(self):
        animation = self._make_region_animation("bumper", 1.0)
        animation.ball_x, animation.ball_y = 0.0, 20.0
        animation.ball_vx, animation.ball_vy = 20.0, 0.0
        animation.ball_x = 1.0
        animation._collide_with_plants(0.0, 20.0)

        self.assertLess(animation.ball_vx, 0.0)
        self.assertEqual(animation._plant_bumper_hits, 1)
        # Clearance beside the core is planning geometry, not a collision surface.
        animation.ball_x, animation.ball_y = 0.0, 19.0
        before = animation._plant_bumper_hits
        animation._collide_with_plants(0.0, 19.0)
        self.assertEqual(animation._plant_bumper_hits, before)

    def test_portal_cycles_all_regions_preserving_velocity_and_cooldown(self):
        animation = self._make_region_animation("portal", 0.0)
        original_velocity = (13.0, -7.0)
        for index, source in enumerate(GLOBE_REGION_ORDER):
            animation._plant_portal_exit_region = None
            animation._plant_portal_cooldown_updates = 0
            animation.ball_x, animation.ball_y = float(index + 1), 20.0
            animation.ball_vx, animation.ball_vy = original_velocity
            animation._collide_with_plants(animation.ball_x, animation.ball_y)
            self.assertEqual(
                animation._plant_portal_exit_region,
                GLOBE_REGION_ORDER[(index + 1) % 7],
            )
            self.assertEqual((animation.ball_vx, animation.ball_vy), original_velocity)

        teleports = animation._plant_teleports
        animation._collide_with_plants(animation.ball_x, animation.ball_y)
        self.assertEqual(animation._plant_teleports, teleports)
        self.assertEqual(teleports, 7)

    def test_hazard_exact_contact_drains_ball_and_counts(self):
        animation = self._make_region_animation("hazard", 0.8)
        starting_balls = animation.balls
        animation.ball_x, animation.ball_y = 1.0, 20.0

        animation._collide_with_plants(1.0, 20.0)

        self.assertEqual(animation.balls, starting_balls - 1)
        self.assertEqual(animation._plant_hazards, 1)
        self.assertGreater(animation.drain_time, 0.85)

    def test_seeded_bumper_run_is_finite_and_reports_contacts(self):
        animation = self._make_region_animation("bumper", 0.7)
        for _ in range(80):
            animation.ball_x, animation.ball_y = 0.0, 20.0
            animation.ball_vx, animation.ball_vy = 20.0, 0.0
            animation._update(0.05)
        stats = animation.get_runtime_stats()
        self.assertTrue(np.isfinite([
            animation.ball_x, animation.ball_y, animation.ball_vx, animation.ball_vy
        ]).all())
        self.assertGreater(stats["plant_bumper_hits"], 0)


if __name__ == "__main__":
    unittest.main()
