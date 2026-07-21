"""A camera-mapped parade of recognizable flags from around the world."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

import numpy as np

from animation import AnimationBase


Color = Tuple[int, int, int]


class WorldFlagsAnimation(AnimationBase):
    ANIMATION_NAME = "World Flags"
    ANIMATION_DESCRIPTION = "A scrolling parade of world flags adapted to the photographed LED wall"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    CATALOG = (
        ("USA", "United States", "us", ()),
        ("CAN", "Canada", "canada", ()),
        ("MEX", "Mexico", "vertical", ((0, 104, 71), (255, 255, 255), (206, 17, 38))),
        ("BRA", "Brazil", "brazil", ()),
        ("ARG", "Argentina", "horizontal", ((108, 172, 228), (255, 255, 255), (108, 172, 228))),
        ("COL", "Colombia", "horizontal", ((252, 209, 22), (252, 209, 22), (0, 56, 147), (206, 17, 38))),
        ("GBR", "United Kingdom", "uk", ()),
        ("FRA", "France", "vertical", ((0, 35, 149), (255, 255, 255), (237, 41, 57))),
        ("DEU", "Germany", "horizontal", ((0, 0, 0), (221, 0, 0), (255, 206, 0))),
        ("ITA", "Italy", "vertical", ((0, 146, 70), (255, 255, 255), (206, 43, 55))),
        ("ESP", "Spain", "horizontal", ((170, 21, 27), (241, 191, 0), (241, 191, 0), (170, 21, 27))),
        ("IRL", "Ireland", "vertical", ((22, 155, 98), (255, 255, 255), (255, 136, 62))),
        ("NLD", "Netherlands", "horizontal", ((174, 28, 40), (255, 255, 255), (33, 70, 139))),
        ("BEL", "Belgium", "vertical", ((0, 0, 0), (253, 218, 36), (239, 51, 64))),
        ("POL", "Poland", "horizontal", ((255, 255, 255), (220, 20, 60))),
        ("UKR", "Ukraine", "horizontal", ((0, 87, 184), (255, 215, 0))),
        ("SWE", "Sweden", "nordic", ((0, 106, 167), (254, 204, 0))),
        ("NOR", "Norway", "nordic_double", ((186, 12, 47), (255, 255, 255), (0, 32, 91))),
        ("DNK", "Denmark", "nordic", ((198, 12, 48), (255, 255, 255))),
        ("FIN", "Finland", "nordic", ((255, 255, 255), (0, 53, 128))),
        ("GRC", "Greece", "greece", ()),
        ("CHE", "Switzerland", "swiss", ()),
        ("JPN", "Japan", "disc", ((255, 255, 255), (188, 0, 45))),
        ("CHN", "China", "china", ()),
        ("IND", "India", "india", ()),
        ("BGD", "Bangladesh", "disc", ((0, 106, 78), (244, 42, 65))),
        ("IDN", "Indonesia", "horizontal", ((255, 0, 0), (255, 255, 255))),
        ("PHL", "Philippines", "philippines", ()),
        ("THA", "Thailand", "horizontal", ((165, 25, 49), (255, 255, 255), (45, 42, 74), (45, 42, 74), (255, 255, 255), (165, 25, 49))),
        ("VNM", "Vietnam", "china", ((218, 37, 29), (255, 255, 0))),
        ("AUS", "Australia", "australia", ()),
        ("NZL", "New Zealand", "australia", ((0, 36, 125), (204, 20, 43))),
        ("NGA", "Nigeria", "vertical", ((0, 135, 81), (255, 255, 255), (0, 135, 81))),
        ("ZAF", "South Africa", "south_africa", ()),
        ("KEN", "Kenya", "horizontal", ((0, 0, 0), (255, 255, 255), (187, 18, 38), (187, 18, 38), (255, 255, 255), (0, 98, 51))),
        ("EGY", "Egypt", "horizontal", ((206, 17, 38), (255, 255, 255), (0, 0, 0))),
        ("TUR", "Turkey", "disc", ((227, 10, 23), (255, 255, 255))),
        ("ISR", "Israel", "israel", ()),
    )

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "display_mode": "parade",
            "country": "USA",
            "speed": 7.0,
            "flag_height": 21,
            "gap": 3,
            "flip_horizontal": False,
            "flip_vertical": True,
            "map_path": "config/webcam_pixel_map.json",
            "map_mode": "compensate",
            "visibility_boost": 0.35,
        })
        self.params = {**self.default_params, **self.config}
        self.strip_count, self.leds_per_strip = self.get_strip_info()
        self._flags = self._render_catalog()
        self._visibility = np.ones(self.get_pixel_count(), dtype=np.float32)
        self._occluded = np.zeros(self.get_pixel_count(), dtype=bool)
        self._mapped_scratch = np.empty((self.get_pixel_count(), 3), dtype=np.float32)
        self._map_error = ""
        self._load_map()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        return {
            "display_mode": {"type": "str", "default": "parade", "description": "parade or single"},
            "country": {"type": "str", "default": "USA", "description": "ISO code used in single mode"},
            "speed": {"type": "float", "min": -40.0, "max": 40.0, "default": 7.0, "description": "Parade scroll speed in pixels per second"},
            "flag_height": {"type": "int", "min": 12, "max": 40, "default": 21, "description": "Height of each parade flag"},
            "gap": {"type": "int", "min": 0, "max": 12, "default": 3, "description": "Black rows between parade flags"},
            "brightness": {"type": "float", "min": 0.05, "max": 1.0, "default": 1.0, "description": "Overall brightness"},
            "flip_horizontal": {"type": "bool", "default": False, "description": "Mirror the wall left-to-right"},
            "flip_vertical": {"type": "bool", "default": True, "description": "Mirror the wall top-to-bottom"},
            "map_path": {"type": "str", "default": "config/webcam_pixel_map.json", "description": "Camera-derived pixel map JSON"},
            "map_mode": {"type": "str", "default": "compensate", "description": "compensate, mask, or off"},
            "visibility_boost": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.35, "description": "Extra drive for partially obscured cells"},
        }

    def update_parameters(self, new_params: Dict[str, Any]):
        previous_path = str(self.params.get("map_path"))
        previous_height = int(self.params.get("flag_height", 21))
        super().update_parameters(new_params)
        if int(self.params.get("flag_height", 21)) != previous_height:
            self._flags = self._render_catalog()
        if str(self.params.get("map_path")) != previous_path:
            self._load_map()

    def generate_frame(self, time_elapsed: float, frame_count: int):
        canvas = self._single_canvas() if str(self.params.get("display_mode", "parade")).lower() == "single" else self._parade_canvas(time_elapsed)
        if bool(self.params.get("flip_horizontal", False)):
            canvas = canvas[::-1, :, :]
        if bool(self.params.get("flip_vertical", True)):
            canvas = canvas[:, ::-1, :]

        frame = self.next_frame_buffer(clear=False)
        frame[:] = canvas.reshape((-1, 3))
        mode = str(self.params.get("map_mode", "compensate")).lower()
        if mode == "mask":
            frame[self._occluded] = 0
        elif mode == "compensate":
            boost = max(0.0, min(1.0, float(self.params.get("visibility_boost", 0.35))))
            factors = 1.0 + (1.0 - self._visibility) * boost
            np.multiply(frame, factors[:, None], out=self._mapped_scratch)
            np.clip(self._mapped_scratch, 0.0, 255.0, out=self._mapped_scratch)
            frame[:] = self._mapped_scratch
        return self.apply_brightness_array(frame, out=frame)

    def get_runtime_stats(self) -> Dict[str, Any]:
        stats = {
            "flag_count": len(self.CATALOG),
            "mapped_pixels": int(self._visibility.size),
            "occluded_pixels": int(np.count_nonzero(self._occluded)),
            "map_mode": str(self.params.get("map_mode", "compensate")),
        }
        if self._map_error:
            stats["map_error"] = self._map_error
        return stats

    def _single_canvas(self) -> np.ndarray:
        code = str(self.params.get("country", "USA")).upper()
        index = next((i for i, item in enumerate(self.CATALOG) if item[0] == code), 0)
        flag = self._flags[index]
        canvas = np.zeros((self.strip_count, self.leds_per_strip, 3), dtype=np.uint8)
        y0 = max(0, (self.leds_per_strip - flag.shape[1]) // 2)
        canvas[:, y0:y0 + flag.shape[1]] = flag[:, : self.leds_per_strip - y0]
        return canvas

    def _parade_canvas(self, time_elapsed: float) -> np.ndarray:
        gap = max(0, int(self.params.get("gap", 3)))
        block = self._flags[0].shape[1] + gap
        virtual_height = block * len(self._flags)
        speed = float(self.params.get("speed", 7.0))
        offset = int(math.floor(time_elapsed * speed)) % virtual_height
        canvas = np.zeros((self.strip_count, self.leds_per_strip, 3), dtype=np.uint8)
        for y in range(self.leds_per_strip):
            virtual_y = (y + offset) % virtual_height
            flag_index, row = divmod(virtual_y, block)
            if row < self._flags[flag_index].shape[1]:
                canvas[:, y] = self._flags[flag_index][:, row]
        return canvas

    def _render_catalog(self) -> Tuple[np.ndarray, ...]:
        height = max(12, min(40, int(self.params.get("flag_height", 21))))
        return tuple(self._render_flag(kind, colors, self.strip_count, height) for _, _, kind, colors in self.CATALOG)

    @staticmethod
    def _bands(canvas: np.ndarray, colors: Sequence[Color], vertical: bool) -> None:
        size = canvas.shape[0] if vertical else canvas.shape[1]
        for index, color in enumerate(colors):
            start, end = round(index * size / len(colors)), round((index + 1) * size / len(colors))
            if vertical:
                canvas[start:end, :] = color
            else:
                canvas[:, start:end] = color

    @staticmethod
    def _disc(canvas: np.ndarray, color: Color, center=(0.5, 0.5), radius=0.22) -> None:
        width, height = canvas.shape[:2]
        xx, yy = np.ogrid[:width, :height]
        mask = ((xx / width - center[0]) ** 2 + (yy / height - center[1]) ** 2) <= radius ** 2
        canvas[mask] = color

    @staticmethod
    def _star(canvas: np.ndarray, color: Color, center=(0.5, 0.5), radius=0.17) -> None:
        width, height = canvas.shape[:2]
        cx, cy = center[0] * width, center[1] * height
        for x in range(width):
            for y in range(height):
                angle = math.atan2(y - cy, x - cx)
                distance = math.hypot((x - cx) / max(1, width), (y - cy) / max(1, height))
                edge = radius * (0.42 if int((angle + math.pi) / (math.pi / 5)) % 2 else 1.0)
                if distance <= edge:
                    canvas[x, y] = color

    @classmethod
    def _render_flag(cls, kind: str, colors: Sequence[Color], width: int, height: int) -> np.ndarray:
        c = np.zeros((width, height, 3), dtype=np.uint8)
        if kind == "vertical":
            cls._bands(c, colors, True)
        elif kind == "horizontal":
            cls._bands(c, colors, False)
        elif kind == "disc":
            c[:] = colors[0]
            cls._disc(c, colors[1], center=(0.5 if colors[0] == (255, 255, 255) else 0.44, 0.5), radius=0.23)
        elif kind in {"nordic", "nordic_double"}:
            c[:] = colors[0]
            x, y, thickness = round(width * 0.38), height // 2, max(1, height // 7)
            c[max(0, x - thickness):x + thickness + 1] = colors[1]
            c[:, max(0, y - thickness):y + thickness + 1] = colors[1]
            if kind == "nordic_double":
                inner = max(1, thickness // 2)
                c[x - inner:x + inner + 1] = colors[2]
                c[:, y - inner:y + inner + 1] = colors[2]
        elif kind == "us":
            cls._bands(c, ((178, 34, 52), (255, 255, 255)) * 7, False)
            c[: round(width * 0.42), : round(height * 0.54)] = (60, 59, 110)
            c[1:round(width * 0.42):3, 1:round(height * 0.54):3] = (255, 255, 255)
        elif kind == "uk":
            c[:] = (1, 33, 105)
            for x in range(width):
                y = round(x * height / width)
                c[max(0, x - 1):x + 2, max(0, y - 1):y + 2] = (255, 255, 255)
                y2 = height - 1 - y
                c[max(0, x - 1):x + 2, max(0, y2 - 1):y2 + 2] = (255, 255, 255)
            c[width // 2 - 2:width // 2 + 2] = (200, 16, 46)
            c[:, height // 2 - 2:height // 2 + 2] = (200, 16, 46)
        elif kind == "brazil":
            c[:] = (0, 156, 59)
            for x in range(width):
                half = int((1.0 - abs(x / max(1, width - 1) - 0.5) * 2.0) * height * 0.42)
                c[x, height // 2 - half:height // 2 + half + 1] = (255, 223, 0)
            cls._disc(c, (0, 39, 118), radius=0.19)
        elif kind == "canada":
            c[:] = (255, 255, 255)
            c[: width // 4] = (255, 0, 0)
            c[width - width // 4:] = (255, 0, 0)
            cls._star(c, (255, 0, 0), radius=0.16)
        elif kind == "swiss":
            c[:] = (218, 41, 28)
            c[width // 2 - 2:width // 2 + 2, height // 4:3 * height // 4] = (255, 255, 255)
            c[width // 3:2 * width // 3, height // 2 - 2:height // 2 + 2] = (255, 255, 255)
        elif kind == "china":
            background, star = colors if colors else ((222, 41, 16), (255, 222, 0))
            c[:] = background
            cls._star(c, star, center=(0.22, 0.30), radius=0.14)
        elif kind == "india":
            cls._bands(c, ((255, 153, 51), (255, 255, 255), (19, 136, 8)), False)
            cls._disc(c, (0, 0, 128), radius=0.10)
            cls._disc(c, (255, 255, 255), radius=0.055)
        elif kind == "greece":
            cls._bands(c, ((13, 94, 175), (255, 255, 255)) * 5, False)
            c[: width // 2, : height // 2] = (13, 94, 175)
            c[width // 4 - 1:width // 4 + 2, : height // 2] = (255, 255, 255)
            c[: width // 2, height // 4 - 1:height // 4 + 2] = (255, 255, 255)
        elif kind == "philippines":
            cls._bands(c, ((0, 56, 168), (206, 17, 38)), False)
            for x in range(width // 2):
                half = round((1 - x / max(1, width // 2)) * height / 2)
                c[x, height // 2 - half:height // 2 + half + 1] = (255, 255, 255)
            cls._disc(c, (252, 209, 22), center=(0.13, 0.5), radius=0.07)
        elif kind == "australia":
            background, star = colors if colors else ((0, 36, 125), (255, 255, 255))
            c[:] = background
            cls._star(c, star, center=(0.70, 0.58), radius=0.09)
            cls._star(c, star, center=(0.84, 0.26), radius=0.06)
            c[: width // 2, : height // 2] = cls._render_flag("uk", (), max(1, width // 2), max(1, height // 2))
        elif kind == "israel":
            c[:] = (255, 255, 255)
            c[:, 2:4] = (0, 56, 184)
            c[:, height - 4:height - 2] = (0, 56, 184)
            cls._star(c, (0, 56, 184), radius=0.13)
        elif kind == "south_africa":
            c[:] = (0, 122, 77)
            c[:, : height // 3] = (222, 56, 49)
            c[:, 2 * height // 3:] = (0, 35, 149)
            for x in range(width // 2):
                half = round((1 - x / max(1, width // 2)) * height / 2)
                c[x, height // 2 - half:height // 2 + half + 1] = (0, 0, 0)
        else:
            c[:] = (255, 0, 255)
        return c

    def _load_map(self) -> None:
        self._visibility.fill(1.0)
        self._occluded.fill(False)
        self._map_error = ""
        path = Path(str(self.params.get("map_path", "config/webcam_pixel_map.json")))
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("geometry", {}).get("total_leds") != self.get_pixel_count():
                raise ValueError("pixel-map geometry does not match the controller")
            for pixel in payload.get("pixels", []):
                index = int(pixel["index"])
                if 0 <= index < self.get_pixel_count():
                    self._visibility[index] = max(0.0, min(1.0, float(pixel.get("visibility", 1.0))))
                    self._occluded[index] = bool(pixel.get("occluded", False))
        except Exception as exc:
            self._map_error = str(exc)
