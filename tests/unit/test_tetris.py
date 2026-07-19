"""Unit tests for concurrent Tetris pieces."""

import unittest

from animation import RenderedFrame
from animation.core.manager import PreviewLEDController
from animation.plugins.tetris import (
    MAX_SPAWNS_PER_UPDATE,
    MAX_TETROMINO_COUNT,
    TetrisAnimation,
)


class TetrisAnimationTests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(strips=12, leds_per_strip=20)

    def test_defaults_to_five_active_tetrominoes(self):
        animation = TetrisAnimation(self.controller)

        animation.generate_frame(0.0, 0)

        self.assertEqual(animation.tetromino_count, 5)
        self.assertEqual(len(animation.active_pieces), 5)
        self.assertEqual(
            animation.get_parameter_schema()["tetromino_count"]["default"],
            5,
        )

    def test_configures_active_tetromino_count(self):
        animation = TetrisAnimation(self.controller, {"tetromino_count": 3})

        animation.generate_frame(0.0, 0)

        self.assertEqual(len(animation.active_pieces), 3)

    def test_supports_dozens_of_scattered_tetrominoes(self):
        animation = TetrisAnimation(
            self.controller,
            {"tetromino_count": 48, "speed": 0.2},
        )
        animation.random.seed(1234)

        for frame_count in range(6):
            animation.generate_frame(frame_count * 0.04, frame_count)

        positions = {(piece.x, piece.y) for piece in animation.active_pieces}
        rows = {piece.y for piece in animation.active_pieces}
        self.assertEqual(len(animation.active_pieces), 48)
        self.assertGreater(len(positions), 20)
        self.assertGreater(len(rows), 10)
        self.assertEqual(
            animation.get_parameter_schema()["tetromino_count"]["max"],
            MAX_TETROMINO_COUNT,
        )

    def test_high_density_work_is_throttled(self):
        animation = TetrisAnimation(self.controller, {"tetromino_count": 128})

        animation.generate_frame(0.0, 0)

        self.assertEqual(len(animation.active_pieces), MAX_SPAWNS_PER_UPDATE)
        self.assertEqual(animation._effective_render_fps(), 150.0)
        self.assertLess(animation._effective_drop_speed(), animation.drop_speed)

    def test_150_fps_cadence_under_200_hz_manager_loop(self):
        animation = TetrisAnimation(self.controller)

        frames = [
            animation.generate_frame(frame_count / 200.0, frame_count)
            for frame_count in range(200)
        ]

        changed_frames = sum(frame.changed for frame in frames)
        self.assertGreaterEqual(changed_frames, 149)
        self.assertLessEqual(changed_frames, 151)

    def test_runtime_count_changes_are_applied(self):
        animation = TetrisAnimation(self.controller, {"tetromino_count": 2})
        animation.generate_frame(0.0, 0)

        animation.update_parameters({"tetromino_count": 4})
        animation.generate_frame(0.01, 1)
        self.assertEqual(len(animation.active_pieces), 4)

        animation.update_parameters({"tetromino_count": 1})
        self.assertEqual(len(animation.active_pieces), 1)

    def test_render_rate_cap_reuses_unchanged_frame(self):
        animation = TetrisAnimation(self.controller, {"render_fps": 60.0})

        first = animation.generate_frame(0.0, 0)
        skipped = animation.generate_frame(0.001, 1)
        next_frame = animation.generate_frame(0.02, 2)

        self.assertIsInstance(first, RenderedFrame)
        self.assertTrue(first.changed)
        self.assertFalse(skipped.changed)
        self.assertIs(skipped.pixels, first.pixels)
        self.assertTrue(next_frame.changed)

    def test_pieces_keep_independent_movement_state(self):
        animation = TetrisAnimation(self.controller, {"tetromino_count": 2})
        animation.generate_frame(0.0, 0)
        first, second = animation.active_pieces
        first.y = -3
        first_y = first.y
        second_position = (second.x, second.y, second.rotation)

        moved = animation._move_piece(first, 0, 1)

        self.assertTrue(moved)
        self.assertEqual(first.y, first_y + 1)
        self.assertEqual((second.x, second.y, second.rotation), second_position)

    def test_manual_input_only_pauses_and_moves_selected_piece(self):
        animation = TetrisAnimation(self.controller, {"tetromino_count": 2})
        animation.generate_frame(0.0, 0)
        first, second = animation.active_pieces
        second.action_accumulator = 0.0
        second.fall_progress = 0.0
        second_position = (second.x, second.y, second.rotation)

        animation.handle_input("down")
        animation.generate_frame(0.01, 1)

        self.assertGreater(first.manual_override, 0.0)
        self.assertEqual(second.manual_override, 0.0)
        self.assertEqual((second.x, second.y, second.rotation), second_position)


if __name__ == "__main__":
    unittest.main()
