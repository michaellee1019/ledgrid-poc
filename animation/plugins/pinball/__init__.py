#!/usr/bin/env python3
"""Fast, self-playing, arcade pinball animation for the physical LED grid."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from animation import AnimationBase
from animation.libraries.palette_field import AnimatedPaletteField


Color = Tuple[int, int, int]


@dataclass
class Burst:
    x: float
    y: float
    color: Color
    age: float = 0.0
    life: float = 0.55


class PinballAnimation(AnimationBase):
    """A CPU-light pinball show inspired by colorful 1990s PC tables."""

    ANIMATION_NAME = "Arcade Pinball"
    ANIMATION_DESCRIPTION = "Fast self-playing 90s PC pinball with scores, streaks, jackpots, and minigames"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    NAVY = (0, 3, 18)
    BLUE = (0, 34, 92)
    CYAN = (0, 235, 255)
    WHITE = (255, 255, 245)
    YELLOW = (255, 226, 0)
    ORANGE = (255, 84, 0)
    RED = (255, 18, 44)
    MAGENTA = (255, 0, 190)
    GREEN = (20, 255, 80)

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.width, self.height = self.get_strip_info()
        self.default_params.update({
            "speed": 1.35,
            "brightness": 1.0,
            "render_fps": 100.0,
            "chaos": 0.72,
            "seed": 95,
        })
        self.params = {**self.default_params, **self.config}
        self.random = random.Random(int(self.params.get("seed", 95)))

        self._static = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self._canvas = np.zeros_like(self._static)
        self._plant_foliage = np.zeros((self.height, self.width), dtype=bool)
        self._plant_globes = np.zeros((self.height, self.width), dtype=bool)
        self._plant_clearance = np.zeros((self.height, self.width), dtype=bool)
        self._plant_geometry_identity: Optional[int] = None
        self._plant_hits = 0
        hues = np.linspace(0.0, 1.0, 256, endpoint=False, dtype=np.float32)
        saturation = np.full(256, 0.94, dtype=np.float32)
        values = (0.10 + 0.20 * (np.sin(hues * math.tau * 3.0) * 0.5 + 0.5)).astype(np.float32)
        self._psy_palette = self.hsv_to_rgb_array(hues, saturation, values)
        self._background_field = AnimatedPaletteField(
            self.width, self.height, self._psy_palette
        )
        self._build_table()

        self.ball_x = self.width - 4.0
        self.ball_y = self.height - 18.0
        self.ball_vx = -7.0
        self.ball_vy = -76.0
        self.score = 95000
        self.display_score = float(self.score)
        self.streak = 0
        self.multiplier = 1
        self.balls = 3
        self.mode = "READY"
        self.mode_time = 0.0
        self.mode_count = 0
        self.drain_time = 0.0
        self.last_elapsed: Optional[float] = None
        self.last_render_elapsed: Optional[float] = None
        self.last_rendered_frame: Optional[np.ndarray] = None
        self.bursts: List[Burst] = []
        self.lamps = [False] * 5
        self._next_mode_at = 5.5
        self._event_cooldown = 0.0
        self._hit_flash = 0.0
        self._failure_flash = 0.0
        self._success_flash = 0.0
        self._sim_time = 0.0
        if self.plant_aware_enabled():
            self._prepare_plant_table()
            self.ball_x, self.ball_y = self._nearest_plant_safe_point(
                self.ball_x, self.ball_y
            )

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            "speed": {
                "type": "float", "min": 0.4, "max": 3.0, "default": 1.35,
                "description": "Ball and event speed",
            },
            "render_fps": {
                "type": "float", "min": 24.0, "max": 120.0, "default": 100.0,
                "description": "Maximum simulation and render rate",
            },
            "chaos": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 0.72,
                "description": "Frequency of sparks, callouts, and surprise shots",
            },
            "seed": {
                "type": "int", "min": 0, "max": 9999, "default": 95,
                "description": "Repeatable table action seed",
            },
        })
        return schema

    # Drawing helpers operate in logical top-to-bottom table coordinates.
    def _pixel(self, image: np.ndarray, x: int, y: int, color: Color, additive: bool = False):
        if 0 <= x < self.width and 0 <= y < self.height:
            if additive:
                image[y, x] = np.maximum(image[y, x], color)
            else:
                image[y, x] = color

    def _line(self, image: np.ndarray, x0: int, y0: int, x1: int, y1: int, color: Color):
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        error = dx + dy
        while True:
            self._pixel(image, x0, y0, color)
            if x0 == x1 and y0 == y1:
                return
            twice = 2 * error
            if twice >= dy:
                error += dy
                x0 += sx
            if twice <= dx:
                error += dx
                y0 += sy

    def _circle(self, image: np.ndarray, cx: int, cy: int, radius: int, color: Color, fill: bool = False):
        radius = max(0, radius)
        for yy in range(-radius, radius + 1):
            for xx in range(-radius, radius + 1):
                distance = xx * xx + yy * yy
                if distance <= radius * radius and (fill or distance >= (radius - 1) ** 2):
                    self._pixel(image, cx + xx, cy + yy, color)

    def _soft_dot(self, image: np.ndarray, x: float, y: float, color: Color, radius: float = 1.8):
        """Draw a subpixel-positioned radial glow with a brilliant core."""
        left, right = math.floor(x - radius), math.ceil(x + radius)
        top, bottom = math.floor(y - radius), math.ceil(y + radius)
        inv_radius = 1.0 / max(0.1, radius)
        for py in range(top, bottom + 1):
            for px in range(left, right + 1):
                distance = math.hypot(px - x, py - y) * inv_radius
                if distance >= 1.0:
                    continue
                weight = (1.0 - distance) ** 0.65
                shaded = tuple(min(255, int(channel * weight)) for channel in color)
                self._pixel(image, px, py, shaded, True)

    def _build_table(self):
        table = self._static
        table[:] = self.NAVY
        if self.width < 6 or self.height < 20:
            return

        # Subtle vertical PC-blue playfield bands.
        table[:, 1:-1:4] = (0, 7, 28)
        top = max(12, min(18, self.height // 7))
        bottom = self.height - 5
        left, right = 1, self.width - 2
        self._line(table, left, top, left, bottom - 9, self.CYAN)
        self._line(table, right, top, right, bottom - 9, self.CYAN)
        self._line(table, left, top, self.width // 2, top - 4, self.MAGENTA)
        self._line(table, self.width // 2, top - 4, right, top, self.MAGENTA)

        # Shooter lane and launch arrow.
        lane = max(left + 3, right - 3)
        self._line(table, lane, top + 8, lane, bottom - 2, (50, 120, 190))
        self._line(table, lane + 1, top + 8, right, top + 14, self.CYAN)
        self._line(table, lane - 1, bottom - 11, lane - 1, bottom - 6, self.YELLOW)
        self._line(table, lane - 2, bottom - 8, lane - 1, bottom - 11, self.YELLOW)
        self._line(table, lane, bottom - 8, lane - 1, bottom - 11, self.YELLOW)

        # Ramps make the silhouette recognizable even on 16 columns.
        self._line(table, left + 2, top + 23, self.width // 2 - 2, top + 12, (70, 170, 255))
        self._line(table, self.width // 2 - 2, top + 12, right - 3, top + 27, (70, 170, 255))
        self._line(table, left + 3, top + 54, self.width // 2, top + 42, self.MAGENTA)
        self._line(table, self.width // 2, top + 42, right - 4, top + 55, self.MAGENTA)

        # Three pop bumpers with a hot center and cool metal rim.
        for index, (bx, by) in enumerate(self._bumper_positions()):
            self._circle(table, bx, by, self._bumper_radius() + 1, (30, 100, 150))
            self._circle(table, bx, by, self._bumper_radius(), (100, 10, 80), True)
            self._pixel(table, bx, by, (255, 80 + index * 50, 20))

        # Five drop targets / mode lamps.
        lamp_y = top + 68
        for index in range(5):
            x = 3 + int(index * max(1, (self.width - 7) / 4))
            self._pixel(table, x, lamp_y, (35, 55, 75))
            self._pixel(table, x, lamp_y + 1, (70, 20, 50))

        # Slingshots and drain guides.
        sling_y = bottom - 27
        self._line(table, left + 2, sling_y - 10, left + 7, sling_y, self.RED)
        self._line(table, left + 7, sling_y, left + 3, sling_y + 5, self.ORANGE)
        self._line(table, right - 3, sling_y - 10, right - 8, sling_y, self.RED)
        self._line(table, right - 8, sling_y, right - 4, sling_y + 5, self.ORANGE)
        self._line(table, left, bottom - 9, self.width // 2 - 3, bottom - 1, (80, 140, 180))
        self._line(table, right, bottom - 9, self.width // 2 + 3, bottom - 1, (80, 140, 180))

    def _bumper_positions(self) -> List[Tuple[int, int]]:
        top = max(12, min(18, self.height // 7))
        return [
            (max(4, self.width // 3), top + 31),
            (min(self.width - 5, (self.width * 2) // 3), top + 31),
            (self.width // 2, top + 46),
        ]

    def _bumper_radius(self) -> int:
        return 1 if self.width < 24 else 2

    def _prepare_plant_table(self):
        """Project calibrated strip/LED masks into playfield coordinates."""
        if not self.plant_aware_enabled():
            return
        masks = self.get_plant_masks()
        if self._plant_geometry_identity == id(masks):
            return
        # Frames are rendered bottom-to-top into canonical (strip, LED) order.
        self._plant_foliage[:] = masks.foliage.T[::-1]
        self._plant_globes[:] = masks.globes.T[::-1]
        self._plant_clearance[:] = masks.clearance.T[::-1]
        self._plant_geometry_identity = id(masks)

    def _nearest_plant_safe_point(self, x: float, y: float) -> Tuple[float, float]:
        """Find a deterministic nearby launch/collision point outside clearance."""
        self._prepare_plant_table()
        origin_x = min(self.width - 1, max(0, int(round(x))))
        origin_y = min(self.height - 1, max(0, int(round(y))))
        max_radius = max(self.width, self.height)
        for radius in range(max_radius + 1):
            candidates = []
            top, bottom = origin_y - radius, origin_y + radius
            left, right = origin_x - radius, origin_x + radius
            perimeter = []
            if 0 <= top < self.height:
                perimeter.extend((xx, top) for xx in range(max(0, left), min(self.width, right + 1)))
            if radius and 0 <= bottom < self.height:
                perimeter.extend((xx, bottom) for xx in range(max(0, left), min(self.width, right + 1)))
            for yy in range(max(0, top + 1), min(self.height, bottom)):
                if 0 <= left < self.width:
                    perimeter.append((left, yy))
                if radius and right != left and 0 <= right < self.width:
                    perimeter.append((right, yy))
            for xx, yy in perimeter:
                if 2 <= xx <= self.width - 3 and not self._plant_clearance[yy, xx]:
                    candidates.append((abs(yy - y) + abs(xx - x), yy, xx))
            if candidates:
                _, safe_y, safe_x = min(candidates)
                return float(safe_x), float(safe_y)
        return x, y

    def _collide_with_plants(self, previous_x: float, previous_y: float):
        """Treat masked plants as swept, scoring pinball deflectors."""
        if not self.plant_aware_enabled():
            return
        self._prepare_plant_table()
        distance = math.hypot(self.ball_x - previous_x, self.ball_y - previous_y)
        steps = max(1, int(math.ceil(distance * 2.0)))
        collision = None
        for step in range(1, steps + 1):
            fraction = step / steps
            x = previous_x + (self.ball_x - previous_x) * fraction
            y = previous_y + (self.ball_y - previous_y) * fraction
            px, py = int(round(x)), int(round(y))
            if (0 <= px < self.width and 0 <= py < self.height
                    and self._plant_clearance[py, px]):
                collision = (px, py)
                break
        if collision is None:
            return

        px, py = collision
        # Clearance is itself collidable, so attribute its outer edge to a
        # nearby globe when appropriate instead of downgrading it to foliage.
        attribution_radius = max(0, int(self.params.get("plant_clearance", 1))) + 1
        x0, x1 = max(0, px - attribution_radius), min(self.width, px + attribution_radius + 1)
        y0, y1 = max(0, py - attribution_radius), min(self.height, py + attribution_radius + 1)
        is_globe = bool(np.any(self._plant_globes[y0:y1, x0:x1]))
        self.ball_x, self.ball_y = self._nearest_plant_safe_point(previous_x, previous_y)
        # A full reversal is stable even inside dense foliage. Globes kick
        # harder, while foliage behaves like a rubberized routing rail.
        kick = 1.08 if is_globe else 0.88
        self.ball_vx = -self.ball_vx * kick
        self.ball_vy = -self.ball_vy * kick
        if abs(self.ball_vx) < 5.0:
            self.ball_vx = 5.0 if self.ball_x < px else -5.0
        if abs(self.ball_vy) < 8.0:
            self.ball_vy = -8.0 if self.ball_y >= py else 8.0
        if self._event_cooldown <= 0.0:
            color = self.YELLOW if is_globe else self.GREEN
            self._award(2500 if is_globe else 600, "", color)
            self.bursts.append(Burst(px, py, color, life=0.45))
            self._event_cooldown = 0.10
            self._plant_hits += 1

    def generate_frame(self, time_elapsed: float, frame_count: int) -> Any:
        fps = max(24.0, min(120.0, float(self.params.get("render_fps", 100.0))))
        if (self.last_rendered_frame is not None and self.last_render_elapsed is not None
                and 0.0 <= time_elapsed - self.last_render_elapsed < (1.0 / fps) - 1e-9):
            return self.rendered_frame(self.last_rendered_frame, changed=False)

        if self.last_elapsed is None or time_elapsed < self.last_elapsed:
            dt = 0.0
        else:
            dt = min(0.05, time_elapsed - self.last_elapsed)
        self.last_elapsed = time_elapsed
        self.last_render_elapsed = time_elapsed
        self._update(dt * max(0.1, float(self.params.get("speed", 1.35))))
        self._render()

        frame = self.next_frame_buffer(clear=False)
        # Canonical layout is strip-major; physical LEDs run bottom-to-top.
        frame.reshape(self.width, self.height, 3)[:] = self._canvas[::-1].transpose(1, 0, 2)
        self.apply_brightness_array(frame, out=frame)
        self.last_rendered_frame = frame
        return self.rendered_frame(frame, changed=True)

    def _update(self, dt: float):
        if dt <= 0.0:
            return
        self._sim_time += dt
        self._event_cooldown = max(0.0, self._event_cooldown - dt)
        self._hit_flash = max(0.0, self._hit_flash - dt * 4.0)
        self._failure_flash = max(0.0, self._failure_flash - dt * 2.5)
        self._success_flash = max(0.0, self._success_flash - dt * 2.2)
        self.display_score += (self.score - self.display_score) * min(1.0, dt * 12.0)

        for burst in self.bursts:
            burst.age += dt
        self.bursts[:] = [burst for burst in self.bursts if burst.age < burst.life]

        if self.mode != "READY":
            self.mode_time -= dt
            if self.mode_time <= 0.0:
                if self.mode in ("JACKPOT", "MULTI"):
                    self._success("NICE", 25000)
                self.mode = "READY"
                self.multiplier = 1
        elif self._sim_time >= self._next_mode_at:
            self._start_minigame()

        if self.drain_time > 0.0:
            self.drain_time -= dt
            if self.drain_time <= 0.0:
                self._launch_ball()
            return

        # Small, stable pinball simulation. The table is narrow, so velocities
        # are deliberately biased upward to keep action distributed vertically.
        gravity = 31.0
        previous_x, previous_y = self.ball_x, self.ball_y
        self.ball_vy += gravity * dt
        self.ball_x += self.ball_vx * dt
        self.ball_y += self.ball_vy * dt
        left, right = 2.0, self.width - 3.0
        table_top = max(10.0, self.height / 10.0)

        if self.ball_x < left:
            self.ball_x = left
            self.ball_vx = abs(self.ball_vx) * 0.92 + 2.0
            self._wall_spark()
        elif self.ball_x > right:
            self.ball_x = right
            self.ball_vx = -abs(self.ball_vx) * 0.92 - 2.0
            self._wall_spark()
        if self.ball_y < table_top:
            self.ball_y = table_top
            self.ball_vy = abs(self.ball_vy) + 8.0
            self._award(750, "750", self.CYAN)

        self._collide_with_plants(previous_x, previous_y)

        radius = self._bumper_radius() + 1.2
        for bx, by in self._bumper_positions():
            dx, dy = self.ball_x - bx, self.ball_y - by
            distance2 = dx * dx + dy * dy
            if distance2 < radius * radius and self._event_cooldown <= 0.0:
                if distance2 <= 0.04:
                    speed = math.hypot(self.ball_vx, self.ball_vy) or 1.0
                    nx, ny = -self.ball_vx / speed, -self.ball_vy / speed
                else:
                    distance = math.sqrt(distance2)
                    nx, ny = dx / distance, dy / distance
                impulse = 42.0
                self.ball_x = bx + nx * radius
                self.ball_y = by + ny * radius
                self.ball_vx = nx * impulse + self.random.uniform(-8.0, 8.0)
                self.ball_vy = ny * impulse - 14.0
                self._event_cooldown = 0.08
                self._hit_bumper(bx, by)

        target_y = max(12, min(18, self.height // 7)) + 68
        if abs(self.ball_y - target_y) < 1.2 and self._event_cooldown <= 0.0:
            index = min(4, max(0, int((self.ball_x - 2) * 5 / max(1, self.width - 4))))
            if not self.lamps[index]:
                self.lamps[index] = True
                self._award(1500, "+1500", self.GREEN)
                if all(self.lamps):
                    self.lamps = [False] * 5
                    self._success("BANK", 15000)
            self.ball_vy = -abs(self.ball_vy) - 9.0
            self._event_cooldown = 0.09

        flipper_y = self.height - 16.0
        if self.ball_y >= flipper_y and self.ball_y < self.height - 5:
            # Autoplay flippers: a hard upward kick with aim jitter.
            self.ball_y = flipper_y
            self.ball_vy = -self.random.uniform(72.0, 96.0)
            target_x = self.random.uniform(4.0, max(5.0, self.width - 5.0))
            self.ball_vx = (target_x - self.ball_x) * 1.1
            self._award(100, "", self.YELLOW, streak=False)
            self.bursts.append(Burst(self.ball_x, self.ball_y, self.YELLOW, life=0.3))

        # A rare miss gives the failure animation real contrast.
        chaos = max(0.0, min(1.0, float(self.params.get("chaos", 0.72))))
        miss_line = self.height - 7.0
        if self.ball_y > miss_line or (self._sim_time > 2 and self.random.random() < dt * 0.025 * chaos):
            self._drain()

    def _award(self, points: int, label: str, color: Color, streak: bool = True):
        if streak:
            self.streak += 1
        gain = points * max(1, self.multiplier)
        self.score += gain
        self._hit_flash = 1.0

    def _hit_bumper(self, x: float, y: float):
        self._award(1000 + self.streak * 100, "+1000", self.ORANGE)
        self.bursts.append(Burst(x, y, self.random.choice((self.ORANGE, self.MAGENTA, self.CYAN))))
        if self.streak in (5, 10, 20):
            self.multiplier = min(5, self.multiplier + 1)
        if self.streak and self.streak % 12 == 0:
            self._success("STREAK", 10000)

    def _wall_spark(self):
        if self._event_cooldown <= 0.0:
            self._award(250, "+250", self.CYAN, streak=False)
            self.bursts.append(Burst(self.ball_x, self.ball_y, self.CYAN, life=0.25))
            self._event_cooldown = 0.05

    def _success(self, label: str, bonus: int):
        self.score += bonus * max(1, self.multiplier)
        self._success_flash = 1.0
        for x in range(3, self.width - 2, max(2, self.width // 8)):
            self.bursts.append(Burst(x, self.height * 0.5, self.random.choice((self.GREEN, self.YELLOW, self.CYAN))))

    def _drain(self):
        self.balls -= 1
        self.streak = 0
        self.multiplier = 1
        self._failure_flash = 1.0
        self.drain_time = 0.85
        self.bursts.append(Burst(self.ball_x, self.height - 7, self.RED, life=0.7))
        if self.balls <= 0:
            self.balls = 3
            self.score = max(95000, self.score // 2)

    def _launch_ball(self):
        self.ball_x = self.width - 4.0
        self.ball_y = self.height - 18.0
        if self.plant_aware_enabled():
            self.ball_x, self.ball_y = self._nearest_plant_safe_point(
                self.ball_x, self.ball_y
            )
        self.ball_vx = -self.random.uniform(4.0, 12.0)
        self.ball_vy = -self.random.uniform(82.0, 105.0)

    def _start_minigame(self):
        modes = ("JACKPOT", "MULTI", "SKILL")
        self.mode = modes[self.mode_count % len(modes)]
        self.mode_count += 1
        self.mode_time = 3.5
        self.multiplier = min(5, 2 + self.mode_count % 3)
        self._success_flash = 1.0
        self._next_mode_at = self._sim_time + self.random.uniform(7.0, 11.0)

    def _render(self):
        canvas = self._canvas
        np.copyto(canvas, self._static)
        phase = self._sim_time

        # A dark rainbow interference field moves underneath the table art.
        # Palette lookup is much cheaper than per-pixel HSV/trigonometry at 100 Hz.
        np.maximum(canvas, self._background_field.render(phase), out=canvas)

        # Animated border chase lights, exactly on the physical outer columns.
        chase = int(phase * 18.0)
        for y in range(8, self.height - 8, 4):
            color = (self.CYAN, self.MAGENTA, self.YELLOW)[((y // 4) + chase) % 3]
            self._pixel(canvas, 0, y, color)
            self._pixel(canvas, self.width - 1, self.height - 1 - y, color)

        # A tiny spinner and satellite lamps fill the long center playfield with
        # continuous mechanical-looking motion without maintaining particles.
        spinner_y = int(self.height * 0.67)
        spinner_x = self.width // 2
        spinner_phase = int(phase * 14.0) % 8
        spinner_offsets = ((0, -3), (2, -2), (3, 0), (2, 2), (0, 3), (-2, 2), (-3, 0), (-2, -2))
        for offset in (0, 3, 6):
            dx, dy = spinner_offsets[(spinner_phase + offset) % 8]
            self._pixel(canvas, spinner_x + dx, spinner_y + dy,
                        (self.CYAN, self.YELLOW, self.MAGENTA)[offset // 3], True)
        self._line(canvas, spinner_x - 2, spinner_y, spinner_x + 2, spinner_y, (80, 120, 180))

        lamp_y = max(12, min(18, self.height // 7)) + 68
        for index, lit in enumerate(self.lamps):
            x = 3 + int(index * max(1, (self.width - 7) / 4))
            if lit or (self.mode != "READY" and (index + int(phase * 12)) % 3 == 0):
                color = self.GREEN if lit else self.MAGENTA
                self._soft_dot(canvas, x, lamp_y, color, 2.2)

        # Each bumper breathes with a different phase and hue, producing
        # gradients rather than flat on/off discs.
        bumper_colors = (self.ORANGE, self.MAGENTA, self.CYAN)
        for index, (bx, by) in enumerate(self._bumper_positions()):
            pulse = 0.5 + 0.5 * math.sin(phase * 8.0 + index * 2.1)
            color = tuple(int(channel * (0.35 + pulse * 0.65)) for channel in bumper_colors[index])
            self._soft_dot(canvas, bx, by, color, self._bumper_radius() + 2.2 + pulse)
            self._soft_dot(canvas, bx, by, self.WHITE, 0.8 + pulse * 0.4)

        # Flashing bumpers and concentric collision shockwaves.
        if self._hit_flash > 0.0:
            bx, by = min(self._bumper_positions(), key=lambda p: (p[0] - self.ball_x) ** 2 + (p[1] - self.ball_y) ** 2)
            self._circle(canvas, bx, by, self._bumper_radius() + int(self._hit_flash * 3), self.WHITE)
        for burst in self.bursts:
            progress = burst.age / max(0.01, burst.life)
            radius = 1 + int(progress * 7)
            fade = max(0.0, 1.0 - progress)
            color = tuple(int(channel * fade) for channel in burst.color)
            self._circle(canvas, int(burst.x), int(burst.y), radius, color)
            # Four deterministic sparks avoid particle objects and trig.
            spark = radius + 2
            self._pixel(canvas, int(burst.x) + spark, int(burst.y), color, True)
            self._pixel(canvas, int(burst.x) - spark, int(burst.y), color, True)
            self._pixel(canvas, int(burst.x), int(burst.y) + spark, color, True)
            self._pixel(canvas, int(burst.x), int(burst.y) - spark, color, True)

        if self.plant_aware_enabled():
            self._prepare_plant_table()
            # Foliage reads as a cool translucent rubber rail; globes are
            # unmistakable gold scoring landmarks without hiding the ball.
            canvas[self._plant_foliage] = np.maximum(
                canvas[self._plant_foliage], np.array((8, 72, 24), dtype=np.uint8)
            )
            globe_pulse = 125 + int(55 * (0.5 + 0.5 * math.sin(phase * 5.0)))
            canvas[self._plant_globes] = np.maximum(
                canvas[self._plant_globes],
                np.array((globe_pulse, globe_pulse // 2, 8), dtype=np.uint8),
            )

        # Flippers alternate rapidly with layered neon edges.
        flip = int(phase * 9.0) % 2
        fy = self.height - 16
        center = self.width // 2
        self._line(canvas, 4, fy + flip + 1, center - 2, fy - 2 + flip, self.MAGENTA)
        self._line(canvas, self.width - 5, fy + flip + 1, center + 2, fy - 2 + flip, self.CYAN)
        self._line(canvas, 4, fy + flip, center - 2, fy - 3 + flip, self.WHITE)
        self._line(canvas, self.width - 5, fy + flip, center + 2, fy - 3 + flip, self.WHITE)
        self._pixel(canvas, 4, fy + flip, self.RED)
        self._pixel(canvas, self.width - 5, fy + flip, self.RED)

        # Subpixel radial layers keep the fast ball fluid at 100 Hz while the
        # brilliant white core remains easy to follow on individual LEDs.
        if self.drain_time <= 0.0:
            for trail in range(5, 0, -1):
                fraction = trail / 5.0
                tx = self.ball_x - self.ball_vx * 0.010 * trail
                ty = self.ball_y - self.ball_vy * 0.010 * trail
                trail_color = (
                    int(35 * fraction),
                    int(90 * fraction),
                    int((180 + 60 * math.sin(phase * 12.0 + trail)) * fraction),
                )
                self._soft_dot(canvas, tx, ty, trail_color, 1.0 + fraction)
            self._soft_dot(canvas, self.ball_x, self.ball_y, self.CYAN, 2.7)
            self._soft_dot(canvas, self.ball_x, self.ball_y, self.WHITE, 1.35)

        # Full-table event signals are additive and intentionally brief.
        if self._success_flash > 0.0:
            value = int(90 * self._success_flash)
            np.maximum(canvas, np.array((0, value, value // 2), dtype=np.uint8), out=canvas)
        if self._failure_flash > 0.0 and int(phase * 16) % 2 == 0:
            value = int(110 * self._failure_flash)
            canvas[:, :, 0] = np.maximum(canvas[:, :, 0], value)

        # Minigames become a double-helix light show instead of text.
        if self.mode != "READY":
            colors = (self.MAGENTA, self.CYAN, self.YELLOW)
            for y in range(18, self.height - 22, 5):
                wave = math.sin(phase * 10.0 + y * 0.23)
                x = self.width * 0.5 + wave * self.width * 0.34
                color = colors[(y // 5 + self.mode_count) % len(colors)]
                self._soft_dot(canvas, x, y, color, 1.7)
                self._soft_dot(canvas, self.width - 1 - x, y, color, 1.2)

    def get_runtime_stats(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "display_score": int(self.display_score),
            "streak": self.streak,
            "multiplier": self.multiplier,
            "balls": self.balls,
            "minigame": self.mode,
            "active_effects": len(self.bursts),
            "plant_hits": self._plant_hits,
        }
