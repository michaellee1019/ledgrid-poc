#!/usr/bin/env python3
"""Volume-conserving, diffuser-aware water tank animation."""

from __future__ import annotations

import math
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from animation import AnimationBase
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


class Hole:
    """A puncture through the front wall of the virtual tank."""

    def __init__(self, x: float, y: float, radius: float, opened_at: float,
                 manual: bool = False, dry_time: float = 0.0):
        self.x = x
        self.y = y
        self.radius = radius
        self.opened_at = opened_at
        self.manual = manual
        self.dry_time = dry_time


class FluidTankAnimation(AnimationBase):
    """Conserved-volume water with a shallow-water surface and pressure holes."""

    ANIMATION_NAME = "Fluid Tank"
    ANIMATION_DESCRIPTION = "Volume-conserving water, bubbles, waves, and pressure-driven punctures"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"
    PLANT_MODIFIER_SUPPORT = frozenset(("obstacle", "refract", "slow_zone"))
    CC_PER_CELL = 5.0

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.default_params.update({
            'speed': 1.0,
            'drop_rate': 1.0,
            'target_fill_time': 60.0,
            'flow_steps': 2,
            'cell_height_mm': 17.1,
            'drop_terminal_velocity_m_s': 9.0,
            'max_drop_rate': 240.0,
            'bubble_interval': 2.4,
            'bubble_strength': 1.2,
            'ripple_damping': 0.985,
            'ripple_speed': 0.28,
            'surface_shimmer': 0.35,
            'foam_bias': 0.25,
            'caustic_strength': 0.18,
            'full_threshold': 0.94,
            'auto_hole': True,
            'hole_radius': 1.5,
            'hole_flash_duration': 0.45,
            'hole_cooldown': 2.0,
            'target_drain_time': 3.0,
            'serpentine': False,
            'plant_flow_deflection': 1.0,
        })
        self.params = {**self.default_params, **self.config}
        self.panel_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.panel_leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.width = self.panel_strips
        self.height = self.panel_leds_per_strip
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
            'drop_rate': {'type': 'float', 'min': 0.1, 'max': 20.0, 'default': 1.0, 'description': 'Water-volume inflow multiplier.'},
            'target_fill_time': {'type': 'float', 'min': 5.0, 'max': 600.0, 'default': 60.0, 'description': 'Seconds to add one tank volume at drop_rate=1.'},
            'flow_steps': {'type': 'int', 'min': 1, 'max': 8, 'default': 2, 'description': 'Surface physics passes per frame.'},
            'cell_height_mm': {'type': 'float', 'min': 5.0, 'max': 100.0, 'default': 17.1, 'description': 'Physical vertical pitch of one diffuser cell; sets real gravitational acceleration.'},
            'drop_terminal_velocity_m_s': {'type': 'float', 'min': 2.0, 'max': 12.0, 'default': 9.0, 'description': 'Maximum falling-water speed in air.'},
            'max_drop_rate': {'type': 'float', 'min': 30.0, 'max': 500.0, 'default': 240.0, 'description': 'Maximum distinct conserved droplets per second at extreme inflow.'},
            'bubble_interval': {'type': 'float', 'min': 0.3, 'max': 8.0, 'default': 2.4, 'description': 'Seconds between bubbles.'},
            'bubble_strength': {'type': 'float', 'min': 0.2, 'max': 2.5, 'default': 1.2, 'description': 'Surface impulse when a bubble bursts.'},
            'ripple_damping': {'type': 'float', 'min': 0.90, 'max': 0.999, 'default': 0.985, 'description': 'Surface wave persistence.'},
            'ripple_speed': {'type': 'float', 'min': 0.05, 'max': 1.2, 'default': 0.28, 'description': 'Surface wave propagation speed.'},
            'surface_shimmer': {'type': 'float', 'min': 0.0, 'max': 1.5, 'default': 0.35, 'description': 'Specular surface intensity.'},
            'foam_bias': {'type': 'float', 'min': 0.0, 'max': 1.0, 'default': 0.25, 'description': 'Foam created by energetic waves.'},
            'caustic_strength': {'type': 'float', 'min': 0.0, 'max': 0.5, 'default': 0.18, 'description': 'Slow broad underwater light bands.'},
            'full_threshold': {'type': 'float', 'min': 0.5, 'max': 1.0, 'default': 0.94, 'description': 'Level for the automatic demonstration puncture.'},
            'auto_hole': {'type': 'bool', 'default': True, 'description': 'Automatically puncture near the floor when full.'},
            'hole_radius': {'type': 'float', 'min': 0.6, 'max': 5.0, 'default': 1.5, 'description': 'Default puncture radius in diffuser cells.'},
            'hole_flash_duration': {'type': 'float', 'min': 0.1, 'max': 2.0, 'default': 0.45, 'description': 'Patch flash duration after a hole dries.'},
            'hole_cooldown': {'type': 'float', 'min': 0.5, 'max': 10.0, 'default': 2.0, 'description': 'Delay before another automatic puncture.'},
            'target_drain_time': {'type': 'float', 'min': 0.5, 'max': 15.0, 'default': 3.0, 'description': 'Calibration: full tank drain time for one floor hole.'},
            'serpentine': {'type': 'bool', 'default': False, 'description': 'Flip every other strip for serpentine wiring.'},
            'plant_flow_deflection': {'type': 'float', 'min': 0.0, 'max': 2.0, 'default': 1.0, 'description': 'How strongly calibrated plant structure steers droplets and bubbles when plant-aware mode is enabled.'},
        })
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        super().update_parameters(new_params)
        if {
            'plant_aware', 'plant_modifiers', 'plant_clearance', 'plant_mask_path',
            'plant_globe_mask_path', 'plant_flow_deflection',
        } & new_params.keys():
            self._plant_geometry_identity = None
            self._water_grid_cache_time = -1.0

    @property
    def capacity_cells(self) -> float:
        return float(self.width * self.height)

    def _reset_state(self):
        self.volume_cells = 0.0
        self.surface_offset = np.zeros(self.width, dtype=np.float32)
        self.surface_velocity = np.zeros(self.width, dtype=np.float32)
        self.water = np.zeros((self.height, self.width), dtype=np.int8)  # compatibility/debug view
        self.last_time: Optional[float] = None
        self.fill_cycle_start_time = 0.0
        self.awaiting_cycle_reset = False
        self.inlet_reservoir_cells = 0.0
        self.inlet_particles: List[Dict[str, float]] = []
        self.bubbles: List[Dict[str, float]] = []
        self.bubble_accumulator = 0.0
        self.holes: List[Hole] = []
        self.patch_flashes: List[Dict[str, float]] = []
        self.spray_particles: List[Dict[str, float]] = []
        self.hole_cooldown_timer = 0.0
        self.max_bubble_rise = 0.0
        self.last_spray_time = 0.0
        self.last_manual_hole_time = 0.0
        self.total_inflow_cells = 0.0
        self.total_landed_cells = 0.0
        self.total_drained_cells = 0.0
        self.last_stats: Dict[str, Any] = {}
        self._yy = np.arange(self.height, dtype=np.float32)[:, None]
        self._xx = np.arange(self.width, dtype=np.float32)[None, :]
        self._row_centers = self._yy + 0.5
        self._edge_light = np.clip(
            1.0 - np.minimum(self._xx, self.width - 1.0 - self._xx) / 1.5,
            0.0,
            1.0,
        )
        self._caustic_cache = np.zeros((self.height, self.width), dtype=np.float32)
        self._caustic_cache_time = -1.0
        xs = np.arange(self.width, dtype=np.int32)[None, :]
        ys = np.arange(self.height, dtype=np.int32)[:, None]
        normal_led = self.height - 1 - np.broadcast_to(ys, (self.height, self.width))
        serpentine_led = self.height - 1 - np.where(
            xs % 2 == 1,
            self.height - 1 - ys,
            ys,
        )
        self._normal_flat_idx = (xs * self.panel_leds_per_strip + normal_led).ravel()
        self._serpentine_flat_idx = (xs * self.panel_leds_per_strip + serpentine_led).ravel()
        self._water_grid_cache = np.zeros((self.height, self.width, 3), dtype=np.float32)
        self._water_grid_cache_time = -1.0
        self._plant_foliage = np.zeros((self.height, self.width), dtype=bool)
        self._plant_globes = np.zeros((self.height, self.width), dtype=bool)
        self._plant_clearance = np.zeros((self.height, self.width), dtype=bool)
        self._plant_obstacle = np.zeros((self.height, self.width), dtype=bool)
        self._plant_distance = np.full((self.height, self.width), float(max(self.width, self.height)), dtype=np.float32)
        self._plant_normal_row = np.zeros((self.height, self.width), dtype=np.float32)
        self._plant_normal_col = np.zeros((self.height, self.width), dtype=np.float32)
        self._plant_geometry_identity = None
        self._plant_mask_error = ''
        self._plant_flow_deflections = 0
        self._plant_slow_zone_steps = 0
        self._plant_slow_zone_seconds = 0.0
        self._plant_refracted_pixels = 0
        self._plant_refract_source_flat = np.arange(
            self.width * self.height, dtype=np.intp
        )
        self._plant_refract_alpha = np.zeros((self.height, self.width, 1), dtype=np.float32)
        self._plant_refract_inverse_alpha = np.ones_like(self._plant_refract_alpha)
        self._plant_refract_scratch = np.zeros((self.height, self.width, 3), dtype=np.float32)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        if self._plant_effects_enabled():
            self._refresh_plant_geometry()
        if self.last_time is None:
            dt_real = 1.0 / 30.0
        else:
            dt_real = max(0.0, min(0.25, time_elapsed - self.last_time))
        self.last_time = time_elapsed
        # Gravity, pressure and water transport use real elapsed time. The
        # manager's global animation speed scale must not slow physical water.
        dt = max(0.001, dt_real)

        self._maybe_auto_hole(time_elapsed)
        if not self.holes and not self.awaiting_cycle_reset:
            self._queue_inflow(dt_real)
        self._drain_holes(dt, time_elapsed)
        self._update_surface(dt)
        self._update_inlet_particles(dt, time_elapsed)
        self._update_bubbles(dt, time_elapsed)
        self._update_spray(dt)
        self._update_patch_flashes(dt)
        self.hole_cooldown_timer = max(0.0, self.hole_cooldown_timer - dt)
        self._maybe_reset_cycle(time_elapsed)

        coverage, surface_y = self._coverage_and_surface()
        self.water = (coverage >= 0.5).astype(np.int8)
        self._snapshot_stats(time_elapsed, dt_real, surface_y)
        return self._render_frame(time_elapsed, coverage, surface_y)

    def _refresh_plant_geometry(self):
        """Project physical mask coordinates into the tank's top-down canvas."""
        masks = self.get_plant_masks()
        if self._plant_geometry_identity == id(masks):
            return
        self._plant_foliage[:] = masks.foliage.T[::-1]
        self._plant_globes[:] = masks.globes.T[::-1]
        self._plant_clearance[:] = masks.clearance.T[::-1]
        self._plant_obstacle[:] = masks.obstacle.T[::-1]
        self._plant_distance[:] = masks.distance.T[::-1]
        row_gradient, col_gradient = np.gradient(self._plant_distance)
        magnitude = np.hypot(row_gradient, col_gradient)
        self._plant_normal_row.fill(0.0)
        self._plant_normal_col.fill(0.0)
        np.divide(row_gradient, magnitude, out=self._plant_normal_row, where=magnitude > 0)
        np.divide(col_gradient, magnitude, out=self._plant_normal_col, where=magnitude > 0)
        self._plant_geometry_identity = id(masks)
        self._plant_mask_error = masks.error
        self._prepare_plant_refraction()
        self._water_grid_cache_time = -1.0

    def _prepare_plant_refraction(self) -> None:
        strength = self.plant_modifier_strength('refract')
        radius = max(2.0, float(self.params.get('plant_clearance', 1)) + 4.0)
        falloff = np.clip(1.0 - self._plant_distance / radius, 0.0, 1.0)
        displacement = falloff * (0.5 + 2.5 * strength)
        source_rows = np.clip(
            np.rint(self._yy + self._plant_normal_row * displacement),
            0, self.height - 1,
        ).astype(np.intp)
        source_cols = np.clip(
            np.rint(self._xx + self._plant_normal_col * displacement),
            0, self.width - 1,
        ).astype(np.intp)
        self._plant_refract_source_flat[:] = (
            source_rows * self.width + source_cols
        ).ravel()
        self._plant_refract_alpha[:, :, 0] = falloff * strength
        np.subtract(1.0, self._plant_refract_alpha, out=self._plant_refract_inverse_alpha)
        self._plant_refracted_pixels = int(np.count_nonzero(falloff > 0.0)) if strength > 0 else 0

    def _nearest_clear_x(self, x: float, y: float) -> float:
        """Keep a moving landmark visible by shifting it to the nearest clear cell."""
        if not self._obstacle_enabled():
            return x
        row = max(0, min(self.height - 1, int(round(y))))
        origin = max(0, min(self.width - 1, int(round(x))))
        if not self._plant_clearance[row, origin]:
            return x
        strength = (
            max(0.0, float(self.params.get('plant_flow_deflection', 1.0)))
            if self._legacy_plant_mode()
            else self.plant_modifier_strength('obstacle')
        )
        if strength <= 0.0:
            return x
        for distance in range(1, self.width):
            # Prefer the side the particle was already closest to, then remain
            # deterministic when it is exactly centered on a diffuser cell.
            candidates = (origin - distance, origin + distance)
            for candidate in candidates:
                if 0 <= candidate < self.width and not self._plant_clearance[row, candidate]:
                    self._plant_flow_deflections += 1
                    target = float(candidate)
                    # Exact cores stay solid at every enabled strength. Strength
                    # controls how assertively the surrounding clearance routes.
                    effective = 1.0 if self._plant_obstacle[row, origin] else min(1.0, strength)
                    return x + (target - x) * effective
        return x

    def _legacy_plant_mode(self) -> bool:
        raw_state = self.params.get('plant_modifiers') or {}
        return bool(self.params.get('plant_aware', False)) and not raw_state.get('active')

    def _plant_effects_enabled(self) -> bool:
        return self._legacy_plant_mode() or any(
            self.plant_modifier_enabled(modifier) for modifier in self.PLANT_MODIFIER_SUPPORT
        )

    def _obstacle_enabled(self) -> bool:
        return self._legacy_plant_mode() or self.plant_modifier_enabled('obstacle')

    def _slow_zone_factor(self, x: float, y: float, dt: float = 0.0) -> float:
        if not self.plant_modifier_enabled('slow_zone'):
            return 1.0
        row = max(0, min(self.height - 1, int(round(y))))
        col = max(0, min(self.width - 1, int(round(x))))
        radius = max(2.0, float(self.params.get('plant_clearance', 1)) + 3.0)
        influence = max(0.0, 1.0 - float(self._plant_distance[row, col]) / radius)
        if influence <= 0.0:
            return 1.0
        strength = self.plant_modifier_strength('slow_zone')
        factor = max(0.2, 1.0 - 0.8 * strength * influence)
        self._plant_slow_zone_steps += 1
        self._plant_slow_zone_seconds += max(0.0, dt) * (1.0 - factor)
        return factor

    def _queue_inflow(self, dt: float):
        """Create conserved airborne water packets; do not fill until impact."""
        fill_time = max(5.0, float(self.params.get('target_fill_time', 60.0)))
        multiplier = max(0.1, float(self.params.get('drop_rate', 1.0)))
        flow_cells_s = self.capacity_cells / fill_time * multiplier
        airborne = sum(p['volume_cells'] for p in self.inlet_particles)
        system_volume = self.volume_cells + airborne + self.inlet_reservoir_cells
        supplied = min(flow_cells_s * dt, max(0.0, self.capacity_cells - system_volume))
        self.inlet_reservoir_cells += supplied
        self.total_inflow_cells += supplied

        # One default drop is exactly one 5 cc cell. At extreme flow rates,
        # droplets become larger instead of creating thousands of particles.
        max_rate = max(30.0, float(self.params.get('max_drop_rate', 240.0)))
        spawn_rate = min(flow_cells_s, max_rate)
        packet_volume = max(1.0, flow_cells_s / max(spawn_rate, 1.0))
        while self.inlet_reservoir_cells + 1e-9 >= packet_volume and len(self.inlet_particles) < 512:
            self.inlet_reservoir_cells -= packet_volume
            x = random.uniform(self.width * 0.25, self.width * 0.75)
            if self._obstacle_enabled():
                x = self._nearest_clear_x(x, 0.0)
            self.inlet_particles.append({
                'x': x, 'y': -random.uniform(0.0, 1.5),
                'vy': random.uniform(0.0, 0.35),
                'volume_cells': packet_volume,
                'life': 3.0,
            })

    def _update_surface(self, dt: float):
        if self.width <= 1:
            return
        steps = max(1, int(self.params.get('flow_steps', 2)))
        sub_dt = min(0.04, dt / steps)
        wave_speed = float(self.params.get('ripple_speed', 0.28)) * 42.0
        damping_frame = float(self.params.get('ripple_damping', 0.985))
        damping = damping_frame ** (sub_dt * 30.0)
        for _ in range(steps):
            left = np.empty_like(self.surface_offset)
            right = np.empty_like(self.surface_offset)
            left[1:] = self.surface_offset[:-1]
            left[0] = self.surface_offset[1]
            right[:-1] = self.surface_offset[1:]
            right[-1] = self.surface_offset[-2]
            laplacian = left + right - 2.0 * self.surface_offset
            self.surface_velocity += laplacian * wave_speed * sub_dt
            self.surface_velocity *= damping
            self.surface_offset += self.surface_velocity * sub_dt
            self.surface_offset -= float(np.mean(self.surface_offset))
            np.clip(self.surface_offset, -3.5, 3.5, out=self.surface_offset)

    def _surface_y_values(self) -> np.ndarray:
        mean_depth = self.volume_cells / max(1, self.width)
        depths = np.clip(mean_depth + self.surface_offset, 0.0, float(self.height))
        return self.height - depths

    def _coverage_and_surface(self) -> Tuple[np.ndarray, np.ndarray]:
        surface_y = self._surface_y_values()
        coverage = np.clip(self._row_centers - surface_y[None, :] + 0.5, 0.0, 1.0)
        return coverage.astype(np.float32), surface_y

    def _surface_at(self, x: float, values: Optional[np.ndarray] = None) -> float:
        if values is None:
            values = self._surface_y_values()
        if self.width == 1:
            return float(values[0])
        x = max(0.0, min(self.width - 1.0, x))
        x0 = int(math.floor(x))
        x1 = min(self.width - 1, x0 + 1)
        t = x - x0
        return float(values[x0] * (1.0 - t) + values[x1] * t)

    def _impulse(self, x: float, strength: float):
        center = int(round(x))
        for dx, falloff in ((0, 1.0), (-1, 0.45), (1, 0.45), (-2, 0.15), (2, 0.15)):
            nx = center + dx
            if 0 <= nx < self.width:
                self.surface_velocity[nx] += strength * falloff

    def _update_inlet_particles(self, dt: float, now: float):
        active: List[Dict[str, float]] = []
        surface_values = self._surface_y_values()
        cell_height_m = max(0.005, float(self.params.get('cell_height_mm', 17.1)) / 1000.0)
        gravity_cells_s2 = 9.80665 / cell_height_m
        terminal_cells_s = max(2.0, float(self.params.get('drop_terminal_velocity_m_s', 9.0))) / cell_height_m
        for p in self.inlet_particles:
            local_dt = dt * self._slow_zone_factor(p['x'], p['y'], dt)
            p['vy'] = min(terminal_cells_s, p['vy'] + gravity_cells_s2 * local_dt)
            p['y'] += p['vy'] * local_dt
            if self._obstacle_enabled():
                p['x'] = self._nearest_clear_x(p['x'], p['y'])
            p['life'] -= dt
            surface = self._surface_at(p['x'], surface_values)
            if p['y'] >= surface:
                landed = min(p['volume_cells'], max(0.0, self.capacity_cells - self.volume_cells))
                self.volume_cells += landed
                self.total_landed_cells += landed
                # Later drops in the same frame must see the newly raised level.
                surface_values -= landed / max(1, self.width)
                np.maximum(surface_values, 0.0, out=surface_values)
                momentum = min(3.0, 0.25 + p['vy'] / max(1.0, terminal_cells_s) * 2.0)
                self._impulse(p['x'], momentum)
                continue
            if p['life'] > 0.0 and p['y'] < self.height:
                active.append(p)
        self.inlet_particles = active

    def _update_bubbles(self, dt: float, now: float):
        if self._fill_ratio() > 0.08:
            self.bubble_accumulator += dt
        interval = max(0.3, float(self.params.get('bubble_interval', 2.4)))
        while self.bubble_accumulator >= interval:
            self.bubble_accumulator -= interval
            x = random.uniform(1.0, max(1.0, self.width - 2.0))
            if self._obstacle_enabled():
                x = self._nearest_clear_x(x, self.height - 1.0)
            radius = random.uniform(0.55, 1.35)
            self.bubbles.append({
                'x': x, 'origin_y': self.height - 0.5, 'y': self.height - 0.5,
                'radius': radius, 'vy': -random.uniform(5.0, 8.0),
                'phase': random.uniform(0.0, math.tau), 'age': 0.0,
            })

        active: List[Dict[str, float]] = []
        surface_values = self._surface_y_values()
        for bubble in self.bubbles:
            local_dt = dt * self._slow_zone_factor(bubble['x'], bubble['y'], dt)
            bubble['age'] += local_dt
            bubble['phase'] += local_dt * (3.0 + bubble['radius'])
            bubble['x'] += math.sin(bubble['phase']) * 0.45 * local_dt
            bubble['x'] = max(0.5, min(self.width - 1.5, bubble['x']))
            bubble['y'] += bubble['vy'] * local_dt
            if self._obstacle_enabled():
                routed_x = self._nearest_clear_x(bubble['x'], bubble['y'])
                if routed_x != bubble['x']:
                    bubble['phase'] += math.pi
                    bubble['x'] = max(0.5, min(self.width - 1.5, routed_x))
            bubble['radius'] = min(1.8, bubble['radius'] + 0.018 * local_dt)
            surface = self._surface_at(bubble['x'], surface_values)
            rise = bubble['origin_y'] - bubble['y']
            self.max_bubble_rise = max(self.max_bubble_rise, rise)
            if bubble['y'] - bubble['radius'] <= surface:
                strength = float(self.params.get('bubble_strength', 1.2)) * bubble['radius']
                self._impulse(bubble['x'], -strength)
                continue
            if bubble['y'] < self.height and surface < self.height:
                active.append(bubble)
        self.bubbles = active

    def _maybe_auto_hole(self, now: float):
        if not bool(self.params.get('auto_hole', True)) or self.holes or self.patch_flashes:
            return
        if self.hole_cooldown_timer > 0.0 or self._fill_ratio() < float(self.params.get('full_threshold', 0.94)):
            return
        radius = float(self.params.get('hole_radius', 1.5))
        margin = min(max(radius + 0.5, 1.0), max(1.0, self.width / 2.0))
        x = random.uniform(margin, max(margin, self.width - 1.0 - margin))
        if self._obstacle_enabled():
            x = self._nearest_clear_x(x, self.height - 1.0)
        self._activate_hole(now, x, self.height - 0.5, radius, manual=False)

    def _activate_hole(self, now: float, x: float, y: float, radius: float, manual: bool) -> bool:
        x = max(0.0, min(self.width - 1.0, float(x)))
        y = max(0.0, min(self.height - 0.5, float(y)))
        if self._obstacle_enabled():
            self._refresh_plant_geometry()
            x = self._nearest_clear_x(x, y)
        radius = max(0.6, min(5.0, float(radius)))
        self.holes.append(Hole(x=x, y=y, radius=radius, opened_at=now, manual=manual))
        self._impulse(x, 2.2)
        if manual:
            self.last_manual_hole_time = now
        return True

    def trigger_hole(self, x: float, y: float, radius: Optional[float] = None) -> bool:
        """Punch a hole at a grid coordinate. Multiple simultaneous holes are supported."""
        now = self.last_time if self.last_time is not None else time.time()
        return self._activate_hole(now, x, y, radius or float(self.params.get('hole_radius', 1.5)), manual=True)

    def trigger_random_hole(self):
        """Backward-compatible external hook for a random puncture."""
        x = random.uniform(0.0, max(0.0, self.width - 1.0))
        surface = self._surface_at(x)
        y = random.uniform(max(surface + 2.0, 0.0), self.height - 0.5) if surface + 2.0 < self.height - 0.5 else self.height - 0.5
        return self.trigger_hole(x, y)

    def _drain_holes(self, dt: float, now: float):
        if not self.holes or self.volume_cells <= 0.0:
            return
        target_time = max(0.5, float(self.params.get('target_drain_time', 3.0)))
        default_radius = max(0.6, float(self.params.get('hole_radius', 1.5)))
        reference_area = math.pi * default_radius * default_radius
        coefficient = (2.0 * self.width * math.sqrt(max(1.0, self.height))) / (reference_area * target_time)
        drained = 0.0
        active: List[Hole] = []
        mean_surface_y = self.height - self.volume_cells / max(1, self.width)
        for hole in self.holes:
            # Hydrostatic head follows the mean free surface. Using a single
            # wave trough here could falsely expose and permanently close a
            # floor hole while several rows of water remain elsewhere.
            head = max(0.0, hole.y - mean_surface_y)
            if head > 0.08 and self.volume_cells > 0.0:
                area = math.pi * hole.radius * hole.radius
                amount = min(self.volume_cells, coefficient * area * math.sqrt(head) * dt)
                self.volume_cells -= amount
                drained += amount
                hole.dry_time = 0.0
                self._impulse(hole.x, -min(1.2, amount / max(1.0, self.width)))
                self._spawn_spray(hole, amount, head)
                active.append(hole)
            else:
                hole.dry_time += dt
                if hole.dry_time < 0.7:
                    active.append(hole)
                else:
                    self.patch_flashes.append({'x': hole.x, 'y': hole.y, 'radius': hole.radius, 'life': float(self.params.get('hole_flash_duration', 0.45)), 'max_life': float(self.params.get('hole_flash_duration', 0.45))})
        self.holes = active
        if drained > 0.0:
            self.total_drained_cells += drained
            self.last_spray_time = now
            self.awaiting_cycle_reset = self.awaiting_cycle_reset or any(not h.manual for h in self.holes)
        if not self.holes and drained > 0.0:
            self.hole_cooldown_timer = max(self.hole_cooldown_timer, float(self.params.get('hole_cooldown', 2.0)))

    def _spawn_spray(self, hole: Hole, amount: float, head: float):
        expected = min(4, int(amount) + (1 if random.random() < amount % 1.0 else 0))
        for _ in range(expected):
            if len(self.spray_particles) >= 80:
                break
            speed = min(24.0, 5.0 + math.sqrt(head) * 1.8)
            self.spray_particles.append({
                'x': hole.x + random.uniform(-0.35, 0.35),
                'y': hole.y + random.uniform(-0.3, 0.3),
                'vx': random.uniform(-1.8, 1.8),
                'vy': -random.uniform(speed * 0.45, speed),
                'life': random.uniform(0.35, 0.8),
            })

    def _update_spray(self, dt: float):
        active: List[Dict[str, float]] = []
        for p in self.spray_particles:
            p['vy'] += 12.0 * dt
            p['x'] += p['vx'] * dt
            p['y'] += p['vy'] * dt
            p['life'] -= dt
            if p['life'] > 0.0 and -2.0 <= p['y'] < self.height + 2.0:
                active.append(p)
        self.spray_particles = active

    def _update_patch_flashes(self, dt: float):
        for flash in self.patch_flashes:
            flash['life'] -= dt
        self.patch_flashes = [flash for flash in self.patch_flashes if flash['life'] > 0.0]

    def _maybe_reset_cycle(self, now: float):
        if self.awaiting_cycle_reset and not self.holes and not self.patch_flashes and self._fill_ratio() <= 0.01:
            self.awaiting_cycle_reset = False
            self.fill_cycle_start_time = now

    def _fill_ratio(self) -> float:
        return self.volume_cells / self.capacity_cells if self.capacity_cells > 0 else 0.0

    def _render_frame(self, now: float, coverage: np.ndarray, surface_y: np.ndarray) -> np.ndarray:
        height, width = self.height, self.width
        yy, xx = self._yy, self._xx
        if self._water_grid_cache_time < 0.0 or now - self._water_grid_cache_time >= 1.0 / 60.0:
            self._water_grid_cache = self._render_water_base(now, coverage, surface_y)
            self._water_grid_cache_time = now
        grid = self._water_grid_cache.copy()

        # Rim-lit bubbles: dark core, bright upper rim.
        for bubble in self.bubbles:
            dx = xx - bubble['x']
            dy = yy - bubble['y']
            dist2 = dx * dx + dy * dy
            core_r = bubble['radius'] * 0.58
            rim_r = bubble['radius'] * 1.15
            core = dist2 < core_r * core_r
            rim = (dist2 >= core_r * core_r) & (dist2 <= rim_r * rim_r)
            grid[core] *= 0.28
            upper_rim = rim * np.clip(0.75 - dy / max(0.3, bubble['radius']), 0.15, 1.0)
            t = upper_rim[:, :, None]
            grid = grid * (1.0 - t * 0.72) + np.array([174.0, 232.0, 255.0]) * t * 0.72

        # Inlet streaks and spray are deliberately brighter than the body.
        for p in self.inlet_particles:
            px = int(round(p['x']))
            y0 = int(round(p['y']))
            # A short exposure trail prevents high-speed droplets from strobing
            # between diffuser cells at 150-200 Hz.
            trail_length = max(1, min(8, int(math.ceil(p['vy'] / 150.0))))
            for tail in range(trail_length):
                py = y0 - tail
                if 0 <= px < width and 0 <= py < height:
                    alpha = 1.0 - tail / max(1.0, trail_length + 0.5)
                    # Direct assignment avoids dozens of tiny temporary NumPy
                    # arrays per frame on the Raspberry Pi.
                    grid[py, px] = (170.0 * alpha, 220.0 * alpha, 255.0 * alpha)
        for p in self.spray_particles:
            px, py = int(round(p['x'])), int(round(p['y']))
            if 0 <= px < width and 0 <= py < height:
                alpha = min(1.0, p['life'] * 2.0)
                grid[py, px] = (205.0 * alpha, 240.0 * alpha, 255.0 * alpha)

        # A puncture reads as a black aperture with a turbulent bright rim.
        for hole in self.holes:
            dist = np.sqrt((xx - hole.x) ** 2 + (yy - hole.y) ** 2)
            core = dist <= hole.radius * 0.62
            rim = (dist > hole.radius * 0.62) & (dist <= hole.radius * 1.28)
            grid[core] = np.array([0.0, 1.0, 2.0])
            flicker = 0.72 + 0.28 * math.sin(now * 17.0 + hole.x)
            rim_alpha = np.clip(1.0 - np.abs(dist - hole.radius) / max(0.3, hole.radius * 0.55), 0.0, 1.0) * rim * flicker
            grid = grid * (1.0 - rim_alpha[:, :, None]) + np.array([135.0, 218.0, 255.0]) * rim_alpha[:, :, None]
        for flash in self.patch_flashes:
            dist = np.sqrt((xx - flash['x']) ** 2 + (yy - flash['y']) ** 2)
            alpha = np.clip(1.0 - dist / (flash['radius'] * 1.5), 0.0, 1.0) * (flash['life'] / max(0.001, flash['max_life']))
            grid = grid * (1.0 - alpha[:, :, None]) + np.array([150.0, 225.0, 255.0]) * alpha[:, :, None]

        if self.plant_modifier_enabled('refract'):
            grid = self._apply_plant_refraction(grid)

        np.clip(grid, 0, 255, out=grid)
        return self._map_grid_to_pixels(grid.astype(np.uint8))

    def _apply_plant_refraction(self, grid: np.ndarray) -> np.ndarray:
        """Presentation-only displacement around calibrated geometry."""
        if self._plant_refracted_pixels <= 0:
            self._plant_refracted_pixels = 0
            return grid
        np.take(
            grid.reshape(-1, 3), self._plant_refract_source_flat,
            axis=0, out=self._plant_refract_scratch.reshape(-1, 3),
        )
        np.multiply(
            self._plant_refract_scratch, self._plant_refract_alpha,
            out=self._plant_refract_scratch,
        )
        np.multiply(grid, self._plant_refract_inverse_alpha, out=grid)
        np.add(grid, self._plant_refract_scratch, out=grid)
        return grid

    def _render_water_base(self, now: float, coverage: np.ndarray,
                           surface_y: np.ndarray) -> np.ndarray:
        """Render the slower water body at 60 Hz; particles remain full-rate."""
        yy, xx = self._yy, self._xx
        depth = np.maximum(0.0, yy + 0.5 - surface_y[None, :])
        depth_t = np.clip(depth / max(8.0, self.height * 0.72), 0.0, 1.0)

        air = np.array([1.0, 2.0, 4.0], dtype=np.float32)
        surface_color = np.array([52.0, 145.0, 238.0], dtype=np.float32)
        deep_color = np.array([3.0, 31.0, 67.0], dtype=np.float32)
        water_color = surface_color[None, None, :] * (1.0 - depth_t[:, :, None]) + deep_color[None, None, :] * depth_t[:, :, None]

        caustic_strength = float(self.params.get('caustic_strength', 0.18))
        # Caustics move slowly, so recomputing transcendental functions at LED
        # refresh rate wastes most of the Pi's frame budget. A 30 Hz lighting
        # field remains visually continuous while droplets/surface run at 200 Hz.
        if self._caustic_cache_time < 0.0 or now - self._caustic_cache_time >= 1.0 / 30.0:
            self._caustic_cache = (
                np.sin(xx * 0.47 + yy * 0.09 - now * 1.3)
                + np.sin(xx * 0.19 - yy * 0.13 + now * 0.77)
            ) * 0.5
            self._caustic_cache_time = now
        caustic = self._caustic_cache * caustic_strength * np.clip(1.0 - depth / 72.0, 0.0, 1.0)
        water_color *= (1.0 + caustic[:, :, None])

        surface_band = np.clip(1.0 - np.abs(depth - 0.55) / 1.25, 0.0, 1.0)
        shimmer = float(self.params.get('surface_shimmer', 0.35))
        water_color += surface_band[:, :, None] * np.array([45.0, 62.0, 72.0]) * shimmer

        meniscus = self._edge_light * np.clip(1.0 - depth / 3.5, 0.0, 1.0)
        water_color += meniscus[:, :, None] * np.array([38.0, 58.0, 72.0])

        grid = air[None, None, :] * (1.0 - coverage[:, :, None]) + water_color * coverage[:, :, None]

        if self._obstacle_enabled():
            # Foliage is porous submerged structure: it absorbs some blue body
            # light but catches moving green caustics. Rooting globes read as
            # solid warm glass landmarks. Simulation landmarks are rendered
            # afterward, so bubbles, drops, holes, and spray remain legible.
            foliage = self._plant_foliage
            if np.any(foliage):
                foliage_light = np.clip(
                    0.55 + 0.45 * self._caustic_cache, 0.15, 1.0
                )[:, :, None]
                foliage_color = np.array([5.0, 84.0, 38.0]) * foliage_light
                grid[foliage] = grid[foliage] * 0.38 + foliage_color[foliage] * 0.62
            globes = self._plant_globes
            if np.any(globes):
                globe_color = np.empty_like(grid)
                globe_color[:, :, 0] = 118.0 + 34.0 * np.clip(self._caustic_cache, -1.0, 1.0)
                globe_color[:, :, 1] = 82.0 + 18.0 * np.clip(self._caustic_cache, -1.0, 1.0)
                globe_color[:, :, 2] = 38.0
                grid[globes] = grid[globes] * 0.22 + globe_color[globes] * 0.78

        if self.plant_modifier_enabled('slow_zone'):
            radius = max(2.0, float(self.params.get('plant_clearance', 1)) + 3.0)
            halo = np.clip(1.0 - self._plant_distance / radius, 0.0, 1.0)
            halo *= 0.18 * self.plant_modifier_strength('slow_zone')
            grid = grid * (1.0 - halo[:, :, None]) + np.array([40.0, 72.0, 108.0]) * halo[:, :, None]

        wave_energy = np.abs(self.surface_velocity)[None, :]
        foam = (surface_band > 0.42) & (wave_energy > (1.4 - float(self.params.get('foam_bias', 0.25)))) & (coverage > 0.05)
        if np.any(foam):
            grid[foam] = grid[foam] * 0.25 + np.array([190.0, 225.0, 245.0]) * 0.75
        return grid

    def _map_grid_to_pixels(self, grid: np.ndarray) -> np.ndarray:
        flat_idx = (
            self._serpentine_flat_idx
            if bool(self.params.get('serpentine', False))
            else self._normal_flat_idx
        )
        pixels = self.next_frame_buffer(clear=True)
        pixels[flat_idx] = grid.reshape(-1, 3)
        return self.apply_brightness_array(pixels, out=pixels)

    def _snapshot_stats(self, now: float, dt: float, surface_y: np.ndarray):
        fill_time = max(5.0, float(self.params.get('target_fill_time', 60.0)))
        expected = 0.0 if self.awaiting_cycle_reset else min(1.0, max(0.0, now - self.fill_cycle_start_time) / fill_time)
        hole_preview = [
            {'x': round(h.x, 2), 'y': round(h.y, 2), 'radius': round(h.radius, 2), 'head': round(max(0.0, h.y - (self.height - self.volume_cells / max(1, self.width))), 2), 'manual': h.manual}
            for h in self.holes[:8]
        ]
        self.last_stats = {
            'time': now, 'dt_real': dt,
            'width': self.width, 'height': self.height, 'total_cells': int(self.capacity_cells),
            'cc_per_cell': self.CC_PER_CELL, 'capacity_cc': self.capacity_cells * self.CC_PER_CELL,
            'volume_cells': self.volume_cells, 'volume_cc': self.volume_cells * self.CC_PER_CELL,
            'airborne_volume_cells': sum(p['volume_cells'] for p in self.inlet_particles),
            'airborne_volume_cc': sum(p['volume_cells'] for p in self.inlet_particles) * self.CC_PER_CELL,
            'queued_inlet_volume_cells': self.inlet_reservoir_cells,
            'fill_ratio': self._fill_ratio(), 'expected_ratio': expected,
            'spawn_allowed': not self.holes and not self.awaiting_cycle_reset,
            'awaiting_cycle_reset': self.awaiting_cycle_reset,
            'hole_active': bool(self.holes), 'hole_count': len(self.holes), 'hole_preview': hole_preview,
            'hole_flash_timer': max((f['life'] for f in self.patch_flashes), default=0.0),
            'hole_cooldown_timer': self.hole_cooldown_timer,
            'hole_water_remaining': int(self.volume_cells) if self.holes else 0,
            'bubble_count': len(self.bubbles), 'max_bubble_rise': self.max_bubble_rise,
            'spray_particle_count': len(self.spray_particles), 'last_spray_time': self.last_spray_time,
            'last_manual_hole_time': self.last_manual_hole_time,
            'drop_glow_count': len(self.inlet_particles),
            'surface_min_y': float(np.min(surface_y)), 'surface_max_y': float(np.max(surface_y)),
            'total_inflow_cc': self.total_inflow_cells * self.CC_PER_CELL,
            'total_landed_cc': self.total_landed_cells * self.CC_PER_CELL,
            'total_drained_cc': self.total_drained_cells * self.CC_PER_CELL,
        }
        if self.bubbles:
            self.last_stats['bubble_preview'] = [
                {'x': round(b['x'], 2), 'y': round(b['y'], 2), 'radius': round(b['radius'], 2), 'vy': round(b['vy'], 2)}
                for b in self.bubbles[:4]
            ]
        if self._plant_effects_enabled():
            self.last_stats.update({
                'plant_aware': True,
                'plant_active_modifiers': list(self.plant_modifier_state().active),
                'plant_foliage_pixels': int(np.count_nonzero(self._plant_foliage)),
                'plant_globe_pixels': int(np.count_nonzero(self._plant_globes)),
                'plant_flow_deflections': self._plant_flow_deflections,
                'plant_slow_zone_steps': self._plant_slow_zone_steps,
                'plant_slow_zone_seconds': self._plant_slow_zone_seconds,
                'plant_refracted_pixels': self._plant_refracted_pixels,
            })
            if self._plant_mask_error:
                self.last_stats['plant_mask_error'] = self._plant_mask_error

    def get_runtime_stats(self) -> Dict[str, Any]:
        return dict(self.last_stats)
