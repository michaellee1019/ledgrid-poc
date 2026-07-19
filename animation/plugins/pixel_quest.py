#!/usr/bin/env python3
"""An original, self-playing 8-bit overworld adventure.

The animation borrows the readable top-down grammar of early console quests
without reproducing any proprietary maps, characters, names, or sprites.  A
small adventurer crosses three generated biomes, wins four authored encounters,
and opens a relic chest before the story loops.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

from animation import AnimationBase


Color = Tuple[int, int, int]


class PixelQuestAnimation(AnimationBase):
    """Looping top-down exploration vignette with deterministic combat."""

    ANIMATION_NAME = "Pixel Quest: Wildlands"
    ANIMATION_DESCRIPTION = (
        "An original 8-bit overworld journey through meadow, forest, and "
        "sun-baked ruins with animated enemy battles and a relic finale"
    )
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    LOOP_SECONDS = 43.0
    WORLD_HEIGHT = 540

    NIGHT = (4, 8, 18)
    INK = (10, 18, 22)
    WHITE = (240, 238, 190)
    GOLD = (255, 205, 45)
    HERO_GREEN = (38, 180, 72)
    HERO_DARK = (12, 76, 45)
    SKIN = (236, 166, 92)
    BLADE = (205, 230, 220)
    HIT = (255, 245, 220)

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.width, self.height = self.get_strip_info()
        self.default_params.update({
            "speed": 1.0,
            "brightness": 1.0,
            "render_fps": 45.0,
            "show_hud": True,
        })
        self.params = {**self.default_params, **self.config}
        self._canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self.last_render_elapsed: Optional[float] = None
        self.last_rendered_frame: Optional[np.ndarray] = None
        self.story_time = 0.0
        self.scene = "title"
        self.defeated = 0

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            "speed": {"type": "float", "min": 0.25, "max": 3.0, "default": 1.0,
                      "description": "Quest playback speed"},
            "render_fps": {"type": "float", "min": 15.0, "max": 90.0, "default": 45.0,
                           "description": "Maximum animation render rate"},
            "show_hud": {"type": "bool", "default": True,
                         "description": "Show hearts, relic count, and biome marker"},
        })
        return schema

    @staticmethod
    def _lerp(start: float, end: float, value: float) -> float:
        return start + (end - start) * max(0.0, min(1.0, value))

    def _quest_state(self, t: float) -> Tuple[float, float, str, Optional[str], float]:
        """Return hero x/y, scene, active enemy, and encounter progress."""
        center = (self.width - 1) / 2
        if t < 2.5:
            return center, 38.0, "title", None, 0.0
        if t < 8.0:
            p = (t - 2.5) / 5.5
            return center + math.sin(p * math.pi * 2) * self.width * .16, self._lerp(38, 132, p), "meadow", None, 0.0
        if t < 11.0:
            return center - 2, 132.0, "meadow battle", "mossling", (t - 8.0) / 3.0
        if t < 18.0:
            p = (t - 11.0) / 7.0
            return center + math.sin(p * math.pi * 3) * self.width * .2, self._lerp(145, 272, p), "forest", None, 0.0
        if t < 21.0:
            return center + 2, 272.0, "forest battle", "nightwing", (t - 18.0) / 3.0
        if t < 28.0:
            p = (t - 21.0) / 7.0
            return center + math.sin(p * math.pi * 2) * self.width * .18, self._lerp(286, 398, p), "desert", None, 0.0
        if t < 31.0:
            return center - 2, 398.0, "desert battle", "sandclaw", (t - 28.0) / 3.0
        if t < 35.0:
            p = (t - 31.0) / 4.0
            return center + math.sin(p * math.pi) * 4, self._lerp(410, 466, p), "ruins", None, 0.0
        if t < 39.0:
            return center - 2, 466.0, "guardian battle", "stoneguard", (t - 35.0) / 4.0
        return center, 485.0, "relic found", None, (t - 39.0) / 4.0

    def generate_frame(self, time_elapsed: float, frame_count: int) -> Any:
        fps = max(15.0, min(90.0, float(self.params.get("render_fps", 45.0))))
        if (self.last_rendered_frame is not None and self.last_render_elapsed is not None
                and 0.0 <= time_elapsed - self.last_render_elapsed < 1.0 / fps):
            return self.rendered_frame(self.last_rendered_frame, changed=False)

        self.last_render_elapsed = time_elapsed
        speed = max(0.05, float(self.params.get("speed", 1.0)))
        self.story_time = (max(0.0, time_elapsed) * speed) % self.LOOP_SECONDS
        hero_x, hero_y, self.scene, enemy, progress = self._quest_state(self.story_time)
        self.defeated = sum(self.story_time >= moment for moment in (10.55, 20.55, 30.55, 38.55))
        self._render(hero_x, hero_y, enemy, progress)

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

    def _terrain(self, camera_y: float):
        for sy in range(self.height):
            wy = int(camera_y + sy)
            if wy < 170:  # Meadow and creek.
                base = (18, 102 + (wy % 3) * 4, 40)
                self._canvas[sy, :] = base
                creek_x = int(self.width * .22 + math.sin(wy * .075) * 3)
                self._rect(creek_x, sy, 3, 1, (18, 82, 145))
                if (wy * 11 + 7) % 29 < 2:
                    self._pixel((wy * 17 + 3) % self.width, sy, (238, 205, 45))
            elif wy < 340:  # Dense woodland.
                self._canvas[sy, :] = (8, 57 + (wy % 2) * 5, 36)
                if wy % 13 < 3:
                    for x in range((wy * 7) % 9, self.width, 11):
                        self._pixel(x, sy, (18, 100, 52))
                if wy % 37 == 0:
                    self._rect(1, sy, 4, 2, (54, 31, 22))
                    self._rect(self.width - 5, sy, 4, 2, (54, 31, 22))
            else:  # Dunes become an ancient tiled approach.
                base = (174 + (wy % 3) * 4, 112, 38)
                self._canvas[sy, :] = base
                if wy > 430:
                    path_width = max(8, self.width // 2)
                    self._rect((self.width - path_width) // 2, sy, path_width, 1,
                               (114 + (wy % 2) * 10, 96, 70))
                    if wy % 12 == 0:
                        self._pixel((self.width - path_width) // 2, sy, (58, 61, 55))
                        self._pixel((self.width + path_width) // 2 - 1, sy, (58, 61, 55))
                elif (wy * 5) % 31 < 2:
                    self._pixel((wy * 13) % self.width, sy, (232, 175, 58))

        # A readable trail running through every biome.
        for sy in range(self.height):
            wy = camera_y + sy
            trail_x = int((self.width - 1) / 2 + math.sin(wy * .035) * self.width * .16)
            if wy < 430:
                self._pixel(trail_x, sy, (118, 92, 45) if wy < 340 else (205, 145, 55))

    def _hero(self, x: int, y: int, attacking: bool):
        bob = int(self.story_time * 8) & 1
        self._pixel(x, y - 2 + bob, self.SKIN)
        self._rect(x - 1, y - 1 + bob, 3, 2, self.HERO_GREEN)
        self._pixel(x, y + 1 + bob, self.HERO_DARK)
        self._pixel(x - 1, y + 2 + bob, self.INK)
        self._pixel(x + 1, y + 2 + bob, self.INK)
        self._pixel(x - 2, y, (112, 60, 32))  # Small round shield.
        if attacking:
            swing = int(self.story_time * 10) % 3
            self._pixel(x + 2 + swing, y - 1 + swing, self.BLADE)
            self._pixel(x + 3 + swing, y - 2 + swing, self.HIT, True)

    def _enemy(self, kind: str, x: int, y: int, progress: float):
        # Enemies blink out after the finishing blow.
        if progress >= .85:
            for index in range(8):
                angle = index * math.pi / 4
                radius = 1 + int((progress - .85) * 24)
                self._pixel(x + int(math.cos(angle) * radius),
                            y + int(math.sin(angle) * radius), self.GOLD, True)
            return
        flash = progress > .28 and int(progress * 16) % 3 == 0
        if kind == "mossling":
            color = self.HIT if flash else (75, 210, 58)
            self._rect(x - 2, y, 5, 2, color)
            self._pixel(x - 1, y - 1, color)
            self._pixel(x + 1, y - 1, color)
            self._pixel(x - 1, y, self.INK)
            self._pixel(x + 1, y, self.INK)
        elif kind == "nightwing":
            color = self.HIT if flash else (128, 65, 190)
            flap = int(self.story_time * 9) & 1
            self._rect(x - 1, y - 1, 3, 3, color)
            self._pixel(x - 3, y - flap, color)
            self._pixel(x + 3, y - flap, color)
            self._pixel(x, y, (255, 55, 70))
        elif kind == "sandclaw":
            color = self.HIT if flash else (196, 51, 28)
            self._rect(x - 2, y - 1, 5, 3, color)
            self._pixel(x - 3, y - 2, color)
            self._pixel(x + 3, y - 2, color)
            self._pixel(x, y - 2, (58, 28, 18))
        else:
            color = self.HIT if flash else (84, 110, 105)
            self._rect(x - 2, y - 3, 5, 6, color)
            self._pixel(x - 1, y - 1, (255, 50, 35))
            self._pixel(x + 1, y - 1, (255, 50, 35))
            self._pixel(x - 3, y + 2, (48, 58, 55))
            self._pixel(x + 3, y + 2, (48, 58, 55))

    def _hud(self):
        if not bool(self.params.get("show_hud", True)):
            return
        self._canvas[:7, :] = self.NIGHT
        for heart in range(3):
            x = 1 + heart * 4
            self._pixel(x, 2, (245, 40, 48))
            self._pixel(x + 1, 2, (245, 40, 48))
            self._pixel(x, 3, (245, 40, 48))
        for relic in range(self.defeated):
            self._pixel(self.width - 2 - relic * 3, 2, self.GOLD)
            self._pixel(self.width - 2 - relic * 3, 3, (255, 120, 25))

    def _title_card(self):
        if self.story_time >= 2.5:
            return
        # A compact sword-and-sun crest reads better than text on narrow grids.
        cx, cy = self.width // 2, min(self.height // 3, 38)
        radius = 7 + int(math.sin(self.story_time * 4))
        for step in range(12):
            angle = step * math.pi / 6
            self._pixel(cx + int(math.cos(angle) * radius),
                        cy + int(math.sin(angle) * radius), self.GOLD, True)
        self._rect(cx - 1, cy - 7, 3, 12, self.BLADE)
        self._rect(cx - 4, cy + 3, 9, 2, (151, 82, 31))
        self._pixel(cx, cy - 8, self.WHITE)

    def _render(self, hero_x: float, hero_y: float, enemy: Optional[str], progress: float):
        camera_y = max(0.0, min(self.WORLD_HEIGHT - self.height, hero_y - self.height * .58))
        self._terrain(camera_y)
        screen_y = int(round(hero_y - camera_y))
        screen_x = int(round(hero_x))

        attacking = enemy is not None and progress > .12 and progress < .88
        if enemy is not None:
            enemy_y = screen_y + (10 if progress < .2 else 7)
            enemy_x = min(self.width - 5, screen_x + 5)
            self._enemy(enemy, enemy_x, enemy_y, progress)
        self._hero(screen_x, screen_y, attacking)

        if self.scene == "relic found":
            chest_y = int(round(501 - camera_y))
            self._rect(self.width // 2 - 3, chest_y, 7, 4, (116, 57, 24))
            self._rect(self.width // 2 - 2, chest_y - 2, 5, 2, self.GOLD)
            if progress > .25:
                beam = min(self.height, chest_y)
                self._rect(self.width // 2, 7, 1, max(0, beam - 7), (255, 210, 65))
                for offset in (-6, -3, 3, 6):
                    self._pixel(self.width // 2 + offset,
                                max(8, chest_y - 8 - abs(offset)), self.WHITE, True)

        self._title_card()
        self._hud()

    def get_runtime_stats(self) -> Dict[str, Any]:
        return {
            "scene": self.scene,
            "quest_seconds": round(self.story_time, 2),
            "enemies_defeated": self.defeated,
            "biome": ("meadow" if self.story_time < 11 else
                      "forest" if self.story_time < 21 else
                      "desert" if self.story_time < 31 else "sunken ruins"),
        }
