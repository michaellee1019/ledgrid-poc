"""Scene v2 Snake: a deterministic, semantic-palette arcade instrument."""

from __future__ import annotations

import colorsys
import math
import random
from collections import Counter, deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Deque, Mapping

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene

Cell = tuple[int, int]
Direction = tuple[int, int]
UP: Direction = (0, -1)
RIGHT: Direction = (1, 0)
DOWN: Direction = (0, 1)
LEFT: Direction = (-1, 0)
DIRECTIONS = (UP, RIGHT, DOWN, LEFT)
OPPOSITE = {UP: DOWN, DOWN: UP, LEFT: RIGHT, RIGHT: LEFT}


@dataclass
class SnakeAgent:
    body: Deque[Cell] = field(default_factory=deque)
    direction: Direction = RIGHT
    target_length: int = 6
    hue_offset: float = 0.0
    respawn_ticks: int = 0

    @property
    def head(self) -> Cell | None:
        return self.body[0] if self.body else None


class SnakeAnimation(AnimationBase):
    """Self-playing Snake with only local Scene v2 creative controls."""

    ANIMATION_NAME, ANIMATION_DESCRIPTION = "Snake Garden", "Autonomous snakes, food, obstacles, portals, and glowing trails"
    ANIMATION_AUTHOR, ANIMATION_VERSION = "LED Grid Team", "2.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "snake", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset({"semantic_palette_roles", "scaled_context", "effect_intent"})
    PLANT_MODIFIER_SUPPORT = frozenset()
    SIM_HZ, MAX_CATCH_UP_STEPS = 24.0, 16
    RULESETS, OBSTACLES = ("classic", "wrap", "portal", "battle"), ("none", "pillars", "gates", "zigzag")
    STYLES, BACKGROUNDS = ("classic", "rainbow", "neon", "fire", "ice", "sunset", "prism"), ("void", "stars", "grid", "aurora")
    DEFAULTS = MappingProxyType({"move_cadence": 9.0, "snake_count": 3, "initial_length": 7, "max_length": 180, "food_count": 5, "growth_per_food": 3, "ruleset": "wrap", "obstacles": "none", "visual_style": "rainbow", "background": "void", "trails": .72, "trail_decay": 2.5, "glow": .55, "seed": 1976})
    COMPONENT_DESCRIPTOR = ComponentDescriptor(component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER, role=ROLE, timing_policy=TIMING_POLICY, alpha_behavior="opaque", palette_policy=PALETTE_POLICY, plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS)
    SEMANTIC_PALETTES = MappingProxyType({"neutral": ((3., 9., 14.), (77., 232., 123.), (255., 95., 91.)), "mist": ((3., 9., 20.), (73., 188., 255.), (243., 242., 255.)), "spectrum": ((13., 3., 34.), (237., 53., 234.), (42., 237., 218.)), "ember": ((18., 3., 2.), (255., 84., 25.), (255., 205., 74.))})

    def __init__(self, controller: Any, config: Mapping[str, Any] | None = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self._pixels = np.zeros((self.get_pixel_count(), 3), dtype=np.uint8)
        self._canvas = np.zeros((self.width, self.height, 3), dtype=np.uint8)
        self._trail = np.zeros((self.width, self.height, 3), dtype=np.float32)
        self._presentation_context: ResolvedScene | None = None
        self._last_tick: int | None = None
        self._last_render_key: tuple[Any, ...] | None = None
        self.snakes: list[SnakeAgent] = []; self.food: set[Cell] = set(); self.walls: set[Cell] = set(); self.portals: dict[Cell, Cell] = {}
        self.moves = self.food_eaten = self.deaths = 0
        self._reset_world()

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    def get_parameter_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "move_cadence": {"type": "float", "min": 1., "max": 24., "default": 9., "description": "Grid moves each second"},
            "snake_count": {"type": "int", "min": 1, "max": 12, "default": 3, "description": "Autonomous snakes in play"},
            "initial_length": {"type": "int", "min": 3, "max": 24, "default": 7, "description": "Starting body length"},
            "max_length": {"type": "int", "min": 12, "max": 320, "default": 180, "description": "Safe maximum grown length"},
            "food_count": {"type": "int", "min": 1, "max": 24, "default": 5, "description": "Food kept on the board"},
            "growth_per_food": {"type": "int", "min": 1, "max": 12, "default": 3, "description": "Cells gained after eating"},
            "ruleset": {"type": "str", "options": list(self.RULESETS), "default": "wrap", "description": "Edges, portals, or competitive play"},
            "obstacles": {"type": "str", "options": list(self.OBSTACLES), "default": "none", "description": "Obstacle layout"},
            "visual_style": {"type": "str", "options": list(self.STYLES), "default": "rainbow", "description": "Color treatment"},
            "background": {"type": "str", "options": list(self.BACKGROUNDS), "default": "void", "description": "Game backdrop"},
            "trails": {"type": "float", "min": 0., "max": 1., "default": .72, "description": "Trail brightness"},
            "trail_decay": {"type": "float", "min": .2, "max": 8., "default": 2.5, "description": "Trail fade speed"},
            "glow": {"type": "float", "min": 0., "max": 1., "default": .55, "description": "Neighbor-cell glow"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 1976, "description": "Repeatable game world"},
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        candidate = self._normalized_parameters({**self.params, **dict(new_params)})
        reset = any(candidate[name] != self.params[name] for name in {"snake_count", "initial_length", "max_length", "ruleset", "obstacles", "food_count", "seed"})
        self.params = candidate
        if reset: self._reset_world()
        self._last_render_key = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        descriptor = new.descriptor
        if (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value) != (self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE): raise ValueError("Snake received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str): raise ValueError("Snake requires a semantic Scene v2 palette")
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> RenderedFrame:
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> RenderedFrame:
        del frame_count
        if self._presentation_context is None: phase_time, palette_id, parameters = max(0., float(time_elapsed)), "neutral", self.params
        else: phase_time, palette_id, parameters = max(0., float(self._presentation_context.phase_time)), str(self._presentation_context.palette["palette_id"]), self._presentation_context.parameters
        candidate = self._normalized_parameters(parameters)
        if candidate != self.params: self.update_parameters(candidate)
        tick = int(math.floor(phase_time * self.SIM_HZ + 1.e-9))
        if self._last_tick is None or tick < self._last_tick: self._reset_world(); self._last_tick = tick
        else:
            for _ in range(min(tick - self._last_tick, self.MAX_CATCH_UP_STEPS)):
                self._step_game(); self._last_tick += 1
        key = (self._last_tick, palette_id, tuple(self.params.items()))
        if key == self._last_render_key: return RenderedFrame(self._pixels, changed=False, dirty_ranges=())
        self._paint(palette_id, phase_time); self._last_render_key = key
        return RenderedFrame(self._pixels, changed=True)

    def semantic_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"seed": self.params["seed"], "tick": self._last_tick, "moves": self.moves, "food_eaten": self.food_eaten, "snakes": tuple(tuple(s.body) for s in self.snakes)})

    def cadence_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"simulation_hz": self.SIM_HZ, "tick": self._last_tick, "moves": self.moves, "move_cadence": self.params["move_cadence"]})

    def get_runtime_stats(self) -> dict[str, Any]:
        return {"ruleset": self.params["ruleset"], "moves": self.moves, "food_eaten": self.food_eaten, "deaths": self.deaths, "alive_snakes": sum(bool(s.body) for s in self.snakes), "walls": len(self.walls), "effective_moves_per_second": self.params["move_cadence"]}

    def _reset_world(self) -> None:
        self._rng = random.Random(self.params["seed"]); self._trail.fill(0.); self._last_tick = None; self._last_render_key = None; self.moves = self.food_eaten = self.deaths = 0; self.food.clear(); self._build_terrain()
        self.snakes = [SnakeAgent(hue_offset=index / self.params["snake_count"]) for index in range(self.params["snake_count"])]
        for snake in self.snakes: self._spawn_snake(snake)
        self._replenish_food()

    def _build_terrain(self) -> None:
        self.walls.clear(); self.portals.clear(); pattern = self.params["obstacles"]
        if pattern == "pillars":
            for x in range(4, self.width - 3, 7):
                for y in range(7, self.height - 7):
                    if (y // 9) % 2 == 0: self.walls.add((x, y))
        elif pattern == "gates":
            for y in range(12, self.height - 7, 21):
                gap = 2 + (y * 7) % max(3, self.width - 5); self.walls.update((x, y) for x in range(self.width) if abs(x - gap) > 2)
        elif pattern == "zigzag":
            for y in range(7, self.height - 7):
                x = 3 + ((y // 3) % max(2, self.width - 6))
                if y % 11 not in (0, 1, 2): self.walls.add((x, y))
        if self.params["ruleset"] == "portal" and self.width > 4 and self.height > 6:
            for first, second in (((1, self.height // 4), (self.width - 2, self.height * 3 // 4)), ((self.width - 2, self.height // 4), (1, self.height * 3 // 4))):
                if first not in self.walls and second not in self.walls: self.portals[first] = second; self.portals[second] = first

    def _spawn_snake(self, snake: SnakeAgent) -> None:
        occupied, length = self._occupied(), self.params["initial_length"]
        for _ in range(240):
            direction = self._rng.choice(DIRECTIONS); head = (self._rng.randrange(self.width), self._rng.randrange(self.height)); body = deque((head[0] - direction[0] * offset, head[1] - direction[1] * offset) for offset in range(length))
            if all(0 <= x < self.width and 0 <= y < self.height and (x, y) not in occupied | self.walls | set(self.portals) for x, y in body): snake.body, snake.direction, snake.target_length, snake.respawn_ticks = body, direction, length, 0; return
        snake.body.clear(); snake.respawn_ticks = 6 + self._rng.randrange(8)

    def _occupied(self) -> set[Cell]: return {cell for snake in self.snakes for cell in snake.body}

    def _replenish_food(self) -> None:
        blocked, attempts = self._occupied() | self.walls | set(self.portals), 0
        while len(self.food) < self.params["food_count"] and attempts < self.params["food_count"] * 80:
            attempts += 1; cell = (self._rng.randrange(self.width), self._rng.randrange(self.height))
            if cell not in blocked: self.food.add(cell)

    def _advance(self, cell: Cell, direction: Direction) -> Cell | None:
        x, y = cell[0] + direction[0], cell[1] + direction[1]
        if self.params["ruleset"] in ("wrap", "battle"): x %= self.width; y %= self.height
        elif not (0 <= x < self.width and 0 <= y < self.height): return None
        return self.portals.get((x, y), (x, y))

    def _choose_direction(self, snake: SnakeAgent, occupied: set[Cell]) -> Direction:
        choices = [direction for direction in DIRECTIONS if direction != OPPOSITE[snake.direction]]; self._rng.shuffle(choices); best, score = snake.direction, -1.e9
        for direction in choices:
            candidate = self._advance(snake.head, direction)
            if candidate is None or candidate in self.walls: continue
            blocked = occupied - ({snake.body[-1]} if candidate not in self.food and len(snake.body) >= snake.target_length else set())
            if candidate in blocked: continue
            exits = sum(self._advance(candidate, step) not in blocked | self.walls for step in DIRECTIONS); distance = min((self._distance(candidate, food) for food in self.food), default=0); candidate_score = exits * 3. - distance * 1.15 + (.18 if direction == snake.direction else 0.) + self._rng.random() * .1 + (30. if candidate in self.food else 0.)
            if candidate_score > score: best, score = direction, candidate_score
        return best

    def _distance(self, first: Cell, second: Cell) -> int:
        dx, dy = abs(first[0] - second[0]), abs(first[1] - second[1])
        if self.params["ruleset"] in ("wrap", "battle"): dx, dy = min(dx, self.width - dx), min(dy, self.height - dy)
        return dx + dy

    def _step_game(self) -> None:
        # A tick does bounded work and cadence simply chooses which ticks move.
        interval = max(1, int(round(self.SIM_HZ / self.params["move_cadence"])))
        if self.moves % interval == 0: self._move_once()
        self.moves += 1; self._trail *= math.exp(-self.params["trail_decay"] / self.SIM_HZ)

    def _move_once(self) -> None:
        occupied = self._occupied(); plans: dict[int, Cell | None] = {}; directions: dict[int, Direction] = {}
        for index, snake in enumerate(self.snakes):
            if not snake.body:
                snake.respawn_ticks -= 1
                if snake.respawn_ticks <= 0: self._spawn_snake(snake)
                continue
            directions[index] = self._choose_direction(snake, occupied); plans[index] = self._advance(snake.head, directions[index])
        heads, dead = Counter(cell for cell in plans.values() if cell is not None), set()
        for index, candidate in plans.items():
            snake = self.snakes[index]; blocked = occupied - ({snake.body[-1]} if candidate not in self.food and len(snake.body) >= snake.target_length else set()) if candidate is not None else occupied
            if candidate is None or candidate in self.walls or heads[candidate] > 1 or candidate in blocked: dead.add(index)
        for index, candidate in plans.items():
            snake = self.snakes[index]
            if index in dead:
                for cell in snake.body: self._trail[cell] = np.maximum(self._trail[cell], self._snake_color(snake.hue_offset, "neutral", .55))
                snake.body.clear(); snake.respawn_ticks = 6 + self._rng.randrange(8); self.deaths += 1; continue
            snake.direction = directions[index]; snake.body.appendleft(candidate)
            if candidate in self.food: self.food.remove(candidate); snake.target_length = min(self.params["max_length"], snake.target_length + self.params["growth_per_food"]); self.food_eaten += 1
            while len(snake.body) > snake.target_length: self._trail[snake.body.pop()] = np.maximum(self._trail[snake.body[-1]], self._snake_color(snake.hue_offset, "neutral", .5))
        self._replenish_food()

    def _paint(self, palette_id: str, phase_time: float) -> None:
        background, primary, accent = self.SEMANTIC_PALETTES.get(palette_id, self.SEMANTIC_PALETTES["neutral"]); self._canvas[:] = np.asarray(background, dtype=np.uint8); self._paint_background(palette_id, phase_time); np.maximum(self._canvas, np.clip(self._trail * self.params["trails"], 0., 255.).astype(np.uint8), out=self._canvas)
        wall = np.asarray(primary) * .18 + np.asarray(background) * .82
        for cell in self.walls: self._canvas[cell] = wall.astype(np.uint8)
        for index, cell in enumerate(self.portals): self._paint_cell(cell, np.asarray((primary, accent)[index % 2]), self.params["glow"] * .45)
        for index, cell in enumerate(sorted(self.food)): self._paint_cell(cell, self._snake_color((index * .23 + phase_time * .12) % 1., palette_id, .9), self.params["glow"] * .5)
        for snake in self.snakes:
            length = max(1, len(snake.body))
            for index, cell in enumerate(reversed(snake.body)): self._paint_cell(cell, self._snake_color((snake.hue_offset + index / length * .32) % 1., palette_id, .65 + .35 * index / length), self.params["glow"] * .32)
            if snake.head is not None: self._paint_cell(snake.head, self._snake_color((snake.hue_offset + .18) % 1., palette_id, 1.), self.params["glow"] * .65)
        self._pixels[:] = self._canvas.reshape(self.get_pixel_count(), 3)

    def _paint_background(self, palette_id: str, phase_time: float) -> None:
        style = self.params["background"]; background, primary, _ = self.SEMANTIC_PALETTES.get(palette_id, self.SEMANTIC_PALETTES["neutral"])
        if style == "stars":
            rng = random.Random(self.params["seed"] + 101)
            for _ in range(max(3, self.width * self.height // 58)): self._canvas[rng.randrange(self.width), rng.randrange(self.height)] = np.asarray(background) * .65 + np.asarray(primary) * .14
        elif style == "grid": self._canvas[::8, :] = np.asarray(primary) * .12; self._canvas[:, ::8] = np.asarray(primary) * .12
        elif style == "aurora":
            x, y = np.arange(self.width, dtype=np.float32)[:, None], np.arange(self.height, dtype=np.float32)[None, :]; strength = (np.sin(x * .42 + y * .075 + phase_time * .35) + 1.) * .09; self._canvas[:] = np.clip(np.asarray(background) + strength[..., None] * (np.asarray(primary) - np.asarray(background)), 0, 255).astype(np.uint8)

    def _paint_cell(self, cell: Cell, color: np.ndarray, glow: float) -> None:
        x, y = cell
        if not (0 <= x < self.width and 0 <= y < self.height): return
        self._canvas[x, y] = np.maximum(self._canvas[x, y], np.asarray(color, dtype=np.uint8)); halo = np.asarray(color, dtype=np.float32) * glow
        for dx, dy in DIRECTIONS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < self.width and 0 <= ny < self.height: self._canvas[nx, ny] = np.maximum(self._canvas[nx, ny], halo.astype(np.uint8))

    def _snake_color(self, hue: float, palette_id: str, value: float) -> np.ndarray:
        _, primary, accent = self.SEMANTIC_PALETTES.get(palette_id, self.SEMANTIC_PALETTES["neutral"]); style_hue = {"classic": .31, "neon": .61, "fire": .05, "ice": .54, "sunset": .96, "prism": hue * 1.7}.get(self.params["visual_style"], hue); rgb = np.asarray(colorsys.hsv_to_rgb(style_hue % 1., .72 if self.params["visual_style"] == "classic" else .88, value)) * 255.; return np.clip(rgb * .4 + (np.asarray(primary) * .62 + np.asarray(accent) * .38) * .6, 0., 255.)

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown: raise ValueError(f"Snake does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS); result.update(values)
        limits = {"snake_count": (1, 12), "initial_length": (3, 24), "max_length": (12, 320), "food_count": (1, 24), "growth_per_food": (1, 12), "seed": (0, 999999)}
        for name, (low, high) in limits.items():
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high: raise ValueError(f"{name} must be an integer from {low} to {high}")
        if result["initial_length"] > result["max_length"]: raise ValueError("initial_length cannot exceed max_length")
        for name, choices in (("ruleset", cls.RULESETS), ("obstacles", cls.OBSTACLES), ("visual_style", cls.STYLES), ("background", cls.BACKGROUNDS)):
            if result[name] not in choices: raise ValueError(f"{name} must be one of {list(choices)!r}")
        for name, low, high in (("move_cadence", 1., 24.), ("trails", 0., 1.), ("trail_decay", .2, 8.), ("glow", 0., 1.)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not low <= float(value) <= high: raise ValueError(f"{name} must be a finite number from {low} to {high}")
            result[name] = float(value)
        return result
