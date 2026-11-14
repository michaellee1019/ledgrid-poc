#!/usr/bin/env python3
"""Tetris animation that plays itself on the LED grid."""

import random
import threading
from dataclasses import dataclass
from math import sqrt
from time import monotonic
from types import MappingProxyType
from typing import List, Tuple, Dict, Any, Mapping, Optional, Sequence

from animation import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


Color = Tuple[int, int, int]
BoardCell = Optional[Color]
MAX_TETROMINO_COUNT = 128
MAX_SPAWNS_PER_UPDATE = 8
MAX_COORDINATED_PLANS = 3
MAX_SIMULATION_DELTA = 0.05
PLAYER_COLUMNS = 10
PLAYER_ROWS = 23
PLAYER_ROUND_SECONDS = 15.0
PLAYER_MAX_SECONDS = 40.0
PLAYER_LINE_BONUS_SECONDS = 5.0
PLAYER_RESTART_SECONDS = 0.75
PLAYER_INITIAL_FALL_SECONDS = 1.70
PLAYER_FASTEST_FALL_SECONDS = 1.10
PLAYER_RAMP_SECONDS = 30.0
META_SCALE = 2
INNER_FALL_SECONDS = 0.55


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
PIECE_KIND_BY_COLOR = {color: kind for kind, color in PIECE_COLORS.items()}

SEMANTIC_PALETTES: Mapping[str, Mapping[str, Color]] = MappingProxyType({
    "neutral": MappingProxyType({
        "I": (94, 235, 226), "J": (91, 144, 255), "L": (255, 173, 73),
        "O": (255, 238, 101), "S": (96, 238, 158), "T": (218, 104, 236),
        "Z": (255, 111, 98), "flash": (255, 164, 138),
    }),
    "mist": MappingProxyType({
        "I": (120, 238, 245), "J": (105, 162, 245), "L": (245, 183, 111),
        "O": (255, 232, 132), "S": (115, 229, 184), "T": (190, 139, 245),
        "Z": (245, 126, 124), "flash": (245, 172, 142),
    }),
    "spectrum": MappingProxyType({
        "I": (71, 233, 230), "J": (99, 100, 255), "L": (255, 153, 77),
        "O": (246, 238, 91), "S": (95, 244, 139), "T": (223, 95, 242),
        "Z": (255, 95, 134), "flash": (255, 157, 104),
    }),
    "ember": MappingProxyType({
        "I": (255, 196, 91), "J": (255, 133, 70), "L": (255, 167, 82),
        "O": (255, 225, 108), "S": (242, 180, 84), "T": (255, 111, 91),
        "Z": (255, 89, 72), "flash": (255, 148, 92),
    }),
    "rainbow": MappingProxyType({
        "I": (0, 255, 255), "J": (40, 80, 255), "L": (255, 140, 30),
        "O": (255, 230, 40), "S": (40, 255, 80), "T": (220, 40, 255),
        "Z": (255, 40, 60), "flash": (255, 180, 220),
    }),
})


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


# Each template fills the 2x-scaled silhouette with three whole tetrominoes.
# The four unused cells inside the silhouette stay empty.
META_TEMPLATES = {
    "I": ("J.IIII.L", "JJJ..LLL"),
    "J": ("OO....", "OO....", "...JJJ", "IIII.J"),
    "L": ("....OO", "....OO", "LLL...", "L.IIII"),
    "O": ("S.LL", "SS.L", "JS.L", "JJJ."),
    "S": ("...JJJ", "..T..J", ".TTT..", "IIII.."),
    "T": ("..LL..", "...L..", "J..LOO", "JJJ.OO"),
    "Z": ("IIII..", ".TTT..", "..T..L", "...LLL"),
}


@dataclass(frozen=True)
class InnerPlacement:
    kind: str
    rotation: int
    x: int
    y: int


@dataclass(frozen=True)
class MetaRotation:
    cells: tuple[tuple[int, int], ...]
    contents: tuple[InnerPlacement, ...]


def _build_meta_rotations(rows: tuple[str, ...]) -> tuple[MetaRotation, ...]:
    rotations = []
    for _ in range(4):
        pieces = {}
        for y, row in enumerate(rows):
            for x, kind in enumerate(row):
                if kind != ".":
                    pieces.setdefault(kind, []).append((x, y))

        contents = []
        for kind, cells in pieces.items():
            normalized = set(_normalize(cells))
            rotation = next(
                index for index, shape in enumerate(TETROMINOS[kind]["rotations"])
                if set(shape) == normalized
            )
            contents.append(InnerPlacement(
                kind, rotation, min(x for x, _ in cells), min(y for _, y in cells),
            ))
        rotations.append(MetaRotation(
            tuple(cell for cells in pieces.values() for cell in cells), tuple(contents),
        ))
        rows = tuple("".join(column) for column in zip(*rows))[::-1]
    return tuple(rotations)


