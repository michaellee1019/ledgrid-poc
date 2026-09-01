#!/usr/bin/env python3
"""Space Invaders animation that plays itself on the LED grid."""

import random
from typing import List, Tuple, Dict, Any, Optional

from animation import AnimationBase
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


Color = Tuple[int, int, int]


class SpaceInvadersAnimation(AnimationBase):
    """Autoplaying Space Invaders with marching enemies, shields, and shots."""

    ANIMATION_NAME = "Space Invaders"
    ANIMATION_DESCRIPTION = "Autoplaying Space Invaders swarm with shields and laser fire"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    INVADER_FRAMES = [
        [
            "111",
            "101",
        ],
        [
            "111",
            "010",
        ],
    ]

    PLAYER_SHAPE = [
        "111",
    ]

    SHIELD_SHAPE = [
        "111",
        "111",
    ]

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.num_strips = getattr(controller, 'strip_count', DEFAULT_STRIP_COUNT)
        self.leds_per_strip = getattr(controller, 'leds_per_strip', DEFAULT_LEDS_PER_STRIP)
        self.board_width = max(1, self.num_strips - 1)
        self.board_height = self.leds_per_strip

        self.invader_width = len(self.INVADER_FRAMES[0][0])
        self.invader_height = len(self.INVADER_FRAMES[0])
        self.player_width = len(self.PLAYER_SHAPE[0])
        self.player_height = len(self.PLAYER_SHAPE)

        self.default_params.update({
            'speed': 1.0,
            'invader_speed': 1.0,
            'player_speed': 1.0,
            'bullet_speed': 1.0,
            'invader_fire_rate': 0.6,
            'player_fire_rate': 2.5,
            'shield_strength': 1,
        })
        self.params = {**self.default_params, **self.config}

        self.random = random.Random()
        self.last_elapsed: Optional[float] = None

        self.invader_move_timer = 0.0
        self.invader_anim_timer = 0.0
        self.player_move_timer = 0.0
        self.player_fire_cooldown = 0.0
        self.game_over_flash = 0.0
        self.pending_reset = False

        self.level = 1
        self.invader_offset_x = 0
        self.invader_offset_y = 0
        self.invader_direction = 1
        self.drop_step = max(1, self.board_height // 20)

        self.invaders: List[Tuple[int, int]] = []
        self.invader_cells: Dict[Tuple[int, int], int] = {}
        self.bullets: List[Dict[str, Any]] = []
        self.explosions: List[Dict[str, Any]] = []
        self.shields: Dict[Tuple[int, int], int] = {}

        self.player_x = max(0, (self.board_width - self.player_width) // 2)
        self.player_y = max(0, self.board_height - 2)

        self.base_bullet_speed = max(8.0, self.board_height / 6.0)

        self._refresh_runtime_params()
        self._reset_wave(level_reset=True)

    def _refresh_runtime_params(self):
        speed = max(0.2, float(self.params.get('speed', 1.0)))
        level_scale = 1.0 + 0.12 * max(0, self.level - 1)

        invader_speed = max(0.2, float(self.params.get('invader_speed', 1.0)))
        player_speed = max(0.2, float(self.params.get('player_speed', 1.0)))
        bullet_speed = max(0.2, float(self.params.get('bullet_speed', 1.0)))

        self.invader_step_interval = max(0.05, 0.55 / (speed * level_scale * invader_speed))
        self.invader_anim_interval = max(0.1, 0.45 / (speed * level_scale))
        self.player_step_interval = max(0.05, 0.25 / (speed * player_speed))
        self.bullet_step_speed = self.base_bullet_speed * speed * bullet_speed

        self.invader_fire_rate = max(0.05, float(self.params.get('invader_fire_rate', 0.6))) * speed * level_scale
        self.player_fire_rate = max(0.1, float(self.params.get('player_fire_rate', 2.5))) * speed

    def get_parameter_schema(self) -> Dict[str, Any]:
        schema = super().get_parameter_schema()
        schema.update({
            'invader_speed': {
                'type': 'float',
                'min': 0.2,
                'max': 4.0,
                'default': 1.0,
                'description': 'Horizontal marching speed of invaders'
            },
            'player_speed': {
                'type': 'float',
                'min': 0.2,
                'max': 4.0,
                'default': 1.0,
                'description': 'Auto-player movement speed'
            },
            'bullet_speed': {
                'type': 'float',
                'min': 0.2,
                'max': 4.0,
                'default': 1.0,
                'description': 'Laser travel speed multiplier'
            },
            'invader_fire_rate': {
                'type': 'float',
                'min': 0.1,
                'max': 3.0,
                'default': 0.6,
                'description': 'Invader shots per second'
            },
            'player_fire_rate': {
                'type': 'float',
                'min': 0.5,
                'max': 6.0,
                'default': 2.5,
                'description': 'Player shots per second'
            },
            'shield_strength': {
                'type': 'int',
                'min': 0,
                'max': 3,
                'default': 1,
                'description': 'Shield durability in hits (0 disables shields)'
            },
        })
        return schema

    def update_parameters(self, params: Dict[str, Any]):
        super().update_parameters(params)
        self._refresh_runtime_params()

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Color]:
        total_pixels = self.num_strips * self.leds_per_strip
        frame: List[Color] = [(0, 0, 0)] * total_pixels

        delta = 0.0
        if self.last_elapsed is None:
            self.last_elapsed = time_elapsed
        else:
            delta = max(0.0, time_elapsed - self.last_elapsed)
            self.last_elapsed = time_elapsed

        self._update_state(delta)

        if self.game_over_flash > 0.0:
            flash_strength = int(90 * min(1.0, self.game_over_flash))
            if flash_strength > 0:
                tint = self.apply_brightness((flash_strength, 15, 15))
                for strip in range(1, self.num_strips):
                    base_index = strip * self.leds_per_strip
                    for led in range(self.board_height):
                        phys_led = (self.leds_per_strip - 1) - led
                        idx = base_index + phys_led
                        if idx < len(frame):
                            frame[idx] = tint

        for (x, y), hp in self.shields.items():
            if hp <= 0:
                continue
            color = (40, 200, 220) if hp == 1 else (80, 255, 255)
            self._set_cell(frame, x, y, color)

        invader_frame = self.invader_anim_frame
        for idx, (base_x, base_y) in enumerate(self.invaders):
            color = self._invader_color(idx)
            self._draw_sprite(frame, base_x + self.invader_offset_x,
                              base_y + self.invader_offset_y,
                              self.INVADER_FRAMES[invader_frame], color)

        self._draw_sprite(frame, self.player_x, self.player_y, self.PLAYER_SHAPE, (220, 220, 255))

        for bullet in self.bullets:
            color = bullet['color']
            self._set_cell(frame, bullet['x'], bullet['y'], color)

        for explosion in self.explosions:
            alpha = min(1.0, explosion['ttl'] / explosion['duration'])
            color = (int(255 * alpha), int(120 * alpha), int(40 * alpha))
            self._set_cell_blend(frame, explosion['x'], explosion['y'], color, alpha)

        return frame

    def _update_state(self, delta: float):
        if self.board_width <= 0 or self.board_height <= 0:
            return

        if self.game_over_flash > 0.0:
            self.game_over_flash = max(0.0, self.game_over_flash - delta)
            if self.pending_reset and self.game_over_flash <= 0.0:
                self._reset_wave(level_reset=True)
            return

        if self.pending_reset:
            self._reset_wave(level_reset=True)
            return

        self.invader_anim_timer += delta
        if self.invader_anim_timer >= self.invader_anim_interval:
            self.invader_anim_timer = 0.0
            self.invader_anim_frame = (self.invader_anim_frame + 1) % len(self.INVADER_FRAMES)
            self._build_invader_cells()

        if self.player_fire_cooldown > 0.0:
            self.player_fire_cooldown = max(0.0, self.player_fire_cooldown - delta)

        self.invader_move_timer += delta
        while self.invader_move_timer >= self.invader_step_interval:
            self.invader_move_timer -= self.invader_step_interval
            self._step_invaders()

        self.player_move_timer += delta
        while self.player_move_timer >= self.player_step_interval:
            self.player_move_timer -= self.player_step_interval
            self._move_player()

        self._update_bullets(delta)

        if self.invaders:
            if self.random.random() < delta * self.invader_fire_rate:
                self._fire_invader()

        if self.player_fire_cooldown <= 0.0:
            self._fire_player()

        for explosion in list(self.explosions):
            explosion['ttl'] -= delta
            if explosion['ttl'] <= 0.0:
                self.explosions.remove(explosion)

        if not self.invaders and not self.pending_reset:
            self.level += 1
            self._refresh_runtime_params()
            self._reset_wave(level_reset=False)

    def _reset_wave(self, level_reset: bool):
        if level_reset:
            self.level = 1
        self.invaders.clear()
        self.bullets.clear()
        self.explosions.clear()
        self.shields.clear()
        self.invader_offset_x = 0
        self.invader_offset_y = 0
        self.invader_direction = 1
        self.invader_anim_frame = 0
        self.invader_move_timer = 0.0
        self.invader_anim_timer = 0.0
        self.player_move_timer = 0.0
        self.player_fire_cooldown = 0.0
        self.pending_reset = False

        self.player_x = max(0, (self.board_width - self.player_width) // 2)
        self.player_y = max(0, self.board_height - 2)

        self._spawn_invaders()
        self._spawn_shields()
        self._build_invader_cells()

    def _spawn_invaders(self):
        gap_x = 1
        gap_y = 2
        columns = max(2, min(6, (self.board_width + gap_x) // (self.invader_width + gap_x)))
        rows = max(3, min(5, self.board_height // 12))

        formation_width = columns * self.invader_width + (columns - 1) * gap_x
        start_x = max(0, (self.board_width - formation_width) // 2)
        start_y = 2

        for row in range(rows):
            for col in range(columns):
                x = start_x + col * (self.invader_width + gap_x)
                y = start_y + row * (self.invader_height + gap_y)
                self.invaders.append((x, y))

    def _spawn_shields(self):
        strength = int(self.params.get('shield_strength', 1))
        if strength <= 0:
            return

        shield_width = len(self.SHIELD_SHAPE[0])
        shield_height = len(self.SHIELD_SHAPE)
        gap_x = 2
        max_shields = max(1, min(3, (self.board_width + gap_x) // (shield_width + gap_x)))
        total_width = max_shields * shield_width + (max_shields - 1) * gap_x
        start_x = max(0, (self.board_width - total_width) // 2)
        y = max(2, self.player_y - 6)

        for shield in range(max_shields):
            base_x = start_x + shield * (shield_width + gap_x)
            for dy, row in enumerate(self.SHIELD_SHAPE):
                for dx, cell in enumerate(row):
                    if cell != '1':
                        continue
                    self.shields[(base_x + dx, y + dy)] = strength

    def _build_invader_cells(self):
        self.invader_cells = {}
        for idx, (base_x, base_y) in enumerate(self.invaders):
            self._add_sprite_cells(base_x + self.invader_offset_x,
                                   base_y + self.invader_offset_y,
                                   self.INVADER_FRAMES[self.invader_anim_frame],
                                   idx)

    def _add_sprite_cells(self, base_x: int, base_y: int, sprite: List[str], idx: int):
        for dy, row in enumerate(sprite):
            for dx, cell in enumerate(row):
                if cell != '1':
                    continue
                self.invader_cells[(base_x + dx, base_y + dy)] = idx

    def _step_invaders(self):
        if not self.invaders:
            return

        min_x = min(x for x, _ in self.invaders) + self.invader_offset_x
        max_x = max(x for x, _ in self.invaders) + self.invader_offset_x + self.invader_width - 1
        next_offset = self.invader_offset_x + self.invader_direction

        if min_x + self.invader_direction < 0 or max_x + self.invader_direction >= self.board_width:
            self.invader_direction *= -1
            self.invader_offset_y += self.drop_step

            max_y = max(y for _, y in self.invaders) + self.invader_offset_y + self.invader_height - 1
            if max_y >= self.player_y:
                self._trigger_game_over()
                return
        else:
            self.invader_offset_x = next_offset

        self._build_invader_cells()

    def _move_player(self):
        target_x = self._nearest_invader_center()
        if target_x is None:
            return
        player_center = self.player_x + self.player_width // 2
        if player_center < target_x:
            self.player_x = min(self.board_width - self.player_width, self.player_x + 1)
        elif player_center > target_x:
            self.player_x = max(0, self.player_x - 1)

    def _nearest_invader_center(self) -> Optional[int]:
        if not self.invaders:
            return None
        player_center = self.player_x + self.player_width // 2
        best_x = None
        best_dist = None
        for base_x, base_y in self.invaders:
            x = base_x + self.invader_offset_x + self.invader_width // 2
            y = base_y + self.invader_offset_y
            if y < 0:
                continue
            dist = abs(x - player_center)
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_x = x
        return best_x

    def _fire_player(self):
        if not self.invaders:
            return
        player_center = self.player_x + self.player_width // 2
        aligned = any(
            (player_center, y) in self.invader_cells
            for y in range(0, self.player_y)
        )
        if not aligned:
            return
        self._spawn_bullet(player_center, self.player_y - 1, -1, (220, 220, 255), False)
        self.player_fire_cooldown = 1.0 / self.player_fire_rate

    def _fire_invader(self):
        if not self.invaders:
            return
        candidates: Dict[int, Tuple[int, int]] = {}
        for base_x, base_y in self.invaders:
            center_x = base_x + self.invader_offset_x + self.invader_width // 2
            y = base_y + self.invader_offset_y + self.invader_height - 1
            existing = candidates.get(center_x)
            if existing is None or y > existing[1]:
                candidates[center_x] = (center_x, y)
        if not candidates:
            return
        center_x, y = self.random.choice(list(candidates.values()))
        self._spawn_bullet(center_x, y + 1, 1, (255, 80, 80), True)

    def _spawn_bullet(self, x: int, y: int, direction: int, color: Color, from_invader: bool):
        if y < 0 or y >= self.board_height:
            return
        self.bullets.append({
            'x': x,
            'y': y,
            'dy': direction,
            'color': color,
            'from_invader': from_invader,
            'progress': 0.0,
        })

    def _update_bullets(self, delta: float):
        if not self.bullets:
            return
        for bullet in list(self.bullets):
            bullet['progress'] += delta * self.bullet_step_speed
            while bullet['progress'] >= 1.0:
                bullet['progress'] -= 1.0
                bullet['y'] += bullet['dy']
                if bullet['y'] < 0 or bullet['y'] >= self.board_height:
                    if bullet in self.bullets:
                        self.bullets.remove(bullet)
                    break
                if self._handle_bullet_collision(bullet):
                    if bullet in self.bullets:
                        self.bullets.remove(bullet)
                    break

    def _handle_bullet_collision(self, bullet: Dict[str, Any]) -> bool:
        pos = (bullet['x'], bullet['y'])
        if pos in self.shields:
            self.shields[pos] -= 1
            if self.shields[pos] <= 0:
                del self.shields[pos]
            return True

        if bullet['from_invader']:
            if self.player_y <= bullet['y'] < self.player_y + self.player_height:
                if self.player_x <= bullet['x'] < self.player_x + self.player_width:
                    self._trigger_game_over()
                    return True
        else:
            if pos in self.invader_cells:
                idx = self.invader_cells[pos]
                if 0 <= idx < len(self.invaders):
                    self._explode_at(pos[0], pos[1])
                    del self.invaders[idx]
                    self._build_invader_cells()
                return True
        return False

    def _explode_at(self, x: int, y: int):
        self.explosions.append({
            'x': x,
            'y': y,
            'ttl': 0.35,
            'duration': 0.35,
        })

    def _trigger_game_over(self):
        self.game_over_flash = 1.0
        self.pending_reset = True

    def _invader_color(self, idx: int) -> Color:
        row = idx % 3
        if row == 0:
            return (80, 255, 120)
        if row == 1:
            return (60, 200, 255)
        return (255, 180, 60)

    def _draw_sprite(self, frame: List[Color], base_x: int, base_y: int, sprite: List[str], color: Color):
        for dy, row in enumerate(sprite):
            for dx, cell in enumerate(row):
                if cell != '1':
                    continue
                self._set_cell(frame, base_x + dx, base_y + dy, color)

    def _set_cell(self, frame: List[Color], x: int, y: int, color: Color):
        if x < 0 or x >= self.board_width:
            return
        if y < 0 or y >= self.board_height:
            return
        self._set_pixel(frame, x + 1, y, color)

    def _set_cell_blend(self, frame: List[Color], x: int, y: int, color: Color, alpha: float):
        if x < 0 or x >= self.board_width:
            return
        if y < 0 or y >= self.board_height:
            return
        self._set_pixel_blend(frame, x + 1, y, color, alpha)

    def _set_pixel(self, frame: List[Color], strip: int, led: int, color: Color):
        if strip < 0 or strip >= self.num_strips:
            return
        if led < 0 or led >= self.leds_per_strip:
            return
        phys_led = (self.leds_per_strip - 1) - led
        idx = strip * self.leds_per_strip + phys_led
        if 0 <= idx < len(frame):
            frame[idx] = self.apply_brightness(color)

    def _set_pixel_blend(self, frame: List[Color], strip: int, led: int, color: Color, alpha: float):
        if alpha <= 0.0:
            return
        alpha = min(1.0, alpha)
        scaled = self.apply_brightness((
            int(color[0] * alpha),
            int(color[1] * alpha),
            int(color[2] * alpha),
        ))
        if strip < 0 or strip >= self.num_strips:
            return
        if led < 0 or led >= self.leds_per_strip:
            return
        phys_led = (self.leds_per_strip - 1) - led
        idx = strip * self.leds_per_strip + phys_led
        if 0 <= idx < len(frame):
            base = frame[idx]
            frame[idx] = (
                min(255, base[0] + scaled[0]),
                min(255, base[1] + scaled[1]),
                min(255, base[2] + scaled[2]),
            )
