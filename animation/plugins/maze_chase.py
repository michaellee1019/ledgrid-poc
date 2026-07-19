#!/usr/bin/env python3
"""Autoplaying, rules-driven maze chase for the portrait LED grid.

The art and maze are original, while the simulation follows the familiar
arcade rule set: pellets, energizers, score chains, lives, bonus fruit,
scatter/chase schedules, frightened enemies, eyes returning home, and
different deterministic target-tile personalities.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import numpy as np

from animation import AnimationBase


Cell = Tuple[int, int]
Color = Tuple[int, int, int]
Direction = Tuple[int, int]

UP: Direction = (-1, 0)
LEFT: Direction = (0, -1)
DOWN: Direction = (1, 0)
RIGHT: Direction = (0, 1)
DIRECTIONS = (UP, LEFT, DOWN, RIGHT)  # Arcade tie-breaking order.
OPPOSITE = {UP: DOWN, DOWN: UP, LEFT: RIGHT, RIGHT: LEFT}

MAZE = (
    "###############",
    "#o....#.#....o#",
    "#.##.#...#.##.#",
    "#.....#.#.....#",
    "###.#.#.#.#.###",
    "#...#.....#...#",
    "#.###.###.###.#",
    "#.....#.#.....#",
    "#.###.#.#.###.#",
    "#...#.....#...#",
    "###.#.###.#.###",
    "....#.....#....",
    "#.###.###.###.#",
    "#.....#.#.....#",
    "#.###.....###.#",
    "#...#.###.#...#",
    "###.#.....#.###",
    "#.....#.#.....#",
    "#.###.#.#.###.#",
    "#...#.....#...#",
    "###.#.###.#.###",
    "#.............#",
    "#.##.#...#.##.#",
    "#o....#.#....o#",
    "###############",
)
MAZE_HEIGHT = len(MAZE)
MAZE_WIDTH = len(MAZE[0])
TUNNEL_ROW = 11

# Compact display font. The game still works on 16 columns, where only the
# score is shown; labels appear when the physical width can fit them.
FONT = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("110", "001", "111", "100", "111"),
    "3": ("110", "001", "111", "001", "110"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "110", "001", "110"),
    "6": ("011", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "110"),
    "A": ("010", "101", "111", "101", "101"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"),
    "G": ("011", "100", "101", "101", "011"),
    "M": ("101", "111", "111", "101", "101"),
    "O": ("111", "101", "101", "101", "111"),
    "R": ("110", "101", "110", "101", "101"),
    "T": ("111", "010", "010", "010", "010"),
    "Y": ("101", "101", "010", "010", "010"),
    "!": ("010", "010", "010", "000", "010"),
    " ": ("000", "000", "000", "000", "000"),
}


@dataclass
class Actor:
    row: int
    col: int
    direction: Direction
    spawn: Cell
    color: Color
    personality: str = ""
    progress: float = 0.0
    state: str = "normal"  # normal, frightened, eyes

    @property
    def cell(self) -> Cell:
        return self.row, self.col


@dataclass
class ScorePopup:
    value: int
    row: float
    col: float
    age: float = 0.0
    life: float = 0.85


class MazeChaseAnimation(AnimationBase):
    """A complete, self-playing maze-chase match with arcade enemy AI."""

    ANIMATION_NAME = "Neon Maze Chase"
    ANIMATION_DESCRIPTION = "Autoplaying pellet maze with strategic hunters, power chains, scores, lives, and arcade intermissions"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    NAVY = (0, 1, 10)
    WALL = (15, 55, 245)
    WALL_GLOW = (2, 10, 55)
    PELLET = (255, 210, 145)
    HERO = (255, 220, 20)
    WHITE = (255, 255, 245)
    RED = (255, 40, 65)
    PINK = (255, 100, 205)
    CYAN = (20, 230, 255)
    ORANGE = (255, 145, 25)
    FRIGHTENED = (35, 60, 255)
    FRIGHTENED_FLASH = (245, 245, 255)
    FRUIT = (255, 25, 70)

    PLAYER_SPAWN = (21, 7)
    GHOST_SPAWNS = ((11, 7), (11, 6), (11, 8), (9, 7))
    SCATTER_TARGETS = ((0, 14), (0, 0), (24, 14), (24, 0))
    MODE_SCHEDULE = (("scatter", 7.0), ("chase", 20.0), ("scatter", 7.0),
                     ("chase", 20.0), ("scatter", 5.0), ("chase", 20.0),
                     ("scatter", 5.0), ("chase", math.inf))

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.width, self.height = self.get_strip_info()
        self.default_params.update({
            "speed": 1.0,
            "brightness": 1.0,
            "render_fps": 60.0,
            "difficulty": 0.82,
            "show_ai_targets": False,
            "seed": 1980,
        })
        self.params = {**self.default_params, **self.config}
        self.random = random.Random(int(self.params.get("seed", 1980)))
        self._canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self.walkable: Set[Cell] = {
            (row, col) for row, line in enumerate(MAZE)
            for col, value in enumerate(line) if value != "#"
        }
        self.initial_pellets = {
            cell for cell in self.walkable if cell not in self.GHOST_SPAWNS
            and cell != self.PLAYER_SPAWN
        }
        self.initial_energizers = {
            (row, col) for row, line in enumerate(MAZE)
            for col, value in enumerate(line) if value == "o"
        }
        self.initial_pellets -= self.initial_energizers
        self.last_elapsed: Optional[float] = None
        self.last_render_elapsed: Optional[float] = None
        self.last_rendered_frame: Optional[np.ndarray] = None
        self.score = 0
        self.high_score = 0
        self.level = 1
        self.lives = 3
        self.extra_life_awarded = False
        self.game_state = "ready"
        self.state_timer = 1.3
        self.mode_index = 0
        self.mode = "scatter"
        self.mode_timer = self.MODE_SCHEDULE[0][1]
        self.frightened_timer = 0.0
        self.ghost_chain = 0
        self.sim_time = 0.0
        self.fruit: Optional[Cell] = None
        self.fruit_timer = 0.0
        self.fruit_milestones: Set[int] = set()
        self.popups: List[ScorePopup] = []
        self._last_targets: List[Cell] = []
        self._reset_board()
        self._reset_actors()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            "speed": {"type": "float", "min": 0.25, "max": 3.0, "default": 1.0,
                      "description": "Simulation speed multiplier"},
            "render_fps": {"type": "float", "min": 20.0, "max": 90.0, "default": 60.0,
                           "description": "Maximum animation render rate"},
            "difficulty": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.82,
                           "description": "Hunter speed and player risk tolerance"},
            "show_ai_targets": {"type": "bool", "default": False,
                                "description": "Draw faint hunter target tiles"},
            "seed": {"type": "int", "min": 0, "max": 9999, "default": 1980,
                     "description": "Repeatable frightened movement seed"},
        })
        return schema

    def _reset_board(self):
        self.pellets = set(self.initial_pellets)
        self.energizers = set(self.initial_energizers)
        self.fruit = None
        self.fruit_timer = 0.0
        self.fruit_milestones.clear()

    def _reset_actors(self):
        self.mode_index = 0
        self.mode = "scatter"
        self.mode_timer = self.MODE_SCHEDULE[0][1]
        self.player = Actor(*self.PLAYER_SPAWN, LEFT, self.PLAYER_SPAWN, self.HERO)
        colors = (self.RED, self.PINK, self.CYAN, self.ORANGE)
        names = ("direct", "ambush", "vector", "shy")
        self.ghosts = [Actor(*spawn, LEFT if index % 2 == 0 else RIGHT, spawn, colors[index], names[index])
                       for index, spawn in enumerate(self.GHOST_SPAWNS)]
        self.frightened_timer = 0.0
        self.ghost_chain = 0
        self._prime_actor(self.player, self._choose_player_direction())
        for ghost in self.ghosts:
            self._prime_actor(ghost, self._choose_ghost_direction(ghost, self.ghosts.index(ghost)))

    def _prime_actor(self, actor: Actor, direction: Direction):
        actor.direction = direction
        actor.progress = 0.0

    def _neighbor(self, cell: Cell, direction: Direction) -> Cell:
        row, col = cell[0] + direction[0], cell[1] + direction[1]
        if cell == (TUNNEL_ROW, 0) and direction == LEFT:
            return TUNNEL_ROW, MAZE_WIDTH - 1
        if cell == (TUNNEL_ROW, MAZE_WIDTH - 1) and direction == RIGHT:
            return TUNNEL_ROW, 0
        return row, col

    def _available(self, cell: Cell) -> List[Direction]:
        return [direction for direction in DIRECTIONS if self._neighbor(cell, direction) in self.walkable]

    @staticmethod
    def _distance_sq(first: Cell, second: Cell) -> int:
        return (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2

    def _bfs_distance(self, start: Cell, goals: Iterable[Cell], limit: int = 999) -> int:
        goals = set(goals)
        if start in goals:
            return 0
        queue = deque([(start, 0)])
        seen = {start}
        while queue:
            cell, distance = queue.popleft()
            if distance >= limit:
                continue
            for direction in DIRECTIONS:
                neighbor = self._neighbor(cell, direction)
                if neighbor in self.walkable and neighbor not in seen:
                    if neighbor in goals:
                        return distance + 1
                    seen.add(neighbor)
                    queue.append((neighbor, distance + 1))
        return limit + 1

    def _choose_player_direction(self) -> Direction:
        choices = self._available(self.player.cell)
        if len(choices) > 1 and OPPOSITE[self.player.direction] in choices:
            choices.remove(OPPOSITE[self.player.direction])
        if not choices:
            return OPPOSITE[self.player.direction]

        normal_ghosts = [ghost for ghost in self.ghosts if ghost.state == "normal"]
        edible_ghosts = [ghost for ghost in self.ghosts if ghost.state == "frightened"]
        difficulty = max(0.0, min(1.0, float(self.params.get("difficulty", 0.82))))
        scored = []
        for order, direction in enumerate(choices):
            cell = self._neighbor(self.player.cell, direction)
            pellet_distance = self._bfs_distance(cell, self.pellets | self.energizers, 40)
            danger = min((self._bfs_distance(cell, [ghost.cell], 12) for ghost in normal_ghosts), default=20)
            edible = min((self._bfs_distance(cell, [ghost.cell], 25) for ghost in edible_ghosts), default=30)
            score = -pellet_distance * 2.0 + min(danger, 10) * (2.0 + 5.0 * difficulty)
            if danger <= 2:
                score -= 120.0 * difficulty
            if self.frightened_timer > 0.0:
                score -= edible * 5.0
            if cell in self.energizers and danger < 7:
                score += 45.0
            scored.append((score, -order, direction))
        return max(scored)[2]

    def _project(self, cell: Cell, direction: Direction, steps: int) -> Cell:
        return cell[0] + direction[0] * steps, cell[1] + direction[1] * steps

    def _ghost_target(self, ghost: Actor, index: int) -> Cell:
        if ghost.state == "eyes":
            return ghost.spawn
        if self.mode == "scatter":
            return self.SCATTER_TARGETS[index]
        if ghost.personality == "direct":
            return self.player.cell
        if ghost.personality == "ambush":
            return self._project(self.player.cell, self.player.direction, 4)
        if ghost.personality == "vector":
            pivot = self._project(self.player.cell, self.player.direction, 2)
            red = self.ghosts[0].cell
            return pivot[0] * 2 - red[0], pivot[1] * 2 - red[1]
        if self._distance_sq(ghost.cell, self.player.cell) > 64:
            return self.player.cell
        return self.SCATTER_TARGETS[index]

    def _choose_ghost_direction(self, ghost: Actor, index: int) -> Direction:
        choices = self._available(ghost.cell)
        reverse = OPPOSITE[ghost.direction]
        if len(choices) > 1 and reverse in choices:
            choices.remove(reverse)
        if not choices:
            return reverse
        if ghost.state == "frightened":
            return self.random.choice(choices)
        target = self._ghost_target(ghost, index)
        return min(choices, key=lambda direction: (
            self._distance_sq(self._neighbor(ghost.cell, direction), target),
            DIRECTIONS.index(direction),
        ))

    def generate_frame(self, time_elapsed: float, frame_count: int) -> Any:
        fps = max(20.0, min(90.0, float(self.params.get("render_fps", 60.0))))
        if (self.last_rendered_frame is not None and self.last_render_elapsed is not None
                and 0.0 <= time_elapsed - self.last_render_elapsed < 1.0 / fps):
            return self.rendered_frame(self.last_rendered_frame, changed=False)

        if self.last_elapsed is None or time_elapsed < self.last_elapsed:
            dt = 0.0
        else:
            dt = min(0.05, max(0.0, time_elapsed - self.last_elapsed))
        self.last_elapsed = time_elapsed
        self.last_render_elapsed = time_elapsed
        speed = max(0.1, float(self.params.get("speed", 1.0)))
        self._update(dt * speed)
        self._render()

        frame = self.next_frame_buffer(clear=False)
        frame.reshape(self.width, self.height, 3)[:] = self._canvas[::-1].transpose(1, 0, 2)
        self.apply_brightness_array(frame, out=frame)
        self.last_rendered_frame = frame
        return self.rendered_frame(frame, changed=True)

    def _update(self, dt: float):
        if dt <= 0.0:
            return
        self.sim_time += dt
        for popup in self.popups:
            popup.age += dt
            popup.row -= dt * 1.5
        self.popups[:] = [popup for popup in self.popups if popup.age < popup.life]

        if self.game_state != "playing":
            self.state_timer -= dt
            if self.state_timer <= 0.0:
                if self.game_state == "dying":
                    self.lives -= 1
                    if self.lives <= 0:
                        self.game_state, self.state_timer = "game_over", 2.2
                    else:
                        self._reset_actors()
                        self.game_state, self.state_timer = "ready", 1.0
                elif self.game_state == "level_clear":
                    self.level += 1
                    self._reset_board()
                    self._reset_actors()
                    self.game_state, self.state_timer = "ready", 1.0
                elif self.game_state == "game_over":
                    self.score = 0
                    self.level = 1
                    self.lives = 3
                    self.extra_life_awarded = False
                    self._reset_board()
                    self._reset_actors()
                    self.game_state, self.state_timer = "ready", 1.0
                else:
                    self.game_state = "playing"
            return

        self.mode_timer -= dt
        if self.mode_timer <= 0.0 and self.mode_index < len(self.MODE_SCHEDULE) - 1:
            self.mode_index += 1
            self.mode, self.mode_timer = self.MODE_SCHEDULE[self.mode_index]
            for ghost in self.ghosts:
                if ghost.state == "normal":
                    self._reverse_actor(ghost)

        if self.frightened_timer > 0.0:
            self.frightened_timer = max(0.0, self.frightened_timer - dt)
            if self.frightened_timer == 0.0:
                for ghost in self.ghosts:
                    if ghost.state == "frightened":
                        ghost.state = "normal"

        self.fruit_timer = max(0.0, self.fruit_timer - dt)
        if self.fruit is not None and self.fruit_timer == 0.0:
            self.fruit = None

        base_speed = min(10.0, 5.8 + self.level * 0.22)
        difficulty = max(0.0, min(1.0, float(self.params.get("difficulty", 0.82))))
        self._move_actor(self.player, base_speed * 1.03, self._choose_player_direction, dt)
        for index, ghost in enumerate(self.ghosts):
            factor = 1.45 if ghost.state == "eyes" else (0.62 if ghost.state == "frightened" else 0.78 + difficulty * 0.16)
            self._move_actor(ghost, base_speed * factor,
                             lambda ghost=ghost, index=index: self._choose_ghost_direction(ghost, index), dt)
        self._consume_player_cell()
        self._check_collisions()

        remaining = len(self.pellets) + len(self.energizers)
        eaten = len(self.initial_pellets) + len(self.initial_energizers) - remaining
        for milestone in (70, 140):
            if eaten >= milestone and milestone not in self.fruit_milestones:
                self.fruit_milestones.add(milestone)
                self.fruit, self.fruit_timer = (14, 7), 8.0
        if remaining == 0 and self.game_state == "playing":
            self.game_state, self.state_timer = "level_clear", 1.6

    def _move_actor(self, actor: Actor, cells_per_second: float, chooser, dt: float):
        actor.progress += cells_per_second * dt
        while actor.progress >= 1.0:
            actor.progress -= 1.0
            actor.row, actor.col = self._neighbor(actor.cell, actor.direction)
            if actor.state == "eyes" and actor.cell == actor.spawn:
                actor.state = "normal"
            actor.direction = chooser()

    def _reverse_actor(self, actor: Actor):
        """Reverse cleanly even when an actor is between two tile centers."""
        if actor.progress > 0.0:
            actor.row, actor.col = self._neighbor(actor.cell, actor.direction)
            actor.progress = 1.0 - actor.progress
        actor.direction = OPPOSITE[actor.direction]

    def _consume_player_cell(self):
        cell = self.player.cell
        if cell in self.pellets:
            self.pellets.remove(cell)
            self._award(10, cell)
        elif cell in self.energizers:
            self.energizers.remove(cell)
            self._award(50, cell)
            self.frightened_timer = max(2.5, 6.5 - self.level * 0.35)
            self.ghost_chain = 0
            for ghost in self.ghosts:
                if ghost.state == "normal":
                    ghost.state = "frightened"
                    self._reverse_actor(ghost)
        if self.fruit == cell:
            self._award(min(5000, 100 * (2 ** min(5, self.level - 1))), cell, popup=True)
            self.fruit = None
            self.fruit_timer = 0.0

    def _award(self, points: int, cell: Cell, popup: bool = False):
        self.score += points
        self.high_score = max(self.high_score, self.score)
        if popup:
            self.popups.append(ScorePopup(points, float(cell[0]), float(cell[1])))
        if self.score >= 10000 and not self.extra_life_awarded:
            self.extra_life_awarded = True
            self.lives += 1

    def _actor_position(self, actor: Actor) -> Tuple[float, float]:
        neighbor = self._neighbor(actor.cell, actor.direction)
        col_delta = neighbor[1] - actor.col
        if abs(col_delta) > 1:  # Tunnel interpolation crosses the edge.
            col_delta = -1 if actor.direction == LEFT else 1
        return actor.row + actor.direction[0] * actor.progress, actor.col + col_delta * actor.progress

    def _check_collisions(self):
        player_row, player_col = self._actor_position(self.player)
        for ghost in self.ghosts:
            ghost_row, ghost_col = self._actor_position(ghost)
            if (player_row - ghost_row) ** 2 + (player_col - ghost_col) ** 2 > 0.48:
                continue
            if ghost.state == "frightened":
                value = min(1600, 200 * (2 ** self.ghost_chain))
                self.ghost_chain += 1
                ghost.state = "eyes"
                ghost.progress = 0.0
                self._award(value, ghost.cell, popup=True)
            elif ghost.state == "normal":
                self.game_state, self.state_timer = "dying", 1.35
                return

    def _layout(self) -> Tuple[int, int, int, int]:
        header = 8
        scale_x = max(1, self.width // MAZE_WIDTH)
        scale_y = max(1, min(5, (self.height - header - 2) // MAZE_HEIGHT))
        maze_width = MAZE_WIDTH * scale_x
        maze_height = MAZE_HEIGHT * scale_y
        return (scale_x, scale_y, (self.width - maze_width) // 2,
                header + max(0, (self.height - header - maze_height) // 2))

    def _pixel(self, x: int, y: int, color: Color, additive: bool = False):
        if 0 <= x < self.width and 0 <= y < self.height:
            if additive:
                self._canvas[y, x] = np.maximum(self._canvas[y, x], color)
            else:
                self._canvas[y, x] = color

    def _text(self, text: str, x: int, y: int, color: Color):
        for char in text:
            glyph = FONT.get(char, FONT[" "])
            for gy, row in enumerate(glyph):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        self._pixel(x + gx, y + gy, color, True)
            x += 4

    def _centered_text(self, text: str, y: int, color: Color):
        self._text(text, max(0, (self.width - (len(text) * 4 - 1)) // 2), y, color)

    def _cell_rect(self, cell: Cell, color: Color, inset_x: int = 0, inset_y: Optional[int] = None):
        scale_x, scale_y, left, top = self._layout()
        if inset_y is None:
            inset_y = inset_x
        row, col = cell
        x, y = left + col * scale_x + inset_x, top + row * scale_y + inset_y
        cell_width = max(1, scale_x - inset_x * 2)
        cell_height = max(1, scale_y - inset_y * 2)
        self._canvas[max(0, y):min(self.height, y + cell_height),
                     max(0, x):min(self.width, x + cell_width)] = color

    def _draw_actor(self, actor: Actor, hero: bool = False):
        scale_x, scale_y, left, top = self._layout()
        row, col = self._actor_position(actor)
        if col < -0.5:
            col += MAZE_WIDTH
        elif col > MAZE_WIDTH - 0.5:
            col -= MAZE_WIDTH
        cx = int(round(left + (col + 0.5) * scale_x - 0.5))
        cy = int(round(top + (row + 0.5) * scale_y - 0.5))
        radius_x = max(0, scale_x // 2)
        radius_y = max(1, (scale_y - 1) // 2)

        if hero:
            death_progress = 0.0
            if self.game_state == "dying":
                death_progress = 1.0 - self.state_timer / 1.35
                radius_x = max(0, int(radius_x * (1.0 - death_progress)))
                radius_y = max(0, int(radius_y * (1.0 - death_progress)))
            for yy in range(-radius_y, radius_y + 1):
                for xx in range(-radius_x, radius_x + 1):
                    inside = ((xx / max(1, radius_x)) ** 2
                              + (yy / max(1, radius_y)) ** 2 <= 1.0)
                    if inside:
                        angle = math.atan2(yy, xx)
                        facing = math.atan2(actor.direction[0], actor.direction[1])
                        mouth = abs((angle - facing + math.pi) % (2 * math.pi) - math.pi)
                        gape = 0.25 + 0.35 * abs(math.sin(self.sim_time * 11.0)) + death_progress * 2.2
                        if mouth > gape:
                            self._pixel(cx + xx, cy + yy, self.HERO)
            return

        color = actor.color
        if actor.state == "frightened":
            flashing = self.frightened_timer < 2.0 and int(self.sim_time * 8) % 2 == 0
            color = self.FRIGHTENED_FLASH if flashing else self.FRIGHTENED
        if actor.state == "eyes":
            color = self.WHITE
        for yy in range(-radius_y, radius_y + 1):
            for xx in range(-radius_x, radius_x + 1):
                rounded_top = ((xx / max(1, radius_x)) ** 2
                               + (yy / max(1, radius_y)) ** 2 <= 1.0)
                if (yy <= 0 and rounded_top) or (yy > 0 and abs(xx) <= radius_x):
                    self._pixel(cx + xx, cy + yy, color)
        if scale_x >= 2:
            eye_x = max(1, radius_x)
            eye_y = max(1, radius_y // 2)
            self._pixel(cx - eye_x, cy - eye_y, (20, 30, 100) if actor.state == "eyes" else self.WHITE)
            self._pixel(cx + eye_x, cy - eye_y, (20, 30, 100) if actor.state == "eyes" else self.WHITE)

    def _render(self):
        self._canvas[:] = self.NAVY
        scale_x, scale_y, left, top = self._layout()
        pulse = int(12 + 10 * (1.0 + math.sin(self.sim_time * 2.5)))
        for row, line in enumerate(MAZE):
            for col, value in enumerate(line):
                cell = (row, col)
                if value == "#":
                    self._cell_rect(cell, self.WALL_GLOW)
                    # A bright inner core suggests neon tubing at larger scales.
                    if scale_x > 1 or scale_y > 1:
                        self._cell_rect(cell, self.WALL,
                                        inset_x=max(0, scale_x // 3),
                                        inset_y=max(0, scale_y // 3))
                    else:
                        self._cell_rect(cell, (8, 35, 150 + pulse))
                elif cell in self.pellets:
                    self._pixel(left + col * scale_x + scale_x // 2,
                                top + row * scale_y + scale_y // 2,
                                self.PELLET)
                elif cell in self.energizers and int(self.sim_time * 5) % 2 == 0:
                    self._cell_rect(cell, self.WHITE)

        if self.fruit is not None:
            self._cell_rect(self.fruit, self.FRUIT)
        if bool(self.params.get("show_ai_targets", False)):
            self._last_targets = [self._ghost_target(ghost, index) for index, ghost in enumerate(self.ghosts)]
            for target, ghost in zip(self._last_targets, self.ghosts):
                if target in self.walkable:
                    self._cell_rect(target, tuple(channel // 4 for channel in ghost.color))

        for ghost in self.ghosts:
            self._draw_actor(ghost)
        self._draw_actor(self.player, hero=True)

        score = f"{self.score % 1000000:06d}"
        shown = score[-min(6, max(3, self.width // 4)):]
        self._centered_text(shown, 1, self.WHITE)
        for life in range(min(self.lives, 5)):
            self._pixel(1 + life * 2, 6, self.HERO)
        for popup in self.popups:
            fade = max(0.0, 1.0 - popup.age / popup.life)
            color = tuple(int(channel * fade) for channel in self.WHITE)
            label = str(popup.value)
            px = int(left + popup.col * scale_x - (len(label) * 4 - 1) / 2)
            py = int(top + popup.row * scale_y)
            self._text(label, px, py, color)

        if self.game_state == "ready" and self.width >= 20:
            self._centered_text("READY!", self.height // 2 - 2, self.HERO)
        elif self.game_state == "game_over" and self.width >= 28:
            self._centered_text("GAME OVER", self.height // 2 - 2, self.RED)
        elif self.game_state == "level_clear" and int(self.sim_time * 10) % 2 == 0:
            self._canvas[:, :, 2] = np.maximum(self._canvas[:, :, 2], 65)

    def get_runtime_stats(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "high_score": self.high_score,
            "level": self.level,
            "lives": self.lives,
            "state": self.game_state,
            "ghost_mode": "frightened" if self.frightened_timer > 0.0 else self.mode,
            "frightened_seconds": round(self.frightened_timer, 2),
            "pellets_remaining": len(self.pellets) + len(self.energizers),
            "ghost_chain": self.ghost_chain,
            "fruit_active": self.fruit is not None,
        }
