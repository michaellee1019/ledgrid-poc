#!/usr/bin/env python3
"""Autonomous multi-snake animation with several rules-driven visual modes."""

from __future__ import annotations

import colorsys
import math
import random
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import numpy as np

from animation import AnimationBase


Cell = Tuple[int, int]
Direction = Tuple[int, int]
UP: Direction = (0, -1)
RIGHT: Direction = (1, 0)
DOWN: Direction = (0, 1)
LEFT: Direction = (-1, 0)
DIRECTIONS = (UP, RIGHT, DOWN, LEFT)
OPPOSITE = {UP: DOWN, DOWN: UP, LEFT: RIGHT, RIGHT: LEFT}
MAX_SIMULATION_STEPS = 8
SNAKE_SPEED_BASELINE = 10.0


@dataclass
class SnakeAgent:
    body: Deque[Cell] = field(default_factory=deque)
    direction: Direction = RIGHT
    target_length: int = 7
    hue_offset: int = 0
    score: int = 0
    respawn_ticks: int = 0

    @property
    def head(self) -> Optional[Cell]:
        return self.body[0] if self.body else None


class SnakeAnimation(AnimationBase):
    """Self-playing Snake with classic, wrap, portal, and battle rulesets."""

    ANIMATION_NAME = "Snake Garden"
    ANIMATION_DESCRIPTION = "Autonomous growing snakes with portals, battles, labyrinths, glow trails, and curated visual styles"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    STYLES = ("classic", "rainbow", "neon", "fire", "ice", "sunset", "prism")
    BACKGROUNDS = ("void", "stars", "grid", "aurora")
    RULESETS = ("classic", "wrap", "portal", "battle")
    WALL_PATTERNS = ("none", "pillars", "gates", "zigzag")

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.width, self.height = self.get_strip_info()
        self.default_params.update({
            "speed": 1.0,
            "brightness": 0.82,
            "render_fps": 60.0,
            "moves_per_second": 11.0,
            "snake_count": 3,
            "initial_length": 7,
            "max_length": 180,
            "food_count": 5,
            "growth_per_food": 3,
            "ruleset": "wrap",
            "wall_pattern": "none",
            "visual_style": "rainbow",
            "background": "void",
            "trail_strength": 0.72,
            "trail_decay": 2.5,
            "glow": 0.55,
            "seed": 1976,
        })
        self.params = {**self.default_params, **self.config}
        self.random = random.Random(int(self.params.get("seed", 1976)))
        self.snakes: List[SnakeAgent] = []
        self.food: Set[Cell] = set()
        self.walls: Set[Cell] = set()
        self.portals: Dict[Cell, Cell] = {}
        self.moves = 0
        self.food_eaten = 0
        self.deaths = 0
        self.last_elapsed: Optional[float] = None
        self.last_render_elapsed: Optional[float] = None
        self.last_rendered_frame: Optional[np.ndarray] = None
        self._step_accumulator = 0.0
        self._canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self._background = np.zeros_like(self._canvas)
        self._snake_layer = np.zeros_like(self._canvas)
        self._halo_layer = np.zeros_like(self._canvas)
        self._trail = np.zeros((self.height, self.width), dtype=np.float32)
        self._trail_hue = np.zeros((self.height, self.width), dtype=np.uint8)
        self._palette = np.zeros((256, 3), dtype=np.uint8)
        self._reset_world()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            "speed": {"type": "float", "min": 0.2, "max": 4.0, "default": 1.0,
                      "description": "Overall game speed multiplier"},
            "render_fps": {"type": "float", "min": 15.0, "max": 90.0, "default": 60.0,
                           "description": "Maximum visual refresh rate"},
            "moves_per_second": {"type": "float", "min": 2.0, "max": 30.0, "default": 11.0,
                                 "description": "Grid moves per second before speed scaling"},
            "snake_count": {"type": "int", "min": 1, "max": 12, "default": 3,
                            "description": "Number of autonomous snakes"},
            "initial_length": {"type": "int", "min": 3, "max": 30, "default": 7,
                               "description": "Starting length of each snake"},
            "max_length": {"type": "int", "min": 12, "max": 800, "default": 180,
                           "description": "Maximum length before growth stops"},
            "food_count": {"type": "int", "min": 1, "max": 30, "default": 5,
                           "description": "Food items kept on the board"},
            "growth_per_food": {"type": "int", "min": 1, "max": 12, "default": 3,
                                "description": "Body cells gained for each food item"},
            "ruleset": {"type": "str", "default": "wrap", "options": list(self.RULESETS),
                        "description": "Classic edges, wrapping, paired portals, or competitive battle"},
            "wall_pattern": {"type": "str", "default": "none", "options": list(self.WALL_PATTERNS),
                             "description": "Optional obstacle layout"},
            "visual_style": {"type": "str", "default": "rainbow", "options": list(self.STYLES),
                             "description": "Snake color treatment"},
            "background": {"type": "str", "default": "void", "options": list(self.BACKGROUNDS),
                           "description": "Backdrop behind the game"},
            "trail_strength": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.72,
                               "description": "Brightness of fading movement trails"},
            "trail_decay": {"type": "float", "min": 0.2, "max": 8.0, "default": 2.5,
                            "description": "How quickly movement trails disappear"},
            "glow": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.55,
                     "description": "Neighbor-pixel glow around snakes and food"},
            "seed": {"type": "int", "min": 0, "max": 9999, "default": 1976,
                     "description": "Repeatable game and background seed"},
        })
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        structural = {"snake_count", "initial_length", "ruleset", "wall_pattern", "seed"}
        needs_reset = any(key in new_params for key in structural)
        super().update_parameters(new_params)
        if needs_reset:
            self.random.seed(int(self.params.get("seed", 1976)))
            self._reset_world()
        elif any(key in new_params for key in ("visual_style", "background")):
            self._build_palette()
            self._build_background()
        self.last_render_elapsed = None

    def get_runtime_stats(self) -> Dict[str, Any]:
        alive = sum(bool(snake.body) for snake in self.snakes)
        return {
            "ruleset": str(self.params.get("ruleset", "wrap")),
            "moves": self.moves,
            "food_eaten": self.food_eaten,
            "deaths": self.deaths,
            "alive_snakes": alive,
            "longest_snake": max((len(snake.body) for snake in self.snakes), default=0),
            "speed_baseline": SNAKE_SPEED_BASELINE,
            "effective_moves_per_second": self._effective_moves_per_second(),
        }

    def generate_frame(self, time_elapsed: float, frame_count: int) -> Any:
        render_fps = max(15.0, min(90.0, float(self.params.get("render_fps", 60.0))))
        if (self.last_rendered_frame is not None and self.last_render_elapsed is not None
                and 0.0 <= time_elapsed - self.last_render_elapsed < 1.0 / render_fps):
            return self.rendered_frame(self.last_rendered_frame, changed=False)

        if self.last_elapsed is None or time_elapsed < self.last_elapsed:
            dt = 0.0
        else:
            dt = min(0.25, max(0.0, time_elapsed - self.last_elapsed))
        self.last_elapsed = time_elapsed
        self.last_render_elapsed = time_elapsed

        moves_per_second = self._effective_moves_per_second()
        self._step_accumulator += dt * moves_per_second
        steps = 0
        while self._step_accumulator >= 1.0 and steps < MAX_SIMULATION_STEPS:
            self._step_game()
            self._step_accumulator -= 1.0
            steps += 1
        if steps == MAX_SIMULATION_STEPS:
            self._step_accumulator = min(self._step_accumulator, 1.0)

        decay = math.exp(-max(0.2, float(self.params.get("trail_decay", 2.5))) * dt)
        self._trail *= decay
        self._render(time_elapsed)
        frame = self.next_frame_buffer(clear=False)
        frame.reshape(self.width, self.height, 3)[:] = self._canvas[::-1].transpose(1, 0, 2)
        self.apply_brightness_array(frame, out=frame)
        self.last_rendered_frame = frame
        return self.rendered_frame(frame, changed=True)

    def _effective_moves_per_second(self) -> float:
        speed = max(0.2, float(self.params.get("speed", 1.0)))
        moves_per_second = max(2.0, float(self.params.get("moves_per_second", 11.0)))
        return moves_per_second * speed * SNAKE_SPEED_BASELINE

    def _reset_world(self):
        self.moves = self.food_eaten = self.deaths = 0
        self._step_accumulator = 0.0
        self.last_elapsed = None
        self.last_render_elapsed = None
        self.last_rendered_frame = None
        self._trail.fill(0.0)
        self._trail_hue.fill(0)
        self._build_palette()
        self._build_background()
        self._build_walls_and_portals()
        count = max(1, min(12, int(self.params.get("snake_count", 3))))
        self.snakes = [SnakeAgent(hue_offset=(index * 256 // count)) for index in range(count)]
        for snake in self.snakes:
            self._spawn_snake(snake)
        self.food.clear()
        self._replenish_food()

    def _build_palette(self):
        style = str(self.params.get("visual_style", "rainbow"))
        for index in range(256):
            phase = index / 256.0
            if style == "classic":
                hue, saturation, value = 0.31, 0.9, 0.38 + 0.62 * phase
            elif style == "neon":
                hue, saturation, value = (0.52 + phase * 0.34) % 1.0, 0.95, 0.65 + 0.35 * phase
            elif style == "fire":
                hue, saturation, value = 0.01 + phase * 0.14, 1.0, 0.45 + 0.55 * phase
            elif style == "ice":
                hue, saturation, value = 0.48 + phase * 0.12, 0.65, 0.55 + 0.45 * phase
            elif style == "sunset":
                hue, saturation, value = 0.92 + phase * 0.16, 0.82, 0.55 + 0.45 * phase
            elif style == "prism":
                hue, saturation, value = (phase * 1.7) % 1.0, 0.88, 0.62 + 0.38 * phase
            else:
                hue, saturation, value = phase, 0.92, 0.62 + 0.38 * phase
            red, green, blue = colorsys.hsv_to_rgb(hue % 1.0, saturation, value)
            self._palette[index] = (int(red * 255), int(green * 255), int(blue * 255))

    def _build_background(self):
        self._background.fill(0)
        background = str(self.params.get("background", "void"))
        if background == "stars":
            rng = random.Random(int(self.params.get("seed", 1976)) + 101)
            for _ in range(max(3, self.width * self.height // 55)):
                x, y = rng.randrange(self.width), rng.randrange(self.height)
                level = rng.randrange(5, 22)
                self._background[y, x] = (level // 2, level // 2, level)
        elif background == "grid":
            self._background[:] = (0, 1, 5)
            self._background[::8, :] = (2, 8, 18)
            self._background[:, ::8] = (2, 8, 18)
        elif background == "aurora":
            y, x = np.indices((self.height, self.width))
            band = (np.sin(x * 0.42 + y * 0.075) + 1.0) * 0.5
            self._background[..., 0] = (band * 3).astype(np.uint8)
            self._background[..., 1] = (band * 13).astype(np.uint8)
            self._background[..., 2] = (5 + band * 15).astype(np.uint8)

    def _build_walls_and_portals(self):
        self.walls.clear()
        self.portals.clear()
        pattern = str(self.params.get("wall_pattern", "none"))
        if self.width < 5 or self.height < 8:
            return
        if pattern == "pillars":
            for x in range(4, self.width - 3, 7):
                for y in range(8, self.height - 8):
                    if (y // 9) % 2 == 0:
                        self.walls.add((x, y))
        elif pattern == "gates":
            for y in range(14, self.height - 8, 22):
                gap = 2 + (y * 7) % max(3, self.width - 5)
                self.walls.update((x, y) for x in range(self.width) if abs(x - gap) > 2)
        elif pattern == "zigzag":
            for y in range(8, self.height - 8):
                x = 3 + ((y // 3) % max(2, self.width - 6))
                if y % 11 not in (0, 1, 2):
                    self.walls.add((x, y))

        if str(self.params.get("ruleset", "wrap")) == "portal":
            pairs = [((1, self.height // 4), (self.width - 2, self.height * 3 // 4)),
                     ((self.width - 2, self.height // 4), (1, self.height * 3 // 4))]
            for first, second in pairs:
                if first not in self.walls and second not in self.walls:
                    self.portals[first] = second
                    self.portals[second] = first

    def _spawn_snake(self, snake: SnakeAgent) -> bool:
        occupied = self._occupied()
        initial_length = max(3, min(30, int(self.params.get("initial_length", 7))))
        for _ in range(250):
            direction = self.random.choice(DIRECTIONS)
            head = (self.random.randrange(self.width), self.random.randrange(self.height))
            body = deque()
            valid = True
            for offset in range(initial_length):
                cell = (head[0] - direction[0] * offset, head[1] - direction[1] * offset)
                if not (0 <= cell[0] < self.width and 0 <= cell[1] < self.height):
                    valid = False
                    break
                if cell in occupied or cell in self.walls or cell in self.portals:
                    valid = False
                    break
                body.append(cell)
            if valid:
                snake.body = body
                snake.direction = direction
                snake.target_length = initial_length
                snake.respawn_ticks = 0
                return True
        snake.body.clear()
        snake.respawn_ticks = 8
        return False

    def _occupied(self) -> Set[Cell]:
        return {cell for snake in self.snakes for cell in snake.body}

    def _replenish_food(self):
        target = max(1, min(30, int(self.params.get("food_count", 5))))
        blocked = self._occupied() | self.walls | set(self.portals)
        attempts = 0
        while len(self.food) < target and attempts < target * 80:
            attempts += 1
            cell = (self.random.randrange(self.width), self.random.randrange(self.height))
            if cell not in blocked:
                self.food.add(cell)

    def _advance_cell(self, cell: Cell, direction: Direction) -> Optional[Cell]:
        x, y = cell[0] + direction[0], cell[1] + direction[1]
        ruleset = str(self.params.get("ruleset", "wrap"))
        if ruleset in ("wrap", "battle"):
            x %= self.width
            y %= self.height
        elif not (0 <= x < self.width and 0 <= y < self.height):
            return None
        destination = (x, y)
        return self.portals.get(destination, destination)

    def _choose_direction(self, snake: SnakeAgent, occupied: Set[Cell]) -> Direction:
        if not snake.body:
            return snake.direction
        choices = [direction for direction in DIRECTIONS if direction != OPPOSITE[snake.direction]]
        self.random.shuffle(choices)
        best_direction = snake.direction
        best_score = -1e9
        for direction in choices:
            candidate = self._advance_cell(snake.head, direction)
            if candidate is None or candidate in self.walls:
                continue
            eating = candidate in self.food
            blocked = occupied
            if not eating and len(snake.body) >= snake.target_length and snake.body:
                blocked = occupied - {snake.body[-1]}
            if candidate in blocked:
                continue
            if self.food:
                distance = min(self._food_distance(candidate, food) for food in self.food)
            else:
                distance = 0
            exits = 0
            for next_direction in DIRECTIONS:
                neighbor = self._advance_cell(candidate, next_direction)
                if neighbor is not None and neighbor not in blocked and neighbor not in self.walls:
                    exits += 1
            straight_bonus = 0.2 if direction == snake.direction else 0.0
            jitter = self.random.random() * 0.12
            score = exits * 3.0 - distance * 1.15 + straight_bonus + jitter
            if eating:
                score += 30.0
            if score > best_score:
                best_score, best_direction = score, direction
        return best_direction

    def _food_distance(self, first: Cell, second: Cell) -> int:
        dx = abs(first[0] - second[0])
        dy = abs(first[1] - second[1])
        if str(self.params.get("ruleset", "wrap")) in ("wrap", "battle"):
            dx = min(dx, self.width - dx)
            dy = min(dy, self.height - dy)
        return dx + dy

    def _step_game(self):
        occupied = self._occupied()
        plans: Dict[int, Optional[Cell]] = {}
        directions: Dict[int, Direction] = {}
        for index, snake in enumerate(self.snakes):
            if not snake.body:
                snake.respawn_ticks -= 1
                if snake.respawn_ticks <= 0:
                    self._spawn_snake(snake)
                continue
            direction = self._choose_direction(snake, occupied)
            directions[index] = direction
            plans[index] = self._advance_cell(snake.head, direction)

        head_counts = Counter(cell for cell in plans.values() if cell is not None)
        dead: Set[int] = set()
        for index, candidate in plans.items():
            snake = self.snakes[index]
            if candidate is None or candidate in self.walls or head_counts[candidate] > 1:
                dead.add(index)
                continue
            eating = candidate in self.food
            blocked = occupied
            if not eating and len(snake.body) >= snake.target_length:
                blocked = occupied - {snake.body[-1]}
            if candidate in blocked:
                dead.add(index)

        for index, candidate in plans.items():
            snake = self.snakes[index]
            if index in dead:
                for x, y in snake.body:
                    self._trail[y, x] = 1.0
                    self._trail_hue[y, x] = (snake.hue_offset + 32) % 256
                snake.body.clear()
                snake.respawn_ticks = 6 + self.random.randrange(10)
                self.deaths += 1
                continue
            snake.direction = directions[index]
            snake.body.appendleft(candidate)
            ate = candidate in self.food
            if ate:
                self.food.remove(candidate)
                growth = max(1, int(self.params.get("growth_per_food", 3)))
                maximum = max(12, int(self.params.get("max_length", 180)))
                snake.target_length = min(maximum, snake.target_length + growth)
                snake.score += 1
                self.food_eaten += 1
            while len(snake.body) > snake.target_length:
                tail_x, tail_y = snake.body.pop()
                self._trail[tail_y, tail_x] = 1.0
                self._trail_hue[tail_y, tail_x] = snake.hue_offset

        self.moves += 1
        self._replenish_food()

    def _paint_max(self, x: int, y: int, color: np.ndarray, glow: float = 0.0):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        self._canvas[y, x] = np.maximum(self._canvas[y, x], color)
        if glow <= 0.0:
            return
        halo = (color.astype(np.float32) * glow).astype(np.uint8)
        for dx, dy in DIRECTIONS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < self.width and 0 <= ny < self.height:
                self._canvas[ny, nx] = np.maximum(self._canvas[ny, nx], halo)

    def _render(self, time_elapsed: float):
        self._canvas[:] = self._background
        trail_strength = max(0.0, min(1.0, float(self.params.get("trail_strength", 0.72))))
        if trail_strength > 0.0 and np.any(self._trail > 0.01):
            trail_colors = self._palette[self._trail_hue]
            scaled = (trail_colors.astype(np.float32)
                      * (self._trail * trail_strength)[..., None]).astype(np.uint8)
            np.maximum(self._canvas, scaled, out=self._canvas)

        wall_color = np.array((8, 25, 48), dtype=np.uint8)
        for x, y in self.walls:
            self._canvas[y, x] = wall_color
        pulse = 0.72 + 0.28 * math.sin(time_elapsed * 7.0)
        portal_colors = (np.array((30, 80, 255), dtype=np.uint8),
                         np.array((255, 35, 210), dtype=np.uint8))
        for index, (x, y) in enumerate(self.portals):
            self._paint_max(x, y, portal_colors[index % 2], 0.32)

        glow = max(0.0, min(1.0, float(self.params.get("glow", 0.55)))) * 0.45
        for index, (x, y) in enumerate(sorted(self.food)):
            hue = (index * 71 + int(time_elapsed * 35)) % 256
            color = (self._palette[hue].astype(np.float32) * pulse).astype(np.uint8)
            self._paint_max(x, y, color, glow)

        self._snake_layer.fill(0)
        for snake in self.snakes:
            body_length = max(1, len(snake.body))
            for segment, (x, y) in enumerate(reversed(snake.body)):
                phase = segment / body_length
                hue = (snake.hue_offset + int(phase * 105) + self.moves * 2) % 256
                self._snake_layer[y, x] = self._palette[hue]
            if snake.head is not None:
                head_color = np.minimum(self._palette[(snake.hue_offset + 128) % 256].astype(np.uint16) + 80,
                                        255).astype(np.uint8)
                self._snake_layer[snake.head[1], snake.head[0]] = head_color

        np.maximum(self._canvas, self._snake_layer, out=self._canvas)
        if glow > 0.0:
            np.multiply(self._snake_layer, glow, out=self._halo_layer, casting="unsafe")
            np.maximum(self._canvas[1:], self._halo_layer[:-1], out=self._canvas[1:])
            np.maximum(self._canvas[:-1], self._halo_layer[1:], out=self._canvas[:-1])
            np.maximum(self._canvas[:, 1:], self._halo_layer[:, :-1], out=self._canvas[:, 1:])
            np.maximum(self._canvas[:, :-1], self._halo_layer[:, 1:], out=self._canvas[:, :-1])
