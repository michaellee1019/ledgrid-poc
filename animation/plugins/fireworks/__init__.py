#!/usr/bin/env python3
"""Configurable rockets, aerial shells, trails, and twinkling night sky."""

from __future__ import annotations

import colorsys
import math
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import numpy as np

from animation import AnimationBase


Color = Tuple[float, float, float]


@dataclass
class Rocket:
    x: float
    y: float
    vx: float
    vy: float
    target_y: float
    hue: float


@dataclass
class Spark:
    x: float
    y: float
    vx: float
    vy: float
    age: float
    lifetime: float
    color: Color
    size: float = 1.0
    can_split: bool = False
    split: bool = False


class FireworksAnimation(AnimationBase):
    """A small particle system designed for tall, narrow LED installations."""

    ANIMATION_NAME = "Fireworks"
    ANIMATION_DESCRIPTION = "Launching rockets, colorful aerial shells, drifting embers, and sparkling trails"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    PALETTES = {
        "festival": (0.00, 0.08, 0.14, 0.33, 0.53, 0.67, 0.82, 0.92),
        "patriotic": (0.00, 0.00, 0.62, 0.62),
        "gold": (0.08, 0.10, 0.12),
        "cool": (0.48, 0.55, 0.62, 0.72, 0.82),
        "sunset": (0.00, 0.04, 0.08, 0.12, 0.90),
        "forest": (0.25, 0.31, 0.38, 0.46),
        "monochrome": (0.0,),
    }
    STYLES = {"mixed", "chrysanthemum", "ring", "willow", "palm"}

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "speed": 1.0,
            "launch_rate": 0.75,
            "max_rockets": 3,
            "launch_speed": 0.72,
            "launch_spread": 0.85,
            "burst_height_min": 0.50,
            "burst_height_max": 0.88,
            "particles_per_burst": 52,
            "burst_size": 0.27,
            "burst_style": "mixed",
            "palette": "festival",
            "base_hue": 0.02,
            "hue_spread": 0.08,
            "spark_lifetime": 1.8,
            "gravity": 0.34,
            "air_drag": 0.965,
            "trail_persistence": 0.93,
            "trail_intensity": 1.0,
            "rocket_trails": True,
            "secondary_spark_chance": 0.18,
            "twinkle": 0.28,
            "star_density": 0.012,
            "background_level": 0.012,
            "random_seed": 0,
        })
        self.params = {**self.default_params, **self.config}

        self.width, self.height = self.get_strip_info()
        self._rng = random.Random(int(self.params.get("random_seed", 0)))
        self._seed = int(self.params.get("random_seed", 0))
        self._rockets: List[Rocket] = []
        self._sparks: List[Spark] = []
        self._trail = np.zeros((self.height, self.width, 3), dtype=np.float32)
        self._stars = np.zeros((self.height, self.width), dtype=np.float32)
        self._plant_foliage = np.zeros((self.height, self.width), dtype=bool)
        self._plant_globes = np.zeros((self.height, self.width), dtype=bool)
        self._plant_obstacle = np.zeros((self.height, self.width), dtype=bool)
        self._plant_clearance = np.zeros((self.height, self.width), dtype=bool)
        self._plant_geometry = None
        self._plant_active = self.plant_aware_enabled()
        self._plant_foliage_flash = 0.0
        self._plant_globe_flash = 0.0
        self._plant_hits = 0
        self._last_time = None
        self._launch_accumulator = 1.0
        self._burst_count = 0
        self._rebuild_stars()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            "launch_rate": self._float(0.05, 5.0, 0.75, "Average rockets launched per second"),
            "max_rockets": self._int(1, 10, 3, "Maximum rockets rising at once"),
            "launch_speed": self._float(0.2, 1.5, 0.72, "Upward rocket speed"),
            "launch_spread": self._float(0.0, 1.0, 0.85, "How widely launch positions span the display"),
            "burst_height_min": self._float(0.2, 0.95, 0.50, "Lowest burst height as a fraction of the display"),
            "burst_height_max": self._float(0.2, 0.98, 0.88, "Highest burst height as a fraction of the display"),
            "particles_per_burst": self._int(6, 160, 52, "Number of sparks in each aerial shell"),
            "burst_size": self._float(0.04, 0.7, 0.27, "Initial shell expansion speed and size"),
            "burst_style": self._string("mixed", "Shell shape: mixed, chrysanthemum, ring, willow, or palm"),
            "palette": self._string("festival", "Colors: festival, patriotic, gold, cool, sunset, forest, or monochrome"),
            "base_hue": self._float(0.0, 1.0, 0.02, "Primary hue used by monochrome shells"),
            "hue_spread": self._float(0.0, 0.5, 0.08, "Color variation within each shell"),
            "spark_lifetime": self._float(0.25, 5.0, 1.8, "How long burst sparks remain alive"),
            "gravity": self._float(0.0, 1.5, 0.34, "Downward pull on rockets and embers"),
            "air_drag": self._float(0.8, 1.0, 0.965, "Velocity retained by sparks each 60 Hz step"),
            "trail_persistence": self._float(0.5, 0.995, 0.93, "How slowly luminous trails fade"),
            "trail_intensity": self._float(0.1, 2.0, 1.0, "Brightness deposited into trails"),
            "rocket_trails": {"type": "bool", "default": True, "description": "Draw bright tails behind rising rockets"},
            "secondary_spark_chance": self._float(0.0, 1.0, 0.18, "Chance for sparks to split into tiny crackles"),
            "twinkle": self._float(0.0, 1.0, 0.28, "Brightness shimmer on aging sparks"),
            "star_density": self._float(0.0, 0.08, 0.012, "Density of dim background stars"),
            "background_level": self._float(0.0, 0.15, 0.012, "Dark-blue night-sky brightness"),
            "random_seed": self._int(0, 99999, 0, "Repeatable show layout and timing seed"),
        })
        return schema

    @staticmethod
    def _float(minimum: float, maximum: float, default: float, description: str):
        return {"type": "float", "min": minimum, "max": maximum, "default": default, "description": description}

    @staticmethod
    def _int(minimum: int, maximum: int, default: int, description: str):
        return {"type": "int", "min": minimum, "max": maximum, "default": default, "description": description}

    @staticmethod
    def _string(default: str, description: str):
        return {"type": "str", "default": default, "description": description}

    def update_parameters(self, new_params: Dict[str, Any]):
        super().update_parameters(new_params)
        new_seed = int(self.params.get("random_seed", 0))
        if new_seed != self._seed:
            self._seed = new_seed
            self._rng.seed(new_seed)
        if "star_density" in new_params or "random_seed" in new_params:
            self._rebuild_stars()

    def get_runtime_stats(self) -> Dict[str, Any]:
        stats = {
            "rockets": len(self._rockets),
            "sparks": len(self._sparks),
            "bursts": self._burst_count,
            "plant_aware": self.plant_aware_enabled(),
            "plant_hits": self._plant_hits,
        }
        if self.plant_aware_enabled():
            stats.update({
                "plant_foliage_pixels": int(np.count_nonzero(self._plant_foliage)),
                "plant_globe_pixels": int(np.count_nonzero(self._plant_globes)),
            })
            if self._plant_geometry is not None and self._plant_geometry.error:
                stats["plant_mask_error"] = self._plant_geometry.error
        return stats

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        self._ensure_geometry()
        self._plant_active = self.plant_aware_enabled()
        if self._plant_active:
            self._refresh_plant_geometry()
        if self._last_time is None or time_elapsed < self._last_time:
            dt = 1.0 / 60.0
        else:
            dt = min(0.1, max(0.0, time_elapsed - self._last_time))
        self._last_time = time_elapsed
        scaled_dt = dt * max(0.0, float(self.params.get("speed", 1.0)))
        if self._plant_active:
            fade = 0.80 ** (scaled_dt * 60.0)
            self._plant_foliage_flash *= fade
            self._plant_globe_flash *= fade

        persistence = min(0.9999, max(0.0, float(self.params.get("trail_persistence", 0.93))))
        self._trail *= persistence ** (scaled_dt * 60.0)
        self._launch_rockets(scaled_dt)
        self._update_rockets(scaled_dt)
        self._update_sparks(scaled_dt, time_elapsed)

        image = self._trail.copy()
        background = max(0.0, float(self.params.get("background_level", 0.012))) * 255.0
        image[..., 2] += background
        if np.any(self._stars):
            shimmer = 0.65 + 0.35 * np.sin(time_elapsed * 2.2 + self._stars * 19.0)
            image += (self._stars * shimmer)[..., None] * np.array((95.0, 125.0, 190.0), dtype=np.float32)

        if self._plant_active:
            self._render_plant_silhouettes(image)

        np.clip(image, 0.0, 255.0, out=image)
        logical = image.astype(np.uint8)
        frame = self.next_frame_buffer(clear=False)
        # Logical y=0 is the top; physical LED indices run bottom-to-top.
        frame[:] = logical[::-1, :, :].transpose(1, 0, 2).reshape(-1, 3)
        return self.apply_brightness_array(frame, out=frame)

    def _ensure_geometry(self):
        width, height = self.get_strip_info()
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        self._trail = np.zeros((height, width, 3), dtype=np.float32)
        self._plant_foliage = np.zeros((height, width), dtype=bool)
        self._plant_globes = np.zeros((height, width), dtype=bool)
        self._plant_obstacle = np.zeros((height, width), dtype=bool)
        self._plant_clearance = np.zeros((height, width), dtype=bool)
        self._plant_geometry = None
        self._rockets.clear()
        self._sparks.clear()
        self._rebuild_stars()

    def _rebuild_stars(self):
        self._stars = np.zeros((self.height, self.width), dtype=np.float32)
        density = min(0.08, max(0.0, float(self.params.get("star_density", 0.012))))
        star_rng = random.Random(self._seed ^ 0x5A17)
        for y in range(self.height):
            for x in range(self.width):
                if star_rng.random() < density:
                    self._stars[y, x] = star_rng.uniform(0.08, 0.32)

    def _launch_rockets(self, dt: float):
        rate = max(0.0, float(self.params.get("launch_rate", 0.75)))
        self._launch_accumulator += dt * rate
        max_rockets = max(1, int(self.params.get("max_rockets", 3)))
        while self._launch_accumulator >= 1.0 and len(self._rockets) < max_rockets:
            self._launch_accumulator -= 1.0
            spread = min(1.0, max(0.0, float(self.params.get("launch_spread", 0.85))))
            x = 0.5 + self._rng.uniform(-0.5, 0.5) * spread
            low = float(self.params.get("burst_height_min", 0.50))
            high = float(self.params.get("burst_height_max", 0.88))
            low, high = sorted((max(0.05, min(0.98, low)), max(0.05, min(0.98, high))))
            target_y = 1.0 - self._rng.uniform(low, high)
            launch_speed = max(0.05, float(self.params.get("launch_speed", 0.72)))
            vx = self._rng.uniform(-0.025, 0.025)
            if self._plant_active:
                # Favor a vertical sight line whose burst and rising trail can
                # actually be seen instead of spending a shell behind foliage.
                x = self._nearest_visible_launch_x(x, target_y)
                vx = 0.0
            self._rockets.append(Rocket(x, 1.02, vx, -launch_speed, target_y, self._choose_hue()))

    def _update_rockets(self, dt: float):
        gravity = max(0.0, float(self.params.get("gravity", 0.34)))
        survivors = []
        for rocket in self._rockets:
            rocket.x += rocket.vx * dt
            rocket.y += rocket.vy * dt
            rocket.vy += gravity * 0.32 * dt
            if bool(self.params.get("rocket_trails", True)):
                self._deposit(rocket.x, rocket.y, (255.0, 150.0, 55.0), 1.35)
                self._deposit(rocket.x, rocket.y + 0.012, (90.0, 35.0, 8.0), 0.7)
            if rocket.y <= rocket.target_y or rocket.vy >= -0.04:
                self._burst(rocket)
            elif -0.1 <= rocket.x <= 1.1 and rocket.y > -0.1:
                survivors.append(rocket)
        self._rockets = survivors

    def _burst(self, rocket: Rocket):
        requested = max(6, min(160, int(self.params.get("particles_per_burst", 52))))
        style = str(self.params.get("burst_style", "mixed")).lower().strip()
        if style not in self.STYLES:
            style = "mixed"
        if style == "mixed":
            style = self._rng.choice(("chrysanthemum", "ring", "willow", "palm"))
        count = max(6, requested // 2) if style == "palm" else requested
        size = max(0.01, float(self.params.get("burst_size", 0.27)))
        lifetime = max(0.1, float(self.params.get("spark_lifetime", 1.8)))
        hue_spread = max(0.0, float(self.params.get("hue_spread", 0.08)))
        shell_hue = rocket.hue
        split_chance = max(0.0, min(1.0, float(self.params.get("secondary_spark_chance", 0.18))))

        for index in range(count):
            if style == "ring":
                angle = math.tau * index / count + self._rng.uniform(-0.025, 0.025)
                velocity = size * self._rng.uniform(0.90, 1.08)
            elif style == "palm":
                angle = self._rng.uniform(math.pi * 1.10, math.pi * 1.90)
                velocity = size * self._rng.uniform(0.75, 1.35)
            else:
                angle = self._rng.uniform(0.0, math.tau)
                velocity = size * (self._rng.random() ** 0.35)
            if style == "willow":
                velocity *= 0.78
                particle_lifetime = lifetime * self._rng.uniform(1.3, 1.8)
            else:
                particle_lifetime = lifetime * self._rng.uniform(0.72, 1.18)
            hue = (shell_hue + self._rng.uniform(-hue_spread, hue_spread)) % 1.0
            color = self._hue_color(hue, style)
            self._sparks.append(Spark(
                rocket.x, rocket.y,
                math.cos(angle) * velocity,
                math.sin(angle) * velocity,
                0.0, particle_lifetime, color,
                self._rng.uniform(0.75, 1.25),
                self._rng.random() < split_chance,
            ))
        self._deposit(rocket.x, rocket.y, (255.0, 255.0, 240.0), 2.2)
        self._burst_count += 1

    def _update_sparks(self, dt: float, time_elapsed: float):
        gravity = max(0.0, float(self.params.get("gravity", 0.34)))
        drag = min(1.0, max(0.0, float(self.params.get("air_drag", 0.965)))) ** (dt * 60.0)
        twinkle = max(0.0, min(1.0, float(self.params.get("twinkle", 0.28))))
        trail_intensity = max(0.0, float(self.params.get("trail_intensity", 1.0)))
        survivors: List[Spark] = []
        children: List[Spark] = []
        for spark in self._sparks:
            spark.age += dt
            old_x, old_y = spark.x, spark.y
            spark.x += spark.vx * dt
            spark.y += spark.vy * dt
            spark.vx *= drag
            spark.vy = spark.vy * drag + gravity * dt
            life = spark.age / spark.lifetime
            if self._plant_active:
                collision = self._plant_collision_point(old_x, old_y, spark.x, spark.y)
                if collision is not None:
                    self._record_plant_hit(*collision, max(0.15, 1.0 - life))
                    continue
            if spark.can_split and not spark.split and life >= 0.55:
                spark.split = True
                for direction in (-1.0, 1.0):
                    children.append(Spark(
                        spark.x, spark.y,
                        spark.vx + direction * self._rng.uniform(0.025, 0.07),
                        spark.vy + self._rng.uniform(-0.04, 0.04),
                        0.0, spark.lifetime * 0.32, spark.color, 0.65,
                    ))
            if life >= 1.0 or spark.x < -0.12 or spark.x > 1.12 or spark.y > 1.08:
                continue
            fade = (1.0 - life) ** 1.35
            shimmer = 1.0 - twinkle + twinkle * (0.35 + 0.65 * abs(math.sin(time_elapsed * 25.0 + spark.x * 31.0)))
            self._deposit(spark.x, spark.y, spark.color, fade * shimmer * spark.size * trail_intensity)
            survivors.append(spark)
        # Keep pathological live parameter combinations bounded.
        # Plant collision sampling has a little extra cost, and masked sparks
        # contribute less useful light, so retain a smaller stress-path cloud.
        spark_cap = 1000 if self._plant_active else 1400
        self._sparks = (survivors + children)[-spark_cap:]

    def _choose_hue(self) -> float:
        palette = str(self.params.get("palette", "festival")).lower().strip()
        if palette == "random":
            return self._rng.random()
        hues = self.PALETTES.get(palette, self.PALETTES["festival"])
        if palette == "monochrome":
            return float(self.params.get("base_hue", 0.02)) % 1.0
        return self._rng.choice(hues)

    def _hue_color(self, hue: float, style: str) -> Color:
        saturation = max(0.0, min(1.0, float(self.params.get("color_saturation", 1.0))))
        value = max(0.0, min(1.0, float(self.params.get("color_value", 1.0))))
        if style == "willow":
            hue, saturation = 0.105, min(saturation, 0.78)
        red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
        return red * 255.0, green * 255.0, blue * 255.0

    def _deposit(self, x: float, y: float, color: Color, intensity: float):
        """Add a sub-pixel light sample using bilinear filtering."""
        if intensity <= 0.0 or not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return
        px = x * max(0, self.width - 1)
        py = y * max(0, self.height - 1)
        x0, y0 = int(px), int(py)
        fx, fy = px - x0, py - y0
        red = color[0] * intensity
        green = color[1] * intensity
        blue = color[2] * intensity
        for yy, wy in ((y0, 1.0 - fy), (y0 + 1, fy)):
            if not 0 <= yy < self.height:
                continue
            for xx, wx in ((x0, 1.0 - fx), (x0 + 1, fx)):
                weight = wx * wy
                if 0 <= xx < self.width and weight > 0.0:
                    if self._plant_active and self._plant_obstacle[yy, xx]:
                        continue
                    pixel = self._trail[yy, xx]
                    pixel[0] += red * weight
                    pixel[1] += green * weight
                    pixel[2] += blue * weight

    def _refresh_plant_geometry(self):
        masks = self.get_plant_masks()
        if masks is self._plant_geometry:
            return
        # Shared masks are [strip, physical LED], while this particle canvas is
        # [top-down y, x].
        self._plant_foliage[:] = masks.foliage.T[::-1]
        self._plant_globes[:] = masks.globes.T[::-1]
        self._plant_obstacle[:] = masks.obstacle.T[::-1]
        self._plant_clearance[:] = masks.clearance.T[::-1]
        self._plant_geometry = masks

    def _nearest_visible_launch_x(self, requested_x: float, target_y: float) -> float:
        """Choose a low-occlusion vertical route near the authored launch point."""
        if self.width <= 1 or not np.any(self._plant_clearance):
            return requested_x
        target_row = min(self.height - 1, max(0, int(round(target_y * (self.height - 1)))))
        requested_column = requested_x * (self.width - 1)
        best_column = min(self.width - 1, max(0, int(round(requested_column))))
        best_score = -math.inf
        for column in range(self.width):
            route = self._plant_clearance[target_row:, column]
            visible_fraction = 1.0 - float(np.count_nonzero(route)) / max(1, route.size)
            target_safe = 1.0 if not self._plant_clearance[target_row, column] else 0.0
            distance = abs(column - requested_column) / max(1, self.width - 1)
            score = target_safe * 3.0 + visible_fraction - distance * 0.18
            if score > best_score:
                best_score = score
                best_column = column
        return best_column / max(1, self.width - 1)

    def _plant_collision(self, x: float, y: float) -> bool:
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return False
        column = min(self.width - 1, max(0, int(round(x * (self.width - 1)))))
        row = min(self.height - 1, max(0, int(round(y * (self.height - 1)))))
        return bool(self._plant_obstacle[row, column])

    def _plant_collision_point(
        self, x0: float, y0: float, x1: float, y1: float
    ):
        """Return the first masked point crossed by a spark's bounded step."""
        pixel_dx = abs(x1 - x0) * max(1, self.width - 1)
        pixel_dy = abs(y1 - y0) * max(1, self.height - 1)
        steps = max(1, int(math.ceil(max(pixel_dx, pixel_dy))))
        for step in range(1, steps + 1):
            amount = step / steps
            x = x0 + (x1 - x0) * amount
            y = y0 + (y1 - y0) * amount
            if self._plant_collision(x, y):
                return x, y
        return None

    def _record_plant_hit(self, x: float, y: float, energy: float):
        column = min(self.width - 1, max(0, int(round(x * (self.width - 1)))))
        row = min(self.height - 1, max(0, int(round(y * (self.height - 1)))))
        if self._plant_globes[row, column]:
            self._plant_globe_flash = min(1.0, self._plant_globe_flash + energy)
        else:
            self._plant_foliage_flash = min(1.0, self._plant_foliage_flash + energy)
        self._plant_hits += 1

    def _render_plant_silhouettes(self, image: np.ndarray):
        """Reveal plants as cool foliage and warm globe silhouettes on impact."""
        image[self._plant_obstacle] *= 0.18
        halo = self._plant_clearance & ~self._plant_obstacle
        if np.any(halo):
            halo_color = np.array((5.0, 12.0, 24.0), dtype=np.float32)
            image[halo] = np.maximum(image[halo], halo_color)
        if np.any(self._plant_foliage):
            foliage = np.array(
                (7.0, 38.0 + 150.0 * self._plant_foliage_flash, 25.0 + 55.0 * self._plant_foliage_flash),
                dtype=np.float32,
            )
            image[self._plant_foliage] = np.maximum(image[self._plant_foliage], foliage)
        if np.any(self._plant_globes):
            globes = np.array(
                (66.0 + 180.0 * self._plant_globe_flash, 18.0 + 80.0 * self._plant_globe_flash, 82.0),
                dtype=np.float32,
            )
            image[self._plant_globes] = np.maximum(image[self._plant_globes], globes)
