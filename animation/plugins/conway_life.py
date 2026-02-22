#!/usr/bin/env python3
"""Conway's Game of Life animation plugin for the LED grid."""

import math
import random
from typing import Any, Dict, Iterable, List, Optional, Tuple

from animation import AnimationBase
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


Color = Tuple[int, int, int]


class ConwayLifeAnimation(AnimationBase):
    """Conway's Game of Life with smooth transitions and color blending."""

    ANIMATION_NAME = "Conway's Game of Life"
    ANIMATION_DESCRIPTION = "Classic cellular automaton with smooth births/deaths and glider injection"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.1"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.num_strips = getattr(controller, "strip_count", DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, "leds_per_strip", DEFAULT_LEDS_PER_STRIP)
        self.width = max(1, self.num_strips)
        self.height = max(1, self.leds_per_strip)

        self.default_params.update(
            {
                "speed": 1.0,
                "wrap_edges": True,
                "random_density": 0.14,
                "phase_frames": 10,
                "generations_per_second": 5.0,
                "glider_interval": 10.0,
                "stagnation_generations": 120,
            }
        )
        self.params = {**self.default_params, **self.config}

        self.random = random.Random()
        self.grid: List[List[int]] = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.next_grid: List[List[int]] = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.natural_grid: List[List[Optional[Color]]] = [
            [None for _ in range(self.width)] for _ in range(self.height)
        ]
        self.next_natural_grid: List[List[Optional[Color]]] = [
            [None for _ in range(self.width)] for _ in range(self.height)
        ]
        self.neighbor_counts: List[List[int]] = [[0 for _ in range(self.width)] for _ in range(self.height)]

        self.generation = 0
        self.alive_cells = 0
        self.births_last_generation = 0
        self.deaths_last_generation = 0
        self.last_step_elapsed: Optional[float] = None

        self.phase = "color"
        self.phase_frame = 0
        self.frame_progress = 0.0
        self.last_glider_time = 0.0
        self.stagnation_counter = 0
        self.previous_population = -1

        self._initialize_grid(self.params.get("seed_cells"))

    def get_parameter_schema(self) -> Dict[str, Any]:
        schema = super().get_parameter_schema()
        schema.update(
            {
                "wrap_edges": {
                    "type": "bool",
                    "default": True,
                    "description": "Wrap edges so patterns flow around panel borders",
                },
                "random_density": {
                    "type": "float",
                    "min": 0.0,
                    "max": 0.4,
                    "default": 0.14,
                    "description": "Spawn density when no seed cells are provided",
                },
                "phase_frames": {
                    "type": "int",
                    "min": 1,
                    "max": 30,
                    "default": 10,
                    "description": "Frames per color/transition phase",
                },
                "generations_per_second": {
                    "type": "float",
                    "min": 0.5,
                    "max": 20.0,
                    "default": 5.0,
                    "description": "Base simulation generations per second before speed scaling",
                },
                "glider_interval": {
                    "type": "float",
                    "min": 0.0,
                    "max": 60.0,
                    "default": 10.0,
                    "description": "Seconds between automatic glider injections (0 disables)",
                },
                "stagnation_generations": {
                    "type": "int",
                    "min": 0,
                    "max": 1000,
                    "default": 120,
                    "description": "Re-seed after this many stagnant generations (0 disables)",
                },
            }
        )
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        params = dict(new_params)
        spawn_glider = params.pop("spawn_glider", None)
        reseed = bool(params.pop("reseed", False))

        super().update_parameters(params)

        if "seed_cells" in params:
            self._initialize_grid(params.get("seed_cells"))
            return

        if any(
            key in params
            for key in ("wrap_edges", "random_density", "phase_frames", "generations_per_second")
        ):
            self._compute_next_state()

        if reseed:
            self._initialize_grid(None)

        if spawn_glider:
            self._spawn_glider(count=5)

    def get_runtime_stats(self) -> Dict[str, Any]:
        return {
            "generation": self.generation,
            "alive_cells": self.alive_cells,
            "population_ratio": self.alive_cells / max(1, self.width * self.height),
            "births_last_generation": self.births_last_generation,
            "deaths_last_generation": self.deaths_last_generation,
            "phase": self.phase,
            "phase_frame": self.phase_frame,
            "stagnation_counter": self.stagnation_counter,
        }

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Color]:
        total_pixels = self.num_strips * self.leds_per_strip
        frame: List[Color] = [(0, 0, 0)] * total_pixels

        glider_interval = max(0.0, float(self.params.get("glider_interval", 10.0) or 0.0))
        if glider_interval > 0 and (time_elapsed - self.last_glider_time) >= glider_interval:
            self._spawn_glider(count=3)
            self.last_glider_time = time_elapsed

        if self.last_step_elapsed is None:
            self.last_step_elapsed = time_elapsed
        else:
            delta = max(0.0, time_elapsed - self.last_step_elapsed)
            self.last_step_elapsed = time_elapsed
            step_interval = self._step_interval()
            self.frame_progress += delta / step_interval

            # Bound catch-up work if time jumps (pauses/debugger breakpoints).
            steps = 0
            while self.frame_progress >= 1.0 and steps < 40:
                self._advance_phase()
                self.frame_progress -= 1.0
                steps += 1

        for y in range(self.height):
            for x in range(self.width):
                color = self._cell_color(x, y)
                if color:
                    self._set_pixel(frame, x, y, color)

        return frame

    def _initialize_grid(self, seed_cells: Optional[Iterable[Any]]):
        self.grid = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.next_grid = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.natural_grid = [[None for _ in range(self.width)] for _ in range(self.height)]
        self.next_natural_grid = [[None for _ in range(self.width)] for _ in range(self.height)]

        has_explicit_seed = seed_cells is not None
        parsed = self._parse_seed_cells(seed_cells)

        if has_explicit_seed:
            initial_color = self._random_natural_color()
            for x, y in parsed:
                if 0 <= x < self.width and 0 <= y < self.height:
                    self.grid[y][x] = 1
                    self.natural_grid[y][x] = initial_color
        else:
            density = float(self.params.get("random_density", 0.14) or 0.0)
            density = max(0.0, min(0.4, density))
            if density > 0:
                for y in range(self.height):
                    for x in range(self.width):
                        if self.random.random() < density:
                            self.grid[y][x] = 1
                            self.natural_grid[y][x] = self._random_natural_color()

        self.generation = 0
        self.alive_cells = self._count_alive(self.grid)
        self.births_last_generation = 0
        self.deaths_last_generation = 0
        self.previous_population = self.alive_cells
        self.stagnation_counter = 0
        self.last_step_elapsed = None

        self.phase = "color"
        self.phase_frame = 0
        self.frame_progress = 0.0
        self._compute_next_state()

    def _parse_seed_cells(self, seed_cells: Optional[Iterable[Any]]) -> List[Tuple[int, int]]:
        if not seed_cells:
            return []

        parsed: List[Tuple[int, int]] = []
        for item in seed_cells:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    parsed.append((int(item[0]), int(item[1])))
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, dict):
                try:
                    parsed.append((int(item.get("x")), int(item.get("y"))))
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, str) and "," in item:
                parts = item.split(",")
                if len(parts) >= 2:
                    try:
                        parsed.append((int(parts[0].strip()), int(parts[1].strip())))
                    except (TypeError, ValueError):
                        continue
        return parsed

    def _step_interval(self) -> float:
        speed = max(0.1, float(self.params.get("speed", 1.0) or 1.0))
        base_gps = max(0.5, float(self.params.get("generations_per_second", 5.0) or 5.0))
        phase_frames = max(1, int(self.params.get("phase_frames", 10) or 10))
        steps_per_generation = max(1.0, phase_frames * 2.0)
        return 1.0 / max(1.0, base_gps * speed * steps_per_generation)

    def _advance_phase(self):
        phase_frames = max(1, int(self.params.get("phase_frames", 10) or 10))

        self.phase_frame += 1
        if self.phase_frame < phase_frames:
            return

        if self.phase == "color":
            self.phase = "transition"
            self.phase_frame = 0
            return

        self.grid = [row[:] for row in self.next_grid]
        self.natural_grid = [row[:] for row in self.next_natural_grid]
        self.generation += 1
        self.alive_cells = self._count_alive(self.grid)

        if self.alive_cells == self.previous_population:
            self.stagnation_counter += 1
        else:
            self.stagnation_counter = 0
        self.previous_population = self.alive_cells

        stagnation_limit = int(self.params.get("stagnation_generations", 120) or 0)
        if self.alive_cells == 0 or (stagnation_limit > 0 and self.stagnation_counter >= stagnation_limit):
            self._initialize_grid(None)
            return

        self.phase = "color"
        self.phase_frame = 0
        self._compute_next_state()

    def _compute_next_state(self):
        wrap = bool(self.params.get("wrap_edges", True))
        self.next_grid = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.next_natural_grid = [[None for _ in range(self.width)] for _ in range(self.height)]
        self.neighbor_counts = [[0 for _ in range(self.width)] for _ in range(self.height)]

        births = 0
        deaths = 0

        for y in range(self.height):
            for x in range(self.width):
                neighbors = 0
                color_sum = [0, 0, 0]
                color_count = 0

                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx = x + dx
                        ny = y + dy
                        if wrap:
                            nx %= self.width
                            ny %= self.height
                        if 0 <= nx < self.width and 0 <= ny < self.height and self.grid[ny][nx] > 0:
                            neighbors += 1
                            neighbor_color = self.natural_grid[ny][nx]
                            if neighbor_color is not None:
                                color_sum[0] += neighbor_color[0]
                                color_sum[1] += neighbor_color[1]
                                color_sum[2] += neighbor_color[2]
                                color_count += 1

                self.neighbor_counts[y][x] = neighbors
                alive = self.grid[y][x] > 0

                if alive and neighbors in (2, 3):
                    self.next_grid[y][x] = min(self.grid[y][x] + 1, 20)
                    self.next_natural_grid[y][x] = self.natural_grid[y][x]
                elif (not alive) and neighbors == 3:
                    self.next_grid[y][x] = 1
                    self.next_natural_grid[y][x] = self._blend_neighbor_colors(color_sum, color_count)
                    births += 1
                else:
                    self.next_grid[y][x] = 0
                    self.next_natural_grid[y][x] = None
                    if alive:
                        deaths += 1

        self.births_last_generation = births
        self.deaths_last_generation = deaths

    def _cell_color(self, x: int, y: int) -> Optional[Color]:
        alive_now = self.grid[y][x] > 0
        alive_next = self.next_grid[y][x] > 0
        if not alive_now and not alive_next:
            return None

        neighbors = self.neighbor_counts[y][x]
        neighbor_intensity = 0.65 + (neighbors / 8.0) * 0.55
        neighbor_intensity = max(0.4, min(1.3, neighbor_intensity))

        base_spawn = (0, 255, 0)
        base_die = (255, 40, 20)

        natural_now = self.natural_grid[y][x] or self._random_natural_color()
        natural_next = self.next_natural_grid[y][x] or self._random_natural_color()

        phase_frames = max(1, int(self.params.get("phase_frames", 10) or 10))
        phase_ratio = 0.0 if phase_frames <= 1 else self.phase_frame / (phase_frames - 1)

        if self.phase == "color":
            if not alive_now:
                return None

            if alive_now and not alive_next:
                color = self._blend_colors(natural_now, base_die, phase_ratio)
                return self._clamp_color(
                    (
                        int(color[0] * neighbor_intensity),
                        int(color[1] * neighbor_intensity),
                        int(color[2] * neighbor_intensity),
                    )
                )

            shimmer_phase = (self.generation * phase_frames + self.phase_frame) / max(1.0, phase_frames)
            shimmer = 0.92 + 0.08 * math.sin(shimmer_phase * math.tau)
            return self._clamp_color(
                (
                    int(natural_now[0] * neighbor_intensity * shimmer),
                    int(natural_now[1] * neighbor_intensity * shimmer),
                    int(natural_now[2] * neighbor_intensity * shimmer),
                )
            )

        if alive_now and alive_next:
            return self._clamp_color(
                (
                    int(natural_now[0] * neighbor_intensity),
                    int(natural_now[1] * neighbor_intensity),
                    int(natural_now[2] * neighbor_intensity),
                )
            )

        if alive_now and not alive_next:
            fade = 1.0 - phase_ratio
            return self._clamp_color(
                (
                    int(base_die[0] * neighbor_intensity * fade),
                    int(base_die[1] * neighbor_intensity * fade),
                    int(base_die[2] * neighbor_intensity * fade),
                )
            )

        if (not alive_now) and alive_next:
            fade = phase_ratio
            color = self._blend_colors(base_spawn, natural_next, phase_ratio)
            return self._clamp_color(
                (
                    int(color[0] * neighbor_intensity * fade),
                    int(color[1] * neighbor_intensity * fade),
                    int(color[2] * neighbor_intensity * fade),
                )
            )

        return None

    def _blend_neighbor_colors(self, color_sum: List[int], color_count: int) -> Color:
        if color_count <= 0:
            return self._random_natural_color()

        if color_count == 1:
            return self._clamp_color((color_sum[0], color_sum[1], color_sum[2]))

        return self._clamp_color(
            (
                int(color_sum[0] / color_count),
                int(color_sum[1] / color_count),
                int(color_sum[2] / color_count),
            )
        )

    def _blend_colors(self, first: Color, second: Color, ratio: float) -> Color:
        ratio = max(0.0, min(1.0, ratio))
        inv = 1.0 - ratio
        return (
            int(first[0] * inv + second[0] * ratio),
            int(first[1] * inv + second[1] * ratio),
            int(first[2] * inv + second[2] * ratio),
        )

    def _random_natural_color(self) -> Color:
        return (
            self.random.randint(40, 255),
            self.random.randint(40, 255),
            self.random.randint(40, 255),
        )

    def _clamp_color(self, color: Color) -> Color:
        return (
            max(0, min(255, int(color[0]))),
            max(0, min(255, int(color[1]))),
            max(0, min(255, int(color[2]))),
        )

    def _spawn_glider(self, count: int = 1):
        if self.width < 3 or self.height < 3:
            return

        glider = [(1, 0), (2, 1), (0, 2), (1, 2), (2, 2)]
        max_origin_x = max(0, self.width - 3)
        max_origin_y = max(0, self.height - 3)
        spawn_count = max(1, int(count))

        used_origins = set()
        attempts = 0
        while len(used_origins) < spawn_count and attempts < spawn_count * 8:
            origin_x = self.random.randint(0, max_origin_x)
            origin_y = self.random.randint(0, max_origin_y)
            attempts += 1

            if (origin_x, origin_y) in used_origins:
                continue

            used_origins.add((origin_x, origin_y))
            glider_color = self._random_natural_color()
            for dx, dy in glider:
                x = origin_x + dx
                y = origin_y + dy
                if 0 <= x < self.width and 0 <= y < self.height:
                    self.grid[y][x] = 1
                    self.natural_grid[y][x] = glider_color

        self.phase = "color"
        self.phase_frame = 0
        self.frame_progress = 0.0
        self._compute_next_state()

    def _count_alive(self, target_grid: List[List[int]]) -> int:
        alive = 0
        for row in target_grid:
            for value in row:
                if value > 0:
                    alive += 1
        return alive

    def _set_pixel(self, frame: List[Color], strip: int, led: int, color: Color):
        if strip < 0 or strip >= self.num_strips:
            return

        if led < 0 or led >= self.leds_per_strip:
            return

        # LED columns are physically reversed on each strip in this layout.
        phys_led = (self.leds_per_strip - 1) - led
        idx = strip * self.leds_per_strip + phys_led
        if 0 <= idx < len(frame):
            frame[idx] = self.apply_brightness(color)
