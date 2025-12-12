#!/usr/bin/env python3
"""
Fluid Tank Simulation Animation

A water tank fills from the top with single-pixel drops, ripples on impact,
occasionally breathes bubbles from the floor, and drains through a punctured
hole once full.
"""

import math
import random
from typing import List, Tuple, Dict, Optional, Any
from animation_system import AnimationBase
from led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


class FluidTankAnimation(AnimationBase):
    """Fluid simulation with falling drops, ripples, bubbles, and a draining hole"""

    ANIMATION_NAME = "Fluid Tank"
    ANIMATION_DESCRIPTION = "Realistic water fill with ripples, bubbles, and a draining breach"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.default_params.update({
            'speed': 1.0,
            'drop_rate': 3.0,           # drops per second
            'flow_steps': 2,            # physics iterations per frame
            'bubble_interval': 2.4,     # seconds between bubbles
            'bubble_strength': 1.2,     # ripple strength when a bubble surfaces
            'ripple_damping': 0.985,    # how quickly ripples fade
            'ripple_speed': 0.28,       # wave propagation speed
            'surface_shimmer': 0.35,    # extra sparkle on surface waves
            'foam_bias': 0.25,          # how quickly cresting waves turn white
            'full_threshold': 0.94,     # fill percentage that triggers a hole
            'hole_flash_duration': 0.45,
            'hole_cooldown': 2.0,
            'target_drain_time': 3.0,   # seconds to drain full tank when punctured
            'serpentine': False
        })

        self.params = {**self.default_params, **self.config}

        self.width = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.height = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)

        self.water: List[List[int]] = []
        self.ripple_height: List[List[float]] = []
        self.ripple_velocity: List[List[float]] = []
        self.pending_ripples: List[Tuple[int, int, float]] = []

        self.drop_accumulator = 0.0
        self.last_time = None

        self.bubbles: List[Dict[str, float]] = []
        self.time_since_bubble = 0.0

        self.hole_active = False
        self.hole_position: Tuple[float, float] = (0.0, 0.0)
        self.hole_radius = 1.5  # 3px diameter
        self.last_drain_time = 0.0
        self.hole_flash_timer = 0.0
        self.hole_cooldown_timer = 0.0
        self.drain_reservoir = 0.0  # carries fractional drain budget across frames
        self.drain_reference_volume = 0.0  # total volume at puncture time
        self.hole_open_time = 0.0

        self._reset_state()

    def start(self):
        super().start()
        self.width = getattr(self.controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.height = getattr(self.controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self._reset_state()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'drop_rate': {'type': 'float', 'min': 0.2, 'max': 60.0, 'default': 3.0, 'description': 'Drops per second'},
            'flow_steps': {'type': 'int', 'min': 1, 'max': 8, 'default': 2, 'description': 'Physics iterations per frame'},
            'bubble_interval': {'type': 'float', 'min': 0.3, 'max': 8.0, 'default': 2.4, 'description': 'Seconds between bottom bubbles'},
            'bubble_strength': {'type': 'float', 'min': 0.2, 'max': 2.5, 'default': 1.2, 'description': 'Ripple energy from surfacing bubbles'},
            'ripple_damping': {'type': 'float', 'min': 0.90, 'max': 0.999, 'default': 0.985, 'description': 'How quickly ripples fade'},
            'ripple_speed': {'type': 'float', 'min': 0.05, 'max': 1.2, 'default': 0.28, 'description': 'Wave propagation speed'},
            'surface_shimmer': {'type': 'float', 'min': 0.0, 'max': 1.5, 'default': 0.35, 'description': 'Extra sparkle on surface crests'},
            'foam_bias': {'type': 'float', 'min': 0.0, 'max': 1.0, 'default': 0.25, 'description': 'How quickly ripples turn to foam'},
            'full_threshold': {'type': 'float', 'min': 0.5, 'max': 1.0, 'default': 0.94, 'description': 'Fill level that triggers draining'},
            'hole_flash_duration': {'type': 'float', 'min': 0.1, 'max': 2.0, 'default': 0.45, 'description': 'Flash duration when the hole seals'},
            'hole_cooldown': {'type': 'float', 'min': 0.5, 'max': 10.0, 'default': 2.0, 'description': 'Delay before another puncture'},
            'target_drain_time': {'type': 'float', 'min': 0.5, 'max': 10.0, 'default': 3.0, 'description': 'Seconds to drain the full tank once punctured'},
            'serpentine': {'type': 'bool', 'default': False, 'description': 'Flip every other strip for serpentine wiring'}
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        if self.last_time is None:
            self.last_time = time_elapsed
        dt = max(0.005, min(0.05, time_elapsed - self.last_time))
        self.last_time = time_elapsed

        speed = max(0.1, float(self.params.get('speed', 1.0)))
        dt_scaled = dt * speed

        self._maybe_puncture_hole(time_elapsed)

        prev_water = [row[:] for row in self.water]
        self._spawn_drops(dt_scaled, speed)
        if self.hole_active:
            self._apply_hole(dt_scaled, time_elapsed)

        flow_steps = max(1, int(round(self.params.get('flow_steps', 2) * speed)))
        for _ in range(flow_steps):
            self._flow_iteration()
            if self.hole_active:
                self._apply_hole(dt_scaled, time_elapsed)

        self._collect_impacts(prev_water)
        self._inject_ripples()
        self._update_ripples(dt_scaled)
        self._update_bubbles(dt_scaled, time_elapsed)
        self._update_hole_timers(dt_scaled, time_elapsed)

        return self._render_frame(time_elapsed)

    def _reset_state(self):
        self.water = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.ripple_height = [[0.0 for _ in range(self.width)] for _ in range(self.height)]
        self.ripple_velocity = [[0.0 for _ in range(self.width)] for _ in range(self.height)]
        self.pending_ripples = []
        self.drop_accumulator = 0.0
        self.last_time = None
        self.bubbles = []
        self.time_since_bubble = 0.0
        self.hole_active = False
        self.hole_flash_timer = 0.0
        self.hole_cooldown_timer = 0.0
        self.last_drain_time = 0.0
        self.drain_reservoir = 0.0
        self.drain_reference_volume = 0.0
        self.hole_open_time = 0.0

    def _spawn_drops(self, dt: float, speed: float):
        rate = max(0.5, float(self.params.get('drop_rate', 3.0))) * speed
        self.drop_accumulator += dt * rate
        while self.drop_accumulator >= 1.0:
            self.drop_accumulator -= 1.0
            self._add_water_pixel(random.randrange(self.width))

    def _add_water_pixel(self, x: int):
        for y in range(self.height):
            if self._is_hole_cell(x, y):
                continue
            if self.water[y][x] == 0:
                self.water[y][x] = 1
                break

    def _flow_iteration(self):
        width, height = self.width, self.height
        new_grid = [row[:] for row in self.water]
        coords = [(x, y) for y in range(height) for x in range(width) if self.water[y][x]]
        random.shuffle(coords)

        for x, y in coords:
            if self._is_hole_cell(x, y):
                new_grid[y][x] = 0
                continue

            if new_grid[y][x] == 0:
                continue

            moved = False

            def try_move(nx: int, ny: int) -> bool:
                if 0 <= nx < width and 0 <= ny < height and new_grid[ny][nx] == 0 and not self._is_hole_cell(nx, ny):
                    new_grid[y][x] = 0
                    new_grid[ny][nx] = 1
                    return True
                return False

            if try_move(x, y + 1):
                continue

            diagonals = [(x - 1, y + 1), (x + 1, y + 1)]
            random.shuffle(diagonals)
            for nx, ny in diagonals:
                if try_move(nx, ny):
                    moved = True
                    break
            if moved:
                continue

            below_full = y + 1 >= height or new_grid[y + 1][x] == 1 or self._is_hole_cell(x, y + 1)
            if below_full:
                lateral = [(x - 1, y), (x + 1, y)]
                random.shuffle(lateral)
                for nx, ny in lateral:
                    if 0 <= nx < width and new_grid[ny][nx] == 0:
                        support = ny + 1 >= height or new_grid[ny + 1][nx] == 1 or self._is_hole_cell(nx, ny + 1)
                        if support:
                            if try_move(nx, ny):
                                break

        self.water = new_grid

    def _collect_impacts(self, prev_water: List[List[int]]):
        height = self.height
        width = self.width
        for y in range(height):
            for x in range(width):
                if self.water[y][x] and prev_water[y][x] == 0:
                    supported = y + 1 >= height or self.water[y + 1][x] == 1 or self._is_hole_cell(x, y + 1)
                    if supported:
                        depth_factor = 1.0 - (y / max(1, height - 1))
                        self._queue_ripple(x, y, 0.65 + 0.6 * depth_factor)

    def _queue_ripple(self, x: int, y: int, strength: float):
        self.pending_ripples.append((x, y, strength))

    def _inject_ripples(self):
        if not self.pending_ripples:
            return
        spread = [(0, 0, 1.0), (1, 0, 0.35), (-1, 0, 0.35), (0, 1, 0.35), (0, -1, 0.35)]
        for x, y, strength in self.pending_ripples:
            for dx, dy, falloff in spread:
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.width and 0 <= ny < self.height:
                    self.ripple_velocity[ny][nx] += strength * falloff
        self.pending_ripples = []

    def _update_ripples(self, dt: float):
        damping = float(self.params.get('ripple_damping', 0.985))
        wave_speed = float(self.params.get('ripple_speed', 0.28))
        height_prev = [row[:] for row in self.ripple_height]
        velocity_prev = [row[:] for row in self.ripple_velocity]

        new_height = [[0.0 for _ in range(self.width)] for _ in range(self.height)]
        new_velocity = [[0.0 for _ in range(self.width)] for _ in range(self.height)]

        for y in range(self.height):
            for x in range(self.width):
                if self.water[y][x]:
                    neighbor_sum = 0.0
                    count = 0
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < self.width and 0 <= ny < self.height:
                            neighbor_sum += height_prev[ny][nx]
                            count += 1
                    avg = neighbor_sum / count if count else 0.0
                    vel = velocity_prev[y][x] + (avg - height_prev[y][x]) * wave_speed
                    vel *= damping
                    new_velocity[y][x] = vel
                    new_height[y][x] = height_prev[y][x] + vel
                else:
                    new_velocity[y][x] = velocity_prev[y][x] * 0.85
                    new_height[y][x] = height_prev[y][x] * 0.85

        self.ripple_velocity = new_velocity
        self.ripple_height = new_height

    def _surface_y(self, x: int) -> Optional[int]:
        for y in range(self.height):
            if self.water[y][x]:
                return y
        return None

    def _update_bubbles(self, dt: float, time_elapsed: float):
        self.time_since_bubble += dt
        interval = max(0.3, float(self.params.get('bubble_interval', 2.4)))
        if self.time_since_bubble >= interval and self._fill_ratio() > 0.2:
            self.time_since_bubble = 0.0
            self.bubbles.append({
                'x': random.uniform(0, self.width - 1),
                'y': self.height - 0.1,
                'vy': -0.45
            })

        active_bubbles = []
        for bubble in self.bubbles:
            col = max(0, min(self.width - 1, int(round(bubble['x']))))
            surface = self._surface_y(col)
            if surface is None:
                continue

            bubble['vy'] -= 3.2 * dt
            bubble['vy'] = max(bubble['vy'], -3.0)
            bubble['y'] += bubble['vy'] * dt

            if bubble['y'] <= surface - 0.1:
                self._queue_ripple(col, max(0, surface - 1), float(self.params.get('bubble_strength', 1.2)))
                continue

            bubble['y'] = min(bubble['y'], self.height - 0.2)
            active_bubbles.append(bubble)

        self.bubbles = active_bubbles

    def _fill_ratio(self) -> float:
        total = self.width * self.height
        if total <= 0:
            return 0.0
        filled = sum(sum(row) for row in self.water)
        return filled / total

    def _maybe_puncture_hole(self, time_elapsed: float):
        if self.hole_active or self.hole_flash_timer > 0.0 or self.hole_cooldown_timer > 0.0:
            return
        if self._fill_ratio() < float(self.params.get('full_threshold', 0.94)):
            return

        y_min = max(0, int(self.height * 0.9))
        cy = random.randint(y_min, max(y_min, self.height - 1))
        margin = max(2, int(math.ceil(self.hole_radius)))
        if self.width > margin * 2:
            cx = random.randint(margin, self.width - margin - 1)
        else:
            cx = random.randint(0, self.width - 1)

        self.hole_position = (float(cx), float(cy))
        self.hole_active = True
        self.last_drain_time = time_elapsed
        self.drain_reservoir = 0.0
        self.drain_reference_volume = max(1, sum(sum(row) for row in self.water))
        self.hole_open_time = time_elapsed
        self._queue_ripple(cx, cy, 1.4)

    def _apply_hole(self, dt: float, time_elapsed: float):
        drained = False
        cx, cy = self.hole_position
        r2 = self.hole_radius * self.hole_radius
        filled_positions = []

        for y in range(self.height):
            dy = y - cy
            if abs(dy) > self.hole_radius:
                continue
            for x in range(self.width):
                dx = x - cx
                if dx * dx + dy * dy <= r2:
                    if self.water[y][x]:
                        filled_positions.append((x, y))
                    self.ripple_height[y][x] *= 0.55
                    self.ripple_velocity[y][x] *= 0.55

        total_water = sum(sum(row) for row in self.water)
        if total_water > 0 and filled_positions:
            target_time = max(0.5, float(self.params.get('target_drain_time', 3.0)))
            reference = max(total_water, self.drain_reference_volume or total_water)
            drain_rate = reference / target_time  # cells per second
            self.drain_reservoir += drain_rate * dt
            allowed = min(len(filled_positions), int(self.drain_reservoir))
            if allowed > 0:
                random.shuffle(filled_positions)
                for x, y in filled_positions[:allowed]:
                    if self.water[y][x]:
                        self.water[y][x] = 0
                        drained = True
                self.drain_reservoir -= allowed

        if drained:
            self.last_drain_time = time_elapsed

    def _update_hole_timers(self, dt: float, time_elapsed: float):
        if self.hole_active:
            remaining = self._hole_water_count()
            min_open = max(0.5, float(self.params.get('target_drain_time', 3.0)))
            if remaining == 0 and (time_elapsed - self.last_drain_time) > 0.35 and (time_elapsed - self.hole_open_time) >= min_open:
                self._trigger_hole_flash()

        if self.hole_flash_timer > 0.0:
            self.hole_flash_timer = max(0.0, self.hole_flash_timer - dt)
        if self.hole_cooldown_timer > 0.0:
            self.hole_cooldown_timer = max(0.0, self.hole_cooldown_timer - dt)

    def _trigger_hole_flash(self):
        self.hole_active = False
        self.hole_flash_timer = float(self.params.get('hole_flash_duration', 0.45))
        self.hole_cooldown_timer = max(self.hole_cooldown_timer, float(self.params.get('hole_cooldown', 2.0)))
        self.drain_reservoir = 0.0
        cx, cy = self.hole_position
        self._queue_ripple(int(round(cx)), int(round(cy)), 1.8)

    def _is_hole_cell(self, x: int, y: int) -> bool:
        if not self.hole_active:
            return False
        cx, cy = self.hole_position
        dx = x - cx
        dy = y - cy
        return dx * dx + dy * dy <= self.hole_radius * self.hole_radius

    def _hole_water_count(self) -> int:
        if not self.hole_active:
            return 0
        cx, cy = self.hole_position
        r2 = self.hole_radius * self.hole_radius
        count = 0
        for y in range(self.height):
            dy = y - cy
            if abs(dy) > self.hole_radius:
                continue
            for x in range(self.width):
                dx = x - cx
                if dx * dx + dy * dy <= r2 and self.water[y][x]:
                    count += 1
        return count

    def _hole_visual_intensity(self, x: int, y: int, time_elapsed: float) -> float:
        cx, cy = self.hole_position
        dx = x - cx
        dy = y - cy
        dist2 = dx * dx + dy * dy
        r2 = self.hole_radius * self.hole_radius
        if dist2 > r2 * 1.4:
            return 0.0
        if self.hole_active:
            return 1.0 - min(1.0, dist2 / (r2 * 1.4))
        if self.hole_flash_timer > 0.0:
            flash_phase = (self.hole_flash_timer / max(0.0001, float(self.params.get('hole_flash_duration', 0.45)))) * math.pi * 2.0
            flash = 0.6 + 0.4 * math.sin(flash_phase)
            return flash * (1.0 - min(1.0, dist2 / (r2 * 1.4)))
        return 0.0

    def _is_surface_cell(self, x: int, y: int) -> bool:
        return self.water[y][x] and (y == 0 or self.water[y - 1][x] == 0)

    def _render_frame(self, time_elapsed: float) -> List[Tuple[int, int, int]]:
        width, height = self.width, self.height
        serpentine = bool(self.params.get('serpentine', False))

        bubble_cells = {}
        for bubble in self.bubbles:
            bx = max(0, min(width - 1, int(round(bubble['x']))))
            by = max(0, min(height - 1, int(round(bubble['y']))))
            bubble_cells[(bx, by)] = True

        pixels: List[Tuple[int, int, int]] = [(0, 0, 0)] * (width * height)

        air_color = (1, 2, 5)
        deep_water = (6, 40, 80)
        surface_water = (70, 160, 255)
        foam_color = (210, 235, 255)
        hole_flash_color = (140, 220, 255)

        shimmer = float(self.params.get('surface_shimmer', 0.35))
        foam_bias = float(self.params.get('foam_bias', 0.25))

        for y in range(height):
            depth_factor = y / max(1, height - 1)
            base_water = self._mix_color(surface_water, deep_water, depth_factor * 0.7)
            for x in range(width):
                mapped_x = x if not (serpentine and y % 2 == 1) else (width - 1 - x)
                idx = y * width + mapped_x

                hole_intensity = self._hole_visual_intensity(x, y, time_elapsed)
                if hole_intensity > 0.0:
                    color = self._mix_color((0, 0, 0), hole_flash_color, min(1.0, hole_intensity))
                    pixels[idx] = self.apply_brightness(color)
                    continue

                if self.water[y][x]:
                    wave = self.ripple_height[y][x]
                    surface = self._is_surface_cell(x, y)
                    crest_boost = shimmer * max(0.0, wave)
                    brightness = 1.0 + wave * 0.9 + (0.2 if surface else 0.0) + crest_boost
                    color = self._scale_color(base_water, brightness)

                    if surface and abs(wave) > 0.18:
                        foam_mix = min(1.0, foam_bias + abs(wave) * 0.8)
                        color = self._mix_color(color, foam_color, foam_mix)

                    if (x, y) in bubble_cells:
                        color = self._mix_color(color, (150, 230, 255), 0.7)
                else:
                    color = air_color

                pixels[idx] = self.apply_brightness(color)

        return pixels

    @staticmethod
    def _mix_color(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
        t = max(0.0, min(1.0, t))
        return (
            int(a[0] * (1.0 - t) + b[0] * t),
            int(a[1] * (1.0 - t) + b[1] * t),
            int(a[2] * (1.0 - t) + b[2] * t)
        )

    @staticmethod
    def _scale_color(color: Tuple[int, int, int], scale: float) -> Tuple[int, int, int]:
        scale = max(0.0, scale)
        return (
            max(0, min(255, int(color[0] * scale))),
            max(0, min(255, int(color[1] * scale))),
            max(0, min(255, int(color[2] * scale)))
        )
