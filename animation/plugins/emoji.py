#!/usr/bin/env python3
"""
Emoji Animation Plugin

Renders a pixel-art emoji centered on the LED grid with a gentle pulse.
"""

import math
from typing import List, Tuple, Dict, Any
from animation import AnimationBase


class EmojiAnimation(AnimationBase):
    """Display a pixel emoji on the grid with a soft breathing glow"""

    ANIMATION_NAME = "Emoji"
    ANIMATION_DESCRIPTION = "Displays a pixel emoji centered on the grid with a subtle pulse"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    EMOJI_PATTERNS: Dict[str, List[str]] = {
        'smile': [
            ".....FFF.....",
            "...HFFFFFF...",
            "..FFFFFFFFF..",
            ".FFF.E.E.FFF.",
            ".FFF.....FFF.",
            "..FFFMMMFFF..",
            "...FFFFFFF..."
        ],
        'heart': [
            "...HH...HH...",
            "..HHHH.HHHH..",
            ".HHHHHHHHHHH.",
            ".HHHHHHHHHHH.",
            "..HHHHHHHHH..",
            "...HHHHHHH...",
            "....HHHHH...."
        ],
        # New emojis
        '🍆': [  # Eggplant
            "....HHH.....",
            "...HHHH.....",
            "..MMMMM.....",
            ".MMMMMMM....",
            ".MMMMMMM....",
            ".MMMMMMM....",
            "..MMMMM....."
        ],
        '🔥': [  # Fire (8 pixels wide)
            "...H....",
            "..HHH...",
            ".HHHHH..",
            "HHHHHHHH",
            "HHHHHHHH",
            ".HHHHHH.",
            "..HHHH.."
        ],
        '🤖': [  # Robot
            ".HHHHHHHHH..",
            ".H.E...E.H..",
            ".H.......H..",
            ".H..MMM..H..",
            ".H.......H..",
            ".HHHHHHHHH..",
            "............"
        ],
        '👀': [  # Eyes
            ".EEE...EEE..",
            "EEEEE.EEEEE.",
            "EE.EEEEE.EE.",
            "EEEEE.EEEEE.",
            ".EEE...EEE..",
            "............",
            "............"
        ],
        '👻': [  # Ghost
            "..FFFFFFF...",
            ".FFFFFFFFF..",
            ".FF.E.E.FF..",
            ".FFFFFFFFF..",
            ".FF..M..FF..",
            ".FFFFFFFFF..",
            ".F.F.F.F.F.."
        ],
        '🌈': [  # Rainbow
            "............",
            ".HHHHHHHHH..",
            "HHHHHHHHHHHH",
            "HHHHHHHHHHHH",
            "............",
            "............",
            "............"
        ],
        # Letters A-Z (8 pixels wide, 4 pixels tall to fit multiple lines)
        'A': [
            "..FFFF..",
            ".FF..FF.",
            ".FFFFFF.",
            ".FF..FF."
        ],
        'B': [
            ".FFFFFF.....",
            ".FF...FF....",
            ".FF...FF....",
            ".FFFFFF.....",
            ".FF...FF....",
            ".FF...FF....",
            ".FFFFFF....."
        ],
        'C': [
            "..FFFFF.....",
            ".FF...FF....",
            ".FF.........",
            ".FF.........",
            ".FF.........",
            ".FF...FF....",
            "..FFFFF....."
        ],
        'D': [
            ".FFFFFF.....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FFFFFF....."
        ],
        'E': [
            ".FFFFFFF....",
            ".FF.........",
            ".FF.........",
            ".FFFFF......",
            ".FF.........",
            ".FF.........",
            ".FFFFFFF...."
        ],
        'F': [
            ".FFFFFFF....",
            ".FF.........",
            ".FF.........",
            ".FFFFF......",
            ".FF.........",
            ".FF.........",
            ".FF........."
        ],
        'G': [
            "..FFFFF.....",
            ".FF...FF....",
            ".FF.........",
            ".FF..FFF....",
            ".FF...FF....",
            ".FF...FF....",
            "..FFFFF....."
        ],
        'H': [
            ".FF..FF.",
            ".FF..FF.",
            ".FF..FF.",
            ".FFFFFF.",
            ".FF..FF.",
            ".FF..FF.",
            ".FF..FF."
        ],
        'I': [
            ".FFFFFF.",
            "..FFF...",
            "..FFF...",
            "..FFF...",
            "..FFF...",
            "..FFF...",
            ".FFFFFF."
        ],
        'J': [
            ".FFFFFFF....",
            "....FF......",
            "....FF......",
            "....FF......",
            "....FF......",
            ".FF.FF......",
            "..FFF......."
        ],
        'K': [
            ".FF...FF....",
            ".FF..FF.....",
            ".FF.FF......",
            ".FFFF.......",
            ".FF.FF......",
            ".FF..FF.....",
            ".FF...FF...."
        ],
        'L': [
            ".FF.........",
            ".FF.........",
            ".FF.........",
            ".FF.........",
            ".FF.........",
            ".FF.........",
            ".FFFFFFF...."
        ],
        'M': [
            ".FF...FF....",
            ".FFF.FFF....",
            ".FFFFFFF....",
            ".FF.F.FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF...."
        ],
        'N': [
            ".FF...FF....",
            ".FFF..FF....",
            ".FFFF.FF....",
            ".FF.FFFF....",
            ".FF..FFF....",
            ".FF...FF....",
            ".FF...FF...."
        ],
        'O': [
            "..FFFFF.....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            "..FFFFF....."
        ],
        'P': [
            ".FFFFFF.....",
            ".FF...FF....",
            ".FF...FF....",
            ".FFFFFF.....",
            ".FF.........",
            ".FF.........",
            ".FF........."
        ],
        'Q': [
            "..FFFFF.....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF.F.FF....",
            ".FF..FF.....",
            "..FFFFF.F..."
        ],
        'R': [
            ".FFFFFF.....",
            ".FF...FF....",
            ".FF...FF....",
            ".FFFFFF.....",
            ".FF.FF......",
            ".FF..FF.....",
            ".FF...FF...."
        ],
        'S': [
            "..FFFFF.....",
            ".FF...FF....",
            ".FF.........",
            "..FFFFF.....",
            "......FF....",
            ".FF...FF....",
            "..FFFFF....."
        ],
        'T': [
            ".FFFFFFF....",
            "...FFF......",
            "...FFF......",
            "...FFF......",
            "...FFF......",
            "...FFF......",
            "...FFF......"
        ],
        'U': [
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            "..FFFFF....."
        ],
        'V': [
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            "..FF.FF.....",
            "...FFF......"
        ],
        'W': [
            ".FF...FF....",
            ".FF...FF....",
            ".FF...FF....",
            ".FF.F.FF....",
            ".FFFFFFF....",
            ".FFF.FFF....",
            ".FF...FF...."
        ],
        'X': [
            ".FF...FF....",
            "..FF.FF.....",
            "...FFF......",
            "...FFF......",
            "...FFF......",
            "..FF.FF.....",
            ".FF...FF...."
        ],
        'Y': [
            ".FF...FF....",
            "..FF.FF.....",
            "...FFF......",
            "...FFF......",
            "...FFF......",
            "...FFF......",
            "...FFF......"
        ],
        'Z': [
            ".FFFFFFF....",
            "......FF....",
            ".....FF.....",
            "....FF......",
            "...FF.......",
            "..FF........",
            ".FFFFFFF...."
        ],
        # Numbers 0-9
        '0': [
            "..FFFFF.....",
            ".FF...FF....",
            ".FF..FFF....",
            ".FF.F.FF....",
            ".FFF..FF....",
            ".FF...FF....",
            "..FFFFF....."
        ],
        '1': [
            "...FF.......",
            "..FFF.......",
            "...FF.......",
            "...FF.......",
            "...FF.......",
            "...FF.......",
            ".FFFFF......"
        ],
        '2': [
            "..FFFFF.....",
            ".FF...FF....",
            "......FF....",
            "....FFF.....",
            "..FFF.......",
            ".FF.........",
            ".FFFFFFF...."
        ],
        '3': [
            "..FFFFF.....",
            ".FF...FF....",
            "......FF....",
            "...FFFF.....",
            "......FF....",
            ".FF...FF....",
            "..FFFFF....."
        ],
        '4': [
            "....FFF.....",
            "...FFFF.....",
            "..FF.FF.....",
            ".FF..FF.....",
            ".FFFFFFF....",
            ".....FF.....",
            ".....FF....."
        ],
        '5': [
            ".FFFFFFF....",
            ".FF.........",
            ".FFFFFF.....",
            "......FF....",
            "......FF....",
            ".FF...FF....",
            "..FFFFF....."
        ],
        '6': [
            "..FFFFF.....",
            ".FF...FF....",
            ".FF.........",
            ".FFFFFF.....",
            ".FF...FF....",
            ".FF...FF....",
            "..FFFFF....."
        ],
        '7': [
            ".FFFFFFF....",
            "......FF....",
            ".....FF.....",
            "....FF......",
            "...FF.......",
            "..FF........",
            "..FF........"
        ],
        '8': [
            "..FFFFF.....",
            ".FF...FF....",
            ".FF...FF....",
            "..FFFFF.....",
            ".FF...FF....",
            ".FF...FF....",
            "..FFFFF....."
        ],
        '9': [
            "..FFFFF.....",
            ".FF...FF....",
            ".FF...FF....",
            "..FFFFFF....",
            "......FF....",
            ".FF...FF....",
            "..FFFFF....."
        ]
    }

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
        total_pixels = self.get_pixel_count()

        pattern_name = str(self.params.get('emoji', 'smile')).lower()
        base_pattern = self.EMOJI_PATTERNS.get(pattern_name, self.EMOJI_PATTERNS['smile'])

        pattern = self._fit_pattern_to_grid(base_pattern, strip_count, leds_per_strip)
        pattern_height = len(pattern)
        pattern_width = len(pattern[0]) if pattern else 0

        # Pre-fill with the background color
        background = self.apply_brightness(self._color_from_params('background', (2, 6, 12)))
        pixel_colors = [background] * total_pixels

        if not pattern:
            return pixel_colors

        palette = self._build_palette(time_elapsed, pattern_name)
        serpentine = bool(self.params.get('serpentine', False))

        x_offset = int(self.params.get('x_offset', 0))
        y_offset = int(self.params.get('y_offset', 0))

        start_strip = max(0, min(strip_count - pattern_height, (strip_count - pattern_height) // 2 + y_offset))
        start_led = max(0, min(leds_per_strip - pattern_width, (leds_per_strip - pattern_width) // 2 + x_offset))

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

        return pixel_colors

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
        r = int(self.params.get(f'{prefix}_red', default[0]))
        g = int(self.params.get(f'{prefix}_green', default[1]))
        b = int(self.params.get(f'{prefix}_blue', default[2]))
        return (
            max(0, min(255, r)),
            max(0, min(255, g)),
            max(0, min(255, b))
        )

    def _scale_color(self, color: Tuple[int, int, int], scale: float) -> Tuple[int, int, int]:
        return (
            max(0, min(255, int(color[0] * scale))),
            max(0, min(255, int(color[1] * scale))),
            max(0, min(255, int(color[2] * scale)))
        )

    def _mix_colors(self, base: Tuple[int, int, int], overlay: Tuple[int, int, int], mix: float) -> Tuple[int, int, int]:
        mix = max(0.0, min(1.0, mix))
        return (
            int(base[0] * (1 - mix) + overlay[0] * mix),
            int(base[1] * (1 - mix) + overlay[1] * mix),
            int(base[2] * (1 - mix) + overlay[2] * mix)
        )

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
