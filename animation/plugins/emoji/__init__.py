#!/usr/bin/env python3
"""
Emoji Animation Plugin

Renders a pixel-art emoji centered on the LED grid with a gentle pulse.
"""

import math
from typing import List, Tuple, Dict, Any

import numpy as np

from animation import AnimationBase
from animation.libraries.color import mix_rgb, parameter_rgb, scale_rgb
from animation.libraries.pixel_art import EMOJI_PATTERNS


class EmojiAnimation(AnimationBase):
    """Display a pixel emoji on the grid with a soft breathing glow"""

    ANIMATION_NAME = "Emoji"
    ANIMATION_DESCRIPTION = "Displays a pixel emoji centered on the grid with a subtle pulse"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    EMOJI_PATTERNS = EMOJI_PATTERNS

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.default_params.update({
            'emoji': 'smile',
            'pulse_speed': 0.8,
            'background_red': 2,
            'background_green': 6,
            'background_blue': 12,
            'primary_red': 255,
            'primary_green': 200,
            'primary_blue': 40,
            'accent_red': 235,
            'accent_green': 60,
            'accent_blue': 70,
            'serpentine': False,
            'x_offset': 0,
            'y_offset': 0
        })

        self.params = {**self.default_params, **self.config}
        self._fitted_pattern_key = None
        self._fitted_pattern = None
        self._plant_layout_key = None
        self._plant_layout_origin = None
        self._plant_layout_avoided_pixels = 0

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'emoji': {
                'type': 'str',
                'default': 'smile',
                'description': "Emoji to render ('smile' or 'heart')"
            },
            'pulse_speed': {
                'type': 'float',
                'min': 0.0,
                'max': 3.0,
                'default': 0.8,
                'description': 'Speed of the subtle breathing effect'
            },
            'x_offset': {
                'type': 'int',
                'min': -100,
                'max': 100,
                'default': 0,
                'description': 'Horizontal offset from center (LEDs)'
            },
            'y_offset': {
                'type': 'int',
                'min': -50,
                'max': 50,
                'default': 0,
                'description': 'Vertical offset from center (strips)'
            },
            'serpentine': {
                'type': 'bool',
                'default': False,
                'description': 'Flip every other strip to match serpentine wiring'
            },
            'background_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 2, 'description': 'Background red'},
            'background_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 6, 'description': 'Background green'},
            'background_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 12, 'description': 'Background blue'},
            'primary_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Primary color red'},
            'primary_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 200, 'description': 'Primary color green'},
            'primary_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 40, 'description': 'Primary color blue'},
            'accent_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 235, 'description': 'Accent color red'},
            'accent_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 60, 'description': 'Accent color green'},
            'accent_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 70, 'description': 'Accent color blue'}
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        strip_count, leds_per_strip = self.get_strip_info()
        pattern_name = str(self.params.get('emoji', 'smile')).lower()
        base_pattern = self.EMOJI_PATTERNS.get(pattern_name, self.EMOJI_PATTERNS['smile'])

        pattern_key = (pattern_name, strip_count, leds_per_strip)
        if pattern_key != self._fitted_pattern_key:
            self._fitted_pattern = self._fit_pattern_to_grid(base_pattern, strip_count, leds_per_strip)
            self._fitted_pattern_key = pattern_key
        pattern = self._fitted_pattern
        pattern_height = len(pattern)
        pattern_width = len(pattern[0]) if pattern else 0

        # Pre-fill with the background color
        background = self.apply_brightness(self._color_from_params('background', (2, 6, 12)))
        pixel_colors = self.next_frame_buffer(clear=False)
        pixel_colors[:] = background

        if not pattern:
            return pixel_colors

        palette = self._build_palette(time_elapsed, pattern_name)
        serpentine = bool(self.params.get('serpentine', False))

        x_offset = int(self.params.get('x_offset', 0))
        y_offset = int(self.params.get('y_offset', 0))

        start_strip = max(0, min(strip_count - pattern_height, (strip_count - pattern_height) // 2 + y_offset))
        start_led = max(0, min(leds_per_strip - pattern_width, (leds_per_strip - pattern_width) // 2 + x_offset))
        if self.plant_aware_enabled():
            start_strip, start_led = self._plant_aware_origin(
                pattern, start_strip, start_led, serpentine
            )

        for row_idx, row in enumerate(pattern):
            strip = start_strip + row_idx
            if strip >= strip_count:
                break

            for col_idx, cell in enumerate(row):
                led = start_led + col_idx
                if led >= leds_per_strip:
                    break

                color = palette.get(cell)
                if color is None:
                    continue

                mapped_led = led if not (serpentine and strip % 2 == 1) else (leds_per_strip - 1 - led)
                pixel_index = strip * leds_per_strip + mapped_led
                pixel_colors[pixel_index] = self.apply_brightness(color)

        if self.plant_aware_enabled():
            self._render_plant_accents(pixel_colors, time_elapsed)

        return pixel_colors

    def _plant_aware_origin(
        self,
        pattern: List[str],
        preferred_strip: int,
        preferred_led: int,
        serpentine: bool,
    ) -> Tuple[int, int]:
        """Place expressive pixels in the clearest nearby part of the wall.

        Eyes and mouths carry more weight than face fill, so constrained masks
        preserve the emoji's expression before its decorative silhouette.
        """
        masks = self.get_plant_masks()
        strip_count, leds_per_strip = self.get_strip_info()
        height = len(pattern)
        width = len(pattern[0]) if pattern else 0
        key = (
            id(masks), tuple(pattern), preferred_strip, preferred_led, serpentine,
            strip_count, leds_per_strip,
        )
        if key == self._plant_layout_key and self._plant_layout_origin is not None:
            return self._plant_layout_origin

        glyph = [
            (row, col, 12 if cell == 'E' else 10 if cell == 'M' else 3)
            for row, line in enumerate(pattern)
            for col, cell in enumerate(line)
            if cell != '.'
        ]
        preferred_cost = self._plant_overlap_cost(
            glyph, preferred_strip, preferred_led, serpentine, masks
        )
        max_strip = max(0, strip_count - height)
        max_led = max(0, leds_per_strip - width)
        if serpentine:
            best_score = None
            best_origin = (preferred_strip, preferred_led)
            for start_strip in range(max_strip + 1):
                for start_led in range(max_led + 1):
                    obstacle_cost, clearance_cost = self._plant_overlap_cost(
                        glyph, start_strip, start_led, True, masks
                    )
                    distance = (
                        (start_strip - preferred_strip) ** 2
                        + (start_led - preferred_led) ** 2
                    )
                    score = (
                        obstacle_cost, clearance_cost, distance,
                        start_strip, start_led,
                    )
                    if best_score is None or score < best_score:
                        best_score = score
                        best_origin = (start_strip, start_led)
        else:
            # Accumulate every candidate placement at once. This keeps the
            # one-time layout decision comfortably within the frame budget.
            candidate_shape = (max_strip + 1, max_led + 1)
            obstacle_scores = np.zeros(candidate_shape, dtype=np.int32)
            clearance_scores = np.zeros(candidate_shape, dtype=np.int32)
            for row, col, weight in glyph:
                obstacle_scores += (
                    masks.obstacle[
                        row:row + candidate_shape[0],
                        col:col + candidate_shape[1],
                    ] * weight
                )
                clearance_scores += (
                    masks.clearance[
                        row:row + candidate_shape[0],
                        col:col + candidate_shape[1],
                    ] * weight
                )
            strip_grid, led_grid = np.indices(candidate_shape)
            distance = (
                (strip_grid - preferred_strip) ** 2
                + (led_grid - preferred_led) ** 2
            )
            best_flat = np.lexsort((
                led_grid.ravel(), strip_grid.ravel(), distance.ravel(),
                clearance_scores.ravel(), obstacle_scores.ravel(),
            ))[0]
            best_origin = tuple(int(value) for value in np.unravel_index(
                best_flat, candidate_shape
            ))

        best_cost = self._plant_overlap_cost(
            glyph, best_origin[0], best_origin[1], serpentine, masks
        )
        self._plant_layout_avoided_pixels = max(
            0, preferred_cost[1] - best_cost[1]
        )
        self._plant_layout_key = key
        self._plant_layout_origin = best_origin
        return best_origin

    @staticmethod
    def _plant_overlap_cost(glyph, start_strip, start_led, serpentine, masks):
        obstacle_cost = 0
        clearance_cost = 0
        leds_per_strip = masks.obstacle.shape[1]
        for row, col, weight in glyph:
            strip = start_strip + row
            logical_led = start_led + col
            led = (
                leds_per_strip - 1 - logical_led
                if serpentine and strip % 2 == 1
                else logical_led
            )
            if masks.obstacle[strip, led]:
                obstacle_cost += weight
            if masks.clearance[strip, led]:
                clearance_cost += weight
        return obstacle_cost, clearance_cost

    def _render_plant_accents(self, frame, time_elapsed: float):
        """Keep foliage and rooting globes readable as separate scene layers."""
        masks = self.get_plant_masks()
        pulse = (math.sin(time_elapsed * math.pi * 0.7) + 1.0) / 2.0
        foliage = self.apply_brightness((3, int(24 + 14 * pulse), 8))
        globes = self.apply_brightness((int(34 + 18 * pulse), 12, 48))
        frame[masks.foliage_flat] = foliage
        frame[masks.globes_flat] = globes

    def get_runtime_stats(self) -> Dict[str, Any]:
        stats = {
            'plant_aware': self.plant_aware_enabled(),
            'plant_layout_origin': self._plant_layout_origin,
            'plant_avoided_weight': self._plant_layout_avoided_pixels,
        }
        if self.plant_aware_enabled():
            masks = self.get_plant_masks()
            stats.update({
                'plant_foliage_pixels': masks.foliage_count,
                'plant_globe_pixels': masks.globe_count,
                'plant_mask_error': masks.error,
            })
        return stats

    def _build_palette(self, time_elapsed: float, emoji_name: str) -> Dict[str, Tuple[int, int, int]]:
        pulse_speed = max(0.0, float(self.params.get('pulse_speed', 0.8)))
        pulse = (math.sin(time_elapsed * pulse_speed * 2 * math.pi) + 1.0) / 2.0
        accent_wave = (math.sin(time_elapsed * (pulse_speed * 1.5 + 0.3) * 2 * math.pi + 1.1) + 1.0) / 2.0

        primary = self._color_from_params('primary', (255, 200, 40))
        accent = self._color_from_params('accent', (235, 60, 70))
        background = self._color_from_params('background', (2, 6, 12))

        face = self._scale_color(primary, 0.8 + 0.25 * pulse)
        highlight = self._mix_colors(primary, accent, 0.25)
        highlight = self._scale_color(highlight, 0.9 + 0.25 * accent_wave)
        mouth_base = self._mix_colors(accent, (90, 40, 20), 0.35)
        mouth = self._scale_color(mouth_base, 0.8 + 0.2 * pulse)
        eye = (20, 20, 20)

        color_map = {
            '.': background,
            'F': face,
            'H': highlight,
            'E': eye,
            'M': mouth
        }

        if emoji_name == 'heart':
            heart_fill = self._scale_color(accent, 0.8 + 0.35 * pulse)
            color_map['H'] = heart_fill
            color_map['F'] = self._scale_color(accent, 0.95 + 0.2 * accent_wave)

        return color_map

    def _color_from_params(self, prefix: str, default: Tuple[int, int, int]) -> Tuple[int, int, int]:
        return parameter_rgb(self.params, prefix, default)

    def _scale_color(self, color: Tuple[int, int, int], scale: float) -> Tuple[int, int, int]:
        return scale_rgb(color, scale)

    def _mix_colors(self, base: Tuple[int, int, int], overlay: Tuple[int, int, int], mix: float) -> Tuple[int, int, int]:
        return mix_rgb(base, overlay, mix)

    def _fit_pattern_to_grid(self, pattern: List[str], max_height: int, max_width: int) -> List[str]:
        if not pattern:
            return []

        base_height = len(pattern)
        base_width = len(pattern[0])

        target_height = min(base_height, max_height)
        target_width = min(base_width, max_width)

        row_indices = self._spread_indices(base_height, target_height)
        col_indices = self._spread_indices(base_width, target_width)

        return ["".join(pattern[r][c] for c in col_indices) for r in row_indices]

    def _spread_indices(self, size: int, target: int) -> List[int]:
        """Pick evenly spaced indices so patterns shrink cleanly when the grid is smaller."""
        if size <= 0 or target <= 0:
            return []
        if target >= size:
            return list(range(size))
        if target == 1:
            return [size // 2]

        step = (size - 1) / (target - 1)
        return [min(size - 1, max(0, round(i * step))) for i in range(target)]
