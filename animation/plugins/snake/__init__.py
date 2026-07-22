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
from animation.core.plant_awareness import GLOBE_REGION_ORDER


Cell = Tuple[int, int]
Direction = Tuple[int, int]
UP: Direction = (0, -1)
RIGHT: Direction = (1, 0)
DOWN: Direction = (0, 1)
LEFT: Direction = (-1, 0)
DIRECTIONS = (UP, RIGHT, DOWN, LEFT)
OPPOSITE = {UP: DOWN, DOWN: UP, LEFT: RIGHT, RIGHT: LEFT}
MAX_SIMULATION_STEPS = 4
SNAKE_SPEED_BASELINE = 10.0


@dataclass
class SnakeAgent:
    body: Deque[Cell] = field(default_factory=deque)
    direction: Direction = RIGHT
    target_length: int = 7
    hue_offset: int = 0
    score: int = 0
    respawn_ticks: int = 0
    portal_exit_region: Optional[str] = None
    portal_cooldown_ticks: int = 0

    @property
    def head(self) -> Optional[Cell]:
        return self.body[0] if self.body else None


class SnakeAnimation(AnimationBase):
    """Self-playing Snake with classic, wrap, portal, and battle rulesets."""

    ANIMATION_NAME = "Snake Garden"
    ANIMATION_DESCRIPTION = "Autonomous growing snakes with portals, battles, labyrinths, glow trails, and curated visual styles"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    PLANT_MODIFIER_SUPPORT = frozenset(("obstacle", "portal", "hazard"))

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
        self._plant_obstacles: Set[Cell] = set()
        self._plant_clearance: Set[Cell] = set()
        self._plant_foliage: Set[Cell] = set()
        self._plant_globes: Set[Cell] = set()
        self._plant_regions: Dict[str, Set[Cell]] = {}
        self._plant_region_bounds: Dict[str, Tuple[int, int, int, int]] = {}
        self.moves = 0
        self.food_eaten = 0
        self.deaths = 0
        self.plant_contacts = 0
        self.plant_teleports = 0
        self.plant_hazard_deaths = 0
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
        plant_geometry = {"plant_clearance", "plant_mask_path", "plant_globe_mask_path"}
        old_state = self.plant_modifier_state()
        was_plant_aware = self._plant_effects_enabled()
        needs_reset = any(key in new_params for key in structural)
        super().update_parameters(new_params)
        modifier_changed = (
            "plant_modifiers" in new_params and self.plant_modifier_state() != old_state
        )
        needs_reset = (
            needs_reset
            or ("plant_aware" in new_params and self._plant_effects_enabled() != was_plant_aware)
            or (self._plant_effects_enabled() and bool(plant_geometry & new_params.keys()))
        )
        if needs_reset:
            self.random.seed(int(self.params.get("seed", 1976)))
            self._reset_world()
        elif modifier_changed:
            # Modifier changes invalidate geometry/plans but are not simulation
            # ticks and therefore must not consume randomness or move entities.
            self._refresh_plant_geometry()
            self._build_walls_and_portals()
        elif any(key in new_params for key in ("visual_style", "background")):
            self._build_palette()
            self._build_background()
        self.last_render_elapsed = None

    def get_runtime_stats(self) -> Dict[str, Any]:
        alive = sum(bool(snake.body) for snake in self.snakes)
        requested_rate = self._requested_moves_per_second()
        effective_rate = self._effective_moves_per_second()
        return {
            "ruleset": str(self.params.get("ruleset", "wrap")),
            "moves": self.moves,
            "food_eaten": self.food_eaten,
            "deaths": self.deaths,
            "plant_contacts": self.plant_contacts,
            "plant_teleports": self.plant_teleports,
            "plant_hazard_deaths": self.plant_hazard_deaths,
            "alive_snakes": alive,
            "longest_snake": max((len(snake.body) for snake in self.snakes), default=0),
            "speed_baseline": SNAKE_SPEED_BASELINE,
            "requested_moves_per_second": requested_rate,
            "effective_moves_per_second": effective_rate,
            "simulation_rate_capped": effective_rate < requested_rate,
            **({
                "plant_obstacle_cells": len(self._plant_obstacles),
                "plant_clearance_cells": len(self._plant_clearance),
                "plant_foliage_cells": len(self._plant_foliage),
                "plant_globe_cells": len(self._plant_globes),
            } if self._plant_effects_enabled() else {}),
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
        render_fps = max(15.0, min(90.0, float(self.params.get("render_fps", 60.0))))
        return min(self._requested_moves_per_second(), render_fps * MAX_SIMULATION_STEPS)

    def _requested_moves_per_second(self) -> float:
        speed = max(0.2, float(self.params.get("speed", 1.0)))
        moves_per_second = max(2.0, float(self.params.get("moves_per_second", 11.0)))
        return moves_per_second * speed * SNAKE_SPEED_BASELINE

    def _reset_world(self):
        self.moves = self.food_eaten = self.deaths = 0
        self.plant_contacts = self.plant_teleports = self.plant_hazard_deaths = 0
        self._step_accumulator = 0.0
        self.last_elapsed = None
        self.last_render_elapsed = None
        self.last_rendered_frame = None
        self._trail.fill(0.0)
        self._trail_hue.fill(0)
        self._build_palette()
        self._build_background()
        self._refresh_plant_geometry()
        self._build_walls_and_portals()
        count = max(1, min(12, int(self.params.get("snake_count", 3))))
        self.snakes = [SnakeAgent(hue_offset=(index * 256 // count)) for index in range(count)]
        for snake in self.snakes:
            self._spawn_snake(snake)
        self.food.clear()
        self._replenish_food()

    def _refresh_plant_geometry(self):
        """Translate calibrated strip/LED masks into Snake's ``(x, y)`` cells."""
        self._plant_obstacles.clear()
        self._plant_clearance.clear()
        self._plant_foliage.clear()
        self._plant_globes.clear()
        self._plant_regions.clear()
        self._plant_region_bounds.clear()
        if not self._plant_effects_enabled():
            return
        masks = self.get_plant_masks()
        planning_masks = masks
        if self.plant_modifier_enabled("obstacle") and not self._legacy_plant_mode():
            configured = max(0, int(self.params.get("plant_clearance", 1)))
            planning_masks = self.get_plant_masks(
                int(round(configured * self.plant_modifier_strength("obstacle")))
            )
        # Shared logical masks are (strip, led); the game canvas is indexed [y, x].
        self._plant_obstacles.update((int(x), int(y)) for y, x in np.argwhere(masks.obstacle.T))
        self._plant_clearance.update(
            (int(x), int(y)) for y, x in np.argwhere(planning_masks.clearance.T)
        )
        self._plant_foliage.update(
            (int(x), int(y)) for y, x in np.argwhere(masks.foliage.T)
        )
        self._plant_globes.update(
            (int(x), int(y)) for y, x in np.argwhere(masks.globes.T)
        )
        for name in GLOBE_REGION_ORDER:
            region_mask = masks.globe_region_masks.get(name)
            if region_mask is None:
                continue
            cells = {(int(x), int(y)) for y, x in np.argwhere(region_mask.T)}
            if not cells:
                continue
            self._plant_regions[name] = cells
            xs, ys = zip(*cells)
            self._plant_region_bounds[name] = (min(xs), min(ys), max(xs), max(ys))

    def _legacy_plant_mode(self) -> bool:
        return bool(self.params.get("plant_aware", False))

    def _plant_effects_enabled(self) -> bool:
        return self._legacy_plant_mode() or any(
            self.plant_modifier_enabled(modifier)
            for modifier in self.PLANT_MODIFIER_SUPPORT
        )

    def _terrain_blocked(self, cell: Cell, planning: bool = False) -> bool:
        if cell in self.walls:
            return True
        if self._legacy_plant_mode():
            return cell in self._plant_clearance
        if not self.plant_modifier_enabled("obstacle"):
            return False
        return cell in (self._plant_clearance if planning else self._plant_obstacles)

    def _region_for_cell(self, cell: Cell) -> Optional[str]:
        for name in GLOBE_REGION_ORDER:
            if cell in self._plant_regions.get(name, ()):
                return name
        return None

    def _portal_destination(self, cell: Cell, snake: Optional[SnakeAgent] = None) -> Tuple[Cell, Optional[str]]:
        if not self.plant_modifier_enabled("portal") or len(self._plant_regions) < 2:
            return cell, None
        source = self._region_for_cell(cell)
        if source is None or (snake is not None and snake.portal_exit_region is not None):
            return cell, None
        source_index = GLOBE_REGION_ORDER.index(source)
        for offset in range(1, len(GLOBE_REGION_ORDER) + 1):
            target = GLOBE_REGION_ORDER[(source_index + offset) % len(GLOBE_REGION_ORDER)]
            if target in self._plant_regions:
                break
        else:
            return cell, None
        sx0, sy0, sx1, sy1 = self._plant_region_bounds[source]
        tx0, ty0, tx1, ty1 = self._plant_region_bounds[target]
        fx = 0.5 if sx1 == sx0 else (cell[0] - sx0) / (sx1 - sx0)
        fy = 0.5 if sy1 == sy0 else (cell[1] - sy0) / (sy1 - sy0)
        ideal = (tx0 + fx * (tx1 - tx0), ty0 + fy * (ty1 - ty0))
        destination = min(
            self._plant_regions[target],
            key=lambda point: ((point[0] - ideal[0]) ** 2 + (point[1] - ideal[1]) ** 2, point[1], point[0]),
        )
        return destination, target

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
                if (not self._terrain_blocked(first, planning=True)
                        and not self._terrain_blocked(second, planning=True)):
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
                if cell in occupied or self._terrain_blocked(cell, planning=True) or cell in self.portals:
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
        plant_blocked = self._plant_clearance if (
            self._legacy_plant_mode() or self.plant_modifier_enabled("obstacle")
        ) else set()
        blocked = self._occupied() | self.walls | plant_blocked | set(self.portals)
        attempts = 0
        while len(self.food) < target and attempts < target * 80:
            attempts += 1
            cell = (self.random.randrange(self.width), self.random.randrange(self.height))
            if cell not in blocked:
                self.food.add(cell)

    def _raw_advance_cell(self, cell: Cell, direction: Direction) -> Optional[Cell]:
        x, y = cell[0] + direction[0], cell[1] + direction[1]
        ruleset = str(self.params.get("ruleset", "wrap"))
        if ruleset in ("wrap", "battle"):
            x %= self.width
            y %= self.height
        elif not (0 <= x < self.width and 0 <= y < self.height):
            return None
        return self.portals.get((x, y), (x, y))

    def _advance_cell(
        self, cell: Cell, direction: Direction, snake: Optional[SnakeAgent] = None
    ) -> Optional[Cell]:
        destination = self._raw_advance_cell(cell, direction)
        if destination is None:
            return None
        return self._portal_destination(destination, snake)[0]

    def _choose_direction(self, snake: SnakeAgent, occupied: Set[Cell]) -> Direction:
        if not snake.body:
            return snake.direction
        choices = [direction for direction in DIRECTIONS if direction != OPPOSITE[snake.direction]]
        self.random.shuffle(choices)
        best_direction = snake.direction
        best_score = -1e9
        for direction in choices:
            candidate = self._advance_cell(snake.head, direction, snake)
            if candidate is None or self._terrain_blocked(candidate, planning=True):
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
                neighbor = self._advance_cell(candidate, next_direction, snake)
                if neighbor is not None and neighbor not in blocked and not self._terrain_blocked(neighbor, planning=True):
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
        for snake in self.snakes:
            if snake.portal_cooldown_ticks > 0:
                snake.portal_cooldown_ticks -= 1
            if (snake.portal_exit_region is not None and snake.portal_cooldown_ticks <= 0
                    and snake.head not in self._plant_regions.get(snake.portal_exit_region, ())):
                snake.portal_exit_region = None
        occupied = self._occupied()
        plans: Dict[int, Optional[Cell]] = {}
        portal_exits: Dict[int, Optional[str]] = {}
        directions: Dict[int, Direction] = {}
        for index, snake in enumerate(self.snakes):
            if not snake.body:
                snake.respawn_ticks -= 1
                if snake.respawn_ticks <= 0:
                    self._spawn_snake(snake)
                continue
            direction = self._choose_direction(snake, occupied)
            directions[index] = direction
            raw = self._raw_advance_cell(snake.head, direction)
            if raw is None:
                plans[index] = None
                portal_exits[index] = None
            else:
                plans[index], portal_exits[index] = self._portal_destination(raw, snake)

        head_counts = Counter(cell for cell in plans.values() if cell is not None)
        dead: Set[int] = set()
        hazard_dead: Set[int] = set()
        for index, candidate in plans.items():
            snake = self.snakes[index]
            exact_contact = candidate in self._plant_obstacles if candidate is not None else False
            hazard_contact = exact_contact and self.plant_modifier_enabled("hazard")
            if hazard_contact:
                self.plant_contacts += 1
                self.plant_hazard_deaths += 1
                hazard_dead.add(index)
            if (candidate is None or self._terrain_blocked(candidate)
                    or hazard_contact or head_counts[candidate] > 1):
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
                snake.respawn_ticks = (
                    6 + self.random.randrange(10)
                    + (int(round(8 * self.plant_modifier_strength("hazard")))
                       if index in hazard_dead else 0)
                )
                self.deaths += 1
                continue
            snake.direction = directions[index]
            snake.body.appendleft(candidate)
            exit_region = portal_exits.get(index)
            if exit_region is not None:
                snake.portal_exit_region = exit_region
                snake.portal_cooldown_ticks = 1
                self.plant_contacts += 1
                self.plant_teleports += 1
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

        if self._plant_effects_enabled():
            foliage_color = np.array((3, 36, 12), dtype=np.uint8)
            portal_strength = self.plant_modifier_strength("portal")
            hazard_strength = self.plant_modifier_strength("hazard")
            if portal_strength > 0:
                globe_color = np.array(
                    (40 + int(75 * portal_strength), 20, 70 + int(120 * portal_strength)),
                    dtype=np.uint8,
                )
            elif hazard_strength > 0:
                globe_color = np.array(
                    (80 + int(150 * hazard_strength), 12, 18), dtype=np.uint8
                )
            else:
                globe_color = np.array((88, 22, 105), dtype=np.uint8)
            for x, y in self._plant_foliage:
                self._canvas[y, x] = np.maximum(self._canvas[y, x], foliage_color)
            for x, y in self._plant_globes:
                self._paint_max(x, y, globe_color, 0.18)

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
