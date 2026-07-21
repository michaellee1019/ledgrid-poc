#!/usr/bin/env python3
"""Clock faces ranging from practical timepieces to atmospheric time studies."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

import numpy as np

from animation import AnimationBase


Color = Tuple[int, int, int]


class ClockAnimation(AnimationBase):
    ANIMATION_NAME = "Clock"
    ANIMATION_DESCRIPTION = "Useful and atmospheric clocks with composable faces, palettes, and backgrounds"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    FACE_OPTIONS = (
        "digital", "analog", "binary", "orbit", "linear",
        "segments", "minimal", "hourglass", "calendar", "word",
    )
    BACKGROUND_OPTIONS = (
        "solid", "gradient", "radial", "stars", "aurora",
        "scanlines", "horizon", "grid",
    )
    PALETTES: Dict[str, Tuple[Color, Color, Color, Color]] = {
        "amber": ((4, 1, 0), (255, 126, 20), (255, 224, 128), (90, 22, 2)),
        "ice": ((0, 5, 12), (70, 210, 255), (225, 252, 255), (8, 52, 92)),
        "mono": ((0, 0, 0), (205, 220, 210), (255, 255, 255), (35, 42, 38)),
        "neon": ((5, 0, 15), (255, 35, 180), (30, 245, 255), (45, 5, 85)),
        "forest": ((0, 7, 4), (70, 220, 120), (220, 255, 190), (8, 55, 28)),
        "sunset": ((10, 1, 10), (255, 80, 65), (255, 205, 95), (72, 10, 66)),
        "ocean": ((0, 4, 18), (20, 120, 230), (80, 255, 220), (4, 30, 90)),
        "violet": ((4, 0, 14), (150, 80, 255), (245, 175, 255), (35, 8, 80)),
        "paper": ((18, 13, 8), (235, 205, 150), (255, 244, 205), (70, 48, 28)),
        "signal": ((2, 3, 2), (255, 45, 25), (255, 245, 225), (65, 10, 5)),
    }

    # Compact 3x5 bitmap glyphs. Rows are encoded most-significant-bit first.
    FONT = {
        "0": (7, 5, 5, 5, 7), "1": (2, 6, 2, 2, 7),
        "2": (7, 1, 7, 4, 7), "3": (7, 1, 7, 1, 7),
        "4": (5, 5, 7, 1, 1), "5": (7, 4, 7, 1, 7),
        "6": (7, 4, 7, 5, 7), "7": (7, 1, 1, 1, 1),
        "8": (7, 5, 7, 5, 7), "9": (7, 5, 7, 1, 7),
        ":": (0, 2, 0, 2, 0), "-": (0, 0, 7, 0, 0), " ": (0, 0, 0, 0, 0),
        "A": (2, 5, 7, 5, 5), "C": (7, 4, 4, 4, 7),
        "D": (6, 5, 5, 5, 6), "E": (7, 4, 6, 4, 7),
        "F": (7, 4, 6, 4, 4), "G": (7, 4, 5, 5, 7),
        "H": (5, 5, 7, 5, 5), "I": (7, 2, 2, 2, 7),
        "L": (4, 4, 4, 4, 7), "M": (5, 7, 7, 5, 5),
        "N": (5, 7, 7, 7, 5), "O": (7, 5, 5, 5, 7),
        "P": (7, 5, 7, 4, 4), "R": (6, 5, 6, 5, 5),
        "S": (7, 4, 7, 1, 7), "T": (7, 2, 2, 2, 2),
        "U": (5, 5, 5, 5, 7), "W": (5, 5, 7, 7, 5),
        "Y": (5, 5, 2, 2, 2),
    }

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "face": "digital", "background": "gradient", "palette": "amber",
            "format_24h": False, "show_seconds": True, "clock_offset_minutes": 0,
            "position_y": 0.5, "scale": 1, "glow": 0.45,
            "motion": 0.7, "density": 0.45, "speed": 1.0,
        })
        self.params = {**self.default_params, **self.config}
        self.width, self.height = self.get_strip_info()
        self._x = np.arange(self.width, dtype=np.float32)[:, None]
        self._y = np.arange(self.height, dtype=np.float32)[None, :]
        self._xn = self._x / max(1, self.width - 1)
        self._yn = self._y / max(1, self.height - 1)
        self._seed = ((self._x * 37 + self._y * 101 + 17) % 997) / 997.0
        self._canvas = np.zeros((self.width, self.height, 3), dtype=np.float32)
        self._last_render_key = None
        self._last_frame = None

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        return {
            "face": {"type": "str", "default": "digital", "options": list(self.FACE_OPTIONS), "description": "Clock face geometry"},
            "background": {"type": "str", "default": "gradient", "options": list(self.BACKGROUND_OPTIONS), "description": "Ambient background treatment"},
            "palette": {"type": "str", "default": "amber", "options": list(self.PALETTES), "description": "Coordinated color palette"},
            "format_24h": {"type": "bool", "default": False, "description": "Use 24-hour time"},
            "show_seconds": {"type": "bool", "default": True, "description": "Show or encode seconds"},
            "clock_offset_minutes": {"type": "int", "min": -720, "max": 840, "default": 0, "description": "Offset from the controller's local clock"},
            "position_y": {"type": "float", "min": 0.08, "max": 0.92, "default": 0.5, "description": "Vertical position of the clock face"},
            "scale": {"type": "int", "min": 1, "max": 3, "default": 1, "description": "Face scale where geometry permits"},
            "glow": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.45, "description": "Halo around clock marks"},
            "motion": {"type": "float", "min": 0.0, "max": 3.0, "default": 0.7, "description": "Background motion amount"},
            "density": {"type": "float", "min": 0.05, "max": 1.0, "default": 0.45, "description": "Background detail density"},
            "speed": {"type": "float", "min": 0.1, "max": 4.0, "default": 1.0, "description": "Ambient motion speed"},
            "brightness": {"type": "float", "min": 0.05, "max": 1.0, "default": 1.0, "description": "Overall brightness"},
        }

    def generate_frame(self, time_elapsed: float, frame_count: int):
        now = self._clock_now()
        background = self._choice("background", self.BACKGROUND_OPTIONS, "gradient")
        face = self._choice("face", self.FACE_OPTIONS, "digital")
        animated = background in {"stars", "aurora", "scanlines", "horizon", "grid"} or face in {"orbit", "hourglass"}
        tick = int(time_elapsed * (12 if animated else 1))
        time_key = (now.year, now.month, now.day, now.hour, now.minute, now.second if self.params.get("show_seconds", True) else 0)
        render_key = (tick if animated else 0, time_key, tuple(sorted(self.params.items())))
        if render_key == self._last_render_key and self._last_frame is not None:
            return self.rendered_frame(self._last_frame, changed=False)

        palette = self.PALETTES[self._choice("palette", tuple(self.PALETTES), "amber")]
        self._render_background(background, palette, time_elapsed)
        draw = {
            "digital": self._draw_digital, "analog": self._draw_analog,
            "binary": self._draw_binary, "orbit": self._draw_orbit,
            "linear": self._draw_linear, "segments": self._draw_segments,
            "minimal": self._draw_minimal, "hourglass": self._draw_hourglass,
            "calendar": self._draw_calendar, "word": self._draw_word,
        }[face]
        marks = np.zeros((self.width, self.height, 3), dtype=np.float32)
        draw(marks, now, palette, time_elapsed)
        self._composite_glow(marks, float(self.params.get("glow", 0.45)))

        frame = self.next_frame_buffer(clear=False)
        np.clip(self._canvas, 0, 255, out=self._canvas)
        frame[:] = self._canvas.reshape((-1, 3))
        self.apply_brightness_array(frame, out=frame)
        self._last_render_key, self._last_frame = render_key, frame
        return self.rendered_frame(frame)

    def _clock_now(self) -> datetime:
        """Return wall time for the face; isolated so render tests stay deterministic."""
        return datetime.now().astimezone() + timedelta(
            minutes=int(self.params.get("clock_offset_minutes", 0))
        )

    def _choice(self, key: str, options: Sequence[str], fallback: str) -> str:
        value = str(self.params.get(key, fallback)).lower()
        return value if value in options else fallback

    def _render_background(self, style: str, palette, elapsed: float) -> None:
        base, primary, secondary, shadow = (np.asarray(c, dtype=np.float32) for c in palette)
        motion = float(self.params.get("motion", 0.7))
        speed = float(self.params.get("speed", 1.0))
        density = float(self.params.get("density", 0.45))
        t = elapsed * speed
        self._canvas[:] = base
        if style == "solid":
            return
        if style == "gradient":
            blend = (0.10 + 0.42 * self._yn)[:, :, None]
            self._canvas[:] = base + (shadow - base) * blend
        elif style == "radial":
            cy = float(self.params.get("position_y", 0.5))
            dist = np.sqrt(((self._xn - 0.5) * 1.7) ** 2 + ((self._yn - cy) * 0.65) ** 2)
            blend = np.clip(1.0 - dist, 0.0, 1.0)[:, :, None] * 0.55
            self._canvas[:] = base + (shadow - base) * blend
        elif style == "stars":
            stars = self._seed > (0.985 - density * 0.025)
            twinkle = 0.35 + 0.65 * np.sin(self._seed * 80 + t * (1.5 + motion)) ** 2
            self._canvas[stars] += secondary * twinkle[stars, None] * 0.6
        elif style == "aurora":
            wave = np.sin(self._xn * 12 + self._yn * 5 + t * motion)
            wave += np.sin(self._xn * 5 - self._yn * 9 - t * motion * 0.7)
            blend = np.clip((wave - 0.25) * density * 0.16, 0, 0.32)[:, :, None]
            self._canvas[:] = base + primary * blend + secondary * np.roll(blend, 7, axis=1) * 0.35
        elif style == "scanlines":
            line = (np.sin(self._y * math.pi * (0.35 + density) - t * motion * 5) + 1) * 0.5
            self._canvas[:] = base + shadow * line[:, :, None] * 0.28
        elif style == "horizon":
            horizon = 0.68 + 0.05 * math.sin(t * motion)
            sky = np.clip((self._yn - 0.05) / max(horizon, 0.1), 0, 1)[:, :, None]
            self._canvas[:] = base + (shadow - base) * sky * 0.65
            sun = ((self._xn - 0.5) ** 2 + ((self._yn - horizon) * 0.45) ** 2) < (0.08 + density * 0.035) ** 2
            self._canvas[sun] = secondary * 0.72
        elif style == "grid":
            xline = np.mod(self._x + t * motion * 2, max(3, round(9 - density * 5))) < 0.7
            yline = np.mod(self._y - t * motion * 4, max(4, round(14 - density * 7))) < 0.7
            lines = xline | yline
            self._canvas[lines] += primary * 0.16

    def _center_y(self) -> int:
        return int(round(float(self.params.get("position_y", 0.5)) * (self.height - 1)))

    @staticmethod
    def _paint(canvas: np.ndarray, x: int, y: int, color: Iterable[float]) -> None:
        if 0 <= x < canvas.shape[0] and 0 <= y < canvas.shape[1]:
            np.maximum(canvas[x, y], color, out=canvas[x, y])

    def _text(self, canvas, text: str, x: int, y: int, color, scale: int = 1, spacing: int = 1) -> None:
        cursor = x
        for character in text.upper():
            rows = self.FONT.get(character, self.FONT[" "])
            for row, bits in enumerate(rows):
                for column in range(3):
                    if bits & (1 << (2 - column)):
                        for dx in range(scale):
                            for dy in range(scale):
                                self._paint(canvas, cursor + column * scale + dx, y + row * scale + dy, color)
            cursor += 3 * scale + spacing

    @staticmethod
    def _text_width(text: str, scale: int = 1, spacing: int = 1) -> int:
        return max(0, len(text) * (3 * scale + spacing) - spacing)

    def _line(self, canvas, start, end, color) -> None:
        x0, y0 = start
        x1, y1 = end
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for step in range(steps + 1):
            ratio = step / steps
            self._paint(canvas, round(x0 + (x1 - x0) * ratio), round(y0 + (y1 - y0) * ratio), color)

    def _display_hour(self, now: datetime) -> int:
        if bool(self.params.get("format_24h", False)):
            return now.hour
        return now.hour % 12 or 12

    def _draw_digital(self, c, now, palette, _elapsed):
        text = f"{self._display_hour(now):02d}:{now.minute:02d}"
        x = (self.width - self._text_width(text)) // 2
        y = self._center_y() - 3
        self._text(c, text, x, y, palette[2])
        if bool(self.params.get("show_seconds", True)):
            seconds = f"{now.second:02d}"
            self._text(c, seconds, (self.width - self._text_width(seconds)) // 2, y + 8, palette[1])

    def _draw_analog(self, c, now, palette, _elapsed):
        cx, cy = self.width // 2, self._center_y()
        radius = max(4, min(self.width // 2 - 2, 6 + int(self.params.get("scale", 1)) * 3))
        for hour in range(12):
            angle = math.tau * hour / 12 - math.pi / 2
            self._paint(c, round(cx + math.cos(angle) * radius), round(cy + math.sin(angle) * radius), palette[1])
        second = now.second + now.microsecond / 1_000_000
        minute = now.minute + second / 60
        hour = (now.hour % 12) + minute / 60
        for value, period, length, color in ((hour, 12, 0.52, palette[2]), (minute, 60, 0.78, palette[2])):
            angle = math.tau * value / period - math.pi / 2
            self._line(c, (cx, cy), (round(cx + math.cos(angle) * radius * length), round(cy + math.sin(angle) * radius * length)), color)
        if bool(self.params.get("show_seconds", True)):
            angle = math.tau * second / 60 - math.pi / 2
            self._line(c, (cx, cy), (round(cx + math.cos(angle) * radius * 0.9), round(cy + math.sin(angle) * radius * 0.9)), palette[1])
        self._paint(c, cx, cy, palette[2])

    def _draw_binary(self, c, now, palette, _elapsed):
        values = (now.hour // 10, now.hour % 10, now.minute // 10, now.minute % 10, now.second // 10, now.second % 10)
        bits = 4
        gap, cell = 2, 3
        total = len(values) * cell + (len(values) - 1) * gap
        x0, y0 = (self.width - total) // 2, self._center_y() - bits * 2
        for column, value in enumerate(values):
            x = x0 + column * (cell + gap)
            for bit in range(bits):
                color = palette[2] if value & (1 << (bits - 1 - bit)) else palette[3]
                for dx in range(cell):
                    self._paint(c, x + dx, y0 + bit * 3, color)

    def _draw_orbit(self, c, now, palette, elapsed):
        cx, cy = self.width // 2, self._center_y()
        values = ((now.hour % 12) / 12, now.minute / 60, now.second / 60)
        for index, (value, color) in enumerate(zip(values, (palette[3], palette[1], palette[2]))):
            radius = 5 + index * 4
            for sample in range(max(16, radius * 5)):
                angle = math.tau * sample / max(16, radius * 5)
                if sample % 2 == 0:
                    self._paint(c, round(cx + math.cos(angle) * radius), round(cy + math.sin(angle) * radius), np.asarray(color) * 0.28)
            angle = math.tau * value - math.pi / 2 + elapsed * 0.03 * index
            self._paint(c, round(cx + math.cos(angle) * radius), round(cy + math.sin(angle) * radius), color)

    def _draw_linear(self, c, now, palette, _elapsed):
        values = (now.hour / 24, now.minute / 60, now.second / 60)
        cy = self._center_y()
        for row, (value, color) in enumerate(zip(values, (palette[3], palette[1], palette[2]))):
            y = cy - 4 + row * 4
            length = round(value * (self.width - 2))
            for x in range(1, self.width - 1):
                self._paint(c, x, y, color if x <= length else np.asarray(color) * 0.13)

    def _draw_segments(self, c, now, palette, _elapsed):
        values = (now.hour / 24, now.minute / 60, now.second / 60)
        widths = (5, 7, 9)
        cy = self._center_y()
        for index, (value, width) in enumerate(zip(values, widths)):
            height = max(4, round(value * 24))
            x0 = self.width // 2 - width // 2
            y0 = cy + 12
            color = (palette[3], palette[1], palette[2])[index]
            for y in range(y0 - height, y0):
                for x in range(x0, x0 + width):
                    if (x + y) % 2 == index % 2:
                        self._paint(c, x, y, color)

    def _draw_minimal(self, c, now, palette, _elapsed):
        cy = self._center_y()
        for x in range(1, self.width - 1):
            self._paint(c, x, cy, np.asarray(palette[3]) * 0.35)
        for value, period, y, color in ((now.hour, 24, cy - 2, palette[3]), (now.minute, 60, cy, palette[1]), (now.second, 60, cy + 2, palette[2])):
            self._paint(c, round(1 + value / period * (self.width - 3)), y, color)

    def _draw_hourglass(self, c, now, palette, elapsed):
        cx, cy, radius = self.width // 2, self._center_y(), min(12, self.width // 2 - 2)
        top, bottom = cy - radius, cy + radius
        self._line(c, (cx - radius, top), (cx + radius, top), palette[3])
        self._line(c, (cx - radius, bottom), (cx + radius, bottom), palette[3])
        self._line(c, (cx - radius, top), (cx + radius, bottom), palette[3])
        self._line(c, (cx + radius, top), (cx - radius, bottom), palette[3])
        progress = (now.second + now.microsecond / 1_000_000) / 60
        for row in range(radius):
            half = round((radius - row) * (1 - progress))
            y = top + row
            for x in range(cx - half, cx + half + 1, 2):
                self._paint(c, x, y, palette[1])
        pile = round(radius * progress)
        for row in range(pile):
            half = round(row * radius / max(1, pile))
            for x in range(cx - half, cx + half + 1, 2):
                self._paint(c, x, bottom - row, palette[2])
        self._paint(c, cx, cy + int(elapsed * 8) % max(1, radius), palette[2])

    def _draw_calendar(self, c, now, palette, _elapsed):
        y = self._center_y() - 9
        clock = f"{self._display_hour(now):02d}:{now.minute:02d}"
        day = now.strftime("%a").upper()
        date = now.strftime("%m-%d")
        for line, color in ((clock, palette[2]), (day, palette[1]), (date, palette[3])):
            self._text(c, line, (self.width - self._text_width(line)) // 2, y, color)
            y += 7

    def _draw_word(self, c, now, palette, _elapsed):
        if 5 <= now.hour < 12:
            word = "DAWN"
        elif 12 <= now.hour < 17:
            word = "DAY"
        elif 17 <= now.hour < 21:
            word = "DUSK"
        else:
            word = "NIGHT"
        y = self._center_y() - 7
        self._text(c, word, (self.width - self._text_width(word)) // 2, y, palette[2])
        hour = f"{self._display_hour(now):02d}"
        self._text(c, hour, (self.width - self._text_width(hour)) // 2, y + 9, palette[1])

    def _composite_glow(self, marks: np.ndarray, amount: float) -> None:
        amount = max(0.0, min(1.0, amount))
        if amount:
            halo = np.zeros_like(marks)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                halo += np.roll(marks, (dx, dy), axis=(0, 1))
            self._canvas += halo * (amount * 0.13)
        np.maximum(self._canvas, marks, out=self._canvas)
