#!/usr/bin/env python3
"""Christmas Tree animation with gifts, falling snow, and twinkling lights."""

import math
import random
from typing import Any, Dict, List, Tuple

from animation import AnimationBase


class ChristmasTreeAnimation(AnimationBase):
    """Festive scene featuring a Christmas tree, snow, and presents."""

    ANIMATION_NAME = "Christmas Tree"
    ANIMATION_DESCRIPTION = "Christmas tree with twinkling lights, gently falling snow, and presents"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    LIGHT_COLORS: Tuple[Tuple[int, int, int], ...] = (
        (255, 120, 80),
        (255, 230, 140),
        (120, 200, 255),
        (180, 255, 160),
        (255, 150, 210),
    )

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.default_params.update({
            'speed': 1.0,
            'twinkle_speed': 1.0,
            'snowfall_density': 0.45,
            'snow_layer_depth': 3,
            'tree_height': 18,
            'light_count': 24
        })
        self.params = {**self.default_params, **self.config}

        self.random = random.Random()
        self.snowflakes: List[Dict[str, float]] = []
        self._last_update_time: float = 0.0
        self._layout_cache_key: Tuple[int, ...] = tuple()

        # Cached layout data populated when dimensions/params change
        self._tree_pixels: List[Tuple[int, int, float]] = []
        self._trunk_pixels: List[Tuple[int, int]] = []
        self._present_blocks: List[Dict[str, Any]] = []
        self._star_pixels: List[Tuple[int, int]] = []
        self._light_nodes: List[Dict[str, Any]] = []
        self._ground_noise: List[float] = []

        self._snow_depth = 3
        self._snow_start_y = controller.leds_per_strip - self._snow_depth
        self._snow_contact_y = max(0, self._snow_start_y - 1)
        self._tree_center = controller.strip_count // 2

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'twinkle_speed': {
                'type': 'float',
                'min': 0.5,
                'max': 3.0,
                'default': 1.0,
                'description': 'Speed of light twinkling'
            },
            'snowfall_density': {
                'type': 'float',
                'min': 0.1,
                'max': 1.5,
                'default': 0.45,
                'description': 'Number of falling snowflakes'
            },
            'snow_layer_depth': {
                'type': 'int',
                'min': 1,
                'max': 8,
                'default': 3,
                'description': 'Thickness of the snow resting on the ground'
            },
            'tree_height': {
                'type': 'int',
                'min': 10,
                'max': 40,
                'default': 18,
                'description': 'Overall height of the tree (foliage plus trunk)'
            },
            'light_count': {
                'type': 'int',
                'min': 6,
                'max': 60,
                'default': 24,
                'description': 'Number of twinkling bulbs placed across the tree'
            }
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        strip_count, leds_per_strip = self.get_strip_info()
        self._build_static_elements()

        total_pixels = strip_count * leds_per_strip
        pixel_colors: List[Tuple[int, int, int]] = []
        for strip in range(strip_count):
            for led in range(leds_per_strip):
                altitude = 1.0 - (led / max(leds_per_strip - 1, 1))
                base = 5 + int(10 * altitude)
                green = 15 + int(25 * altitude)
                blue = 30 + int(55 * altitude)
                pixel_colors.append((base, green, blue))

        delta_time = self._compute_delta(time_elapsed)
        self._update_snowflakes(delta_time, time_elapsed)

        self._draw_ground(pixel_colors, time_elapsed)
        self._draw_tree(pixel_colors)
        self._draw_trunk(pixel_colors, time_elapsed)
        self._draw_presents(pixel_colors)
        self._draw_lights(pixel_colors, time_elapsed)
        self._draw_star(pixel_colors, time_elapsed)
        self._draw_falling_snow(pixel_colors, time_elapsed)

        return [self.apply_brightness(self._clamp_color(color)) for color in pixel_colors]

    def _build_static_elements(self):
        strip_count, leds_per_strip = self.get_strip_info()
        snow_depth = max(1, min(int(self.params.get('snow_layer_depth', 3)), leds_per_strip // 4))
        tree_height_requested = int(self.params.get('tree_height', 18))
        light_count = max(6, int(self.params.get('light_count', 24)))

        cache_key = (strip_count, leds_per_strip, snow_depth, tree_height_requested, light_count)
        if cache_key == self._layout_cache_key and self._tree_pixels:
            return

        self._layout_cache_key = cache_key
        self._snow_depth = snow_depth
        self._snow_start_y = max(0, leds_per_strip - snow_depth)
        self._snow_contact_y = max(0, self._snow_start_y - 1)
        ground_y = max(0, self._snow_contact_y)
        available_height = ground_y + 1
        tree_height = max(10, min(tree_height_requested, available_height))
        trunk_height = max(2, min(5, tree_height // 5 + 1))
        if tree_height - trunk_height < 4:
            trunk_height = max(1, tree_height - 4)
        foliage_height = tree_height - trunk_height

        foliage_bottom_y = max(0, ground_y - trunk_height)
        foliage_top_y = max(0, foliage_bottom_y - foliage_height + 1)
        self._tree_center = strip_count // 2

        base_half_width = max(2, min(strip_count // 2 - 1, foliage_height // 2 + 1))

        self._tree_pixels = []
        self._trunk_pixels = []
        self._present_blocks = []
        self._star_pixels = []

        denom = max(foliage_height - 1, 1)
        for row in range(foliage_height):
            y = foliage_bottom_y - row
            from_bottom = row / denom
            width_factor = from_bottom ** 0.9
            half_width = max(0, min(base_half_width, int(width_factor * base_half_width)))
            if row < 2:
                half_width = 0
            start_x = max(0, self._tree_center - half_width - (1 if row % 3 == 0 and half_width < base_half_width else 0))
            end_x = min(strip_count - 1, self._tree_center + half_width + (1 if row % 4 == 0 and half_width < base_half_width else 0))
            for x in range(start_x, end_x + 1):
                from_top = (y - foliage_top_y) / max(foliage_height - 1, 1)
                from_top = max(0.0, min(1.0, from_top))
                self._tree_pixels.append((x, y, 1.0 - from_top))

        trunk_width = 3 if strip_count >= 18 else 2
        trunk_width = min(trunk_width, strip_count)
        trunk_start_x = max(0, min(strip_count - trunk_width, self._tree_center - trunk_width // 2))
        for dx in range(trunk_width):
            for dy in range(trunk_height):
                x = trunk_start_x + dx
                y = ground_y - dy
                self._trunk_pixels.append((x, max(0, y)))

        star_y = foliage_top_y - 1
        if star_y >= 0:
            self._star_pixels.append((self._tree_center, star_y))
            if star_y - 1 >= 0:
                self._star_pixels.append((self._tree_center, star_y - 1))

        present_bottom_y = ground_y
        present_specs = [
            {'offset': -6, 'size': 3, 'color': (210, 60, 60), 'trim': (255, 230, 150)},
            {'offset': -2, 'size': 2, 'color': (60, 140, 230), 'trim': (230, 250, 255)},
            {'offset': 3, 'size': 3, 'color': (230, 150, 40), 'trim': (255, 255, 190)}
        ]
        for spec in present_specs:
            size = max(2, min(spec['size'], strip_count))
            start_x = int(self._tree_center + spec['offset'])
            start_x = max(0, min(strip_count - size, start_x))
            top_y = max(0, present_bottom_y - size + 1)
            self._present_blocks.append({
                'start_x': start_x,
                'width': size,
                'height': size,
                'top_y': top_y,
                'bottom_y': present_bottom_y,
                'color': spec['color'],
                'trim_color': spec['trim']
            })

        foliage_positions = [(x, y) for (x, y, _) in self._tree_pixels if y < present_bottom_y]
        if foliage_positions:
            light_count = min(light_count, len(foliage_positions))
            self._light_nodes = []
            chosen = self.random.sample(foliage_positions, light_count)
            for x, y in chosen:
                self._light_nodes.append({
                    'x': x,
                    'y': y,
                    'phase': self.random.random() * math.tau,
                    'speed': self.random.uniform(0.6, 1.4),
                    'color_index': self.random.randrange(len(self.LIGHT_COLORS))
                })
        else:
            self._light_nodes = []

        self._ground_noise = [self.random.random() * math.tau for _ in range(strip_count)]

    def _compute_delta(self, time_elapsed: float) -> float:
        if self._last_update_time == 0.0:
            delta = 1.0 / 40.0
        else:
            delta = time_elapsed - self._last_update_time
        self._last_update_time = time_elapsed
        if delta <= 0 or delta > 1.0:
            delta = 1.0 / 40.0
        return delta

    def _update_snowflakes(self, delta_time: float, time_elapsed: float):
        strip_count, leds_per_strip = self.get_strip_info()
        snowfall_density = max(0.1, float(self.params.get('snowfall_density', 0.45)))
        target_flakes = max(10, int(strip_count * snowfall_density * 3))
        base_speed = max(0.2, float(self.params.get('speed', 1.0)))

        while len(self.snowflakes) < target_flakes:
            self.snowflakes.append(self._spawn_flake(base_speed))

        for flake in self.snowflakes:
            drift_wiggle = math.sin(time_elapsed * 0.5 + flake['phase']) * 0.15
            flake['x'] += (flake['drift'] + drift_wiggle) * delta_time
            flake['y'] += flake['speed'] * delta_time

            if flake['x'] < 0:
                flake['x'] += strip_count
            elif flake['x'] >= strip_count:
                flake['x'] -= strip_count

            if flake['y'] >= self._snow_contact_y - self.random.random() * 1.5:
                flake.update(self._spawn_flake(base_speed))

    def _spawn_flake(self, base_speed: float) -> Dict[str, float]:
        strip_count, leds_per_strip = self.get_strip_info()
        return {
            'x': self.random.uniform(0, strip_count - 1),
            'y': -self.random.uniform(0.0, leds_per_strip * 0.25),
            'speed': self.random.uniform(2.0, 5.5) * base_speed,
            'drift': self.random.uniform(-0.35, 0.35),
            'phase': self.random.random() * math.tau
        }

    def _draw_ground(self, pixel_colors: List[Tuple[int, int, int]], time_elapsed: float):
        strip_count, leds_per_strip = self.get_strip_info()
        if not self._ground_noise:
            self._ground_noise = [self.random.random() * math.tau for _ in range(strip_count)]

        for strip in range(strip_count):
            undulation = (math.sin(self._ground_noise[strip] + time_elapsed * 0.4) + 1) * 0.5
            for depth in range(self._snow_depth):
                y = self._snow_start_y + depth
                if y >= leds_per_strip:
                    continue
                depth_ratio = depth / max(self._snow_depth - 1, 1)
                brightness = 0.7 + undulation * 0.15 - depth_ratio * 0.2
                base = int(190 + 55 * brightness)
                blue = min(255, base + 25)
                color = (base, base, blue)
                self._set_pixel(pixel_colors, strip, y, color)

    def _draw_tree(self, pixel_colors: List[Tuple[int, int, int]]):
        for x, y, topness in self._tree_pixels:
            branch_wave = math.sin((x - self._tree_center) * 0.8 + y * 0.1)
            shading = max(0.2, min(1.0, 0.35 + 0.65 * topness + branch_wave * 0.05))
            r = int(10 + 28 * shading)
            g = int(80 + 120 * shading)
            b = int(25 + 35 * (1.0 - shading))
            self._set_pixel(pixel_colors, x, y, (r, g, b))

    def _draw_trunk(self, pixel_colors: List[Tuple[int, int, int]], time_elapsed: float):
        for x, y in self._trunk_pixels:
            sway = math.sin(time_elapsed * 0.7 + x * 0.3)
            color = (
                int(90 + 20 * sway),
                int(55 + 15 * sway),
                30
            )
            self._set_pixel(pixel_colors, x, y, color)

    def _draw_presents(self, pixel_colors: List[Tuple[int, int, int]]):
        for block in self._present_blocks:
            start_x = block['start_x']
            width = block['width']
            height = block['height']
            top_y = block['top_y']
            bottom_y = block['bottom_y']
            for dx in range(width):
                for dy in range(height):
                    x = start_x + dx
                    y = bottom_y - dy
                    color = block['color']
                    if dx == width // 2 or dy == height // 2:
                        color = block['trim_color']
                    self._set_pixel(pixel_colors, x, y, color)

    def _draw_lights(self, pixel_colors: List[Tuple[int, int, int]], time_elapsed: float):
        twinkle_speed = max(0.1, float(self.params.get('twinkle_speed', 1.0)))
        for light in self._light_nodes:
            palette = self.LIGHT_COLORS[light['color_index']]
            phase = light['phase'] + time_elapsed * twinkle_speed * light['speed']
            intensity = (math.sin(phase * 2 * math.pi) + 1) * 0.5
            flicker = 0.6 + 0.4 * intensity
            color = tuple(min(255, int(c * flicker)) for c in palette)
            if intensity > 0.95 and self.random.random() < 0.05:
                light['color_index'] = self.random.randrange(len(self.LIGHT_COLORS))
            self._set_pixel(pixel_colors, light['x'], light['y'], color)

    def _draw_star(self, pixel_colors: List[Tuple[int, int, int]], time_elapsed: float):
        if not self._star_pixels:
            return
        flicker = 0.85 + 0.15 * math.sin(time_elapsed * 3.5)
        for idx, (x, y) in enumerate(self._star_pixels):
            base = 230 + idx * 15
            color = (
                int(base * flicker),
                int((base + 20) * flicker),
                int(120 * flicker)
            )
            self._set_pixel(pixel_colors, x, y, color)

    def _draw_falling_snow(self, pixel_colors: List[Tuple[int, int, int]], time_elapsed: float):
        strip_count, leds_per_strip = self.get_strip_info()
        for flake in self.snowflakes:
            x = int(round(flake['x'])) % strip_count
            y = int(round(flake['y']))
            if 0 <= y < leds_per_strip:
                shimmer = 0.75 + 0.25 * math.sin(time_elapsed * 5.0 + flake['phase'])
                base = int(200 * shimmer)
                color = (base, base, min(255, base + 40))
                self._set_pixel(pixel_colors, x, y, color)

    def _set_pixel(self, pixel_colors: List[Tuple[int, int, int]], x: int, y: int, color: Tuple[int, int, int]):
        strip_count, leds_per_strip = self.get_strip_info()
        if 0 <= x < strip_count and 0 <= y < leds_per_strip:
            index = x * leds_per_strip + y
            pixel_colors[index] = self._clamp_color(color)

    @staticmethod
    def _clamp_color(color: Tuple[int, int, int]) -> Tuple[int, int, int]:
        return tuple(max(0, min(255, int(component))) for component in color)
