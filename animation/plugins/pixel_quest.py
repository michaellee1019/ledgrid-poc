#!/usr/bin/env python3
"""Procedural, self-playing 8-bit overworld adventure for a portrait LED wall."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from animation import AnimationBase


Color = Tuple[int, int, int]


@dataclass
class Monster:
    kind: str
    level: int
    hp: int
    max_hp: int
    boss: bool
    x: float
    attack_timer: float = 0.0
    hit_flash: float = 0.0


@dataclass
class PowerUp:
    kind: str
    x: float
    age: float = 0.0
    applied: bool = False


@dataclass
class Burst:
    x: float
    y: float
    color: Color
    age: float = 0.0
    life: float = 0.7


class PixelQuestAnimation(AnimationBase):
    """Endless procedural quest with deterministic autoplay combat."""

    ANIMATION_NAME = "Pixel Quest: Wildlands"
    ANIMATION_DESCRIPTION = (
        "An endless procedural RPG with generated biomes, leveling heroes and "
        "monsters, powerups, spell combat, and escalating boss fights"
    )
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"

    BIOMES = ("meadow", "forest", "desert", "ruins", "crystal")
    MONSTERS = {
        "meadow": ("mossling", "razorhog", "bee_knight"),
        "forest": ("nightwing", "sporeling", "dire_wolf"),
        "desert": ("sandclaw", "scarab", "fire_imp"),
        "ruins": ("stoneguard", "lost_armor", "void_eye"),
        "crystal": ("frost_wisp", "prism_bug", "storm_orb"),
    }
    BOSSES = {
        "meadow": "thorn_titan",
        "forest": "moon_drake",
        "desert": "dune_colossus",
        "ruins": "arcane_guardian",
        "crystal": "prism_hydra",
    }

    NIGHT = (3, 6, 16)
    INK = (7, 13, 24)
    WHITE = (245, 245, 220)
    GOLD = (255, 202, 32)
    HERO_CYAN = (48, 220, 255)
    HERO_DARK = (7, 62, 118)
    HERO_SCARF = (255, 66, 92)
    SKIN = (236, 166, 92)
    BLADE = (215, 240, 235)
    MAGIC = (176, 70, 255)
    HEALTH = (255, 38, 68)
    MANA = (35, 125, 255)
    HIT = (255, 250, 225)

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.width, self.height = self.get_strip_info()
        self.default_params.update({
            "speed": 1.0,
            "brightness": 1.0,
            "render_fps": 45.0,
            "difficulty": 1.0,
            "seed": 1986,
            "show_hud": True,
        })
        self.params = {**self.default_params, **self.config}
        self.random = random.Random(int(self.params.get("seed", 1986)))
        self._canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self.last_elapsed: Optional[float] = None
        self.last_render_elapsed: Optional[float] = None
        self.last_rendered_frame: Optional[np.ndarray] = None
        self.session_defeated = 0
        self.session_bosses = 0
        self.session_powerups = 0
        self.session_level_ups = 0
        self.longest_run = 0.0
        self.run_number = 0
        self.bursts: List[Burst] = []
        self._reset_run()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            "speed": {"type": "float", "min": 0.25, "max": 3.0, "default": 1.0,
                      "description": "Quest and combat speed"},
            "render_fps": {"type": "float", "min": 15.0, "max": 90.0, "default": 45.0,
                           "description": "Maximum visual refresh rate"},
            "difficulty": {"type": "float", "min": 0.55, "max": 1.8, "default": 1.0,
                           "description": "Monster health and damage scaling"},
            "seed": {"type": "int", "min": 0, "max": 9999, "default": 1986,
                     "description": "Repeatable procedural adventure seed"},
            "show_hud": {"type": "bool", "default": True,
                         "description": "Show health, magic, XP, and levels"},
        })
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        reset = "seed" in new_params
        super().update_parameters(new_params)
        if reset:
            self.random.seed(int(self.params.get("seed", 1986)))
            self.run_number = 0
            self._reset_session_counters()
            self._reset_run()
        self.last_render_elapsed = None

    def _reset_session_counters(self):
        self.session_defeated = 0
        self.session_bosses = 0
        self.session_powerups = 0
        self.session_level_ups = 0
        self.longest_run = 0.0

    def _reset_run(self):
        self.run_number += 1
        seed = int(self.params.get("seed", 1986)) + self.run_number * 100003
        self.random.seed(seed)
        self.run_time = 0.0
        self.stage_number = 0
        self.stage_progress = 0.0
        self.stage_duration = 22.0
        self.stage_seed = seed
        self.biome = "meadow"
        self.mode = "title"
        self.mode_time = 0.0
        self.hero_level = 1
        self.hero_xp = 0
        self.hero_xp_next = 28
        self.hero_max_hp = 42
        self.hero_hp = self.hero_max_hp
        self.hero_max_mp = 24
        self.hero_mp = self.hero_max_mp
        self.hero_attack = 10
        self.hero_magic = 15
        self.hero_x = (self.width - 1) / 2
        self.hero_attack_timer = 0.0
        self.combat_turn = 0
        self.current_monster: Optional[Monster] = None
        self.powerup: Optional[PowerUp] = None
        self.encounter_thresholds: List[float] = []
        self.encounter_index = 0
        self.stage_start_ratio = .25
        self.stage_end_ratio = .75
        self.stage_bends = 1.5
        self.attack_effect = ""
        self.attack_flash = 0.0
        self.level_flash = 0.0
        self.damage_flash = 0.0
        self.bursts.clear()
        self.last_elapsed = None
        self.last_render_elapsed = None
        self.last_rendered_frame = None

    def _begin_stage(self, number: int):
        self.stage_number = number
        stage_rng = random.Random(
            int(self.params.get("seed", 1986)) + self.run_number * 100003 + number * 7919
        )
        choices = list(self.BIOMES)
        if number > 1 and self.biome in choices:
            choices.remove(self.biome)
        self.biome = choices[stage_rng.randrange(len(choices))]
        self.stage_seed = stage_rng.randrange(1_000_000)
        self.stage_duration = stage_rng.uniform(18.0, 28.0)
        encounter_count = min(5, 2 + number // 3)
        self.encounter_thresholds = []
        for index in range(encounter_count):
            base = (index + 1) / (encounter_count + 1)
            self.encounter_thresholds.append(max(.12, min(.88, base + stage_rng.uniform(-.045, .045))))
        self.encounter_thresholds.sort()
        self.encounter_index = 0
        self.stage_progress = 0.0
        self.stage_start_ratio = stage_rng.uniform(.18, .34)
        self.stage_end_ratio = stage_rng.uniform(.68, .82)
        if number & 1:
            self.stage_start_ratio, self.stage_end_ratio = self.stage_end_ratio, self.stage_start_ratio
        self.stage_bends = stage_rng.uniform(1.0, 2.75)
        self.current_monster = None
        self.powerup = None
        self.mode = "travel"
        self.mode_time = 0.0

    def _spawn_monster(self):
        is_last = self.encounter_index == len(self.encounter_thresholds) - 1
        boss = self.stage_number % 4 == 0 and is_last
        level = max(1, self.stage_number + self.encounter_index // 2 + (2 if boss else 0))
        difficulty = max(.55, min(1.8, float(self.params.get("difficulty", 1.0))))
        if boss:
            kind = self.BOSSES[self.biome]
            max_hp = int((62 + level * 13) * difficulty)
        else:
            kinds = self.MONSTERS[self.biome]
            kind = kinds[(self.stage_seed + self.encounter_index * 13) % len(kinds)]
            max_hp = int((16 + level * 6) * difficulty)
        facing = -1 if self.hero_x > (self.width - 1) / 2 else 1
        monster_x = max(4.0, min(self.width - 5.0, self.hero_x + facing * (8 if boss else 7)))
        self.current_monster = Monster(kind, level, max_hp, max_hp, boss, monster_x)
        self.encounter_index += 1
        self.hero_attack_timer = .28
        self.combat_turn = 0
        self.mode = "boss" if boss else "combat"
        self.mode_time = 0.0
        self.attack_effect = "warning"
        self.attack_flash = .45

    def _advance_game(self, dt: float):
        dt = max(0.0, min(.25, dt))
        self.run_time += dt
        self.longest_run = max(self.longest_run, self.run_time)
        self.mode_time += dt
        self.attack_flash = max(0.0, self.attack_flash - dt)
        self.level_flash = max(0.0, self.level_flash - dt)
        self.damage_flash = max(0.0, self.damage_flash - dt)
        if self.current_monster is not None:
            self.current_monster.hit_flash = max(0.0, self.current_monster.hit_flash - dt)
        for burst in self.bursts:
            burst.age += dt
        self.bursts = [burst for burst in self.bursts if burst.age < burst.life]

        if self.mode == "title":
            if self.mode_time >= 2.5:
                self._begin_stage(1)
            return
        if self.mode == "travel":
            self.stage_progress = min(1.0, self.stage_progress + dt / self.stage_duration)
            self.hero_x = self._route_x(self.stage_progress)
            if (self.encounter_index < len(self.encounter_thresholds)
                    and self.stage_progress >= self.encounter_thresholds[self.encounter_index]):
                self._spawn_monster()
            elif self.stage_progress >= 1.0 and self.encounter_index >= len(self.encounter_thresholds):
                self.mode = "stage_clear"
                self.mode_time = 0.0
                self.hero_hp = min(self.hero_max_hp, self.hero_hp + max(2, self.hero_max_hp // 10))
                self._add_burst(self.hero_x, self.height * .58, self.GOLD, 1.15)
            return
        if self.mode in ("combat", "boss"):
            self._advance_combat(dt)
            return
        if self.mode == "reward":
            if self.powerup is not None:
                self.powerup.age += dt
                if self.powerup.age >= .65 and not self.powerup.applied:
                    self._apply_powerup(self.powerup)
            if self.mode_time >= 1.75:
                self.powerup = None
                self.mode = "travel"
                self.mode_time = 0.0
            return
        if self.mode == "stage_clear":
            if self.mode_time >= 2.4:
                self._begin_stage(self.stage_number + 1)
            return
        if self.mode == "game_over" and self.mode_time >= 3.2:
            self._reset_run()

    def _advance_combat(self, dt: float):
        monster = self.current_monster
        if monster is None:
            self.mode = "travel"
            return
        self.hero_attack_timer -= dt
        monster.attack_timer -= dt
        attacks = 0
        while self.hero_attack_timer <= 0.0 and monster.hp > 0 and attacks < 2:
            use_magic = self.hero_mp >= 5 and (monster.boss or self.combat_turn % 3 == 2)
            if use_magic:
                damage = self.hero_magic + self.random.randrange(0, 5 + self.hero_level)
                self.hero_mp -= 5
                self.attack_effect = "magic"
                color = self.MAGIC
            else:
                damage = self.hero_attack + self.random.randrange(0, 4 + self.hero_level // 2)
                self.hero_mp = min(self.hero_max_mp, self.hero_mp + 1)
                self.attack_effect = "slash"
                color = self.BLADE
            if self.random.random() < .11 + min(.12, self.hero_level * .006):
                damage *= 2
                self.attack_effect = "critical"
                color = self.GOLD
            monster.hp = max(0, monster.hp - damage)
            monster.hit_flash = .16
            self.attack_flash = .22
            self.combat_turn += 1
            self._add_burst(monster.x, self.height * .64, color)
            interval = max(.38, .67 - self.hero_level * .012)
            self.hero_attack_timer += interval
            attacks += 1
        if monster.hp <= 0:
            self._win_combat(monster)
            return

        enemy_attacks = 0
        while monster.attack_timer <= 0.0 and enemy_attacks < 2:
            dodge_chance = min(.42, .20 + self.hero_level * .012)
            if self.random.random() >= dodge_chance:
                difficulty = max(.55, min(1.8, float(self.params.get("difficulty", 1.0))))
                raw = (3 + monster.level // 2 + (3 if monster.boss else 0)) * difficulty
                damage = max(1, int(raw) - self.hero_level // 3)
                self.hero_hp = max(0, self.hero_hp - damage)
                self.damage_flash = .28
                self._add_burst(self.hero_x, self.height * .58, self.HEALTH)
            else:
                self.attack_effect = "dodge"
                self.attack_flash = .22
            monster.attack_timer += .82 if not monster.boss else .66
            enemy_attacks += 1
        if self.hero_hp <= 0:
            self.current_monster = None
            self.mode = "game_over"
            self.mode_time = 0.0
            self.damage_flash = 1.2

    def _win_combat(self, monster: Monster):
        self.session_defeated += 1
        if monster.boss:
            self.session_bosses += 1
        xp = (10 + monster.level * 5) * (3 if monster.boss else 1)
        self.hero_xp += xp
        while self.hero_xp >= self.hero_xp_next:
            self.hero_xp -= self.hero_xp_next
            self.hero_level += 1
            self.session_level_ups += 1
            self.hero_xp_next = int(self.hero_xp_next * 1.34 + 8)
            self.hero_max_hp += 7
            self.hero_max_mp += 4
            self.hero_attack += 3
            self.hero_magic += 4
            self.hero_hp = min(self.hero_max_hp, self.hero_hp + 18)
            self.hero_mp = self.hero_max_mp
            self.level_flash = 1.5
            self._add_burst(self.hero_x, self.height * .58, self.GOLD, 1.35)
        if self.hero_hp < self.hero_max_hp * .68:
            kind = "health"
        elif self.hero_mp < self.hero_max_mp * .55:
            kind = "magic"
        else:
            kind = "health" if self.random.random() < .52 else "magic"
        self.powerup = PowerUp(kind, monster.x)
        self.current_monster = None
        self.mode = "reward"
        self.mode_time = 0.0
        self.attack_effect = "victory"
        self.attack_flash = .65

    def _apply_powerup(self, powerup: PowerUp):
        powerup.applied = True
        self.session_powerups += 1
        if powerup.kind == "health":
            self.hero_hp = min(self.hero_max_hp, self.hero_hp + max(12, self.hero_max_hp // 3))
            color = self.HEALTH
        else:
            self.hero_mp = min(self.hero_max_mp, self.hero_mp + max(10, self.hero_max_mp // 2))
            color = self.MANA
        self._add_burst(self.hero_x, self.height * .58, color, 1.0)

    def _add_burst(self, x: float, y: float, color: Color, life: float = .7):
        if len(self.bursts) >= 18:
            self.bursts.pop(0)
        self.bursts.append(Burst(x, y, color, life=life))

    def _route_x(self, progress: float) -> float:
        eased = progress * progress * (3.0 - 2.0 * progress)
        ratio = self.stage_start_ratio + (self.stage_end_ratio - self.stage_start_ratio) * eased
        ratio += math.sin(progress * math.tau * self.stage_bends + self.stage_seed * .001) * .105
        return self._screen_x(ratio)

    def _screen_x(self, ratio: float) -> float:
        return max(3.0, min(self.width - 4.0, (self.width - 1) * ratio))

    def generate_frame(self, time_elapsed: float, frame_count: int) -> Any:
        fps = max(15.0, min(90.0, float(self.params.get("render_fps", 45.0))))
        if (self.last_rendered_frame is not None and self.last_render_elapsed is not None
                and 0.0 <= time_elapsed - self.last_render_elapsed < 1.0 / fps):
            return self.rendered_frame(self.last_rendered_frame, changed=False)

        if self.last_elapsed is None or time_elapsed < self.last_elapsed:
            dt = 0.0
        else:
            speed = max(.05, float(self.params.get("speed", 1.0)))
            dt = (time_elapsed - self.last_elapsed) * speed
        self.last_elapsed = time_elapsed
        self.last_render_elapsed = time_elapsed
        self._advance_game(dt)
        self._render()

        frame = self.next_frame_buffer(clear=False)
        frame.reshape(self.width, self.height, 3)[:] = self._canvas[::-1].transpose(1, 0, 2)
        self.apply_brightness_array(frame, out=frame)
        self.last_rendered_frame = frame
        return self.rendered_frame(frame, changed=True)

    def _pixel(self, x: int, y: int, color: Color, additive: bool = False):
        if 0 <= x < self.width and 0 <= y < self.height:
            if additive:
                self._canvas[y, x] = np.maximum(self._canvas[y, x], color)
            else:
                self._canvas[y, x] = color

    def _rect(self, x: int, y: int, w: int, h: int, color: Color):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.width, x + w), min(self.height, y + h)
        if x0 < x1 and y0 < y1:
            self._canvas[y0:y1, x0:x1] = color

    def _world_scroll(self) -> float:
        progress = self.stage_progress if self.stage_number else 0.0
        return self.stage_number * 977.0 + progress * 430.0

    def _terrain(self, camera_y: float):
        palettes = {
            "meadow": ((10, 64, 42), (139, 101, 48), (29, 119, 70)),
            "forest": ((4, 29, 35), (92, 67, 45), (11, 73, 58)),
            "desert": ((148, 75, 28), (224, 139, 48), (255, 183, 55)),
            "ruins": ((42, 40, 57), (104, 95, 94), (151, 136, 107)),
            "crystal": ((12, 35, 74), (69, 75, 125), (50, 213, 238)),
        }
        base, trail, accent = palettes[self.biome]
        for sy in range(self.height):
            wy = int(camera_y + sy)
            shade = ((wy * 17 + self.stage_seed) % 5) - 2
            self._canvas[sy, :] = tuple(max(0, channel + shade) for channel in base)
            path_x = int((self.width - 1) / 2 + math.sin(wy * .031 + self.stage_seed * .01) * self.width * .18)
            self._pixel(path_x - 2, sy, tuple(max(0, c - 52) for c in trail))
            self._pixel(path_x - 1, sy, trail)
            self._pixel(path_x, sy, trail)
            self._pixel(path_x + 1, sy, trail)
            if (wy * 13 + self.stage_seed) % 37 < 2:
                x = (wy * 19 + self.stage_seed) % self.width
                self._pixel(x, sy, accent)
            if self.biome == "crystal" and (wy + self.stage_seed) % 23 == 0:
                self._pixel((wy * 7) % self.width, sy, (190, 80, 255))

    def _scenery(self, camera_y: float, hero_world_y: float, foreground: bool):
        first = int(camera_y) // 21 * 21
        last = int(camera_y + self.height) + 22
        for wy in range(first, last, 21):
            if (wy > hero_world_y) != foreground:
                continue
            sy = int(round(wy - camera_y))
            hashed = wy * 41 + self.stage_seed * 7
            x = 3 + hashed % max(1, self.width - 7)
            side = (hashed // 17) & 1
            if self.biome == "forest":
                x = 3 if side == 0 else self.width - 4
                self._rect(x - 3, sy - 1, 7, 2, (2, 14, 22))
                self._rect(x - 1, sy - 7, 3, 7, (82, 45, 27))
                self._rect(x - 3, sy - 10, 7, 4, (7, 61, 45))
                self._rect(x - 2, sy - 11, 5, 2, (16, 91, 58))
            elif self.biome == "meadow":
                self._rect(x - 1, sy, 3, 1, (3, 35, 31))
                self._pixel(x, sy - 1, (28, 128, 72))
                self._pixel(x + (1 if side else -1), sy - 2, (255, 170, 45))
            elif self.biome == "desert":
                self._rect(x - 2, sy, 5, 2, (82, 40, 27))
                self._rect(x - 1, sy - 3, 3, 4, (211, 105, 38))
                self._pixel(x, sy - 4, (255, 178, 57))
            elif self.biome == "ruins":
                x = 3 if side == 0 else self.width - 4
                self._rect(x - 2, sy, 5, 2, (25, 25, 35))
                self._rect(x - 1, sy - 8, 3, 8, (91, 91, 102))
                self._rect(x - 2, sy - 9, 5, 2, (149, 132, 112))
            else:
                self._rect(x - 2, sy, 5, 2, (5, 19, 48))
                self._pixel(x, sy - 6, (215, 110, 255))
                self._rect(x - 1, sy - 5, 3, 5, (41, 199, 236))
                self._pixel(x, sy - 4, self.WHITE)

    def _atmosphere(self):
        colors = {
            "meadow": (255, 205, 55), "forest": (35, 210, 115),
            "desert": (255, 175, 65), "ruins": (155, 85, 230),
            "crystal": (95, 225, 255),
        }
        color = colors[self.biome]
        for index in range(10):
            phase = self.run_time * (.55 + index * .031) + index * 11.7 + self.stage_seed
            x = int((math.sin(phase * .71) * .5 + .5) * max(1, self.width - 1))
            y = 8 + int((phase * (4.0 + index % 3)) % max(1, self.height - 10))
            level = .45 + .55 * (math.sin(phase * 2.1) * .5 + .5)
            self._pixel(x, y, tuple(int(c * level) for c in color), True)

    def _hero_screen_x(self) -> int:
        x = self.hero_x
        if self.mode in ("combat", "boss"):
            x += math.sin(self.mode_time * 5.2) * (2.0 if self.attack_effect == "dodge" else .75)
        elif self.mode == "reward" and self.powerup is not None:
            x += (self.powerup.x - x) * min(1.0, self.mode_time / .75)
        return int(round(max(3, min(self.width - 4, x))))

    def _hero(self, x: int, y: int, facing: int):
        bob = int(self.run_time * 8) & 1
        self._rect(x - 2, y + 2, 5, 2, (2, 11, 24))
        self._pixel(x, y - 2 + bob, self.SKIN)
        self._rect(x - 1, y - 1 + bob, 3, 2, self.HERO_CYAN)
        self._pixel(x - facing, y - 1 + bob, self.HERO_SCARF)
        self._pixel(x, y + 1 + bob, self.HERO_DARK)
        self._pixel(x - 1, y + 2 + bob, self.INK)
        self._pixel(x + 1, y + 2 + bob, self.INK)
        self._pixel(x - facing * 2, y, (255, 132, 28))
        if self.attack_flash > 0 and self.attack_effect in ("slash", "critical"):
            reach = 3 + int((.22 - min(.22, self.attack_flash)) * 10)
            color = self.GOLD if self.attack_effect == "critical" else self.BLADE
            self._pixel(x + facing * reach, y - 2, color, True)
            self._pixel(x + facing * (reach + 1), y - 1, self.HIT, True)
        orbit_count = min(4, max(0, self.hero_level - 1))
        for index in range(orbit_count):
            angle = self.run_time * 2.4 + index * math.tau / max(1, orbit_count)
            self._pixel(x + int(math.cos(angle) * 4), y + int(math.sin(angle) * 4), self.GOLD, True)

    def _monster(self, monster: Monster, x: int, y: int):
        self._rect(x - (4 if monster.boss else 3), y + 3, 9 if monster.boss else 7, 2, (2, 10, 19))
        flash = monster.hit_flash > 0 and int(monster.hit_flash * 30) & 1
        palette = {
            "meadow": (74, 225, 68), "forest": (174, 68, 218),
            "desert": (244, 67, 28), "ruins": (122, 139, 142),
            "crystal": (46, 192, 255),
        }
        color = self.HIT if flash else palette[self.biome]
        radius = 4 if monster.boss else 2
        self._rect(x - radius, y - radius, radius * 2 + 1, radius * 2 + 1, color)
        if monster.boss:
            horn = self.GOLD if int(self.run_time * 6) & 1 else self.WHITE
            self._pixel(x - 4, y - 5, horn)
            self._pixel(x, y - 6, horn)
            self._pixel(x + 4, y - 5, horn)
            self._pixel(x - 2, y - 1, (255, 36, 55))
            self._pixel(x + 2, y - 1, (255, 36, 55))
        else:
            flap = int(self.run_time * 9) & 1
            self._pixel(x - 3, y - flap, color)
            self._pixel(x + 3, y - flap, color)
            self._pixel(x - 1, y, self.INK)
            self._pixel(x + 1, y, self.INK)
        pips = min(6, monster.level)
        for index in range(pips):
            self._pixel(x - pips // 2 + index, y - radius - 3, self.GOLD)

    def _powerup(self, powerup: PowerUp, y: int):
        x = int(round(powerup.x))
        pulse = int(math.sin(powerup.age * 10) > 0)
        color = self.HEALTH if powerup.kind == "health" else self.MANA
        self._rect(x - 2, y - 2, 5, 5, self.INK)
        if powerup.kind == "health":
            self._rect(x - 1, y, 3, 1, color)
            self._rect(x, y - 1, 1, 3, color)
        else:
            self._rect(x - 1, y - 1, 3, 3, color)
            self._pixel(x, y - 2, self.WHITE)
        if pulse:
            self._pixel(x - 3, y, self.WHITE, True)
            self._pixel(x + 3, y, self.WHITE, True)

    def _effects(self, hero_x: int, hero_y: int, facing: int):
        monster = self.current_monster
        if monster is not None and self.attack_flash > 0 and self.attack_effect == "magic":
            mx = int(round(monster.x))
            steps = max(1, abs(mx - hero_x))
            for step in range(steps + 1):
                x = hero_x + int((mx - hero_x) * step / steps)
                y = hero_y - 1 + int(math.sin(step * 1.8 + self.run_time * 15) * 2)
                self._pixel(x, y, self.MAGIC if step & 1 else self.WHITE, True)
        for burst in self.bursts:
            progress = burst.age / burst.life
            radius = 1 + int(progress * 7)
            for index in range(8):
                angle = index * math.pi / 4 + self.run_time
                x = int(round(burst.x + math.cos(angle) * radius))
                y = int(round(burst.y + math.sin(angle) * radius))
                self._pixel(x, y, burst.color, True)
        if self.level_flash > 0:
            radius = 4 + int((1.5 - self.level_flash) * 7)
            for index in range(12):
                angle = index * math.pi / 6
                self._pixel(hero_x + int(math.cos(angle) * radius),
                            hero_y + int(math.sin(angle) * radius), self.GOLD, True)

    def _bar(self, x: int, y: int, width: int, value: int, maximum: int, color: Color):
        width = max(1, width)
        self._rect(x, y, width, 1, (18, 20, 32))
        filled = int(round(width * max(0.0, min(1.0, value / max(1, maximum)))))
        self._rect(x, y, filled, 1, color)

    def _hud(self):
        if not bool(self.params.get("show_hud", True)):
            return
        self._canvas[:9, :] = self.NIGHT
        bar_width = max(5, self.width // 2 - 3)
        self._pixel(1, 1, self.HEALTH)
        self._bar(3, 1, bar_width, self.hero_hp, self.hero_max_hp, self.HEALTH)
        self._pixel(1, 3, self.MANA)
        self._bar(3, 3, bar_width, self.hero_mp, self.hero_max_mp, self.MANA)
        self._pixel(1, 5, self.GOLD)
        self._bar(3, 5, bar_width, self.hero_xp, self.hero_xp_next, self.GOLD)
        for index in range(min(8, self.hero_level)):
            self._pixel(self.width - 2 - (index % 4) * 2, 1 + (index // 4) * 2, self.HERO_CYAN)
        if self.current_monster is not None:
            color = self.GOLD if self.current_monster.boss else (255, 90, 45)
            self._bar(self.width // 2, 7, self.width - self.width // 2 - 1,
                      self.current_monster.hp, self.current_monster.max_hp, color)

    def _title_card(self):
        cx, cy = self.width // 2, min(self.height // 3, 38)
        radius = 7 + int(math.sin(self.run_time * 4))
        for step in range(12):
            angle = step * math.pi / 6
            self._pixel(cx + int(math.cos(angle) * radius),
                        cy + int(math.sin(angle) * radius), self.GOLD, True)
        self._rect(cx - 1, cy - 7, 3, 12, self.BLADE)
        self._rect(cx - 4, cy + 3, 9, 2, (151, 82, 31))
        self._pixel(cx, cy - 8, self.WHITE)

    def _render(self):
        hero_y = max(16, min(self.height - 13, int(self.height * .58)))
        camera_y = self._world_scroll()
        hero_world_y = camera_y + hero_y
        self._terrain(camera_y)
        self._atmosphere()
        self._scenery(camera_y, hero_world_y, foreground=False)
        hero_x = self._hero_screen_x()
        facing = 1
        if self.current_monster is not None:
            facing = -1 if self.current_monster.x < hero_x else 1
            monster_y = hero_y + (10 if self.current_monster.boss else 8)
            self._monster(self.current_monster, int(round(self.current_monster.x)), monster_y)
        self._hero(hero_x, hero_y, facing)
        if self.powerup is not None:
            self._powerup(self.powerup, hero_y + 7)
        self._effects(hero_x, hero_y, facing)
        self._scenery(camera_y, hero_world_y, foreground=True)

        if self.mode == "stage_clear":
            portal_y = hero_y + 18
            pulse = 4 + int(math.sin(self.mode_time * 7) * 2)
            for index in range(12):
                angle = index * math.pi / 6
                self._pixel(self.width // 2 + int(math.cos(angle) * pulse),
                            portal_y + int(math.sin(angle) * pulse), self.MAGIC, True)
        elif self.mode == "game_over":
            rows = min(self.height, int(self.mode_time * self.height / 2.2))
            self._canvas[self.height - rows:, :, 0] = np.maximum(
                self._canvas[self.height - rows:, :, 0], 92
            )
        elif self.mode == "title":
            self._title_card()
        if self.damage_flash > 0 and int(self.damage_flash * 20) & 1:
            self._canvas[:, 0] = self.HEALTH
            self._canvas[:, -1] = self.HEALTH
        self._hud()

    def logical_state(self) -> Tuple[Any, ...]:
        monster = self.current_monster
        return (
            self.run_number, self.stage_number, self.biome, self.mode,
            round(self.stage_progress, 5), self.hero_level, self.hero_xp,
            self.hero_hp, self.hero_mp, self.session_defeated,
            None if monster is None else (monster.kind, monster.level, monster.hp, monster.boss),
        )

    def get_runtime_stats(self) -> Dict[str, Any]:
        monster = self.current_monster
        return {
            "mode": self.mode,
            "run_seconds": round(self.run_time, 2),
            "longest_run_seconds": round(self.longest_run, 2),
            "stage": self.stage_number,
            "biome": self.biome,
            "hero_level": self.hero_level,
            "hero_hp": self.hero_hp,
            "hero_max_hp": self.hero_max_hp,
            "hero_mp": self.hero_mp,
            "hero_max_mp": self.hero_max_mp,
            "hero_xp": self.hero_xp,
            "hero_xp_next": self.hero_xp_next,
            "monster": None if monster is None else monster.kind,
            "monster_level": None if monster is None else monster.level,
            "enemies_defeated": self.session_defeated,
            "bosses_defeated": self.session_bosses,
            "powerups_collected": self.session_powerups,
            "level_ups": self.session_level_ups,
        }
