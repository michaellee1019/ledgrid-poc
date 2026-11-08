"""Unit tests for concurrent Tetris pieces."""

import unittest

from animation import RenderedFrame
from animation.core.manager import PreviewLEDController
from animation.plugins.tetris import (
    ActivePiece,
    MAX_COORDINATED_PLANS,
    MAX_SPAWNS_PER_UPDATE,
    MAX_TETROMINO_COUNT,
    META_ROTATIONS,
    PLAYER_MAX_SECONDS,
    PLAYER_RESTART_SECONDS,
    TETROMINOS,
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
            {"tetromino_count": 48, "fall_rate": 0.2},
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

    def test_time_rewind_repaints_without_advancing_gameplay_or_rng(self):
        animation = TetrisAnimation(self.controller, {"seed": 4102, "bot_imperfection": 0.0})
        animation.generate_frame(0.0, 0)
        animation.generate_frame(0.02, 1)
        before = (
            tuple(tuple(row) for row in animation.board),
            tuple((piece.kind, piece.rotation, piece.x, piece.y, piece.fall_progress,
                   piece.action_accumulator) for piece in animation.active_pieces),
            animation.lines_cleared,
            animation.random.getstate(),
        )

        rewound = animation.generate_frame(0.0, 2)

        self.assertTrue(rewound.changed)
        self.assertEqual(animation.last_elapsed, 0.0)
        self.assertEqual(before, (
            tuple(tuple(row) for row in animation.board),
            tuple((piece.kind, piece.rotation, piece.x, piece.y, piece.fall_progress,
                   piece.action_accumulator) for piece in animation.active_pieces),
            animation.lines_cleared,
            animation.random.getstate(),
        ))

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

    def test_autoplay_renders_settled_and_falling_pieces_on_wall_edges(self):
        animation = TetrisAnimation(
            PreviewLEDController(strips=33, leds_per_strip=138),
            {"tetromino_count": 1},
        )
        animation.board[-1][0] = TETROMINOS["I"]["color"]
        animation.board[-1][-1] = TETROMINOS["J"]["color"]
        animation.active_pieces.append(ActivePiece("O", x=0, y=5, fall_progress=0.5))

        pixels = animation.generate_frame(0.0, 0).pixels.reshape(33, 138, 3)

        self.assertTrue(pixels[0, 0].any())
        self.assertTrue(pixels[-1, 0].any())
        self.assertTrue(pixels[0, 130:133].any())

    def test_every_meta_rotation_contains_three_real_pieces_and_four_gaps(self):
        for kind, rotations in META_ROTATIONS.items():
            outer = TETROMINOS[kind]["rotations"]
            for turn, layout in enumerate(rotations):
                with self.subTest(kind=kind, turn=turn):
                    shape = outer[turn % len(outer)]
                    contents = [
                        (inner.x + x, inner.y + y)
                        for inner in layout.contents
                        for x, y in TETROMINOS[inner.kind]["rotations"][inner.rotation]
                    ]
                    self.assertEqual(len(layout.contents), 3)
                    self.assertEqual(len(contents), 12)
                    self.assertEqual(len(set(contents)), 12)
                    cells = set(contents)
                    self.assertEqual(cells, set(layout.cells))
                    silhouette = {
                        (2 * x + dx, 2 * y + dy)
                        for x, y in shape for dx in range(2) for dy in range(2)
                    }
                    self.assertEqual(cells - silhouette, set())
                    gaps = silhouette - cells
                    self.assertEqual(len(gaps), 4)
                    self.assertTrue(any(
                        {(x - 1, y), (x + 1, y)} <= cells
                        or {(x, y - 1), (x, y + 1)} <= cells
                        for x, y in gaps
                    ))

    def test_player_moves_the_meta_and_hard_drop_releases_its_contents(self):
        animation = TetrisAnimation(PreviewLEDController(strips=33, leds_per_strip=138))
        animation.generate_frame(0.0, 0)
        animation.set_player_control(True)
        frame = animation.generate_frame(0.1, 1)
        piece, = animation.active_pieces
        self.assertTrue(piece.meta)
        width = animation._shape_extent(animation._piece_shape(piece), axis=0)
        self.assertEqual((piece.x, piece.y), ((animation.board_width - width) // 2, 0))
        self.assertEqual((animation.board_width, animation.board_height), (20, 46))
        pixels = frame.pixels.reshape(33, 138, 3)
        self.assertEqual(len({tuple(p) for p in pixels[:, :-1].reshape(-1, 3) if any(p)}), 3)
        self.assertTrue(pixels[:, -1].any(axis=1).all())

        start_x = piece.x
        animation.handle_input("left")
        animation.handle_input("rotate-right")
        for index in range(1, 16):
            animation.generate_frame(0.1 + index * 0.04, index + 1)
        self.assertEqual(piece.x, start_x - 1)
        self.assertEqual(piece.rotation, 1)
        self.assertIsNone(piece.plan)
        self.assertTrue(12 < piece.y < 20)

        animation.handle_input("drop")
        animation._update_game(0.0)
        self.assertEqual(len(animation.active_pieces), 3)
        self.assertTrue(all(not inner.meta for inner in animation.active_pieces))
        self.assertTrue(all(cell is None for row in animation.board for cell in row))
        for _ in range(12):
            animation._update_game(0.01)
            if animation.active_pieces[0].meta:
                break
        self.assertEqual(sum(cell is not None for row in animation.board for cell in row), 12)
        next_piece, = animation.active_pieces
        self.assertTrue(next_piece.meta)
        self.assertEqual(next_piece.y, 0)

        animation.set_player_control(False)
        animation.generate_frame(0.78, 18)
        self.assertEqual((animation.board_width, animation.board_height), (33, 138))
        self.assertEqual(len(animation.active_pieces), 5)

    def test_contact_releases_meta_without_input(self):
        animation = TetrisAnimation(PreviewLEDController(strips=33, leds_per_strip=138))
        animation.set_player_control(True)
        animation._update_game(0.0)
        meta, = animation.active_pieces
        shape = animation._piece_shape(meta)
        x, y = max(shape, key=lambda cell: cell[1])
        animation.board[meta.y + y + 1][meta.x + x] = (10, 10, 10)
        animation._update_game(0.05)
        self.assertEqual(len(animation.active_pieces), 3)
        self.assertTrue(all(not piece.meta for piece in animation.active_pieces))

    def test_line_clear_rebases_other_falling_pieces(self):
        animation = TetrisAnimation(PreviewLEDController(strips=33, leds_per_strip=138))
        animation.set_player_control(True)
        height = animation.board_height
        animation.board[-1] = [None] * 4 + [(10, 10, 10)] * 16
        falling = ActivePiece("O", x=0, y=height - 6)
        animation.active_pieces = [falling, ActivePiece("I", y=height - 1)]

        animation._step_inner_pieces()

        self.assertEqual(animation.active_pieces, [falling])
        self.assertEqual(falling.y, height - 4)
        self.assertEqual(animation.lines_cleared, 1)
        self.assertEqual(animation.player_round_deadline, 20.0)

    def test_player_round_earns_time_by_clearing_lines_and_caps_at_40_seconds(self):
        animation = TetrisAnimation(PreviewLEDController(strips=33, leds_per_strip=138))
        animation.set_player_control(True)
        self.assertEqual(animation.player_round_deadline, 15.0)
        opening_speed = animation._effective_drop_speed()
        for lines in range(1, 7):
            animation.board[-1] = [None] * 4 + [(10, 10, 10)] * 16
            animation.active_pieces = [ActivePiece("I", y=animation.board_height - 1)]
            animation._step_inner_pieces()
            self.assertEqual(animation.lines_cleared, lines)
            self.assertEqual(animation.player_round_deadline, min(40.0, 15.0 + 5.0 * lines))

        animation.player_round_elapsed = PLAYER_MAX_SECONDS - 0.01
        self.assertGreater(animation._effective_drop_speed(), opening_speed)
        animation._update_game(0.02)
        self.assertEqual(animation.player_round_elapsed, PLAYER_MAX_SECONDS)
        self.assertEqual(animation.player_restart_in, PLAYER_RESTART_SECONDS)
        animation.handle_gamepad_action("drop")
        animation._update_game(PLAYER_RESTART_SECONDS)
        self.assertEqual(animation.player_round_deadline, 15.0)
        self.assertEqual(animation.lines_cleared, 0)
        self.assertTrue(all(cell is None for row in animation.board for cell in row))

    def test_player_arena_fills_every_strip_with_separate_countdown(self):
        animation = TetrisAnimation(PreviewLEDController(strips=33, leds_per_strip=138))
        animation.set_player_control(True)
        animation.board[-1] = [(10, 10, 10)] * animation.board_width
        animation.player_round_elapsed = 7.5
        frame = animation.next_frame_buffer(clear=True)
        animation._render_player_board(frame)
        pixels = frame.reshape(33, 138, 3)
        self.assertTrue(pixels[:, 0].any(axis=1).all())
        self.assertEqual(int(pixels[:, -1].any(axis=1).sum()), 16)

    def test_multiple_pieces_reserve_complementary_line_clearing_moves(self):
        animation = TetrisAnimation(
            self.controller,
            {"tetromino_count": 2, "bot_imperfection": 0.0},
        )
        filled = (10, 10, 10)
        animation.board[-2] = [None] * 4 + [filled] * 7
        animation.board[-1] = [None] * 4 + [filled] * 7
        animation.active_pieces = [ActivePiece("O"), ActivePiece("O")]

        animation._coordinate_plans()

        target_columns = {piece.plan["x"] for piece in animation.active_pieces}
        self.assertEqual(target_columns, {0, 2})
        self.assertTrue(all(piece.plan is not None for piece in animation.active_pieces))

    def test_dense_mode_bounds_shared_planning_horizon(self):
        animation = TetrisAnimation(
            self.controller,
            {"tetromino_count": MAX_TETROMINO_COUNT, "bot_imperfection": 0.0},
        )
        animation.active_pieces = [
            ActivePiece("I", y=-4 + index % 16)
            for index in range(MAX_TETROMINO_COUNT)
        ]

        animation._coordinate_plans()

        planned = sum(piece.plan is not None for piece in animation.active_pieces)
        self.assertLessEqual(planned, MAX_COORDINATED_PLANS)
        self.assertEqual(
            animation.get_runtime_stats()["max_coordinated_plans"],
            MAX_COORDINATED_PLANS,
        )

    def test_bot_steering_scales_for_wide_led_grids(self):
        controller = PreviewLEDController(strips=32, leds_per_strip=140)
        animation = TetrisAnimation(controller, {"bot_imperfection": 0.0})
        piece = ActivePiece("O", x=0, y=0)

        animation._move_toward_x(piece, 20)

        self.assertGreater(piece.x, 1)
        self.assertLessEqual(piece.x, 20)


if __name__ == "__main__":
    unittest.main()
