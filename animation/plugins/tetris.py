#!/usr/bin/env python3
"""Tetris animation that plays itself on the LED grid."""

import random
import threading
from typing import List, Tuple, Dict, Any, Optional

from animation import AnimationBase
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


Color = Tuple[int, int, int]
BoardCell = Optional[Color]


BASE_SHAPES = {
    "I": ["XXXX"],
    "J": ["X..", "XXX"],
    "L": ["..X", "XXX"],
    "O": ["XX", "XX"],
    "S": [".XX", "XX."],
    "T": [".X.", "XXX"],
    "Z": ["XX.", ".XX"],
}

PIECE_COLORS: Dict[str, Color] = {
    "I": (0, 255, 255),
    "J": (0, 102, 255),
    "L": (255, 140, 0),
    "O": (255, 220, 0),
    "S": (0, 255, 120),
    "T": (200, 0, 200),
    "Z": (255, 60, 60),
}


def _normalize(coords: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    min_x = min(x for x, _ in coords)
    min_y = min(y for _, y in coords)
    return [(x - min_x, y - min_y) for x, y in coords]


def _rotate(coords: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    return [(y, -x) for x, y in coords]


def _parse_shape(rows: List[str]) -> List[Tuple[int, int]]:
    coords: List[Tuple[int, int]] = []
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char != '.':
                coords.append((x, y))
    return coords


def _build_rotations(rows: List[str]) -> List[List[Tuple[int, int]]]:
    coords = _parse_shape(rows)
    rotations: List[List[Tuple[int, int]]] = []
    seen = set()
    current = coords
    for _ in range(4):
        normalized = _normalize(current)
        key = tuple(sorted(normalized))
        if key not in seen:
            rotations.append(normalized)
            seen.add(key)
        current = _rotate(current)
    return rotations


TETROMINOS: Dict[str, Dict[str, Any]] = {
    name: {
        'rotations': _build_rotations(patterns),
        'color': PIECE_COLORS[name]
    }
    for name, patterns in BASE_SHAPES.items()
}


class TetrisAnimation(AnimationBase):
    """Classic Tetris with a simple self-playing bot."""

    ANIMATION_NAME = "Tetris"
    ANIMATION_DESCRIPTION = "Autoplaying Tetris board with traditional tetrominoes"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.num_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.board_width = max(1, self.num_strips - 1)  # Keep column 0 dark
        self.board_height = self.leds_per_strip
        self.board: List[List[BoardCell]] = [
            [None for _ in range(self.board_width)]
            for _ in range(self.board_height)
        ]

        self.current_piece: Optional[str] = None
        self.current_rotation = 0
        self.piece_x = 0
        self.piece_y = 0
        self.fall_progress = 0.0
        self.last_elapsed: Optional[float] = None
        self.action_accumulator = 0.0
        self.current_plan: Optional[Dict[str, Any]] = None
        self.lines_cleared = 0
        self.random = random.Random()
        self.game_over_flash = 0.0
        self.input_queue: List[str] = []
        self.input_lock = threading.Lock()
        self.manual_override = 0.0

        self.default_params.update({
            'speed': 1.0,
            'bot_imperfection': 0.18,
        })
        self.params = {**self.default_params, **self.config}

        self.base_drop_speed = max(12.0, self.board_height / 4.0)
        self._refresh_runtime_params()

    def _refresh_runtime_params(self):
        speed = max(0.2, float(self.params.get('speed', 1.0)))
        self.drop_speed = self.base_drop_speed * speed
        self.action_interval = max(0.025, 0.1 / speed)
        self.fail_rate = min(0.6, max(0.0, float(self.params.get('bot_imperfection', 0.18))))

    def get_parameter_schema(self) -> Dict[str, Any]:
        schema = super().get_parameter_schema()
        schema.update({
            'bot_imperfection': {
                'type': 'float',
                'min': 0.0,
                'max': 0.6,
                'default': 0.18,
                'description': 'Chance for the bot to pick a risky move'
            }
        })
        return schema

    def update_parameters(self, params: Dict[str, Any]):
        super().update_parameters(params)
        self._refresh_runtime_params()

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Color]:
        total_pixels = self.num_strips * self.leds_per_strip
        frame: List[Color] = [(0, 0, 0)] * total_pixels

        delta = 0.0
        if self.last_elapsed is None:
            self.last_elapsed = time_elapsed
        else:
            delta = max(0.0, time_elapsed - self.last_elapsed)
            self.last_elapsed = time_elapsed

        self._update_game(delta)

        if self.game_over_flash > 0.0:
            flash_strength = int(80 * min(1.0, self.game_over_flash))
            if flash_strength > 0:
                tint = self.apply_brightness((flash_strength, 10, 10))
                for strip in range(1, self.num_strips):
                    base_index = strip * self.leds_per_strip
                    for led in range(self.board_height):
                        phys_led = (self.leds_per_strip - 1) - led
                        idx = base_index + phys_led
                        if idx < len(frame):
                            frame[idx] = tint

        for y in range(self.board_height):
            row = self.board[y]
            for x in range(self.board_width):
                color = row[x]
                if color:
                    self._set_pixel(frame, x + 1, y, color)

        if self.current_piece:
            active_color = self._active_piece_color(self.current_piece)
            coords = self._current_shape()
            for cx, cy in coords:
                px = self.piece_x + cx
                py = self.piece_y + cy
                if 0 <= px < self.board_width and 0 <= py < self.board_height:
                    self._set_pixel(frame, px + 1, py, active_color)

        return frame

    def handle_input(self, action: str):
        """Handle player input from the D-pad."""
        action = (action or '').lower().replace('_', '-')
        if action == 'up':
            action = 'rotate-right'
        valid = {'left', 'right', 'down', 'rotate-left', 'rotate-right', 'drop'}
        if action not in valid:
            return
        with self.input_lock:
            self.input_queue.append(action)
        self.manual_override = max(self.manual_override, 2.0)

    def _update_game(self, delta: float):
        if self.board_width <= 0 or self.board_height <= 0:
            return

        if self.game_over_flash > 0.0:
            self.game_over_flash = max(0.0, self.game_over_flash - delta)

        if self.current_piece is None:
            self._spawn_piece()

        if self.current_piece is None:
            return

        if self.manual_override > 0.0:
            self.manual_override = max(0.0, self.manual_override - delta)

        self._apply_pending_inputs()

        if self.manual_override <= 0.0:
            self.action_accumulator += delta
            while self.action_accumulator >= self.action_interval:
                self._run_player_step()
                self.action_accumulator -= self.action_interval

        self.fall_progress += delta * self.drop_speed
        while self.fall_progress >= 1.0:
            if not self._move_piece(0, 1):
                self._lock_piece()
                break
            self.fall_progress -= 1.0

    def _spawn_piece(self):
        piece = self.random.choice(list(TETROMINOS.keys()))
        self.current_piece = piece
        self.current_rotation = 0
        self.fall_progress = 0.0
        self.action_accumulator = 0.0
        coords = self._current_shape()
        width = self._shape_extent(coords, axis=0)
        self.piece_x = max(0, (self.board_width - width) // 2)
        self.piece_y = -3
        self.current_plan = None
        if self._collides(self.board, coords, self.piece_x, self.piece_y):
            self._handle_game_over()
        else:
            self._plan_move()

    def _handle_game_over(self):
        self.board = [[None for _ in range(self.board_width)] for _ in range(self.board_height)]
        self.current_piece = None
        self.current_plan = None
        self.game_over_flash = 1.0

    def _run_player_step(self):
        if not self.current_piece:
            return
        if not self.current_plan or self.current_plan.get('piece') != self.current_piece:
            self._plan_move()
        if not self.current_plan:
            return

        target_rotation = self.current_plan['rotation']
        rotation_count = len(TETROMINOS[self.current_piece]['rotations'])
        if rotation_count > 1 and self.current_rotation != target_rotation:
            diff = (target_rotation - self.current_rotation) % rotation_count
            direction = 1 if diff <= rotation_count / 2 else -1
            rotated = self._rotate_piece(direction)
            if rotated:
                return

        target_x = self.current_plan['x']
        if self.piece_x < target_x:
            self._move_piece(1, 0)
        elif self.piece_x > target_x:
            self._move_piece(-1, 0)
        else:
            if self.random.random() < 0.35:
                self._move_piece(0, 1)

    def _plan_move(self):
        if not self.current_piece:
            self.current_plan = None
            return

        options = self._enumerate_moves(self.current_piece)
        if not options:
            self.current_plan = None
            return

        weighted = []
        for option in options:
            noise = self.random.uniform(-4.5, 4.5)
            weighted.append((option['score'] + noise, option))
        weighted.sort(key=lambda item: item[0], reverse=True)

        if len(weighted) > 1 and self.random.random() < self.fail_rate:
            idx = self.random.randint(max(1, len(weighted) // 2), len(weighted) - 1)
            chosen = weighted[idx][1]
        else:
            chosen = weighted[0][1]

        chosen['piece'] = self.current_piece
        self.current_plan = chosen

    def _apply_pending_inputs(self):
        with self.input_lock:
            if not self.input_queue:
                return
            pending = list(self.input_queue)
            self.input_queue.clear()

        for action in pending:
            if not self.current_piece:
                return
            if action == 'left':
                self._move_piece(-1, 0)
            elif action == 'right':
                self._move_piece(1, 0)
            elif action == 'down':
                self._move_piece(0, 1)
            elif action == 'rotate-left':
                self._rotate_piece(-1)
            elif action == 'rotate-right':
                self._rotate_piece(1)
            elif action == 'drop':
                while self._move_piece(0, 1):
                    pass
                self._lock_piece()
                return

    def _enumerate_moves(self, piece: str) -> List[Dict[str, Any]]:
        info = TETROMINOS[piece]
        moves: List[Dict[str, Any]] = []
        for rotation_idx, coords in enumerate(info['rotations']):
            width = self._shape_extent(coords, axis=0)
            max_x = self.board_width - width
            for x in range(0, max_x + 1):
                drop_y = self._find_drop_y(coords, x)
                if drop_y is None:
                    continue
                preview_board = self._commit_preview(coords, x, drop_y)
                score = self._evaluate_board(preview_board)
                moves.append({
                    'rotation': rotation_idx,
                    'x': x,
                    'score': score,
                    'landing_y': drop_y,
                })
        return moves

    def _find_drop_y(self, coords: List[Tuple[int, int]], x_offset: int) -> Optional[int]:
        y = -4
        while True:
            if self._collides(self.board, coords, x_offset, y + 1):
                if self._collides(self.board, coords, x_offset, y):
                    return None
                return y
            y += 1
            if y > self.board_height:
                return None

    def _commit_preview(self, coords: List[Tuple[int, int]], x_offset: int, y_offset: int) -> List[List[BoardCell]]:
        preview = [row[:] for row in self.board]
        color = (255, 255, 255)
        for cx, cy in coords:
            px = x_offset + cx
            py = y_offset + cy
            if 0 <= px < self.board_width and 0 <= py < self.board_height:
                preview[py][px] = color
        return preview

    def _evaluate_board(self, board: List[List[BoardCell]]) -> float:
        heights = [0] * self.board_width
        holes = 0
        for x in range(self.board_width):
            column_filled = False
            for y in range(self.board_height):
                cell = board[y][x]
                if cell and not column_filled:
                    heights[x] = self.board_height - y
                    column_filled = True
                elif not cell and column_filled:
                    holes += 1
        aggregate_height = sum(heights)
        bumpiness = 0
        for x in range(self.board_width - 1):
            bumpiness += abs(heights[x] - heights[x + 1])
        lines = sum(1 for row in board if all(row))
        score = (lines * 12.0) - (aggregate_height * 0.25) - (holes * 1.6) - (bumpiness * 0.4)
        return score

    def _move_piece(self, dx: int, dy: int) -> bool:
        if not self.current_piece:
            return False
        coords = self._current_shape()
        new_x = self.piece_x + dx
        new_y = self.piece_y + dy
        if self._collides(self.board, coords, new_x, new_y):
            return False
        self.piece_x = new_x
        self.piece_y = new_y
        return True

    def _rotate_piece(self, direction: int) -> bool:
        if not self.current_piece:
            return False
        info = TETROMINOS[self.current_piece]
        rotations = info['rotations']
        new_rotation = (self.current_rotation + direction) % len(rotations)
        coords = rotations[new_rotation]
        if self._collides(self.board, coords, self.piece_x, self.piece_y):
            return False
        self.current_rotation = new_rotation
        return True

    def _lock_piece(self):
        if not self.current_piece:
            return
        coords = self._current_shape()
        color = TETROMINOS[self.current_piece]['color']
        overflow = False
        for cx, cy in coords:
            px = self.piece_x + cx
            py = self.piece_y + cy
            if py < 0:
                overflow = True
                continue
            if 0 <= px < self.board_width and 0 <= py < self.board_height:
                self.board[py][px] = color
        if overflow:
            self._handle_game_over()
            return
        cleared = self._clear_lines()
        if cleared:
            self.lines_cleared += cleared
        self.current_piece = None
        self.current_plan = None

    def _clear_lines(self) -> int:
        remaining: List[List[BoardCell]] = []
        cleared = 0
        for row in self.board:
            if all(row):
                cleared += 1
            else:
                remaining.append(row)
        while len(remaining) < self.board_height:
            remaining.insert(0, [None for _ in range(self.board_width)])
        self.board = remaining
        return cleared

    def _collides(self, board: List[List[BoardCell]], coords: List[Tuple[int, int]], x_offset: int, y_offset: int) -> bool:
        for cx, cy in coords:
            px = x_offset + cx
            py = y_offset + cy
            if px < 0 or px >= self.board_width:
                return True
            if py >= self.board_height:
                return True
            if py >= 0 and board[py][px]:
                return True
        return False

    def _current_shape(self) -> List[Tuple[int, int]]:
        if not self.current_piece:
            return []
        rotations = TETROMINOS[self.current_piece]['rotations']
        return rotations[self.current_rotation % len(rotations)]

    def _shape_extent(self, coords: List[Tuple[int, int]], axis: int) -> int:
        if axis == 0:
            return max((x for x, _ in coords), default=0) + 1
        return max((y for _, y in coords), default=0) + 1

    def _active_piece_color(self, piece: str) -> Color:
        base = TETROMINOS[piece]['color']
        return tuple(min(255, int(c * 1.15) + 10) for c in base)

    def _set_pixel(self, frame: List[Color], strip: int, led: int, color: Color):
        if strip < 0 or strip >= self.num_strips:
            return
        if led < 0 or led >= self.leds_per_strip:
            return
        phys_led = (self.leds_per_strip - 1) - led
        idx = strip * self.leds_per_strip + phys_led
        if 0 <= idx < len(frame):
            frame[idx] = self.apply_brightness(color)

    def get_runtime_stats(self) -> Dict[str, Any]:
        return {
            'lines_cleared': self.lines_cleared,
            'bot_fail_rate': self.fail_rate,
        }
