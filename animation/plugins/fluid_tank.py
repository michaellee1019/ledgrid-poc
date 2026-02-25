#!/usr/bin/env python3
"""
Fluid Tank Simulation Animation

A water tank fills from the top with single-pixel drops, ripples on impact,
occasionally breathes bubbles from the floor, and drains through a punctured
hole once full.
"""

import math
import random
import time
from typing import List, Tuple, Dict, Optional, Any

import numpy as np

from animation import AnimationBase
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


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
            'drop_rate': 1.0,
            'target_fill_time': 60.0,
            'flow_steps': 2,
            'bubble_interval': 2.4,
            'bubble_strength': 1.2,
            'ripple_damping': 0.985,
            'ripple_speed': 0.28,
            'surface_shimmer': 0.35,
            'foam_bias': 0.25,
            'full_threshold': 0.94,
            'hole_flash_duration': 0.45,
            'hole_cooldown': 2.0,
            'target_drain_time': 3.0,
            'serpentine': False
        })

        self.params = {**self.default_params, **self.config}

        self.panel_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.panel_leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.width = self.panel_strips
        self.height = self.panel_leds_per_strip

        self.water: np.ndarray = np.array([])
        self.ripple_height: np.ndarray = np.array([])
        self.ripple_velocity: np.ndarray = np.array([])
        self.pending_ripples: List[Tuple[int, int, float]] = []

        self.drop_accumulator = 0.0
        self.last_time = None

        self.bubbles: List[Dict[str, float]] = []
        self.time_since_bubble = 0.0

        self.hole_active = False
        self.hole_position: Tuple[float, float] = (0.0, 0.0)
        self.hole_radius = 1.5
        self.last_drain_time = 0.0
        self.hole_flash_timer = 0.0
        self.hole_cooldown_timer = 0.0
        self.drain_reservoir = 0.0
        self.drain_reference_volume = 0.0
        self.hole_open_time = 0.0
        self.fill_cycle_start_time = 0.0
        self.fill_cycle_initialized = False
        self.awaiting_cycle_reset = False
        self.fill_correction_rate = 0.0
        self.last_fill_stats: Dict[str, Any] = {}
        self.last_stats: Dict[str, Any] = {}
        self.spray_particles: List[Dict[str, float]] = []
        self.drop_glow: List[Dict[str, float]] = []
        self.max_bubble_rise = 0.0
        self.last_spray_time = 0.0
        self.last_manual_hole_time = 0.0

        self._reset_state()

    def start(self):
        super().start()
        self.panel_strips = getattr(self.controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.panel_leds_per_strip = getattr(self.controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.width = self.panel_strips
        self.height = self.panel_leds_per_strip
        self._reset_state()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'drop_rate': {'type': 'float', 'min': 0.1, 'max': 20.0, 'default': 1.0, 'description': 'Fill-speed multiplier (0.5≈2 min calm fill, 1.0≈60s, 5+ = torrential downpour)'},
            'target_fill_time': {'type': 'float', 'min': 5.0, 'max': 600.0, 'default': 60.0, 'description': 'Seconds for a full tank when drop_rate=1. Longer = meditative trickle, shorter = chaotic storm.'},
            'flow_steps': {'type': 'int', 'min': 1, 'max': 8, 'default': 2, 'description': 'Physics passes per frame. Low = chunky, High = buttery smooth (at the expense of CPU).'},
            'bubble_interval': {'type': 'float', 'min': 0.3, 'max': 8.0, 'default': 2.4, 'description': 'Seconds between bubble spawns. Smaller values create frothy aeration, higher values feel calm.'},
            'bubble_strength': {'type': 'float', 'min': 0.2, 'max': 2.5, 'default': 1.2, 'description': 'Ripple energy from surfacing bubbles (0.2 = gentle, 2.5 = geyser).'},
            'ripple_damping': {'type': 'float', 'min': 0.90, 'max': 0.999, 'default': 0.985, 'description': 'How quickly waves fade. Lower = choppy, higher = glassy, longer-lasting ripples.'},
            'ripple_speed': {'type': 'float', 'min': 0.05, 'max': 1.2, 'default': 0.28, 'description': 'Wave propagation speed through the liquid body.'},
            'surface_shimmer': {'type': 'float', 'min': 0.0, 'max': 1.5, 'default': 0.35, 'description': 'Extra sparkle on surface crests. Higher adds glittery highlights.'},
            'foam_bias': {'type': 'float', 'min': 0.0, 'max': 1.0, 'default': 0.25, 'description': 'How quickly ripples turn into white foam. 0 = crystal clear, 1 = foamy.'},
            'full_threshold': {'type': 'float', 'min': 0.5, 'max': 1.0, 'default': 0.94, 'description': 'Fill level that triggers draining (1.0 = brim, 0.8 = earlier breaches).'},
            'hole_flash_duration': {'type': 'float', 'min': 0.1, 'max': 2.0, 'default': 0.45, 'description': 'How long the sealing flash lingers after a hole closes.'},
            'hole_cooldown': {'type': 'float', 'min': 0.5, 'max': 10.0, 'default': 2.0, 'description': 'Minimum seconds before another puncture may spawn automatically.'},
            'target_drain_time': {'type': 'float', 'min': 0.5, 'max': 10.0, 'default': 3.0, 'description': 'Seconds to drain the entire tank once punctured.'},
            'serpentine': {'type': 'bool', 'default': False, 'description': 'Flip every other strip for serpentine wiring'}
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        if self.last_time is None:
            dt_real = 1.0 / 30.0
        else:
            dt_real = max(0.0, time_elapsed - self.last_time)
        self.last_time = time_elapsed

        dt_physics = max(0.005, min(0.05, dt_real if dt_real > 0.0 else 1.0 / 60.0))

        speed = max(0.1, float(self.params.get('speed', 1.0)))
        dt_scaled = dt_physics * speed

        drop_dt = min(max(dt_real, 0.0), 1.0)
        if drop_dt <= 0.0:
            drop_dt = dt_physics
        spawn_budget = drop_dt

        fill_stats = self._update_fill_guidance(time_elapsed)
        self._maybe_puncture_hole(time_elapsed)

        prev_water = self.water.copy()
        spawn_allowed = bool(fill_stats.get('spawn_allowed', True)) and not self.hole_active
        if spawn_allowed:
            self._spawn_drops(spawn_budget)
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
        self._update_drop_glow(dt_physics)
        self._update_spray_particles(dt_physics)
        self._update_hole_timers(dt_scaled, time_elapsed)
        self._snapshot_stats(
            time_elapsed=time_elapsed,
            dt_real=dt_real,
            dt_physics=dt_physics,
            drop_dt=drop_dt,
            spawn_budget=spawn_budget,
            speed=speed,
            flow_steps=flow_steps,
            fill_stats=fill_stats
        )

        return self._render_frame(time_elapsed)

    def _reset_state(self):
        self.water = np.zeros((self.height, self.width), dtype=np.int8)
        self.ripple_height = np.zeros((self.height, self.width), dtype=np.float32)
        self.ripple_velocity = np.zeros((self.height, self.width), dtype=np.float32)
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
        self.fill_cycle_start_time = 0.0
        self.fill_cycle_initialized = False
        self.awaiting_cycle_reset = False
        self.fill_correction_rate = 0.0
        self.last_fill_stats = {}
        self.last_stats = {}
        self.spray_particles = []
        self.drop_glow = []
        self.max_bubble_rise = 0.0
        self.last_spray_time = 0.0
        self.last_manual_hole_time = 0.0

    def _spawn_drops(self, dt: float):
        fill_time = max(5.0, float(self.params.get('target_fill_time', 60.0)))
        drop_multiplier = max(0.1, float(self.params.get('drop_rate', 1.0)))
        base_rate = (self.width * self.height) / fill_time
        rate = (base_rate + self.fill_correction_rate) * drop_multiplier
        self.drop_accumulator += dt * rate
        while self.drop_accumulator >= 1.0:
            self.drop_accumulator -= 1.0
            self._add_water_pixel(random.randrange(self.width))

    def _add_water_pixel(self, x: int):
        for y in range(self.height):
            if self._is_hole_cell(x, y):
                continue
            if self.water[y, x] == 0:
                self.water[y, x] = 1
                self.drop_glow.append({'x': x, 'y': y, 'life': 0.35, 'max_life': 0.35, 'intensity': 1.0})
                break

    def _flow_iteration(self):
        width, height = self.width, self.height
        new_grid = self.water.copy()
        ys, xs = np.where(self.water)
        coords = list(zip(xs.tolist(), ys.tolist()))
        random.shuffle(coords)

        for x, y in coords:
            if self._is_hole_cell(x, y):
                new_grid[y, x] = 0
                continue

            if new_grid[y, x] == 0:
                continue

            def try_move(nx: int, ny: int) -> bool:
                if 0 <= nx < width and 0 <= ny < height and new_grid[ny, nx] == 0 and not self._is_hole_cell(nx, ny):
                    new_grid[y, x] = 0
                    new_grid[ny, nx] = 1
                    return True
                return False

            if try_move(x, y + 1):
                continue

            diagonals = [(x - 1, y + 1), (x + 1, y + 1)]
            random.shuffle(diagonals)
            moved = False
            for nx, ny in diagonals:
                if try_move(nx, ny):
                    moved = True
                    break
            if moved:
                continue

            below_full = y + 1 >= height or new_grid[y + 1, x] == 1 or self._is_hole_cell(x, y + 1)
            if below_full:
                lateral = [(x - 1, y), (x + 1, y)]
                random.shuffle(lateral)
                for nx, ny in lateral:
                    if 0 <= nx < width and new_grid[ny, nx] == 0:
                        support = ny + 1 >= height or new_grid[ny + 1, nx] == 1 or self._is_hole_cell(nx, ny + 1)
                        if support:
                            if try_move(nx, ny):
                                break

        self.water = new_grid

    def _collect_impacts(self, prev_water: np.ndarray):
        landed = (self.water == 1) & (prev_water == 0)
        if not np.any(landed):
            return
        height = self.height
        supported = np.zeros_like(landed)
        supported[height - 1, :] = landed[height - 1, :]
        supported[:height - 1, :] = landed[:height - 1, :] & (
            (self.water[1:, :] == 1) | self._hole_cell_mask()[:height - 1, :]
        )
        ys, xs = np.where(supported)
        for y, x in zip(ys.tolist(), xs.tolist()):
            depth_factor = 1.0 - (y / max(1, height - 1))
            self._queue_ripple(x, y, 0.65 + 0.6 * depth_factor)

    def _hole_cell_mask(self) -> np.ndarray:
        """Return a boolean (height, width) mask of hole cells."""
        if not self.hole_active:
            return np.zeros((self.height, self.width), dtype=bool)
        cx, cy = self.hole_position
        r2 = self.hole_radius * self.hole_radius
        ys = np.arange(self.height)[:, None]
        xs = np.arange(self.width)[None, :]
        return ((xs - cx) ** 2 + (ys - cy) ** 2) <= r2

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
                    self.ripple_velocity[ny, nx] += strength * falloff
        self.pending_ripples = []

    def _update_ripples(self, dt: float):
        damping = float(self.params.get('ripple_damping', 0.985))
        wave_speed = float(self.params.get('ripple_speed', 0.28))
        h = self.ripple_height
        v = self.ripple_velocity
        water = self.water

        # Compute neighbor average using shifted arrays
        neighbor_sum = np.zeros_like(h)
        count = np.zeros_like(h)

        neighbor_sum[:, 1:] += h[:, :-1]; count[:, 1:] += 1  # left neighbor
        neighbor_sum[:, :-1] += h[:, 1:]; count[:, :-1] += 1  # right neighbor
        neighbor_sum[1:, :] += h[:-1, :]; count[1:, :] += 1   # top neighbor
        neighbor_sum[:-1, :] += h[1:, :]; count[:-1, :] += 1   # bottom neighbor

        avg = np.divide(neighbor_sum, count, out=np.zeros_like(h), where=count > 0)

        water_mask = water.astype(bool)
        new_v = np.where(water_mask,
                         (v + (avg - h) * wave_speed) * damping,
                         v * 0.85)
        new_h = np.where(water_mask,
                         h + new_v,
                         h * 0.85)

        self.ripple_velocity = new_v
        self.ripple_height = new_h

    def _surface_y(self, x: int) -> Optional[int]:
        col = self.water[:, x]
        indices = np.nonzero(col)[0]
        if indices.size > 0:
            return int(indices[0])
        return None

    def _update_bubbles(self, dt: float, time_elapsed: float):
        self.time_since_bubble += dt
        interval = max(0.3, float(self.params.get('bubble_interval', 2.4)))
        fill_ratio = self._fill_ratio()
        if self.time_since_bubble >= interval and fill_ratio > 0.08:
            self.time_since_bubble = 0.0
            bubble = {
                'x': random.uniform(0, self.width - 1),
                'y': self.height - 0.1,
                'vy': -0.6,
                'origin_y': self.height - 0.1
            }
            self.bubbles.append(bubble)

        active_bubbles = []
        for bubble in self.bubbles:
            col = max(0, min(self.width - 1, int(round(bubble['x']))))
            surface = self._surface_y(col)
            if surface is None:
                continue

            bubble['vy'] -= 2.8 * dt
            bubble['vy'] = max(bubble['vy'], -3.2)
            bubble['y'] += bubble['vy'] * dt

            if bubble['y'] <= surface - 0.1:
                rise = bubble.get('origin_y', bubble['y']) - bubble['y']
                if rise > 0:
                    self.max_bubble_rise = max(self.max_bubble_rise, rise)
                self._queue_ripple(col, max(0, surface - 1), float(self.params.get('bubble_strength', 1.2)))
                continue

            bubble['y'] = min(bubble['y'], self.height - 0.2)
            bubble['y'] = max(-1.0, bubble['y'])
            active_bubbles.append(bubble)

        self.bubbles = active_bubbles
    
    def _update_spray_particles(self, dt: float):
        if not self.spray_particles:
            return
        gravity = 1.6
        active: List[Dict[str, float]] = []
        for particle in self.spray_particles:
            particle['vy'] += gravity * dt
            particle['x'] += particle['vx'] * dt
            particle['y'] += particle['vy'] * dt
            particle['life'] -= dt
            if particle['life'] <= 0.0 or particle['y'] < -2.0 or particle['y'] >= self.height + 2:
                continue
            active.append(particle)
        self.spray_particles = active
    
    def _update_drop_glow(self, dt: float):
        if not self.drop_glow:
            return
        updated: List[Dict[str, float]] = []
        for glow in self.drop_glow:
            glow['life'] -= dt
            if glow['life'] <= 0.0:
                continue
            glow['intensity'] = max(0.0, glow['life'] / glow.get('max_life', 0.4))
            updated.append(glow)
        self.drop_glow = updated

    def _fill_ratio(self) -> float:
        total = self.width * self.height
        if total <= 0:
            return 0.0
        return float(np.sum(self.water)) / total

    def _maybe_puncture_hole(self, time_elapsed: float):
        if self.hole_active or self.hole_flash_timer > 0.0 or self.hole_cooldown_timer > 0.0:
            return
        if self._fill_ratio() < float(self.params.get('full_threshold', 0.94)):
            return
        self._activate_hole(time_elapsed)

    def _activate_hole(self, time_elapsed: float, cx: Optional[int] = None, cy: Optional[int] = None, force: bool = False) -> bool:
        if self.hole_active or (not force and (self.hole_flash_timer > 0.0 or self.hole_cooldown_timer > 0.0)):
            return False

        if cx is None or cy is None:
            y_min = max(0, int(self.height * 0.85))
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
        self.drain_reference_volume = max(1, int(np.sum(self.water)))
        self.hole_open_time = time_elapsed
        self.awaiting_cycle_reset = True
        self.fill_correction_rate = 0.0
        self._queue_ripple(int(cx), int(cy), 1.4)
        return True

    def trigger_random_hole(self):
        """External hook for UI/commands to punch a random hole."""
        now = self.last_time if self.last_time is not None else time.time()
        if self._activate_hole(now, force=True):
            self.last_manual_hole_time = now

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
                    if self.water[y, x]:
                        filled_positions.append((x, y))
                    self.ripple_height[y, x] *= 0.55
                    self.ripple_velocity[y, x] *= 0.55

        total_water = int(np.sum(self.water))
        removed_total = 0
        if total_water > 0:
            target_time = max(0.5, float(self.params.get('target_drain_time', 3.0)))
            reference = max(total_water, self.drain_reference_volume or total_water)
            drain_rate = reference / target_time
            self.drain_reservoir += drain_rate * dt
            allowed = min(int(self.drain_reservoir), total_water)
            if allowed > 0:
                self.drain_reservoir -= allowed
                random.shuffle(filled_positions)
                for x, y in filled_positions:
                    if self.water[y, x] and removed_total < allowed:
                        self.water[y, x] = 0
                        removed_total += 1
                if removed_total < allowed:
                    removed_total += self._bulk_drain_water(allowed - removed_total)
                if removed_total > 0:
                    drained = True
                    self._spawn_spray_particles(cx, cy, removed_total)
                    self.last_spray_time = time_elapsed

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
        mask = self._hole_cell_mask()
        return int(np.sum(self.water[mask]))

    def _bulk_drain_water(self, amount: int) -> int:
        if amount <= 0:
            return 0
        removed = 0
        for y in range(self.height - 1, -1, -1):
            for x in range(self.width):
                if self.water[y, x]:
                    self.water[y, x] = 0
                    removed += 1
                    if removed >= amount:
                        return removed
        return removed

    def _spawn_spray_particles(self, cx: float, cy: float, count: int):
        if count <= 0:
            return
        max_particles = min(60, count * 2)
        for _ in range(max_particles):
            particle = {
                'x': cx + random.uniform(-1.2, 1.2),
                'y': cy + random.uniform(-0.5, 0.5),
                'vx': random.uniform(-2.0, 2.0),
                'vy': -random.uniform(3.0, 5.5),
                'life': random.uniform(0.4, 0.9)
            }
            self.spray_particles.append(particle)

    def _snapshot_stats(self, time_elapsed: float, dt_real: float, dt_physics: float,
                        drop_dt: float, spawn_budget: float, speed: float,
                        flow_steps: int, fill_stats: Optional[Dict[str, Any]]):
        total_cells = self.width * self.height
        fill_time = max(5.0, float(self.params.get('target_fill_time', 60.0)))
        drop_multiplier = max(0.1, float(self.params.get('drop_rate', 1.0)))
        base_rate = (total_cells / fill_time) if fill_time > 0 else 0.0
        effective_rate = (base_rate + self.fill_correction_rate) * drop_multiplier

        stats = {
            'time': time_elapsed,
            'dt_real': dt_real,
            'dt_physics': dt_physics,
            'drop_dt': drop_dt,
            'spawn_budget': spawn_budget,
            'speed': speed,
            'flow_steps': flow_steps,
            'fill_ratio': self._fill_ratio(),
            'fill_correction_rate': self.fill_correction_rate,
            'drop_accumulator': self.drop_accumulator,
            'base_drop_rate': base_rate,
            'drop_multiplier': drop_multiplier,
            'effective_drop_rate': effective_rate,
            'width': self.width,
            'height': self.height,
            'total_cells': total_cells,
            'hole_active': self.hole_active,
            'hole_flash_timer': self.hole_flash_timer,
            'hole_cooldown_timer': self.hole_cooldown_timer,
            'awaiting_cycle_reset': self.awaiting_cycle_reset,
            'bubble_count': len(self.bubbles),
            'pending_ripples': len(self.pending_ripples),
            'hole_water_remaining': self._hole_water_count() if self.hole_active else 0,
            'drain_reference_volume': self.drain_reference_volume,
            'spray_particle_count': len(self.spray_particles),
            'max_bubble_rise': self.max_bubble_rise,
            'last_spray_time': self.last_spray_time,
            'last_manual_hole_time': self.last_manual_hole_time,
            'drop_glow_count': len(self.drop_glow)
        }

        if fill_stats:
            stats.update({k: v for k, v in fill_stats.items() if k not in stats})

        if self.bubbles:
            stats['bubble_preview'] = [
                {
                    'x': round(bubble['x'], 2),
                    'y': round(bubble['y'], 2),
                    'vy': round(bubble['vy'], 2)
                }
                for bubble in self.bubbles[:4]
            ]

        if self.spray_particles:
            stats['spray_preview'] = [
                {
                    'x': round(p['x'], 2),
                    'y': round(p['y'], 2),
                    'life': round(p['life'], 2)
                }
                for p in self.spray_particles[:6]
            ]

        self.last_stats = stats

    def _update_fill_guidance(self, time_elapsed: float) -> Dict[str, Any]:
        total_cells = self.width * self.height
        fill_ratio = self._fill_ratio()
        fill_time = max(5.0, float(self.params.get('target_fill_time', 60.0)))

        stats = {
            'time': time_elapsed,
            'total_cells': total_cells,
            'fill_ratio': fill_ratio,
            'fill_cycle_start_time': self.fill_cycle_start_time,
            'fill_cycle_initialized': self.fill_cycle_initialized,
            'awaiting_cycle_reset': self.awaiting_cycle_reset,
            'hole_active': self.hole_active,
            'hole_flash_timer': self.hole_flash_timer,
            'target_fill_time': fill_time,
            'spawn_allowed': True
        }

        if total_cells <= 0:
            self.fill_correction_rate = 0.0
            stats['fill_correction_rate'] = self.fill_correction_rate
            stats['spawn_allowed'] = False
            self.last_fill_stats = stats
            return stats

        if not self.fill_cycle_initialized and not self.hole_active and not self.awaiting_cycle_reset:
            self.fill_cycle_start_time = time_elapsed
            self.fill_cycle_initialized = True
            stats['fill_cycle_start_time'] = self.fill_cycle_start_time

        if self.awaiting_cycle_reset:
            if not self.hole_active and self.hole_flash_timer <= 0.0 and fill_ratio <= 0.05:
                self.fill_cycle_start_time = time_elapsed
                self.awaiting_cycle_reset = False
                self.fill_cycle_initialized = True
                stats['fill_cycle_start_time'] = self.fill_cycle_start_time
                stats['awaiting_cycle_reset'] = False
            else:
                self.fill_correction_rate = 0.0
                stats['fill_correction_rate'] = self.fill_correction_rate
                stats['spawn_allowed'] = False
                self.last_fill_stats = stats
                return stats

        if self.hole_active:
            self.fill_correction_rate = 0.0
            stats['fill_correction_rate'] = self.fill_correction_rate
            stats['spawn_allowed'] = False
            self.last_fill_stats = stats
            return stats

        elapsed = max(0.0, time_elapsed - self.fill_cycle_start_time)
        expected_ratio = min(1.0, elapsed / fill_time)
        stats['fill_cycle_elapsed'] = elapsed
        stats['expected_ratio'] = expected_ratio

        overfill = fill_ratio >= expected_ratio + 0.01
        if overfill:
            self.fill_correction_rate = 0.0
            stats['spawn_allowed'] = False
        else:
            deficit = expected_ratio - fill_ratio
            correction_window = max(fill_time * 0.25, 5.0)
            self.fill_correction_rate = (deficit * total_cells) / correction_window
            stats['spawn_allowed'] = True

        stats['fill_correction_rate'] = self.fill_correction_rate
        stats['overfill'] = overfill
        self.last_fill_stats = stats
        return stats

    def _render_frame(self, time_elapsed: float) -> np.ndarray:
        width, height = self.width, self.height
        serpentine = bool(self.params.get('serpentine', False))
        total_out = self.panel_leds_per_strip * self.panel_strips

        # Build overlay lookup grids
        bubble_grid = np.zeros((height, width), dtype=bool)
        for bubble in self.bubbles:
            bx = max(0, min(width - 1, int(round(bubble['x']))))
            by = max(0, min(height - 1, int(round(bubble['y']))))
            bubble_grid[by, bx] = True

        spray_grid = np.zeros((height, width), dtype=np.float32)
        for particle in self.spray_particles:
            sx = int(round(particle['x']))
            sy = int(round(particle['y']))
            if 0 <= sx < width and 0 <= sy < height:
                spray_grid[sy, sx] = max(spray_grid[sy, sx], min(1.0, particle['life']))

        glow_grid = np.zeros((height, width), dtype=np.float32)
        for glow in self.drop_glow:
            gx = int(glow['x'])
            gy = int(glow['y'])
            if 0 <= gx < width and 0 <= gy < height:
                glow_grid[gy, gx] = max(glow_grid[gy, gx], glow.get('intensity', 0.0))

        # Hole visual intensity grid
        hole_intensity_grid = np.zeros((height, width), dtype=np.float32)
        if self.hole_active or self.hole_flash_timer > 0.0:
            cx, cy = self.hole_position
            r2 = self.hole_radius * self.hole_radius
            ys = np.arange(height)[:, None].astype(np.float32)
            xs = np.arange(width)[None, :].astype(np.float32)
            dist2 = (xs - cx) ** 2 + (ys - cy) ** 2
            in_range = dist2 <= r2 * 1.4
            if self.hole_active:
                hole_intensity_grid = np.where(in_range, 1.0 - np.minimum(1.0, dist2 / (r2 * 1.4)), 0.0)
            elif self.hole_flash_timer > 0.0:
                flash_phase = (self.hole_flash_timer / max(0.0001, float(self.params.get('hole_flash_duration', 0.45)))) * math.pi * 2.0
                flash = 0.6 + 0.4 * math.sin(flash_phase)
                hole_intensity_grid = np.where(in_range, flash * (1.0 - np.minimum(1.0, dist2 / (r2 * 1.4))), 0.0)

        # Surface detection: water cell with no water above
        surface_mask = np.zeros((height, width), dtype=bool)
        water_bool = self.water.astype(bool)
        surface_mask[0, :] = water_bool[0, :]
        surface_mask[1:, :] = water_bool[1:, :] & ~water_bool[:-1, :]

        # Color constants
        air_color = np.array([1, 2, 5], dtype=np.float32)
        deep_water = np.array([6, 40, 80], dtype=np.float32)
        surface_water = np.array([70, 160, 255], dtype=np.float32)
        foam_color = np.array([210, 235, 255], dtype=np.float32)
        hole_flash_color = np.array([140, 220, 255], dtype=np.float32)
        spray_color = np.array([200, 240, 255], dtype=np.float32)
        drop_color = np.array([180, 220, 255], dtype=np.float32)
        bubble_color = np.array([150, 230, 255], dtype=np.float32)

        shimmer = float(self.params.get('surface_shimmer', 0.35))
        foam_bias = float(self.params.get('foam_bias', 0.25))

        # Depth factor per row: 0 at top, 1 at bottom
        depth_factor = np.arange(height, dtype=np.float32) / max(1, height - 1)
        # Base water color per row: mix surface_water and deep_water
        t = depth_factor * 0.7
        base_water_rows = surface_water[None, :] * (1.0 - t[:, None]) + deep_water[None, :] * t[:, None]
        # Expand to (height, width, 3)
        base_water = np.broadcast_to(base_water_rows[:, None, :], (height, width, 3)).copy()

        # Compute pixel colors for the grid
        wave = self.ripple_height
        brightness = 1.0 + wave * 0.9
        brightness[surface_mask] += 0.2
        crest_boost = shimmer * np.maximum(0.0, wave)
        brightness += crest_boost

        # Scale water colors by brightness
        grid_colors = base_water * brightness[:, :, None]

        # Foam on surface cells with significant waves
        foam_candidates = surface_mask & (np.abs(wave) > 0.18)
        if np.any(foam_candidates):
            foam_t = np.minimum(1.0, foam_bias + np.abs(wave) * 0.8)
            foam_t_3d = foam_t[:, :, None]
            foamed = grid_colors * (1.0 - foam_t_3d) + foam_color[None, None, :] * foam_t_3d
            grid_colors = np.where(foam_candidates[:, :, None], foamed, grid_colors)

        # Bubble cells overlay
        if np.any(bubble_grid):
            bubble_mix = grid_colors * 0.3 + bubble_color[None, None, :] * 0.7
            grid_colors = np.where(bubble_grid[:, :, None], bubble_mix, grid_colors)

        # Air cells
        air_mask = ~water_bool
        grid_colors[air_mask] = air_color

        # Spray overlay
        spray_active = spray_grid > 0.0
        if np.any(spray_active):
            spray_t = np.minimum(1.0, spray_grid * 1.4)[:, :, None]
            sprayed = grid_colors * (1.0 - spray_t) + spray_color[None, None, :] * spray_t
            grid_colors = np.where(spray_active[:, :, None], sprayed, grid_colors)

        # Drop glow overlay
        glow_active = glow_grid > 0.0
        if np.any(glow_active):
            glow_t = np.minimum(1.0, glow_grid)[:, :, None]
            glowed = grid_colors * (1.0 - glow_t) + drop_color[None, None, :] * glow_t
            grid_colors = np.where(glow_active[:, :, None], glowed, grid_colors)

        # Hole intensity overlay (overrides everything)
        hole_active_pixels = hole_intensity_grid > 0.0
        if np.any(hole_active_pixels):
            hole_t = np.minimum(1.0, hole_intensity_grid)[:, :, None]
            hole_mixed = np.zeros_like(grid_colors)
            hole_mixed[:] = hole_flash_color[None, None, :]
            hole_mixed = hole_mixed * hole_t
            grid_colors = np.where(hole_active_pixels[:, :, None], hole_mixed, grid_colors)

        # Clamp and convert to uint8
        np.clip(grid_colors, 0, 255, out=grid_colors)

        # Map (y, x) grid → flat output pixel array using vectorized index math
        xs = np.arange(width)[None, :].astype(np.int32)   # (1, W)
        ys = np.arange(height)[:, None].astype(np.int32)  # (H, 1)

        if serpentine:
            odd_strip = (xs % 2 == 1)
            led_index = np.where(odd_strip, height - 1 - ys, ys)
        else:
            led_index = np.broadcast_to(ys, (height, width)).copy()

        led_index = height - 1 - led_index
        flat_idx = xs * self.panel_leds_per_strip + led_index  # (H, W)

        pixels = np.zeros((total_out, 3), dtype=np.uint8)
        grid_u8 = grid_colors.astype(np.uint8)
        flat_idx_1d = flat_idx.ravel()
        grid_u8_1d = grid_u8.reshape(-1, 3)
        pixels[flat_idx_1d] = grid_u8_1d

        return self.apply_brightness_array(pixels)
    
    def get_runtime_stats(self) -> Dict[str, Any]:
        """Expose the latest fill/flow telemetry for debugging."""
        return dict(self.last_stats) if self.last_stats else {}
