"""Deterministic coverage for opt-in plant-aware pinball behavior."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
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
            self.controller, {"seed": 317, "plant_aware": False}
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


if __name__ == "__main__":
    unittest.main()
