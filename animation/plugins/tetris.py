#!/usr/bin/env python3
"""Tetris animation that plays itself on the LED grid."""

import random
import threading
from dataclasses import dataclass
from math import sqrt
from typing import List, Tuple, Dict, Any, Optional

from animation import AnimationBase
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


Color = Tuple[int, int, int]
BoardCell = Optional[Color]
MAX_TETROMINO_COUNT = 128
MAX_SPAWNS_PER_UPDATE = 8
MAX_SIMULATION_DELTA = 0.05


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


@dataclass(eq=False)
class ActivePiece:
    """All mutable state for one independently moving tetromino."""

    kind: str
    rotation: int = 0
    x: int = 0
    y: int = -3
    fall_progress: float = 0.0
    last_fall_rows: int = 0
    action_accumulator: float = 0.0
    plan: Optional[Dict[str, Any]] = None
    manual_override: float = 0.0


class TetrisAnimation(AnimationBase):
    """Classic Tetris with a simple self-playing bot."""

    ANIMATION_NAME = "Tetris"
    ANIMATION_DESCRIPTION = "Autoplaying Tetris board with independently falling tetrominoes"
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

        self.active_pieces: List[ActivePiece] = []
        self.last_elapsed: Optional[float] = None
        self.last_render_elapsed: Optional[float] = None
        self.next_render_elapsed: Optional[float] = None
        self.last_rendered_frame: Optional[Any] = None
        self.lines_cleared = 0
        self.random = random.Random()
        self.game_over_flash = 0.0
        self.input_queue: List[str] = []
        self.input_lock = threading.Lock()
        self.input_piece_index = 0

        self.default_params.update({
            'speed': 3.0,
            'tetromino_count': 5,
            'bot_imperfection': 0.18,
            'smooth_drop': True,
            'smooth_drop_strength': 0.6,
            'smooth_drop_max_pieces': 32,
            'render_fps': 150.0,
            'high_density_render_fps': 150.0,
        })
        self.params = {**self.default_params, **self.config}

        self.base_drop_speed = max(12.0, self.board_height / 4.0)
        self._refresh_runtime_params()

    def _refresh_runtime_params(self):
        speed = max(0.2, float(self.params.get('speed', 1.0)))
        self.drop_speed = self.base_drop_speed * speed
        self.action_interval = max(0.0125, 0.1 / speed)
        self.fail_rate = min(0.6, max(0.0, float(self.params.get('bot_imperfection', 0.18))))
        self.tetromino_count = max(
            1,
            min(MAX_TETROMINO_COUNT, int(self.params.get('tetromino_count', 5))),
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        schema = super().get_parameter_schema()
        schema.update({
            'tetromino_count': {
                'type': 'int',
                'min': 1,
                'max': MAX_TETROMINO_COUNT,
                'default': 5,
                'description': 'Number of independently falling tetrominoes'
            },
            'bot_imperfection': {
                'type': 'float',
                'min': 0.0,
                'max': 0.6,
                'default': 0.18,
                'description': 'Chance for the bot to pick a risky move'
            },
            'speed': {
                'type': 'float',
                'min': 0.2,
                'max': 5.0,
                'default': 2.0,
                'description': 'Playback speed multiplier'
            },
            'smooth_drop': {
                'type': 'bool',
                'default': True,
                'description': 'Blend falling pieces across rows for smoother motion'
            },
            'smooth_drop_strength': {
                'type': 'float',
                'min': 0.0,
                'max': 1.0,
                'default': 0.6,
                'description': 'Blend intensity for smooth falling pieces'
            },
            'smooth_drop_max_pieces': {
                'type': 'int',
                'min': 0,
                'max': MAX_TETROMINO_COUNT,
                'default': 32,
                'description': 'Disable costly sub-row blending above this piece count'
            },
            'render_fps': {
                'type': 'float',
                'min': 15.0,
                'max': 200.0,
                'default': 150.0,
                'description': 'Maximum Tetris simulation and render rate'
            },
            'high_density_render_fps': {
                'type': 'float',
                'min': 15.0,
                'max': 200.0,
                'default': 150.0,
                'description': 'Render rate used above the smooth-piece limit'
            }
        })
        return schema

    def update_parameters(self, params: Dict[str, Any]):
        super().update_parameters(params)
        self._refresh_runtime_params()
        if len(self.active_pieces) > self.tetromino_count:
            del self.active_pieces[self.tetromino_count:]
        self.last_render_elapsed = None
        self.next_render_elapsed = None

    def generate_frame(self, time_elapsed: float, frame_count: int) -> Any:
        render_fps = self._effective_render_fps()
        if (
            self.last_rendered_frame is not None
            and self.next_render_elapsed is not None
            and time_elapsed < self.next_render_elapsed
        ):
            return self.rendered_frame(self.last_rendered_frame, changed=False)

        render_interval = 1.0 / render_fps
        if self.next_render_elapsed is None or (
            self.last_render_elapsed is not None and time_elapsed < self.last_render_elapsed
        ):
            self.next_render_elapsed = time_elapsed + render_interval
        else:
            while self.next_render_elapsed <= time_elapsed:
                self.next_render_elapsed += render_interval
        self.last_render_elapsed = time_elapsed
        frame = self.next_frame_buffer(clear=True)

        delta = 0.0
        if self.last_elapsed is None:
            self.last_elapsed = time_elapsed
        else:
            delta = min(MAX_SIMULATION_DELTA, max(0.0, time_elapsed - self.last_elapsed))
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

        smooth_piece_limit = max(0, int(self.params.get('smooth_drop_max_pieces', 32)))
        smooth_drop = (
            bool(self.params.get('smooth_drop', True))
            and len(self.active_pieces) <= smooth_piece_limit
        )
        active_colors = {
            kind: self._active_piece_color(kind)
            for kind in TETROMINOS
        }
        for piece in self.active_pieces:
            active_color = active_colors[piece.kind]
            coords = self._piece_shape(piece)
            fall_offset = max(0.0, min(1.0, piece.fall_progress))
            for cx, cy in coords:
                px = piece.x + cx
                py = piece.y + cy
                base_row = py
                next_row = py + 1
                if 0 <= px < self.board_width:
                    if smooth_drop and piece.last_fall_rows > 1:
                        for step in range(1, piece.last_fall_rows):
                            trail_row = base_row - step
                            alpha = 1.0 - (step / piece.last_fall_rows)
                            if 0 <= trail_row < self.board_height:
                                self._set_pixel_blend(frame, px + 1, trail_row, active_color, alpha)
                    if not smooth_drop or fall_offset <= 0.0:
                        if 0 <= base_row < self.board_height:
                            self._set_pixel(frame, px + 1, base_row, active_color)
                    else:
                        if 0 <= base_row < self.board_height:
                            self._set_pixel_blend(frame, px + 1, base_row, active_color, 1.0 - fall_offset)
                        if 0 <= next_row < self.board_height:
                            self._set_pixel_blend(frame, px + 1, next_row, active_color, fall_offset)

        self.last_rendered_frame = frame
        return self.rendered_frame(frame, changed=True)

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
        self.last_render_elapsed = None
        self.next_render_elapsed = None

    def _update_game(self, delta: float):
        if self.board_width <= 0 or self.board_height <= 0:
            return

        if self.game_over_flash > 0.0:
            self.game_over_flash = max(0.0, self.game_over_flash - delta)

        spawn_budget = MAX_SPAWNS_PER_UPDATE
        spawn_budget -= self._replenish_active_pieces(spawn_budget)
        for piece in self.active_pieces:
            piece.last_fall_rows = 0

        self._apply_pending_inputs()

        for piece in list(self.active_pieces):
            if piece.manual_override > 0.0:
                piece.manual_override = max(0.0, piece.manual_override - delta)
            else:
                piece.action_accumulator += delta
                while piece.action_accumulator >= self.action_interval:
                    self._run_player_step(piece)
                    piece.action_accumulator -= self.action_interval

        effective_drop_speed = self._effective_drop_speed()
        for piece in list(self.active_pieces):
            if not self.active_pieces:
                break
            piece.fall_progress += delta * effective_drop_speed
            while piece.fall_progress >= 1.0:
                if not self._move_piece(piece, 0, 1):
                    self._lock_piece(piece)
                    break
                piece.fall_progress -= 1.0
                piece.last_fall_rows += 1

        self._replenish_active_pieces(spawn_budget)

    def _replenish_active_pieces(self, spawn_budget: int = MAX_SPAWNS_PER_UPDATE) -> int:
        spawned = 0
        while len(self.active_pieces) < self.tetromino_count and spawned < spawn_budget:
            if not self._spawn_piece():
                break
            spawned += 1
        return spawned

    def _effective_render_fps(self) -> float:
        render_fps = max(15.0, min(200.0, float(self.params.get('render_fps', 150.0))))
        smooth_piece_limit = max(0, int(self.params.get('smooth_drop_max_pieces', 32)))
        if self.tetromino_count > smooth_piece_limit:
            high_density_fps = max(
                15.0,
                min(200.0, float(self.params.get('high_density_render_fps', 150.0))),
            )
            render_fps = min(render_fps, high_density_fps)
        return render_fps

    def _effective_drop_speed(self) -> float:
        # Preserve the original pace at normal density, then reduce per-piece
        # work so total movement does not grow linearly into the hundreds.
        density_scale = max(1.0, sqrt(self.tetromino_count / 8.0))
        return self.drop_speed / density_scale

    def _spawn_piece(self) -> bool:
        piece = ActivePiece(kind=self.random.choice(list(TETROMINOS.keys())))
        piece.rotation = self.random.randrange(len(TETROMINOS[piece.kind]['rotations']))
        coords = self._piece_shape(piece)
        width = self._shape_extent(coords, axis=0)
        piece.x = self.random.randint(0, max(0, self.board_width - width))
        landing_y = self._find_drop_y(coords, piece.x)
        if landing_y is None or landing_y < piece.y:
            self._handle_game_over()
            return False

        # Scatter a newly created batch across every valid point in its fall
        # path. Active pieces intentionally do not reserve space for each
        # other, so even large counts remain independent rather than forming
        # coordinated spawn lanes.
        piece.y = self.random.randint(piece.y, landing_y)
        piece.fall_progress = self.random.random()
        piece.action_accumulator = self.random.random() * self.action_interval
        self.active_pieces.append(piece)
        self._plan_move(piece)
        return True

    def _handle_game_over(self):
        self.board = [[None for _ in range(self.board_width)] for _ in range(self.board_height)]
        self.active_pieces.clear()
        self.game_over_flash = 1.0

    def _run_player_step(self, piece: ActivePiece):
        if not piece.plan or piece.plan.get('piece') != piece.kind:
            self._plan_move(piece)
        if not piece.plan:
            return

        target_rotation = piece.plan['rotation']
        rotation_count = len(TETROMINOS[piece.kind]['rotations'])
        if rotation_count > 1 and piece.rotation != target_rotation:
            diff = (target_rotation - piece.rotation) % rotation_count
            direction = 1 if diff <= rotation_count / 2 else -1
            rotated = self._rotate_piece(piece, direction)
            if rotated:
                return

        target_x = piece.plan['x']
        if piece.x < target_x:
            self._move_piece(piece, 1, 0)
        elif piece.x > target_x:
            self._move_piece(piece, -1, 0)
        else:
            if self.random.random() < 0.35:
                self._move_piece(piece, 0, 1)

    def _plan_move(self, piece: ActivePiece):
        rotations = TETROMINOS[piece.kind]['rotations']
        target_rotation = self.random.randrange(len(rotations))
        width = self._shape_extent(rotations[target_rotation], axis=0)
        max_x = max(0, self.board_width - width)
        piece.plan = {
            'piece': piece.kind,
            'rotation': target_rotation,
            'x': self.random.randint(0, max_x),
        }

    def _apply_pending_inputs(self):
        with self.input_lock:
            if not self.input_queue:
                return
            pending = list(self.input_queue)
            self.input_queue.clear()

        for action in pending:
            if not self.active_pieces:
                return
            piece = self.active_pieces[self.input_piece_index % len(self.active_pieces)]
            piece.manual_override = max(piece.manual_override, 2.0)
            if action == 'left':
                self._move_piece(piece, -1, 0)
            elif action == 'right':
                self._move_piece(piece, 1, 0)
            elif action == 'down':
                self._move_piece(piece, 0, 1)
            elif action == 'rotate-left':
                self._rotate_piece(piece, -1)
            elif action == 'rotate-right':
                self._rotate_piece(piece, 1)
            elif action == 'drop':
                while self._move_piece(piece, 0, 1):
                    pass
                self._lock_piece(piece)
                if self.active_pieces:
                    self.input_piece_index %= len(self.active_pieces)

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

    def _move_piece(self, piece: ActivePiece, dx: int, dy: int) -> bool:
        coords = self._piece_shape(piece)
        new_x = piece.x + dx
        new_y = piece.y + dy
        if self._collides(self.board, coords, new_x, new_y):
            return False
        piece.x = new_x
        piece.y = new_y
        return True

    def _rotate_piece(self, piece: ActivePiece, direction: int) -> bool:
        info = TETROMINOS[piece.kind]
        rotations = info['rotations']
        new_rotation = (piece.rotation + direction) % len(rotations)
        coords = rotations[new_rotation]
        if self._collides(self.board, coords, piece.x, piece.y):
            return False
        piece.rotation = new_rotation
        return True

    def _lock_piece(self, piece: ActivePiece):
        if piece not in self.active_pieces:
            return
        coords = self._piece_shape(piece)
        color = TETROMINOS[piece.kind]['color']
        overflow = False
        for cx, cy in coords:
            px = piece.x + cx
            py = piece.y + cy
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
        self.active_pieces.remove(piece)

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

    def _piece_shape(self, piece: ActivePiece) -> List[Tuple[int, int]]:
        rotations = TETROMINOS[piece.kind]['rotations']
        return rotations[piece.rotation % len(rotations)]

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

    def _set_pixel_blend(self, frame: List[Color], strip: int, led: int, color: Color, alpha: float):
        if alpha <= 0.0:
            return
        strength = float(self.params.get('smooth_drop_strength', 0.6))
        strength = max(0.0, min(1.0, strength))
        alpha = min(1.0, alpha) * strength
        if alpha <= 0.0:
            return
        scaled = self.apply_brightness((
            int(color[0] * alpha),
            int(color[1] * alpha),
            int(color[2] * alpha),
        ))
        if strip < 0 or strip >= self.num_strips:
            return
        if led < 0 or led >= self.leds_per_strip:
            return
        phys_led = (self.leds_per_strip - 1) - led
        idx = strip * self.leds_per_strip + phys_led
        if 0 <= idx < len(frame):
            base = frame[idx]
            frame[idx] = (
                min(255, int(base[0]) + scaled[0]),
                min(255, int(base[1]) + scaled[1]),
                min(255, int(base[2]) + scaled[2]),
            )

    def get_runtime_stats(self) -> Dict[str, Any]:
        return {
            'lines_cleared': self.lines_cleared,
            'bot_fail_rate': self.fail_rate,
            'active_tetrominoes': len(self.active_pieces),
            'tetromino_count': self.tetromino_count,
            'effective_render_fps': self._effective_render_fps(),
            'effective_drop_speed': self._effective_drop_speed(),
            'max_spawns_per_update': MAX_SPAWNS_PER_UPDATE,
        }
