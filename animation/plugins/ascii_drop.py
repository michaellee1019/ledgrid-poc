#!/usr/bin/env python3
"""
ASCII Drop Animation

ASCII characters drop down like Tetris pieces. Characters A-Z, 0-9, and _ 
fall from random positions at the top, with 1px buffer between characters.
The screen clears when it fills up.
"""

import random
import math
from typing import List, Tuple, Dict, Any, Optional
from animation import AnimationBase


class AsciiDropAnimation(AnimationBase):
    """ASCII characters dropping like Tetris pieces"""

    ANIMATION_NAME = "ASCII Drop"
    ANIMATION_DESCRIPTION = "ASCII characters drop down like Tetris pieces from a configurable phrase"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    # 5x7 character bitmaps for A-Z, 0-9, and _
    CHARACTER_BITMAPS: Dict[str, List[str]] = {
        'A': [
            ".XXX.",
            "X...X",
            "X...X", 
            "XXXXX",
            "X...X",
            "X...X",
            "....."
        ],
        'B': [
            "XXXX.",
            "X...X",
            "XXXX.",
            "XXXX.",
            "X...X",
            "XXXX.",
            "....."
        ],
        'C': [
            ".XXX.",
            "X...X",
            "X....",
            "X....",
            "X...X",
            ".XXX.",
            "....."
        ],
        'D': [
            "XXXX.",
            "X...X",
            "X...X",
            "X...X",
            "X...X",
            "XXXX.",
            "....."
        ],
        'E': [
            "XXXXX",
            "X....",
            "XXXX.",
            "XXXX.",
            "X....",
            "XXXXX",
            "....."
        ],
        'F': [
            "XXXXX",
            "X....",
            "XXXX.",
            "XXXX.",
            "X....",
            "X....",
            "....."
        ],
        'G': [
            ".XXX.",
            "X....",
            "X.XXX",
            "X...X",
            "X...X",
            ".XXX.",
            "....."
        ],
        'H': [
            "X...X",
            "X...X",
            "XXXXX",
            "X...X",
            "X...X",
            "X...X",
            "....."
        ],
        'I': [
            "XXXXX",
            "..X..",
            "..X..",
            "..X..",
            "..X..",
            "XXXXX",
            "....."
        ],
        'J': [
            "XXXXX",
            "....X",
            "....X",
            "....X",
            "X...X",
            ".XXX.",
            "....."
        ],
        'K': [
            "X...X",
            "X..X.",
            "X.X..",
            "XX...",
            "X.X..",
            "X..X.",
            "....."
        ],
        'L': [
            "X....",
            "X....",
            "X....",
            "X....",
            "X....",
            "XXXXX",
            "....."
        ],
        'M': [
            "X...X",
            "XX.XX",
            "X.X.X",
            "X...X",
            "X...X",
            "X...X",
            "....."
        ],
        'N': [
            "X...X",
            "XX..X",
            "X.X.X",
            "X..XX",
            "X...X",
            "X...X",
            "....."
        ],
        'O': [
            ".XXX.",
            "X...X",
            "X...X",
            "X...X",
            "X...X",
            ".XXX.",
            "....."
        ],
        'P': [
            "XXXX.",
            "X...X",
            "XXXX.",
            "X....",
            "X....",
            "X....",
            "....."
        ],
        'Q': [
            ".XXX.",
            "X...X",
            "X...X",
            "X.X.X",
            "X..XX",
            ".XXXX",
            "....."
        ],
        'R': [
            "XXXX.",
            "X...X",
            "XXXX.",
            "X.X..",
            "X..X.",
            "X...X",
            "....."
        ],
        'S': [
            ".XXX.",
            "X....",
            ".XXX.",
            "....X",
            "X...X",
            ".XXX.",
            "....."
        ],
        'T': [
            "XXXXX",
            "..X..",
            "..X..",
            "..X..",
            "..X..",
            "..X..",
            "....."
        ],
        'U': [
            "X...X",
            "X...X",
            "X...X",
            "X...X",
            "X...X",
            ".XXX.",
            "....."
        ],
        'V': [
            "X...X",
            "X...X",
            "X...X",
            "X...X",
            ".X.X.",
            "..X..",
            "....."
        ],
        'W': [
            "X...X",
            "X...X",
            "X...X",
            "X.X.X",
            "XX.XX",
            "X...X",
            "....."
        ],
        'X': [
            "X...X",
            ".X.X.",
            "..X..",
            "..X..",
            ".X.X.",
            "X...X",
            "....."
        ],
        'Y': [
            "X...X",
            "X...X",
            ".X.X.",
            "..X..",
            "..X..",
            "..X..",
            "....."
        ],
        'Z': [
            "XXXXX",
            "....X",
            "...X.",
            "..X..",
            ".X...",
            "XXXXX",
            "....."
        ],
        '0': [
            ".XXX.",
            "X...X",
            "X..XX",
            "X.X.X",
            "XX..X",
            ".XXX.",
            "....."
        ],
        '1': [
            "..X..",
            ".XX..",
            "..X..",
            "..X..",
            "..X..",
            ".XXX.",
            "....."
        ],
        '2': [
            ".XXX.",
            "X...X",
            "....X",
            ".XXX.",
            "X....",
            "XXXXX",
            "....."
        ],
        '3': [
            ".XXX.",
            "X...X",
            "..XX.",
            "....X",
            "X...X",
            ".XXX.",
            "....."
        ],
        '4': [
            "X...X",
            "X...X",
            "X...X",
            "XXXXX",
            "....X",
            "....X",
            "....."
        ],
        '5': [
            "XXXXX",
            "X....",
            "XXXX.",
            "....X",
            "X...X",
            ".XXX.",
            "....."
        ],
        '6': [
            ".XXX.",
            "X....",
            "XXXX.",
            "X...X",
            "X...X",
            ".XXX.",
            "....."
        ],
        '7': [
            "XXXXX",
            "....X",
            "...X.",
            "..X..",
            ".X...",
            ".X...",
            "....."
        ],
        '8': [
            ".XXX.",
            "X...X",
            ".XXX.",
            "X...X",
            "X...X",
            ".XXX.",
            "....."
        ],
        '9': [
            ".XXX.",
            "X...X",
            "X...X",
            ".XXXX",
            "....X",
            ".XXX.",
            "....."
        ],
        '_': [
            ".....",
            ".....",
            ".....",
            ".....",
            ".....",
            "XXXXX",
            "....."
        ],
        ' ': [
            ".....",
            ".....",
            ".....",
            ".....",
            ".....",
            ".....",
            "....."
        ]
    }

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        
        self.default_params.update({
            'phrase': 'HELLO WORLD',
            'drop_speed': 2.0,
            'spawn_rate': 1.5,
            'character_color_red': 0,
            'character_color_green': 255,
            'character_color_blue': 100,
            'background_red': 0,
            'background_green': 0,
            'background_blue': 5,
            'serpentine': False
        })
        
        self.params = {**self.default_params, **self.config}
        
        # Animation state
        self.falling_characters: List[Dict[str, Any]] = []
        self.last_spawn_time = 0.0
        self.phrase_index = 0
        self.grid_state: List[List[Optional[str]]] = []
        self.last_time = None

        self._reset_grid()

    def _reset_grid(self):
        """Reset the grid state"""
        strip_count, leds_per_strip = self.get_strip_info()
        self.grid_state = [[None for _ in range(leds_per_strip)] for _ in range(strip_count)]
        self.falling_characters = []

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'phrase': {
                'type': 'str',
                'default': 'HELLO WORLD',
                'description': 'Phrase to drop (A-Z, 0-9, _, space)'
            },
            'drop_speed': {
                'type': 'float',
                'min': 0.5,
                'max': 10.0,
                'default': 2.0,
                'description': 'Speed of falling characters'
            },
            'spawn_rate': {
                'type': 'float',
                'min': 0.1,
                'max': 5.0,
                'default': 1.5,
                'description': 'Rate of character spawning (per second)'
            },
            'character_color_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 0, 'description': 'Character red'},
            'character_color_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Character green'},
            'character_color_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 100, 'description': 'Character blue'},
            'background_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 0, 'description': 'Background red'},
            'background_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 0, 'description': 'Background green'},
            'background_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 5, 'description': 'Background blue'},
            'serpentine': {
                'type': 'bool',
                'default': False,
                'description': 'Flip every other strip to match serpentine wiring'
            }
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        """Generate a frame of the ASCII drop animation"""
        if self.last_time is None:
            self.last_time = time_elapsed

        dt = time_elapsed - self.last_time
        self.last_time = time_elapsed

        strip_count, leds_per_strip = self.get_strip_info()
        total_pixels = self.get_pixel_count()

        # Check if we need to clear the screen (when it's mostly full)
        if self._is_screen_full():
            self._reset_grid()

        # Spawn new characters
        self._spawn_characters(time_elapsed, dt)

        # Update falling characters
        self._update_falling_characters(dt)

        # Render the frame
        return self._render_frame(strip_count, leds_per_strip, total_pixels)

    def _is_screen_full(self) -> bool:
        """Check if the screen is mostly full and needs clearing"""
        strip_count, leds_per_strip = self.get_strip_info()
        filled_pixels = 0
        total_pixels = strip_count * leds_per_strip

        for strip in range(strip_count):
            for led in range(leds_per_strip):
                if self.grid_state[strip][led] is not None:
                    filled_pixels += 1

        # Clear when 80% full
        return filled_pixels / total_pixels > 0.8

    def _spawn_characters(self, time_elapsed: float, dt: float):
        """Spawn new characters from the phrase"""
        spawn_rate = float(self.params.get('spawn_rate', 1.5))

        if time_elapsed - self.last_spawn_time >= (1.0 / spawn_rate):
            phrase = str(self.params.get('phrase', 'HELLO WORLD')).upper()
            if phrase and self.phrase_index < len(phrase):
                char = phrase[self.phrase_index]
                if char in self.CHARACTER_BITMAPS:
                    self._add_falling_character(char)

                self.phrase_index = (self.phrase_index + 1) % len(phrase)
                self.last_spawn_time = time_elapsed

    def _add_falling_character(self, char: str):
        """Add a new falling character at a random position"""
        strip_count, leds_per_strip = self.get_strip_info()
        bitmap = self.CHARACTER_BITMAPS[char]
        char_width = len(bitmap[0])

        # Random starting position with 1px buffer
        max_start_x = max(0, leds_per_strip - char_width - 1)
        start_x = random.randint(1, max_start_x) if max_start_x > 1 else 0

        character = {
            'char': char,
            'x': start_x,
            'y': -len(bitmap),  # Start above the grid
            'bitmap': bitmap
        }

        self.falling_characters.append(character)

    def _update_falling_characters(self, dt: float):
        """Update positions of falling characters"""
        drop_speed = float(self.params.get('drop_speed', 2.0))
        strip_count, leds_per_strip = self.get_strip_info()

        active_characters = []

        for char_data in self.falling_characters:
            # Move character down
            char_data['y'] += drop_speed * dt

            # Check if character has landed or gone off screen
            bitmap = char_data['bitmap']
            char_height = len(bitmap)
            char_width = len(bitmap[0])

            # Check for collision with bottom or existing characters
            landed = False
            bottom_y = int(char_data['y']) + char_height

            if bottom_y >= strip_count:
                # Hit bottom
                landed = True
            else:
                # Check collision with existing characters
                for dy in range(char_height):
                    for dx in range(char_width):
                        if bitmap[dy][dx] == 'X':
                            check_strip = int(char_data['y']) + dy + 1
                            check_led = char_data['x'] + dx

                            if (0 <= check_strip < strip_count and
                                0 <= check_led < leds_per_strip and
                                self.grid_state[check_strip][check_led] is not None):
                                landed = True
                                break
                    if landed:
                        break

            if landed:
                # Place character in grid
                self._place_character_in_grid(char_data)
            else:
                # Keep falling
                active_characters.append(char_data)

        self.falling_characters = active_characters

    def _place_character_in_grid(self, char_data: Dict[str, Any]):
        """Place a landed character in the grid state"""
        strip_count, leds_per_strip = self.get_strip_info()
        bitmap = char_data['bitmap']
        char_height = len(bitmap)
        char_width = len(bitmap[0])

        for dy in range(char_height):
            for dx in range(char_width):
                if bitmap[dy][dx] == 'X':
                    grid_strip = int(char_data['y']) + dy
                    grid_led = char_data['x'] + dx

                    if (0 <= grid_strip < strip_count and
                        0 <= grid_led < leds_per_strip):
                        self.grid_state[grid_strip][grid_led] = char_data['char']

    def _render_frame(self, strip_count: int, leds_per_strip: int, total_pixels: int) -> List[Tuple[int, int, int]]:
        """Render the current frame"""
        # Get colors from parameters
        char_color = (
            int(self.params.get('character_color_red', 0)),
            int(self.params.get('character_color_green', 255)),
            int(self.params.get('character_color_blue', 100))
        )

        background_color = (
            int(self.params.get('background_red', 0)),
            int(self.params.get('background_green', 0)),
            int(self.params.get('background_blue', 5))
        )

        serpentine = bool(self.params.get('serpentine', False))

        # Initialize frame with background
        pixel_colors = [self.apply_brightness(background_color)] * total_pixels

        # Render placed characters in grid
        for strip in range(strip_count):
            for led in range(leds_per_strip):
                if self.grid_state[strip][led] is not None:
                    mapped_led = led if not (serpentine and strip % 2 == 1) else (leds_per_strip - 1 - led)
                    pixel_index = strip * leds_per_strip + mapped_led
                    pixel_colors[pixel_index] = self.apply_brightness(char_color)

        # Render falling characters
        for char_data in self.falling_characters:
            bitmap = char_data['bitmap']
            char_height = len(bitmap)
            char_width = len(bitmap[0])

            for dy in range(char_height):
                for dx in range(char_width):
                    if bitmap[dy][dx] == 'X':
                        strip = int(char_data['y']) + dy
                        led = char_data['x'] + dx

                        if (0 <= strip < strip_count and 0 <= led < leds_per_strip):
                            mapped_led = led if not (serpentine and strip % 2 == 1) else (leds_per_strip - 1 - led)
                            pixel_index = strip * leds_per_strip + mapped_led
                            pixel_colors[pixel_index] = self.apply_brightness(char_color)

        return pixel_colors
