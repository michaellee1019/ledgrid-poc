#!/usr/bin/env python3
"""Conway's Game of Life animation for the LED grid."""

import random
from typing import List, Tuple, Dict, Any, Optional, Iterable

from animation import AnimationBase
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


Color = Tuple[int, int, int]


class ConwayLifeAnimation(AnimationBase):
    """Classic Conway's Game of Life with optional user-seeded starts."""

    ANIMATION_NAME = "Conway's Game of Life"
    ANIMATION_DESCRIPTION = "Cellular automaton with editable starting patterns"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.num_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.width = max(1, self.num_strips)
        self.height = max(1, self.leds_per_strip)

        self.default_params.update({
            'speed': 1.0,
            'wrap_edges': True,
            'random_density': 0.14,
        })
        self.params = {**self.default_params, **self.config}

        self.random = random.Random()
        self.grid: List[List[int]] = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.next_grid: List[List[int]] = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.neighbor_counts: List[List[int]] = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.generation = 0
        self.last_step_elapsed: Optional[float] = None
        self.phase = 'color'
        self.phase_frame = 0
        self.phase_frames = 10
        self.frame_progress = 0.0
        self.target_fps = 100.0
        self.glider_interval = 10.0
        self.last_glider_time = 0.0
        self._initialize_grid(self.params.get('seed_cells'))

    def get_parameter_schema(self) -> Dict[str, Any]:
        schema = super().get_parameter_schema()
        schema.update({
            'wrap_edges': {
                'type': 'bool',
                'default': True,
                'description': 'Wrap edges so patterns flow around the panel'
            },
            'random_density': {
                'type': 'float',
                'min': 0.0,
                'max': 0.4,
                'default': 0.14,
                'description': 'Spawn density when no seed cells are provided'
            },
        })
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        params = dict(new_params)
        spawn_glider = params.pop('spawn_glider', None)
        super().update_parameters(params)
        if 'seed_cells' in params:
            self._initialize_grid(params.get('seed_cells'))
        elif 'wrap_edges' in params:
            self._compute_next_state()
        if spawn_glider:
            self._spawn_glider(count=5)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Color]:
        total_pixels = self.num_strips * self.leds_per_strip
        frame: List[Color] = [(0, 0, 0)] * total_pixels

        step_interval = self._step_interval()
        if self.glider_interval > 0 and (time_elapsed - self.last_glider_time) >= self.glider_interval:
            self._spawn_glider(count=5)
            self.last_glider_time = time_elapsed
        if self.last_step_elapsed is None:
            self.last_step_elapsed = time_elapsed
        else:
            delta = max(0.0, time_elapsed - self.last_step_elapsed)
            self.last_step_elapsed = time_elapsed
            self.frame_progress += delta / step_interval
            steps = 0
            while self.frame_progress >= 1.0 and steps < 30:
                self._advance_phase()
                self.frame_progress -= 1.0
                steps += 1

        for y in range(self.height):
            row = self.grid[y]
            for x in range(self.width):
                color = self._cell_color(x, y)
                if color:
                    self._set_pixel(frame, x, y, color)

        return frame

    def _initialize_grid(self, seed_cells: Optional[Iterable[Any]]):
        self.grid = [[0 for _ in range(self.width)] for _ in range(self.height)]
        has_explicit_seed = seed_cells is not None
        parsed = self._parse_seed_cells(seed_cells)
        if has_explicit_seed:
            for x, y in parsed:
                if 0 <= x < self.width and 0 <= y < self.height:
                    self.grid[y][x] = 1
        else:
            density = float(self.params.get('random_density', 0.14) or 0.0)
            density = max(0.0, min(0.4, density))
            if density > 0:
                for y in range(self.height):
                    for x in range(self.width):
                        if self.random.random() < density:
                            self.grid[y][x] = 1
        self.generation = 0
        self.last_step_elapsed = None
        self.phase = 'color'
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
                    parsed.append((int(item.get('x')), int(item.get('y'))))
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, str) and ',' in item:
                parts = item.split(',')
                if len(parts) >= 2:
                    try:
                        parsed.append((int(parts[0].strip()), int(parts[1].strip())))
                    except (TypeError, ValueError):
                        continue
        return parsed

    def _step_interval(self) -> float:
        speed = max(0.1, float(self.params.get('speed', 1.0)))
        target_fps = self.target_fps * speed
        return 1.0 / max(1.0, target_fps)

    def _advance_phase(self):
        self.phase_frame += 1
        if self.phase_frame < self.phase_frames:
            return
        if self.phase == 'color':
            self.phase = 'transition'
            self.phase_frame = 0
            return
        self.grid = [row[:] for row in self.next_grid]
        self.generation += 1
        self.phase = 'color'
        self.phase_frame = 0
        self._compute_next_state()

    def _compute_next_state(self):
        wrap = bool(self.params.get('wrap_edges', True))
        self.next_grid = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.neighbor_counts = [[0 for _ in range(self.width)] for _ in range(self.height)]
        for y in range(self.height):
            for x in range(self.width):
                neighbors = self._count_neighbors(x, y, wrap)
                self.neighbor_counts[y][x] = neighbors
                alive = self.grid[y][x] > 0
                if alive and neighbors in (2, 3):
                    self.next_grid[y][x] = min(self.grid[y][x] + 1, 20)
                elif (not alive) and neighbors == 3:
                    self.next_grid[y][x] = 1
                else:
                    self.next_grid[y][x] = 0

    def _count_neighbors(self, x: int, y: int, wrap: bool) -> int:
        count = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx = x + dx
                ny = y + dy
                if wrap:
                    nx %= self.width
                    ny %= self.height
                if 0 <= nx < self.width and 0 <= ny < self.height:
                    if self.grid[ny][nx] > 0:
                        count += 1
        return count

    def _cell_color(self, x: int, y: int) -> Optional[Color]:
        alive_now = self.grid[y][x] > 0
        alive_next = self.next_grid[y][x] > 0
        if not alive_now and not alive_next:
            return None

        neighbors = self.neighbor_counts[y][x]
        neighbor_intensity = 0.65 + (neighbors / 8.0) * 0.55
        neighbor_intensity = max(0.4, min(1.3, neighbor_intensity))

        base_green = (0, 255, 120)
        base_red = (255, 40, 20)
        phase_ratio = 0.0
        if self.phase_frames > 1:
            phase_ratio = self.phase_frame / (self.phase_frames - 1)

        if self.phase == 'color':
            if not alive_now:
                return None
            if alive_now and not alive_next:
                red_mix = phase_ratio
                green_mix = 1.0 - phase_ratio
                color = (
                    int((base_green[0] * green_mix + base_red[0] * red_mix) * neighbor_intensity),
                    int((base_green[1] * green_mix + base_red[1] * red_mix) * neighbor_intensity),
                    int((base_green[2] * green_mix + base_red[2] * red_mix) * neighbor_intensity),
                )
                return self._clamp_color(color)
            color = (
                int(base_green[0] * neighbor_intensity),
                int(base_green[1] * neighbor_intensity),
                int(base_green[2] * neighbor_intensity),
            )
            return self._clamp_color(color)

        if alive_now and alive_next:
            color = (
                int(base_green[0] * neighbor_intensity),
                int(base_green[1] * neighbor_intensity),
                int(base_green[2] * neighbor_intensity),
            )
            return self._clamp_color(color)
        if alive_now and not alive_next:
            fade = 1.0 - phase_ratio
            color = (
                int(base_red[0] * neighbor_intensity * fade),
                int(base_red[1] * neighbor_intensity * fade),
                int(base_red[2] * neighbor_intensity * fade),
            )
            return self._clamp_color(color)
        if (not alive_now) and alive_next:
            fade = phase_ratio
            color = (
                int(base_green[0] * neighbor_intensity * fade),
                int(base_green[1] * neighbor_intensity * fade),
                int(base_green[2] * neighbor_intensity * fade),
            )
            return self._clamp_color(color)
        return None

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
            for dx, dy in glider:
                x = origin_x + dx
                y = origin_y + dy
                if 0 <= x < self.width and 0 <= y < self.height:
                    self.grid[y][x] = 1
        self.phase = 'color'
        self.phase_frame = 0
        self.frame_progress = 0.0
        self._compute_next_state()

    def _set_pixel(self, frame: List[Color], strip: int, led: int, color: Color):
        if strip < 0 or strip >= self.num_strips:
            return
        if led < 0 or led >= self.leds_per_strip:
            return
        phys_led = (self.leds_per_strip - 1) - led
        idx = strip * self.leds_per_strip + phys_led
        if 0 <= idx < len(frame):
            frame[idx] = self.apply_brightness(color)