META_ROTATIONS = {kind: _build_meta_rotations(rows) for kind, rows in META_TEMPLATES.items()}


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
    planning_deferred: bool = False
    manual_override: float = 0.0
    meta: bool = False


class TetrisAnimation(AnimationBase):
    """Classic Tetris with a coordinated self-playing bot."""
    PREFERRED_PARAMETERS = ('fall_rate', 'tetromino_count', 'bot_imperfection', 'smooth_drop_strength')
    PARAMETER_CONTROLS = {
        'tetromino_count': {'label': 'bot pieces', 'autoplay_only': True},
        'bot_imperfection': {'label': 'bot errors', 'autoplay_only': True},
        'smooth_drop': {'autoplay_only': True},
        'smooth_drop_strength': {'autoplay_only': True},
        'smooth_drop_max_pieces': {'label': 'smooth piece limit', 'autoplay_only': True},
        'high_density_render_fps': {'label': 'dense render FPS'},
    }
    PLAYER_BUTTONS = {
        'left': ('Move left', True),
        'right': ('Move right', True),
        'up': ('Rotate left', False),
        'down': ('Soft drop', True),
        'a': ('Rotate right', False),
        'b': ('Drop', False),
        'y': ('Release inner pieces', False),
    }


    ANIMATION_NAME = "Tetris"
    ANIMATION_DESCRIPTION = "Autoplaying Tetris board with coordinated falling tetrominoes"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"

    COMPONENT_ID = "tetris"
    COMPONENT_VERSION = 1
    PROVIDER = "python"
    ROLE = "animation"
    FRAME_FORMAT = "rgb_uint8_strip_major"
    TIMING_POLICY = "scaled_context"
    PALETTE_POLICY = "semantic"
    PALETTE_ROLES = tuple(BASE_SHAPES) + ("flash",)
    CAPABILITIES = frozenset({"semantic_palette_roles", "scaled_context", "effect_intent"})
    PLANT_MODIFIER_SUPPORT = frozenset()
    COMPOSER_INTERACTIONS = {
        "directions": ("left", "right", "down", "rotate-left", "rotate-right", "drop"),
    }

    # The game owns only its deterministic simulation and local drawing
    # controls. Scene pace, final plant optics, and presentation brightness
    # are applied once by the Scene v2 runtime after this opaque RGB plane.
    DEFAULTS = MappingProxyType({
        "seed": 4201,
        "tetromino_count": 5,
        "bot_imperfection": 0.18,
        "fall_rate": 3.0,
        "smooth_drop": True,
        "smooth_drop_strength": 0.6,
        "smooth_drop_max_pieces": 32,
        "render_fps": 150.0,
        "high_density_render_fps": 150.0,
    })
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        provider=PROVIDER,
        role=ROLE,
        timing_policy=TIMING_POLICY,
        alpha_behavior="opaque",
        palette_policy=PALETTE_POLICY,
        plant_capabilities=("effect_intent",),
        fidelity_exceptions=(),
        defaults=DEFAULTS,
    )

    def __init__(self, controller, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)

        self.num_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.board_width = self.num_strips
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
        self.player_control = False
        self.player_paused = False
        self.player_round_elapsed = 0.0
        self.player_round_deadline = PLAYER_ROUND_SECONDS
        self.player_restart_in = 0.0
        self.player_fall_progress = 0.0
        self.plans_dirty = False
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self._presentation_context: Optional[ResolvedScene] = None
        self._palette_id = "neutral"
        self.random.seed(self.params["seed"])

        self.base_drop_speed = max(12.0, self.board_height / 4.0)
        self._refresh_runtime_params()

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        """Return the provider-qualified opaque Scene v2 declaration."""

        return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def palette_roles(cls, palette_id: str) -> Mapping[str, Color]:
        return SEMANTIC_PALETTES.get(str(palette_id), SEMANTIC_PALETTES["neutral"])

    def _refresh_runtime_params(self):
        fall_rate = float(self.params['fall_rate'])
        self.drop_speed = self.base_drop_speed * fall_rate
        self.action_interval = max(0.0125, 0.1 / fall_rate)
        self.fail_rate = min(0.6, max(0.0, float(self.params.get('bot_imperfection', 0.18))))
        self.tetromino_count = max(
            1,
            min(MAX_TETROMINO_COUNT, int(self.params.get('tetromino_count', 5))),
        )

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, Any]:
        return {
            'seed': {
                'type': 'int', 'min': 0, 'max': 999999, 'default': 4201,
                'description': 'Deterministic tetromino sequence seed',
            },
            'tetromino_count': {
                'type': 'int',
                'min': 1,
                'max': MAX_TETROMINO_COUNT,
                'default': 5,
                'description': 'Number of coordinated falling tetrominoes'
            },
            'bot_imperfection': {
                'type': 'float',
                'min': 0.0,
                'max': 0.6,
                'default': 0.18,
                'description': 'Chance for the bot to pick a risky move'
            },
            'fall_rate': {
                'type': 'float',
                'min': 0.2,
                'max': 5.0,
                'default': 3.0,
                'description': 'Component-local falling cadence multiplier'
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
            },
        }

    LIVE_TUNABLE_PARAMETERS = frozenset(DEFAULTS) - {"seed"}

    def update_parameters(self, params: Mapping[str, Any]):
        normalized = self._normalized_parameters({**self.params, **dict(params)})
        if normalized == self.params:
            return
        self.params = normalized
        self._refresh_runtime_params()
        if not self.player_control and len(self.active_pieces) > self.tetromino_count:
            del self.active_pieces[self.tetromino_count:]
        self.last_render_elapsed = None
        self.next_render_elapsed = None

    def on_presentation_context_changed(
        self, old: Optional[ResolvedScene], new: ResolvedScene,
    ) -> None:
        del old
        self._validate_context(new)
        self._presentation_context = new
        palette_id = self._context_palette_id(new)
        if palette_id != self._palette_id:
            # Palette edits are presentation-only: repaint immediately without
            # moving pieces, consuming RNG, or waiting for the prior cadence.
            self._palette_id = palette_id
            self.next_render_elapsed = None

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> Any:
        self.set_presentation_context(context)
        self.update_parameters(context.parameters)
        elapsed = context.phase_time
        if self.player_control:
            elapsed = (self.last_elapsed or 0.0) if self.player_paused else self._player_time()
        return self.generate_frame(elapsed, self.frame_count)

    @staticmethod
    def _player_time() -> float:
        """The sprint's countdown runs on the host clock across Looks."""
        return monotonic()

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> Dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(f"Tetris does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS)
        result.update(values)
        seed = result["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 999999:
            raise ValueError("seed must be an integer from 0 to 999999")
        for name, minimum, maximum in (
            ("bot_imperfection", 0.0, 0.6),
            ("fall_rate", 0.2, 5.0),
            ("smooth_drop_strength", 0.0, 1.0),
            ("render_fps", 15.0, 200.0),
            ("high_density_render_fps", 15.0, 200.0),
        ):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= float(value) <= maximum:
                raise ValueError(f"{name} must be a number from {minimum} to {maximum}")
            result[name] = float(value)
        for name, minimum, maximum in (
            ("tetromino_count", 1, MAX_TETROMINO_COUNT),
            ("smooth_drop_max_pieces", 0, MAX_TETROMINO_COUNT),
        ):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
        if not isinstance(result["smooth_drop"], bool):
            raise ValueError("smooth_drop must be boolean")
        return result

    def _validate_context(self, context: ResolvedScene) -> None:
        descriptor = context.descriptor
        if (
            descriptor.component_id, descriptor.version, descriptor.provider.value,
            descriptor.role.value,
        ) != (self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE):
            raise ValueError("Tetris received a context for another component")
        if context.palette is None:
            raise ValueError("Tetris requires a semantic palette context")

    @staticmethod
    def _context_palette_id(context: ResolvedScene) -> str:
        assert context.palette is not None
        return str(context.palette["palette_id"])

    def generate_frame(self, time_elapsed: float, frame_count: int) -> Any:
        time_rewound = (
            self.last_elapsed is not None and time_elapsed < self.last_elapsed
        )
        if time_rewound:
            # A new Scene pace can rewind scaled time. Repaint the requested
            # point now, while the zero delta below preserves game/RNG state.
            self.last_render_elapsed = None
            self.next_render_elapsed = None
        render_fps = self._effective_render_fps()
        if (
            not time_rewound
            and self.last_rendered_frame is not None
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

        if not self.player_paused:
            self._update_game(delta)

        if self.player_control:
            self._render_player_board(frame)
            self.last_rendered_frame = frame
            return self.rendered_frame(frame, changed=True)

        if self.game_over_flash > 0.0:
            flash_strength = int(80 * min(1.0, self.game_over_flash))
            if flash_strength > 0:
                flash = self.palette_roles(self._palette_id)["flash"]
                tint = tuple(int(channel * flash_strength / 80) for channel in flash)
                for strip in range(self.num_strips):
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
                    self._set_pixel(frame, x, y, color)

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
                                self._set_pixel_blend(frame, px, trail_row, active_color, alpha)
                    if not smooth_drop or fall_offset <= 0.0:
                        if 0 <= base_row < self.board_height:
                            self._set_pixel(frame, px, base_row, active_color)
                    else:
                        if 0 <= base_row < self.board_height:
                            self._set_pixel_blend(frame, px, base_row, active_color, 1.0 - fall_offset)
                        if 0 <= next_row < self.board_height:
                            self._set_pixel_blend(frame, px, next_row, active_color, fall_offset)

        self.last_rendered_frame = frame
        return self.rendered_frame(frame, changed=True)

    def handle_input(self, action: str):
        """Handle player input from the D-pad."""
        action = (action or '').lower().replace('_', '-')
        if action == 'up':
            action = 'rotate-right'
        valid = {'left', 'right', 'down', 'rotate-left', 'rotate-right', 'drop'}
        if action not in valid and not (self.player_control and action == 'release'):
            return
        with self.input_lock:
            self.input_queue.append(action)
        self.last_render_elapsed = None
        self.next_render_elapsed = None

    def set_player_control(self, enabled: bool) -> None:
        """Start a fresh player or autoplay board without changing the Look."""
        if enabled == self.player_control:
            return
        self.player_control = enabled
        self.player_paused = False
        self.board_width = self.num_strips
        self.board_height = self.leds_per_strip
        if enabled:
            self.board_width = min(PLAYER_COLUMNS * META_SCALE, self.num_strips)
            self.board_height = min(PLAYER_ROWS * META_SCALE, self.board_height)
        self._reset_player_clock()
        self._reset_board()
        self.random.seed(self.params['seed'])
        self.lines_cleared = 0
        self.game_over_flash = 0.0
        self.input_piece_index = 0
        with self.input_lock:
            self.input_queue.clear()
        self.last_elapsed = None
        self.last_render_elapsed = None
        self.next_render_elapsed = None

    def set_player_paused(self, paused: bool) -> None:
        self.player_paused = paused
        with self.input_lock:
            self.input_queue.clear()
        if not paused:
            self.last_elapsed = None
            self.last_render_elapsed = None
            self.next_render_elapsed = None

    def handle_player_button(self, button: str) -> None:
        action = {"up": "rotate-left", "a": "rotate-right", "b": "drop", "y": "release"}.get(button, button)
        self.handle_gamepad_action(action)

    def handle_gamepad_action(self, action: str) -> None:
        if action == "toggle-player":
            self.set_player_control(not self.player_control)
        elif action == "disconnect":
            self.set_player_control(False)
        elif self.player_control and not self.player_paused and not self.player_restart_in:
            self.handle_input(action)

    def _reset_player_clock(self) -> None:
        self.player_round_elapsed = 0.0
        self.player_round_deadline = PLAYER_ROUND_SECONDS
        self.player_restart_in = 0.0
        self.player_fall_progress = 0.0

    def _advance_player_clock(self, delta: float) -> bool:
        if self.player_restart_in:
            self.player_restart_in = max(0.0, self.player_restart_in - delta)
            if self.player_restart_in:
                return False
            self._reset_player_clock()
            self._reset_board()
            self.lines_cleared = 0
            return True

        self.player_round_elapsed = min(self.player_round_deadline, self.player_round_elapsed + delta)
        if self.player_round_elapsed >= self.player_round_deadline:
            self._handle_game_over()
            return False
        return True

    def _render_player_board(self, frame: Any) -> None:
        pixels = frame.reshape(self.num_strips, self.leds_per_strip, 3)
        has_countdown = self.leds_per_strip > self.board_height
        arena_height = self.leds_per_strip - int(has_countdown)

        def draw(x: int, y: int, color: Color) -> None:
            # Fractional cell edges spread rounding across the wall, including
            # its outer strips. Logical rows run from top to bottom.
            left = x * self.num_strips // self.board_width
            right = (x + 1) * self.num_strips // self.board_width
            top = arena_height - y * arena_height // self.board_height
            bottom = arena_height - (y + 1) * arena_height // self.board_height
            pixels[left:right, bottom:top] = color

        for y, row in enumerate(self.board):
            for x, color in enumerate(row):
                if color is not None:
                    draw(x, y, self._board_color(color))
        for piece in self.active_pieces:
            if piece.meta:
                tint = self.palette_roles(self._palette_id)[piece.kind]
                for inner in META_ROTATIONS[piece.kind][piece.rotation].contents:
                    color = tuple(
                        (3 * channel + shared) // 4
                        for channel, shared in zip(self._active_piece_color(inner.kind), tint)
                    )
                    for cx, cy in TETROMINOS[inner.kind]["rotations"][inner.rotation]:
                        draw(piece.x + inner.x + cx, piece.y + inner.y + cy, color)
            else:
                color = self._active_piece_color(piece.kind)
                for cx, cy in self._piece_shape(piece):
                    draw(piece.x + cx, piece.y + cy, color)

        if has_countdown:
            palette = self.palette_roles(self._palette_id)
            seconds_left = self.player_round_deadline - self.player_round_elapsed
            if self.player_restart_in:
                pixels[:, -1] = palette["Z"]
            else:
                length = max(1, round(self.num_strips * min(1.0, seconds_left / PLAYER_ROUND_SECONDS)))
                pixels[:length, -1] = palette["L" if seconds_left <= 4.0 else "I"]

    def _update_game(self, delta: float):
        if self.board_width <= 0 or self.board_height <= 0:
            return
        if self.player_control:
            self._update_player_game(delta)
            return

        if self.game_over_flash > 0.0:
            self.game_over_flash = max(0.0, self.game_over_flash - delta)

        spawn_budget = MAX_SPAWNS_PER_UPDATE
        spawn_budget -= self._replenish_active_pieces(spawn_budget)
        if self.plans_dirty:
            self._coordinate_plans()
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

    def _update_player_game(self, delta: float) -> None:
        if not self._advance_player_clock(delta):
            return
        if not self.active_pieces and not self._spawn_piece():
            return
        self._apply_pending_inputs()

        speed = self._effective_drop_speed() if self.active_pieces[0].meta else self.board_height / INNER_FALL_SECONDS
        self.player_fall_progress += delta * speed
        while self.active_pieces and self.player_fall_progress >= 1.0 and not self.player_restart_in:
            self.player_fall_progress -= 1.0
            piece = self.active_pieces[0]
            if piece.meta:
                if not self._move_piece(piece, 0, 1):
                    self._release_meta(piece)
            else:
                self._step_inner_pieces()
        if not self.active_pieces and not self.player_restart_in:
            self._spawn_piece()

    def _release_meta(self, piece: ActivePiece) -> None:
        self.active_pieces[:] = [
            ActivePiece(inner.kind, rotation=inner.rotation, x=piece.x + inner.x, y=piece.y + inner.y)
            for inner in META_ROTATIONS[piece.kind][piece.rotation].contents
        ]
        self.player_fall_progress = 0.0

    def _step_inner_pieces(self) -> None:
        # Lock anything grounded, then test again against the new stack. All
        # bodies still in flight can descend together without intersecting.
        for _ in range(len(self.active_pieces)):
            grounded = [
                piece for piece in self.active_pieces
                if self._collides(self.board, self._piece_shape(piece), piece.x, piece.y + 1)
            ]
            if not grounded:
                break
            lowest = max(
                grounded, key=lambda piece: piece.y + self._shape_extent(self._piece_shape(piece), 1),
            )
            self._lock_piece(lowest)
            if self.player_restart_in:
                return
        for piece in self.active_pieces:
            piece.y += 1

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
        if not self.player_control and self.tetromino_count > smooth_piece_limit:
            high_density_fps = max(
                15.0,
                min(200.0, float(self.params.get('high_density_render_fps', 150.0))),
            )
            render_fps = min(render_fps, high_density_fps)
        return render_fps

    def _effective_drop_speed(self) -> float:
        if self.player_control:
            progress = min(1.0, self.player_round_elapsed / PLAYER_RAMP_SECONDS)
            fall_seconds = PLAYER_INITIAL_FALL_SECONDS - progress * (
                PLAYER_INITIAL_FALL_SECONDS - PLAYER_FASTEST_FALL_SECONDS
            )
            return self.board_height / fall_seconds * (float(self.params["fall_rate"]) / self.DEFAULTS["fall_rate"])
        # Preserve the original pace at normal density, then reduce per-piece
        # work so total movement does not grow linearly into the hundreds.
        density_scale = max(1.0, sqrt(self.tetromino_count / 8.0))
        return self.drop_speed / density_scale

    def _spawn_piece(self) -> bool:
        piece = ActivePiece(kind=self.random.choice(list(TETROMINOS.keys())))
        if self.player_control:
            piece.meta = True
            coords = self._piece_shape(piece)
            piece.x = (self.board_width - self._shape_extent(coords, axis=0)) // 2
            piece.y = 0
            if self._collides(self.board, coords, piece.x, piece.y):
                self._handle_game_over()
                return False
            self.active_pieces.append(piece)
            self.player_fall_progress = 0.0
            return True

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
        # Leave at least one visible fall step when space allows. Besides
        # looking better, this gives the coordinated steering loop one tick to
        # execute before a newly spawned piece can lock.
        piece.y = self.random.randint(piece.y, max(piece.y, landing_y - 1))
        piece.fall_progress = self.random.random()
        piece.action_accumulator = self.random.random() * self.action_interval
        self.active_pieces.append(piece)
        self.plans_dirty = True
        return True

    def _reset_board(self):
        self.board = [[None for _ in range(self.board_width)] for _ in range(self.board_height)]
        self.active_pieces.clear()
        self.plans_dirty = False

    def _handle_game_over(self):
        if self.player_control:
            self.player_restart_in = PLAYER_RESTART_SECONDS
            with self.input_lock:
                self.input_queue.clear()
            return
        self._reset_board()
        self.game_over_flash = 1.0

    def _run_player_step(self, piece: ActivePiece):
        if not piece.plan or piece.plan.get('piece') != piece.kind:
            if piece.planning_deferred:
                return
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
            self._move_toward_x(piece, target_x)
        elif piece.x > target_x:
            self._move_toward_x(piece, target_x)
        else:
            if self.random.random() < 0.35:
                self._move_piece(piece, 0, 1)

    def _move_toward_x(self, piece: ActivePiece, target_x: int):
        """Steer far enough per bot tick to traverse unusually wide LED grids."""
        direction = 1 if target_x > piece.x else -1
        max_steps = max(1, (self.board_width + 9) // 10)
        for _ in range(min(max_steps, abs(target_x - piece.x))):
            if not self._move_piece(piece, direction, 0):
                break

    def _plan_move(self, piece: ActivePiece):
        """Refresh the shared plan, retaining this method for bot-step callers."""
        self.plans_dirty = True
        self._coordinate_plans()

    def _coordinate_plans(self):
        """Reserve good landings for every active piece on one projected board.

        Bottom-most pieces are expected to lock first. Each selected placement is
        applied to a virtual board (including line clears) before the next piece
        is evaluated, so concurrent pieces complement rather than duplicate one
        another's moves.
        """
        projected_board = [row[:] for row in self.board]
        ordered_pieces = sorted(
            self.active_pieces,
            key=lambda active: active.y + self._shape_extent(
                self._piece_shape(active), axis=1
            ),
            reverse=True,
        )

        # A short look-ahead is enough to coordinate the pieces that can lock
        # next. Bounding it keeps planning cost constant even at the supported
        # 128-piece visual density; distant pieces simply fall until they enter
        # the horizon.
        for piece in ordered_pieces:
            piece.plan = None
            piece.planning_deferred = True
        ordered_pieces = ordered_pieces[:MAX_COORDINATED_PLANS]

        for piece in ordered_pieces:
            piece.planning_deferred = False
            placement = self._best_placement(piece, projected_board)
            if placement is None:
                piece.plan = None
                continue
            rotation, target_x, landing_y, score = placement
            piece.plan = {
                'piece': piece.kind,
                'rotation': rotation,
                'x': target_x,
                'landing_y': landing_y,
                'score': score,
            }
            projected_board, _ = self._project_placement(
                projected_board,
                TETROMINOS[piece.kind]['rotations'][rotation],
                target_x,
                landing_y,
                TETROMINOS[piece.kind]['color'],
            )
        self.plans_dirty = False

    def _best_placement(
        self,
        piece: ActivePiece,
        board: List[List[BoardCell]],
    ) -> Optional[Tuple[int, int, int, float]]:
        candidates: List[Tuple[int, int, int, float]] = []
        rotations = TETROMINOS[piece.kind]['rotations']
        heights, column_holes, row_fill = self._board_metrics(board)
        board_score_inputs = (
            sum(column_holes),
            sum(heights),
            max(heights, default=0),
            sum(abs(left - right) for left, right in zip(heights, heights[1:])),
        )
        for rotation, coords in enumerate(rotations):
            width = self._shape_extent(coords, axis=0)
            for target_x in range(max(0, self.board_width - width) + 1):
                landing_y = self._find_drop_y_from_heights(
                    coords, target_x, heights
                )
                if landing_y is None or landing_y < piece.y:
                    continue
                score = self._score_placement(
                    coords,
                    target_x,
                    landing_y,
                    heights,
                    row_fill,
                    board_score_inputs,
                )
                candidates.append((rotation, target_x, landing_y, score))

        if not candidates:
            return None
        candidates.sort(key=lambda candidate: candidate[3], reverse=True)
        if self.random.random() < self.fail_rate:
            # Imperfect play still makes a plausible move instead of abandoning
            # all board awareness.
            return self.random.choice(candidates[:min(6, len(candidates))])
        return candidates[0]

    def _find_drop_y_from_heights(
        self,
        coords: List[Tuple[int, int]],
        x_offset: int,
        heights: List[int],
    ) -> Optional[int]:
        """Find a landing in O(4) from each column's top occupied cell."""
        landing_y = min(
            self.board_height - heights[x_offset + cx] - cy - 1
            for cx, cy in coords
        )
        return None if landing_y < -4 else landing_y

    def _project_placement(
        self,
        board: List[List[BoardCell]],
        coords: List[Tuple[int, int]],
        x_offset: int,
        y_offset: int,
        color: Color,
    ) -> Tuple[List[List[BoardCell]], int]:
        projected = [row[:] for row in board]
        for cx, cy in coords:
            px = x_offset + cx
            py = y_offset + cy
            if 0 <= px < self.board_width and 0 <= py < self.board_height:
                projected[py][px] = color
        return self._clear_lines_from(projected)

    def _clear_lines_from(
        self,
        board: List[List[BoardCell]],
    ) -> Tuple[List[List[BoardCell]], int]:
        remaining = [row for row in board if not all(row)]
        cleared = self.board_height - len(remaining)
        empty_rows = [
            [None for _ in range(self.board_width)]
            for _ in range(cleared)
        ]
        return empty_rows + remaining, cleared

    def _board_metrics(
        self,
        board: List[List[BoardCell]],
    ) -> Tuple[List[int], List[int], List[int]]:
        heights = [0] * self.board_width
        occupied = [0] * self.board_width
        row_fill = [len(row) - row.count(None) for row in board]
        for y, (row, filled) in enumerate(zip(board, row_fill)):
            if filled:
                for x, cell in enumerate(row):
                    if cell is not None:
                        if heights[x] == 0:
                            heights[x] = self.board_height - y
                        occupied[x] += 1
        column_holes = [height - count for height, count in zip(heights, occupied)]
        return heights, column_holes, row_fill

    def _score_placement(
        self,
        coords: List[Tuple[int, int]],
        x_offset: int,
        y_offset: int,
        heights: List[int],
        row_fill: List[int],
        board_score_inputs: tuple[int, int, int, int],
    ) -> float:
        projected_holes, aggregate_height, max_height, bumpiness = board_score_inputs
        # Drop candidates are entirely above the old column surfaces. Only
        # touched columns and their adjacent edges affect the projected score.
        projected_heights: Dict[int, int] = {}
        cells_by_column: Dict[int, int] = {}
        cells_by_row: Dict[int, int] = {}
        for cx, cy in coords:
            px = x_offset + cx
            py = y_offset + cy
            if py >= 0:
                height = self.board_height - py
                projected_heights[px] = max(height, projected_heights.get(px, 0))
                cells_by_column[px] = cells_by_column.get(px, 0) + 1
                cells_by_row[py] = cells_by_row.get(py, 0) + 1

        for x, height in projected_heights.items():
            rise = height - heights[x]
            aggregate_height += rise
            projected_holes += rise - cells_by_column[x]
            max_height = max(max_height, height)
        if projected_heights:
            first_edge = max(0, min(projected_heights) - 1)
            last_edge = min(self.board_width - 1, max(projected_heights) + 1)
            for x in range(first_edge, last_edge):
                left, right = heights[x], heights[x + 1]
                bumpiness += abs(projected_heights.get(x, left) - projected_heights.get(x + 1, right))
                bumpiness -= abs(left - right)

        cleared = sum(
            row_fill[y] + added == self.board_width
            for y, added in cells_by_row.items()
        )

        return (
            cleared * 12.0
            - projected_holes * 8.0
            - aggregate_height * 0.45
            - bumpiness * 0.35
            - max_height * 0.8
        )

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
            if self.player_control and not piece.meta:
                return
            piece.manual_override = max(piece.manual_override, 2.0)
            piece.plan = None
            piece.planning_deferred = False
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
            elif action == 'release':
                self._release_meta(piece)
            elif action == 'drop':
                while self._move_piece(piece, 0, 1):
                    pass
                self._lock_piece(piece)
                if self.active_pieces:
                    self.input_piece_index %= len(self.active_pieces)

    def _find_drop_y(
        self,
        coords: List[Tuple[int, int]],
        x_offset: int,
        board: Optional[List[List[BoardCell]]] = None,
    ) -> Optional[int]:
        target_board = self.board if board is None else board
        y = -4
        while True:
            if self._collides(target_board, coords, x_offset, y + 1):
                if self._collides(target_board, coords, x_offset, y):
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
        rotations = META_ROTATIONS[piece.kind] if piece.meta else TETROMINOS[piece.kind]['rotations']
        new_rotation = (piece.rotation + direction) % len(rotations)
        coords = rotations[new_rotation].cells if piece.meta else rotations[new_rotation]
        if self._collides(self.board, coords, piece.x, piece.y):
            return False
        piece.rotation = new_rotation
        return True

    def _lock_piece(self, piece: ActivePiece):
        if piece not in self.active_pieces:
            return
        if piece.meta:
            self._release_meta(piece)
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
        self.active_pieces.remove(piece)
        cleared = self._clear_lines()
        if cleared:
            self.lines_cleared += cleared
            if self.player_control:
                self.player_round_deadline = min(
                    PLAYER_MAX_SECONDS, self.player_round_deadline + cleared * PLAYER_LINE_BONUS_SECONDS,
                )
        self.plans_dirty = not self.player_control

    def _clear_lines(self) -> int:
        full_rows = [y for y, row in enumerate(self.board) if all(row)] if self.player_control else ()
        self.board, cleared = self._clear_lines_from(self.board)
        if full_rows:
            for piece in self.active_pieces:
                bottom = piece.y + self._shape_extent(self._piece_shape(piece), 1) - 1
                piece.y += sum(y > bottom for y in full_rows)
        return cleared

    def _collides(self, board: List[List[BoardCell]], coords: Sequence[Tuple[int, int]], x_offset: int, y_offset: int) -> bool:
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

    def _piece_shape(self, piece: ActivePiece) -> Sequence[Tuple[int, int]]:
        if piece.meta:
            return META_ROTATIONS[piece.kind][piece.rotation].cells
        rotations = TETROMINOS[piece.kind]['rotations']
        return rotations[piece.rotation % len(rotations)]

    def _shape_extent(self, coords: Sequence[Tuple[int, int]], axis: int) -> int:
        if axis == 0:
            return max((x for x, _ in coords), default=0) + 1
        return max((y for _, y in coords), default=0) + 1

    def _active_piece_color(self, piece: str) -> Color:
        base = self.palette_roles(self._palette_id)[piece]
        return tuple(min(255, int(c * 1.15) + 10) for c in base)

    def _board_color(self, color: Color) -> Color:
        """Resolve stored piece identity into the current semantic Look palette."""

        kind = PIECE_KIND_BY_COLOR.get(color)
        return self.palette_roles(self._palette_id)[kind] if kind is not None else color

    def _set_pixel(self, frame: List[Color], strip: int, led: int, color: Color):
        if strip < 0 or strip >= self.num_strips:
            return
        if led < 0 or led >= self.leds_per_strip:
            return
        phys_led = (self.leds_per_strip - 1) - led
        idx = strip * self.leds_per_strip + phys_led
        if 0 <= idx < len(frame):
            frame[idx] = self._board_color(color)

    def _set_pixel_blend(self, frame: List[Color], strip: int, led: int, color: Color, alpha: float):
        if alpha <= 0.0:
            return
        strength = float(self.params.get('smooth_drop_strength', 0.6))
        strength = max(0.0, min(1.0, strength))
        alpha = min(1.0, alpha) * strength
        if alpha <= 0.0:
            return
        scaled = (
            int(color[0] * alpha),
            int(color[1] * alpha),
            int(color[2] * alpha),
        )
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
        stats = {
            'lines_cleared': self.lines_cleared,
            'bot_fail_rate': self.fail_rate,
            'active_tetrominoes': len(self.active_pieces),
            'tetromino_count': self.tetromino_count,
            'effective_render_fps': self._effective_render_fps(),
            'effective_drop_speed': self._effective_drop_speed(),
            'max_spawns_per_update': MAX_SPAWNS_PER_UPDATE,
            'max_coordinated_plans': MAX_COORDINATED_PLANS,
        }
        return stats
