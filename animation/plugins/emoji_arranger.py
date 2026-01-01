#!/usr/bin/env python3
"""
Emoji Arranger Animation

Displays strings of emojis and characters arranged on the grid with proper wrapping.
Supports all emojis, letters A-Z, and numbers 0-9 from the emoji animation patterns.
"""

import math
from typing import Any, Dict, List, Tuple

from animation import AnimationBase


class EmojiArrangerAnimation(AnimationBase):
    """Display strings of emojis and characters with proper wrapping"""

    ANIMATION_NAME = "Emoji Arranger"
    ANIMATION_DESCRIPTION = "Arrange strings of emojis and characters on the grid with wrapping"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        # Import patterns from emoji animation
        from animation.plugins.emoji import EmojiAnimation
        self.emoji_patterns = EmojiAnimation.EMOJI_PATTERNS

        self.default_params.update({
            'text': 'HI🔥',
            'char_spacing': 1,
            'line_spacing': 1,
            'scroll_speed': 0.0,
            'background_red': 2,
            'background_green': 6,
            'background_blue': 12,
            'primary_red': 255,
            'primary_green': 200,
            'primary_blue': 40,
            'accent_red': 235,
            'accent_green': 60,
            'accent_blue': 70,
            'pulse_speed': 0.5,
            'x_offset': 0,
            'y_offset': 0,
            'active_columns': 8  # Hardware limitation
        })

        self.params = {**self.default_params, **self.config}

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'text': {
                'type': 'str',
                'default': 'HI🔥',
                'description': 'Text string to display (emojis, letters A-Z, numbers 0-9)'
            },
            'char_spacing': {
                'type': 'int',
                'min': 0,
                'max': 10,
                'default': 1,
                'description': 'Spacing between characters'
            },
            'line_spacing': {
                'type': 'int',
                'min': 0,
                'max': 10,
                'default': 1,
                'description': 'Spacing between lines'
            },
            'scroll_speed': {
                'type': 'float',
                'min': 0.0,
                'max': 5.0,
                'default': 0.0,
                'description': 'Horizontal scroll speed (0 = no scroll)'
            },
            'pulse_speed': {
                'type': 'float',
                'min': 0.0,
                'max': 3.0,
                'default': 0.5,
                'description': 'Speed of the breathing effect'
            },
            'x_offset': {
                'type': 'int',
                'min': -50,
                'max': 50,
                'default': 0,
                'description': 'Horizontal offset'
            },
            'y_offset': {
                'type': 'int',
                'min': -20,
                'max': 20,
                'default': 0,
                'description': 'Vertical offset'
            },
            'background_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 2, 'description': 'Background red'},
            'background_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 6, 'description': 'Background green'},
            'background_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 12, 'description': 'Background blue'},
            'primary_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Primary color red'},
            'primary_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 200, 'description': 'Primary color green'},
            'primary_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 40, 'description': 'Primary color blue'},
            'accent_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 235, 'description': 'Accent color red'},
            'accent_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 60, 'description': 'Accent color green'},
            'accent_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 70, 'description': 'Accent color blue'},
            'active_columns': {
                'type': 'int',
                'min': 1,
                'max': 140,
                'default': 8,
                'description': 'Number of active columns (hardware limitation)'
            }
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        strip_count, leds_per_strip = self.get_strip_info()
        total_pixels = self.get_pixel_count()

        # Get parameters
        text = str(self.params.get('text', 'HI🔥'))
        char_spacing = int(self.params.get('char_spacing', 1))
        line_spacing = int(self.params.get('line_spacing', 1))
        scroll_speed = float(self.params.get('scroll_speed', 0.0))
        x_offset = int(self.params.get('x_offset', 0))
        y_offset = int(self.params.get('y_offset', 0))

        # Pre-fill with background color
        background = self.apply_brightness(self._color_from_params('background', (2, 6, 12)))
        pixel_colors = [background] * total_pixels

        # Calculate scroll offset
        scroll_offset = int(time_elapsed * scroll_speed * 10) if scroll_speed > 0 else 0

        # Use only active columns for wrapping (hardware limitation)
        active_columns = int(self.params.get('active_columns', 8))

        # Arrange characters with wrapping
        arranged_chars = self._arrange_text_with_wrapping(text, active_columns, char_spacing)
        
        # Render each character
        palette = self._build_palette(time_elapsed)
        
        current_y = y_offset
        for line in arranged_chars:
            current_x = x_offset - scroll_offset
            
            for char in line:
                if char in self.emoji_patterns:
                    self._render_character(char, current_x, current_y, palette, 
                                         strip_count, leds_per_strip, pixel_colors)
                
                # Move to next character position
                char_width = self._get_character_width(char)
                current_x += char_width + char_spacing
            
            # Move to next line
            current_y += 7 + line_spacing  # Character height is 7

            # Stop if we're below the display (140 pixels tall)
            if current_y >= leds_per_strip:
                break

        return pixel_colors

    def _arrange_text_with_wrapping(self, text: str, max_width: int, char_spacing: int) -> List[List[str]]:
        """Arrange text into lines with proper character wrapping"""
        lines = []
        current_line = []
        current_width = 0

        for char in text:
            if char in self.emoji_patterns:
                char_width = self._get_character_width(char)
                needed_width = char_width + (char_spacing if current_line else 0)

                # Check if character fits on current line
                if current_width + needed_width <= max_width:
                    current_line.append(char)
                    current_width += needed_width
                else:
                    # Start new line
                    if current_line:
                        lines.append(current_line)
                    current_line = [char]
                    current_width = char_width
            elif char == ' ':
                # Handle spaces - try to fit, otherwise start new line
                if current_width + 3 <= max_width:  # Space width
                    current_width += 3
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = []
                    current_width = 0

        # Add final line
        if current_line:
            lines.append(current_line)

        return lines

    def _get_character_width(self, char: str) -> int:
        """Get the width of a character pattern"""
        if char in self.emoji_patterns:
            pattern = self.emoji_patterns[char]
            return len(pattern[0]) if pattern else 0
        return 0

    def _render_character(self, char: str, start_x: int, start_y: int, palette: Dict[str, Tuple[int, int, int]],
                         strip_count: int, leds_per_strip: int, pixel_colors: List[Tuple[int, int, int]]):
        """Render a single character at the specified position"""
        if char not in self.emoji_patterns:
            return

        pattern = self.emoji_patterns[char]
        char_height = len(pattern)
        char_width = len(pattern[0]) if pattern else 0

        for row_idx, row in enumerate(pattern):
            strip = start_y + row_idx
            if strip < 0 or strip >= strip_count:
                continue

            for col_idx, cell in enumerate(row):
                led = start_x + col_idx
                if led < 0 or led >= leds_per_strip:
                    continue

                color = palette.get(cell)
                if color is None:
                    continue

                pixel_index = strip * leds_per_strip + led
                if 0 <= pixel_index < len(pixel_colors):
                    pixel_colors[pixel_index] = self.apply_brightness(color)

    def _build_palette(self, time_elapsed: float) -> Dict[str, Tuple[int, int, int]]:
        """Build color palette with breathing effect"""
        pulse_speed = max(0.0, float(self.params.get('pulse_speed', 0.5)))
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

        return {
            '.': background,
            'F': face,
            'H': highlight,
            'E': eye,
            'M': mouth
        }

    def _color_from_params(self, prefix: str, default: Tuple[int, int, int]) -> Tuple[int, int, int]:
        """Extract RGB color from parameters"""
        r = int(self.params.get(f'{prefix}_red', default[0]))
        g = int(self.params.get(f'{prefix}_green', default[1]))
        b = int(self.params.get(f'{prefix}_blue', default[2]))
        return (r, g, b)

    def _scale_color(self, color: Tuple[int, int, int], scale: float) -> Tuple[int, int, int]:
        """Scale color brightness"""
        scale = max(0.0, min(2.0, scale))
        return (
            min(255, int(color[0] * scale)),
            min(255, int(color[1] * scale)),
            min(255, int(color[2] * scale))
        )

    def _mix_colors(self, base: Tuple[int, int, int], overlay: Tuple[int, int, int], mix: float) -> Tuple[int, int, int]:
        """Mix two colors"""
        mix = max(0.0, min(1.0, mix))
        return (
            int(base[0] * (1 - mix) + overlay[0] * mix),
            int(base[1] * (1 - mix) + overlay[1] * mix),
            int(base[2] * (1 - mix) + overlay[2] * mix)
        )
