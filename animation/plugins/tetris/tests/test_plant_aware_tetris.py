"""Deterministic coverage for opt-in plant-aware Tetris behavior."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.tetris import (
    ActivePiece,
    PLANT_GLOBE_COLOR,
    TETROMINOS,
    TetrisAnimation,
)


class PlantAwareTetrisTests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(strips=8, leds_per_strip=12)

    @staticmethod
    def _semantic_state(animation):
        board = tuple(tuple(row) for row in animation.board)
        pieces = tuple(
            (
                piece.kind,
                piece.rotation,
                piece.x,
                piece.y,
                round(piece.fall_progress, 8),
                round(piece.action_accumulator, 8),
                piece.plan,
                piece.planning_deferred,
            )
            for piece in animation.active_pieces
        )
        return board, pieces, animation.lines_cleared, animation.game_over_flash

    def test_disabled_mode_preserves_default_frames_and_simulation(self):
        default = TetrisAnimation(self.controller, {"bot_imperfection": 0.0})
        disabled = TetrisAnimation(
            self.controller,
            {"bot_imperfection": 0.0, "plant_aware": False},
        )
        default.random.seed(4102)
        disabled.random.seed(4102)

        for frame_count, elapsed in enumerate((0.0, 0.02, 0.04, 0.08, 0.12)):
            default_frame = default.generate_frame(elapsed, frame_count)
            disabled_frame = disabled.generate_frame(elapsed, frame_count)
            np.testing.assert_array_equal(default_frame.pixels, disabled_frame.pixels)
            self.assertEqual(self._semantic_state(default), self._semantic_state(disabled))

    def test_enabled_mode_steers_landing_away_from_a_globe(self):
        # On an empty board the ordinary heuristic chooses the left edge first.
        # Cover that complete O-piece landing with a globe and verify the
        # plant-aware projected-board planner selects a visible destination.
        globe_indices = [12, 13, 24, 25]
        with tempfile.TemporaryDirectory() as directory:
            foliage_path = Path(directory) / "foliage.json"
            globe_path = Path(directory) / "globes.json"
            foliage_path.write_text(json.dumps({"covered_indices": []}), encoding="utf-8")
            globe_path.write_text(
                json.dumps({"globe_indices": globe_indices, "region_count": 1}),
                encoding="utf-8",
            )

            ordinary = TetrisAnimation(
                self.controller,
                {"bot_imperfection": 0.0},
            )
            aware = TetrisAnimation(
                self.controller,
                {
                    "bot_imperfection": 0.0,
                    "plant_aware": True,
                    "plant_clearance": 0,
                    "plant_mask_path": str(foliage_path),
                    "plant_globe_mask_path": str(globe_path),
                },
            )
            ordinary.random.seed(9)
            aware.random.seed(9)
            piece = ActivePiece("O")

            ordinary_move = ordinary._best_placement(piece, ordinary.board)
            aware_move = aware._best_placement(piece, aware.board)

            self.assertIsNotNone(ordinary_move)
            self.assertIsNotNone(aware_move)
            self.assertEqual(ordinary_move[1], 0)
            self.assertNotEqual(aware_move[1], ordinary_move[1])
            rotation, target_x, landing_y, _ = aware_move
            masks = aware.get_plant_masks()
            for cx, cy in TETROMINOS["O"]["rotations"][rotation]:
                strip = target_x + cx + 1
                physical_led = 11 - (landing_y + cy)
                self.assertFalse(masks.globes[strip, physical_led])

            frame = aware.generate_frame(0.0, 0).pixels
            self.assertTupleEqual(tuple(frame[globe_indices[0]]), PLANT_GLOBE_COLOR)


if __name__ == "__main__":
    unittest.main()
