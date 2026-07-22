#!/usr/bin/env python3
"""Cozy Christmas scenes with a grounded tree and gently falling snow."""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from animation import AnimationBase


Color = Tuple[int, int, int]


class ChristmasTreeAnimation(AnimationBase):
    """A layered Christmas tree staged in a small winter landscape."""

    ANIMATION_NAME = "Christmas Tree"
    ANIMATION_DESCRIPTION = "Cozy winter scenes with a grounded tree, gifts, garlands, and falling snow"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"

    SCENE_STYLES = ("midnight", "twilight", "aurora", "cabin")
    TREE_PALETTES = ("classic", "gold", "frost", "candy")
    PALETTES: Dict[str, Dict[str, Sequence[Color] | Color]] = {
        "classic": {
            "tree": (18, 126, 52), "tree_dark": (5, 55, 28),
            "lights": ((255, 62, 48), (255, 205, 58), (65, 165, 255), (245, 245, 220)),
            "garland": (225, 45, 48),
        },
        "gold": {
            "tree": (24, 104, 53), "tree_dark": (5, 43, 26),
            "lights": ((255, 238, 168), (255, 181, 54), (255, 248, 220)),
            "garland": (255, 192, 60),
        },
        "frost": {
            "tree": (26, 112, 95), "tree_dark": (5, 45, 54),
            "lights": ((210, 250, 255), (100, 205, 255), (188, 160, 255)),
            "garland": (175, 225, 255),
        },
        "candy": {
            "tree": (35, 132, 72), "tree_dark": (8, 53, 38),
            "lights": ((255, 92, 145), (110, 225, 255), (255, 244, 175), (210, 130, 255)),
            "garland": (255, 225, 232),
        },
    }

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "speed": 1.0,
            "twinkle_speed": 1.0,
            "snowfall_density": 0.35,
            "snow_layer_depth": 4,
            "tree_height": 52,
            "light_count": 34,
            "scene_style": "midnight",
            "tree_palette": "classic",
            "show_garland": True,
            "show_presents": True,
            "seed": 1225,
        })
        self.params = {**self.default_params, **self.config}

        self.width, self.height = self.get_strip_info()
        self.random = random.Random(int(self.params["seed"]))
        self.snowflakes: List[Dict[str, float]] = []
        self._last_update_time: Optional[float] = None
        self._layout_cache_key: Tuple[Any, ...] = ()
        self._background_cache_key: Tuple[Any, ...] = ()

        self._logical = np.zeros((self.width, self.height, 3), dtype=np.uint8)
        self._background = np.zeros_like(self._logical)
        self._x = np.arange(self.width, dtype=np.float32)[:, None]
        self._y = np.arange(self.height, dtype=np.float32)[None, :]
        self._xn = self._x / max(self.width - 1, 1)
        self._yn = self._y / max(self.height - 1, 1)

        self._tree_pixels: List[Tuple[int, int, float]] = []
        self._trunk_pixels: List[Tuple[int, int]] = []
        self._present_blocks: List[Dict[str, Any]] = []
        self._star_pixels: List[Tuple[int, int, float]] = []
        self._light_nodes: List[Dict[str, Any]] = []
        self._garland_pixels: List[Tuple[int, int, float]] = []
        self._snow_cap_pixels: List[Tuple[int, int]] = []
        self._ground_noise: List[float] = []
        self._snow_depth = 4
        self._snow_start_y = max(0, self.height - self._snow_depth)
        self._snow_contact_y = max(0, self._snow_start_y - 1)
        self._tree_center = self.width // 2
        self._plant_foliage = np.zeros((self.width, self.height), dtype=bool)
        self._plant_globes = np.zeros((self.width, self.height), dtype=bool)
        self._plant_obstacle = np.zeros((self.width, self.height), dtype=bool)
        self._plant_clearance = np.zeros((self.width, self.height), dtype=bool)

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            "speed": {"type": "float", "min": 0.1, "max": 4.0, "default": 1.0, "description": "Snowfall and atmosphere speed"},
            "brightness": {"type": "float", "min": 0.05, "max": 1.0, "default": 1.0, "description": "Overall brightness"},
            "color_saturation": {"type": "float", "min": 0.0, "max": 1.0, "default": 1.0, "description": "Compatibility color control"},
            "color_value": {"type": "float", "min": 0.0, "max": 1.0, "default": 1.0, "description": "Compatibility value control"},
            "twinkle_speed": {"type": "float", "min": 0.1, "max": 3.0, "default": 1.0, "description": "Speed of ornament twinkles"},
            "snowfall_density": {"type": "float", "min": 0.0, "max": 1.5, "default": 0.35, "description": "Amount of falling snow"},
            "snow_layer_depth": {"type": "int", "min": 1, "max": 10, "default": 4, "description": "Depth of snow along the bottom edge"},
            "tree_height": {"type": "int", "min": 14, "max": 110, "default": 52, "description": "Tree height including trunk"},
            "light_count": {"type": "int", "min": 6, "max": 80, "default": 34, "description": "Number of glowing ornaments"},
            "scene_style": {"type": "str", "options": list(self.SCENE_STYLES), "default": "midnight", "description": "Winter sky and landscape"},
            "tree_palette": {"type": "str", "options": list(self.TREE_PALETTES), "default": "classic", "description": "Tree, ornament, and garland colors"},
            "show_garland": {"type": "bool", "default": True, "description": "Drape a garland across the branches"},
            "show_presents": {"type": "bool", "default": True, "description": "Place wrapped gifts beneath the tree"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 1225, "description": "Repeatable snow and ornament arrangement"},
        })
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        super().update_parameters(new_params)
        if {
            "plant_aware", "plant_clearance", "plant_mask_path",
            "plant_globe_mask_path",
        } & new_params.keys():
            self._layout_cache_key = ()

    def generate_frame(self, time_elapsed: float, frame_count: int):
        self._build_static_elements()
        self._render_background(time_elapsed)
        np.copyto(self._logical, self._background)

        delta = self._compute_delta(time_elapsed)
        self._update_snowflakes(delta, time_elapsed)
        self._draw_landscape(time_elapsed)
        self._draw_ground(time_elapsed)
        self._draw_tree()
        self._draw_trunk()
        if bool(self.params.get("show_garland", True)):
            self._draw_garland(time_elapsed)
        if bool(self.params.get("show_presents", True)):
            self._draw_presents()
        self._draw_lights(time_elapsed)
        self._draw_star(time_elapsed)
        self._draw_falling_snow(time_elapsed)
        if self.plant_aware_enabled():
            self._draw_plant_landmarks(time_elapsed)

        # Logical y=0 is the visual top. Physical LED 0 is mounted at the
        # visual bottom, so flip once here after the entire scene is composed.
        frame = self.next_frame_buffer(clear=False)
        frame[:] = self._logical[:, ::-1, :].reshape((-1, 3))
        self.apply_brightness_array(frame, out=frame)
        return frame

    def _choice(self, key: str, options: Sequence[str], fallback: str) -> str:
        value = str(self.params.get(key, fallback)).lower()
        return value if value in options else fallback

    def _build_static_elements(self) -> None:
        snow_depth = max(1, min(int(self.params.get("snow_layer_depth", 4)), max(1, self.height // 4)))
        requested_height = int(self.params.get("tree_height", 52))
        light_count = max(6, int(self.params.get("light_count", 34)))
        seed = int(self.params.get("seed", 1225))
        palette_name = self._choice("tree_palette", self.TREE_PALETTES, "classic")
        plant_key = (
            self.plant_aware_enabled(),
            int(self.params.get("plant_clearance", 1)),
            str(self.params.get("plant_mask_path", "")),
            str(self.params.get("plant_globe_mask_path", "")),
        )
        key = (
            self.width, self.height, snow_depth, requested_height, light_count,
            seed, palette_name, plant_key,
        )
        if key == self._layout_cache_key and self._tree_pixels:
            return

        self._layout_cache_key = key
        self.random = random.Random(seed)
        self.snowflakes.clear()
        self._snow_depth = snow_depth
        self._snow_start_y = self.height - snow_depth
        self._snow_contact_y = max(0, self._snow_start_y - 1)
        self._prepare_plant_layers()
        self._tree_center = self.width // 2

        max_height = max(8, self._snow_contact_y - 4)
        tree_height = max(8, min(requested_height, max_height))
        trunk_height = max(3, min(8, round(tree_height * 0.13)))
        foliage_height = max(5, tree_height - trunk_height)
        foliage_bottom = self._snow_contact_y - trunk_height
        foliage_top = max(3, foliage_bottom - foliage_height + 1)
        foliage_height = foliage_bottom - foliage_top + 1
        base_half_width = max(2, min((self.width - 3) // 2, round(foliage_height * 0.31)))
        if self.plant_aware_enabled():
            self._tree_center = self._choose_plant_aware_center(
                foliage_top, foliage_bottom, foliage_height, base_half_width,
            )

        self._tree_pixels = []
        self._trunk_pixels = []
        self._present_blocks = []
        self._star_pixels = []
        self._garland_pixels = []

        tier_count = max(4, min(7, foliage_height // 7))
        for y in range(foliage_top, foliage_bottom + 1):
            progress = (y - foliage_top) / max(foliage_height - 1, 1)
            tier_position = progress * tier_count
            tier = min(tier_count - 1, int(tier_position))
            local = tier_position - tier
            tier_envelope = (tier + 1) / tier_count
            half_width = round(base_half_width * tier_envelope * (0.58 + 0.42 * local))
            half_width = max(0 if y == foliage_top else 1, min(base_half_width, half_width))
            for x in range(max(0, self._tree_center - half_width), min(self.width, self._tree_center + half_width + 1)):
                self._tree_pixels.append((x, y, progress))

        trunk_width = min(self.width, 3 if self.width >= 18 else 2)
        trunk_left = max(0, self._tree_center - trunk_width // 2)
        for x in range(trunk_left, min(self.width, trunk_left + trunk_width)):
            for y in range(foliage_bottom + 1, self._snow_contact_y + 1):
                self._trunk_pixels.append((x, y))

        star_center_y = foliage_top - 2
        for dx, dy, glow in ((0, 0, 1.0), (-1, 0, .82), (1, 0, .82), (0, -1, .9), (0, 1, .9), (-1, -1, .45), (1, -1, .45)):
            x, y = self._tree_center + dx, star_center_y + dy
            if 0 <= x < self.width and 0 <= y < self.height:
                self._star_pixels.append((x, y, glow))

        specs = [
            (-7, 4, (176, 38, 42), (255, 210, 105)),
            (-2, 3, (34, 107, 170), (225, 245, 255)),
            (4, 4, (202, 109, 35), (255, 232, 132)),
        ]
        for offset, size, color, trim in specs:
            size = max(2, min(size, self.width))
            left = max(0, min(self.width - size, self._tree_center + offset))
            self._present_blocks.append({
                "left": left, "top": self._snow_contact_y - size + 1,
                "size": size, "color": color, "trim": trim,
            })

        positions = [
            (x, y) for x, y, progress in self._tree_pixels
            if .12 < progress < .96
            and (not self.plant_aware_enabled() or not self._plant_clearance[x, y])
        ]
        self._light_nodes = []
        for x, y in self.random.sample(positions, min(light_count, len(positions))):
            self._light_nodes.append({
                "x": x, "y": y, "phase": self.random.random() * math.tau,
                "speed": self.random.uniform(.65, 1.3),
                "color_index": self.random.randrange(len(self.PALETTES[palette_name]["lights"])),
            })

        if foliage_height >= 10:
            for ratio in (.32, .52, .72):
                center_y = foliage_top + round((foliage_height - 1) * ratio)
                half_span = max(2, round(base_half_width * ratio * .9))
                for x in range(self._tree_center - half_span, self._tree_center + half_span + 1):
                    curve = 2.2 * (1.0 - ((x - self._tree_center) / max(half_span, 1)) ** 2)
                    self._garland_pixels.append((x, round(center_y + curve), ratio))

        tree_set = {(x, y) for x, y, _ in self._tree_pixels}
        self._snow_cap_pixels = [
            (x, y) for x, y, _ in self._tree_pixels
            if (x, y - 1) not in tree_set and (x + y) % 2 == 0
        ]
        self._ground_noise = [self.random.random() * math.tau for _ in range(self.width)]
        self._background_cache_key = ()

    def _prepare_plant_layers(self) -> None:
        self._plant_foliage.fill(False)
        self._plant_globes.fill(False)
        self._plant_obstacle.fill(False)
        self._plant_clearance.fill(False)
        if not self.plant_aware_enabled():
            return
        masks = self.get_plant_masks()
        # Shared masks use physical LED coordinates; the scene uses visual y=0
        # at the top, hence the same vertical flip used for the final frame.
        self._plant_foliage[:] = masks.foliage[:, ::-1]
        self._plant_globes[:] = masks.globes[:, ::-1]
        self._plant_obstacle[:] = masks.obstacle[:, ::-1]
        self._plant_clearance[:] = masks.clearance[:, ::-1]

    def _choose_plant_aware_center(
        self,
        foliage_top: int,
        foliage_bottom: int,
        foliage_height: int,
        base_half_width: int,
    ) -> int:
        """Fit the authored tree around plants without hiding its identity.

        Foliage/globes inside the broad canopy are useful (the physical plants
        become branches and ornaments), but clearance over the outer silhouette
        and especially the star is expensive.  The deterministic score retains
        the closest-to-center placement when candidates are otherwise equal.
        """
        nominal = self.width // 2
        left = max(1, base_half_width)
        right = min(self.width - 2, self.width - 1 - base_half_width)
        if left > right:
            return nominal

        tier_count = max(4, min(7, foliage_height // 7))
        scored: List[Tuple[Tuple[int, int, int, int], int]] = []
        for center in range(left, right + 1):
            canopy: List[Tuple[int, int]] = []
            outline: List[Tuple[int, int]] = []
            for y in range(foliage_top, foliage_bottom + 1):
                progress = (y - foliage_top) / max(foliage_height - 1, 1)
                tier_position = progress * tier_count
                tier = min(tier_count - 1, int(tier_position))
                local = tier_position - tier
                envelope = (tier + 1) / tier_count
                half_width = round(base_half_width * envelope * (.58 + .42 * local))
                half_width = max(0 if y == foliage_top else 1, min(base_half_width, half_width))
                row = [(x, y) for x in range(center - half_width, center + half_width + 1)]
                canopy.extend(row)
                if row:
                    outline.extend((row[0], row[-1]))

            star_y = foliage_top - 2
            star = [
                (center + dx, star_y + dy)
                for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1))
                if 0 <= center + dx < self.width and 0 <= star_y + dy < self.height
            ]
            star_hits = sum(bool(self._plant_clearance[x, y]) for x, y in star)
            outline_hits = sum(bool(self._plant_clearance[x, y]) for x, y in outline)
            integrated = sum(
                1 + 2 * int(self._plant_globes[x, y])
                for x, y in canopy if self._plant_obstacle[x, y]
            )
            score = (star_hits, outline_hits, -integrated, abs(center - nominal))
            scored.append((score, center))
        return min(scored)[1]

    def _render_background(self, time_elapsed: float) -> None:
        style = self._choice("scene_style", self.SCENE_STYLES, "midnight")
        seed = int(self.params.get("seed", 1225))
        static_key = (self.width, self.height, style, seed)
        animated = style == "aurora"
        if static_key == self._background_cache_key and not animated:
            return
        self._background_cache_key = static_key

        top, bottom = {
            "midnight": ((2, 7, 24), (12, 27, 55)),
            "twilight": ((18, 8, 35), (83, 42, 67)),
            "aurora": ((1, 13, 27), (5, 42, 55)),
            "cabin": ((19, 8, 22), (92, 43, 34)),
        }[style]
        blend = self._yn[:, :, None]
        column = np.asarray(top, dtype=np.float32) + (np.asarray(bottom) - np.asarray(top)) * blend
        base = np.broadcast_to(column, (self.width, self.height, 3)).copy()
        if animated:
            t = time_elapsed * float(self.params.get("speed", 1.0))
            ribbon = np.sin(self._xn * 8.0 + self._yn * 3.5 + t * .55)
            ribbon += .55 * np.sin(self._xn * 14.0 - self._yn * 5.0 - t * .35)
            strength = np.clip((ribbon - .55) * .18, 0, .22)[:, :, None]
            base += np.asarray((20, 165, 112), dtype=np.float32) * strength
            base += np.asarray((62, 55, 160), dtype=np.float32) * np.roll(strength, 9, axis=1) * .55
        np.clip(base, 0, 255, out=base)
        self._background[:] = base.astype(np.uint8)

        rng = random.Random(seed + 41)
        star_count = max(8, self.width * self.height // 180)
        for _ in range(star_count):
            x = rng.randrange(self.width)
            y = rng.randrange(max(1, int(self.height * .64)))
            value = rng.randrange(80, 175) if style != "twilight" else rng.randrange(55, 115)
            self._background[x, y] = (value, value, min(255, value + 35))

    def _draw_landscape(self, time_elapsed: float) -> None:
        style = self._choice("scene_style", self.SCENE_STYLES, "midnight")
        horizon = max(4, self._snow_start_y - 13)
        # Distant trees make the ground plane read as a landscape rather than a stripe.
        for center, height in ((3, 9), (self.width - 4, 12), (8, 7), (self.width - 10, 6)):
            for dy in range(height):
                y = horizon + height - 1 - dy
                half = max(0, dy // 3)
                for x in range(center - half, center + half + 1):
                    self._paint(x, y, (6, 31, 34) if style != "twilight" else (29, 26, 45))

        if style == "cabin":
            left, width, roof_y = 0, 9, self._snow_start_y - 18
            for x in range(left, min(left + width, self.width)):
                roof_offset = abs(x - (left + width // 2))
                for y in range(roof_y + roof_offset // 2, self._snow_start_y):
                    self._paint(x, y, (48, 24, 24))
            glow = .82 + .18 * math.sin(time_elapsed * 1.7)
            warm = (int(255 * glow), int(157 * glow), int(55 * glow))
            for x in (left + 1, left + 2, left + 4, left + 5):
                for y in range(self._snow_start_y - 8, self._snow_start_y - 4):
                    self._paint(x, y, warm)

    def _compute_delta(self, time_elapsed: float) -> float:
        if self._last_update_time is None or time_elapsed < self._last_update_time:
            delta = 1.0 / 40.0
        else:
            delta = min(.1, max(0.0, time_elapsed - self._last_update_time))
        self._last_update_time = time_elapsed
        return delta

    def _update_snowflakes(self, delta: float, time_elapsed: float) -> None:
        density = max(0.0, float(self.params.get("snowfall_density", .35)))
        target = 0 if density <= 0 else max(3, round(self.width * density * 2.2))
        base_speed = max(.1, float(self.params.get("speed", 1.0)))
        if len(self.snowflakes) > target:
            del self.snowflakes[target:]
        while len(self.snowflakes) < target:
            self.snowflakes.append(self._spawn_flake(base_speed, initial=True))

        for flake in self.snowflakes:
            flake["x"] += (flake["drift"] + math.sin(time_elapsed * .7 + flake["phase"]) * .16) * delta
            flake["y"] += flake["speed"] * delta
            flake["x"] %= self.width
            if flake["y"] >= self._snow_contact_y - self.random.random() * 1.5:
                flake.update(self._spawn_flake(base_speed, initial=False))

    def _spawn_flake(self, base_speed: float, *, initial: bool) -> Dict[str, float]:
        return {
            "x": self.random.uniform(0, max(0, self.width - 1)),
            "y": self.random.uniform(0, self._snow_contact_y) if initial else self.random.uniform(-6.0, -1.0),
            "speed": self.random.uniform(2.0, 5.2) * base_speed,
            "drift": self.random.uniform(-.4, .4),
            "phase": self.random.random() * math.tau,
        }

    def _draw_ground(self, time_elapsed: float) -> None:
        for x in range(self.width):
            sparkle = (math.sin(self._ground_noise[x] + time_elapsed * .35) + 1) * .5
            for depth in range(self._snow_depth):
                y = self._snow_start_y + depth
                ratio = depth / max(self._snow_depth - 1, 1)
                value = int(220 + sparkle * 25 - ratio * 28)
                self._paint(x, y, (value, min(255, value + 4), min(255, value + 18)))

    def _draw_tree(self) -> None:
        palette = self.PALETTES[self._choice("tree_palette", self.TREE_PALETTES, "classic")]
        bright = palette["tree"]
        dark = palette["tree_dark"]
        for x, y, progress in self._tree_pixels:
            if self.plant_aware_enabled() and self._plant_obstacle[x, y]:
                continue
            wave = .08 * math.sin(x * 1.3 + y * .27)
            mix = max(.18, min(1.0, .42 + progress * .45 + wave))
            color = tuple(round(dark[i] + (bright[i] - dark[i]) * mix) for i in range(3))
            self._paint(x, y, color)
        if self._choice("tree_palette", self.TREE_PALETTES, "classic") == "frost":
            for x, y in self._snow_cap_pixels:
                if self.plant_aware_enabled() and self._plant_obstacle[x, y]:
                    continue
                self._paint(x, y, (178, 224, 232))

    def _draw_trunk(self) -> None:
        for x, y in self._trunk_pixels:
            self._paint_if_plant_clear(x, y, (91, 50, 25))

    def _draw_garland(self, time_elapsed: float) -> None:
        palette = self.PALETTES[self._choice("tree_palette", self.TREE_PALETTES, "classic")]
        base = palette["garland"]
        pulse = .78 + .12 * math.sin(time_elapsed * .8)
        color = tuple(round(component * pulse) for component in base)
        for x, y, _ in self._garland_pixels:
            self._paint_if_plant_clear(x, y, color)

    def _draw_presents(self) -> None:
        for block in self._present_blocks:
            for dx in range(block["size"]):
                for dy in range(block["size"]):
                    color = block["trim"] if dx == block["size"] // 2 or dy == 1 else block["color"]
                    self._paint_if_plant_clear(block["left"] + dx, block["top"] + dy, color)

    def _draw_lights(self, time_elapsed: float) -> None:
        palette = self.PALETTES[self._choice("tree_palette", self.TREE_PALETTES, "classic")]["lights"]
        speed = max(.1, float(self.params.get("twinkle_speed", 1.0)))
        for light in self._light_nodes:
            intensity = .56 + .44 * (math.sin(light["phase"] + time_elapsed * speed * light["speed"] * math.tau) + 1) * .5
            source = palette[light["color_index"] % len(palette)]
            self._paint_if_plant_clear(
                light["x"], light["y"], tuple(round(c * intensity) for c in source),
            )

    def _draw_star(self, time_elapsed: float) -> None:
        pulse = .88 + .12 * math.sin(time_elapsed * 2.2)
        for x, y, glow in self._star_pixels:
            self._paint_if_plant_clear(
                x, y,
                (round(255 * pulse * glow), round(226 * pulse * glow), round(92 * pulse * glow)),
            )

    def _draw_falling_snow(self, time_elapsed: float) -> None:
        for flake in self.snowflakes:
            shimmer = .72 + .28 * math.sin(time_elapsed * 4.0 + flake["phase"]) ** 2
            value = round(225 * shimmer)
            self._paint_if_plant_clear(
                round(flake["x"]) % self.width, round(flake["y"]),
                (value, value, min(255, value + 25)),
            )

    def _draw_plant_landmarks(self, time_elapsed: float) -> None:
        """Turn the physical plant wall into a second, living tree layer."""
        foliage_pulse = .78 + .12 * math.sin(time_elapsed * .7)
        foliage = np.asarray(
            (round(8 * foliage_pulse), round(83 * foliage_pulse), round(27 * foliage_pulse)),
            dtype=np.uint8,
        )
        self._logical[self._plant_foliage] = np.maximum(
            self._logical[self._plant_foliage], foliage,
        )

        # Rooting globes read as large glass ornaments. Their magenta shimmer
        # distinguishes them from the evergreen foliage.
        globe_pulse = .82 + .18 * np.sin(time_elapsed * 1.15 + self._x * .55)
        globe_color = np.empty_like(self._logical)
        globe_color[..., 0] = np.rint(145 * globe_pulse).astype(np.uint8)
        globe_color[..., 1] = np.rint(53 * globe_pulse).astype(np.uint8)
        globe_color[..., 2] = np.rint(188 * globe_pulse).astype(np.uint8)
        self._logical[self._plant_globes] = np.maximum(
            self._logical[self._plant_globes], globe_color[self._plant_globes],
        )

    def _paint_if_plant_clear(self, x: int, y: int, color: Color) -> None:
        if (
            self.plant_aware_enabled()
            and 0 <= x < self.width and 0 <= y < self.height
            and self._plant_clearance[x, y]
        ):
            return
        self._paint(x, y, color)

    def get_runtime_stats(self) -> Dict[str, Any]:
        if not self.plant_aware_enabled():
            return {"plant_aware": False}
        masks = self.get_plant_masks()
        return {
            "plant_aware": True,
            "plant_foliage_pixels": masks.foliage_count,
            "plant_globe_pixels": masks.globe_count,
            "plant_globe_regions": masks.globe_regions,
            "plant_tree_center": self._tree_center,
        }

    def _paint(self, x: int, y: int, color: Color) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self._logical[x, y] = tuple(max(0, min(255, int(c))) for c in color)
