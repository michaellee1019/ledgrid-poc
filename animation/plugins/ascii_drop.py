#!/usr/bin/env python3
"""Falling 5x7 text that settles into a pile on the LED wall."""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from animation import AnimationBase


class AsciiDropAnimation(AnimationBase):
    """Drop characters from a phrase down the wall under simple gravity."""

    ANIMATION_NAME = "ASCII Drop"
    ANIMATION_DESCRIPTION = "5x7 characters fall and stack across the LED wall"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"

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

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "phrase": "HELLO WORLD",
            "drop_speed": 18.0,
            "spawn_rate": 1.5,
            "character_red": 0,
            "character_green": 255,
            "character_blue": 100,
            "background_red": 0,
            "background_green": 0,
            "background_blue": 5,
            "clear_fill_ratio": 0.72,
            "random_seed": 0,
        })
        self.params = {**self.default_params, **self.config}
        for unused in ("speed", "color_saturation", "color_value"):
            self.params.pop(unused, None)
        self.width, self.height = self.get_strip_info()
        self._settled = np.zeros((self.height, self.width), dtype=np.bool_)
        self._plant_clearance = np.zeros_like(self._settled)
        self._plant_foliage = np.zeros_like(self._settled)
        self._plant_globes = np.zeros_like(self._settled)
        self._plant_mask_error = ""
        self._pieces: List[Dict[str, Any]] = []
        self._glyph_cache: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        self._rng = np.random.default_rng(int(self.params.get("random_seed", 0)))
        self._phrase_index = 0
        self._next_spawn_time = 0.0
        self._last_time: Optional[float] = None
        self._settled_revision = 0
        self._last_render_key = None
        self._last_frame = None
        self._refresh_plant_geometry()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.pop("speed", None)
        schema.pop("color_saturation", None)
        schema.pop("color_value", None)
        schema.update({
            "phrase": {
                "type": "str",
                "default": "HELLO WORLD",
                "description": "Characters to drop (A-Z, 0-9, underscore, and spaces)",
            },
            "drop_speed": {
                "type": "float",
                "min": 1.0,
                "max": 80.0,
                "default": 18.0,
                "description": "Vertical travel speed in pixels per second",
            },
            "spawn_rate": {
                "type": "float",
                "min": 0.1,
                "max": 10.0,
                "default": 1.5,
                "description": "Character slots processed per second",
            },
            "character_red": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "Character red"},
            "character_green": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Character green"},
            "character_blue": {"type": "int", "min": 0, "max": 255, "default": 100, "description": "Character blue"},
            "background_red": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "Background red"},
            "background_green": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "Background green"},
            "background_blue": {"type": "int", "min": 0, "max": 255, "default": 5, "description": "Background blue"},
            "clear_fill_ratio": {
                "type": "float",
                "min": 0.2,
                "max": 0.95,
                "default": 0.72,
                "description": "Settled-pixel fraction that clears the wall",
            },
            "random_seed": {
                "type": "int",
                "min": 0,
                "max": 1000000,
                "default": 0,
                "description": "Repeatable horizontal placement seed",
            },
            "plant_landmark_brightness": {
                "type": "float",
                "min": 0.0,
                "max": 1.0,
                "default": 0.45,
                "description": "Brightness of foliage and globe landmarks in plant-aware mode",
            },
        })
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        old_seed = int(self.params.get("random_seed", 0))
        was_plant_aware = self.plant_aware_enabled()
        super().update_parameters(new_params)
        new_seed = int(self.params.get("random_seed", 0))
        if new_seed != old_seed:
            self._rng = np.random.default_rng(new_seed)
        plant_geometry = {"plant_clearance", "plant_mask_path", "plant_globe_mask_path"}
        if (
            self.plant_aware_enabled() != was_plant_aware
            or (self.plant_aware_enabled() and bool(plant_geometry & new_params.keys()))
        ):
            self._refresh_plant_geometry()
            self._last_render_key = None

    def generate_frame(self, time_elapsed: float, frame_count: int):
        now = max(0.0, float(time_elapsed))
        dt = 0.0 if self._last_time is None else max(0.0, min(0.25, now - self._last_time))
        self._last_time = now

        threshold = min(0.95, max(0.2, float(self.params.get("clear_fill_ratio", 0.72))))
        if self._settled.size and float(np.count_nonzero(self._settled)) / self._settled.size >= threshold:
            self._reset_scene()

        self._spawn_due_characters(now)
        self._advance_pieces(dt)

        render_key = (
            self._settled_revision,
            tuple((piece["char"], piece["x"], piece["row"]) for piece in self._pieces),
            self._color("character"),
            self._color("background"),
            self._plant_render_key(),
        )
        if render_key == self._last_render_key and self._last_frame is not None:
            return self.rendered_frame(self._last_frame, changed=False)

        frame = self.next_frame_buffer(clear=False)
        frame[:] = self._color("background")
        wall = frame.reshape(self.width, self.height, 3)

        settled_y, settled_x = np.nonzero(self._settled)
        if settled_x.size:
            wall[settled_x, self.height - 1 - settled_y] = self._color("character")

        for piece in self._pieces:
            glyph_y, glyph_x = self._glyph(piece["char"])
            y = glyph_y + piece["row"]
            visible = (y >= 0) & (y < self.height) & (piece["x"] + glyph_x < self.width)
            if self.plant_aware_enabled() and np.any(visible):
                visible &= ~self._plant_clearance[y.clip(0, self.height - 1), piece["x"] + glyph_x]
            if np.any(visible):
                wall[piece["x"] + glyph_x[visible], self.height - 1 - y[visible]] = self._color("character")

        if self.plant_aware_enabled():
            strength = min(1.0, max(0.0, float(self.params.get("plant_landmark_brightness", 0.45))))
            if strength > 0.0:
                wall[self._plant_foliage[::-1].T] = self._landmark_color((20, 150, 42), strength)
                wall[self._plant_globes[::-1].T] = self._landmark_color((180, 72, 230), strength)

        self._last_render_key = render_key
        self._last_frame = frame
        return self.rendered_frame(frame)

    def get_runtime_stats(self) -> Dict[str, Any]:
        settled_pixels = int(np.count_nonzero(self._settled))
        stats = {
            "falling_characters": len(self._pieces),
            "settled_pixels": settled_pixels,
            "fill_ratio": settled_pixels / max(1, self._settled.size),
            "phrase_index": self._phrase_index,
            "plant_aware": self.plant_aware_enabled(),
        }
        if self.plant_aware_enabled():
            stats.update({
                "plant_foliage_pixels": int(np.count_nonzero(self._plant_foliage)),
                "plant_globe_pixels": int(np.count_nonzero(self._plant_globes)),
                "plant_clearance_pixels": int(np.count_nonzero(self._plant_clearance)),
            })
            if self._plant_mask_error:
                stats["plant_mask_error"] = self._plant_mask_error
        return stats

    def _spawn_due_characters(self, now: float):
        interval = 1.0 / max(0.1, float(self.params.get("spawn_rate", 1.5)))
        spawned_slots = 0
        while now + 1e-9 >= self._next_spawn_time and spawned_slots < 4:
            self._spawn_next_character()
            self._next_spawn_time += interval
            spawned_slots += 1

    def _spawn_next_character(self):
        phrase = str(self.params.get("phrase", "HELLO WORLD")).upper()
        if not phrase:
            return
        char = phrase[self._phrase_index % len(phrase)]
        self._phrase_index = (self._phrase_index + 1) % len(phrase)
        if char == " " or char not in self.CHARACTER_BITMAPS:
            return

        _, glyph_x = self._glyph(char)
        glyph_width = int(glyph_x.max()) + 1 if glyph_x.size else 1
        max_x = max(0, self.width - glyph_width)
        x = int(self._rng.integers(0, max_x + 1)) if max_x else 0
        if self.plant_aware_enabled() and max_x:
            x = self._least_occluded_lane(glyph_x, x, max_x)
        self._pieces.append({"char": char, "x": x, "row": -1, "progress": 0.0})

    def _advance_pieces(self, dt: float):
        if not self._pieces or dt <= 0.0:
            return
        distance = max(1.0, float(self.params.get("drop_speed", 18.0))) * dt
        active: List[Dict[str, Any]] = []
        for piece in self._pieces:
            piece["progress"] += distance
            landed = False
            while piece["progress"] >= 1.0:
                candidate = piece["row"] + 1
                if self._collides(piece["char"], piece["x"], candidate):
                    if not self._settle(piece):
                        self._reset_scene()
                        return
                    landed = True
                    break
                piece["row"] = candidate
                piece["progress"] -= 1.0
            if not landed:
                active.append(piece)
        self._pieces = active

    def _collides(self, char: str, x: int, row: int) -> bool:
        glyph_y, glyph_x = self._glyph(char)
        y = glyph_y + row
        inside_x = x + glyph_x < self.width
        if np.any((y >= self.height) & inside_x):
            return True
        visible = (y >= 0) & inside_x
        if np.any(self._settled[y[visible], x + glyph_x[visible]]):
            return True
        return bool(
            self.plant_aware_enabled()
            and np.any(self._plant_clearance[y[visible], x + glyph_x[visible]])
        )

    def _settle(self, piece: Dict[str, Any]) -> bool:
        glyph_y, glyph_x = self._glyph(piece["char"])
        y = glyph_y + piece["row"]
        visible = (y >= 0) & (y < self.height) & (piece["x"] + glyph_x < self.width)
        if self.plant_aware_enabled() and np.any(visible):
            visible &= ~self._plant_clearance[y.clip(0, self.height - 1), piece["x"] + glyph_x]
        if not np.any(visible):
            return False

        settled_y = y[visible]
        settled_x = piece["x"] + glyph_x[visible]
        changed = bool(np.any(~self._settled[settled_y, settled_x]))
        if changed:
            self._settled[settled_y, settled_x] = True
            self._settled_revision += 1
        return changed

    def _glyph(self, char: str) -> Tuple[np.ndarray, np.ndarray]:
        cached = self._glyph_cache.get(char)
        if cached is not None:
            return cached
        bitmap = self.CHARACTER_BITMAPS[char]
        coords = [(y, x) for y, row in enumerate(bitmap) for x, value in enumerate(row) if value == "X"]
        if coords:
            glyph_y, glyph_x = (np.asarray(values, dtype=np.int16) for values in zip(*coords))
        else:
            glyph_y = np.empty(0, dtype=np.int16)
            glyph_x = np.empty(0, dtype=np.int16)
        self._glyph_cache[char] = (glyph_y, glyph_x)
        return glyph_y, glyph_x

    def _color(self, prefix: str) -> Tuple[int, int, int]:
        brightness = min(1.0, max(0.0, float(self.params.get("brightness", 1.0))))
        defaults = {"character": (0, 255, 100), "background": (0, 0, 5)}[prefix]
        return tuple(
            max(0, min(255, int(float(self.params.get(f"{prefix}_{channel}", default)) * brightness)))
            for channel, default in zip(("red", "green", "blue"), defaults)
        )

    def _refresh_plant_geometry(self):
        self._plant_clearance.fill(False)
        self._plant_foliage.fill(False)
        self._plant_globes.fill(False)
        self._plant_mask_error = ""
        if not self.plant_aware_enabled():
            return
        masks = self.get_plant_masks()
        # Shared masks are [strip, physical LED]. Falling rows are top-down.
        self._plant_clearance[:] = masks.clearance.T[::-1]
        self._plant_foliage[:] = masks.foliage.T[::-1]
        self._plant_globes[:] = masks.globes.T[::-1]
        self._plant_mask_error = masks.error

    def _least_occluded_lane(self, glyph_x: np.ndarray, preferred: int, max_x: int) -> int:
        """Keep whole falling text lanes away from calibrated plant terrain."""
        scores = []
        unique_columns = np.unique(glyph_x)
        for candidate in range(max_x + 1):
            score = int(np.count_nonzero(self._plant_clearance[:, candidate + unique_columns]))
            scores.append(score)
        best = min(scores)
        candidates = [candidate for candidate, score in enumerate(scores) if score == best]
        return min(candidates, key=lambda candidate: (abs(candidate - preferred), candidate))

    def _plant_render_key(self):
        if not self.plant_aware_enabled():
            return None
        return (
            int(np.count_nonzero(self._plant_foliage)),
            int(np.count_nonzero(self._plant_globes)),
            float(self.params.get("plant_landmark_brightness", 0.45)),
        )

    def _landmark_color(self, color: Tuple[int, int, int], strength: float) -> Tuple[int, int, int]:
        brightness = min(1.0, max(0.0, float(self.params.get("brightness", 1.0))))
        return tuple(int(channel * brightness * strength) for channel in color)

    def _reset_scene(self):
        self._settled.fill(False)
        self._pieces.clear()
        self._settled_revision += 1
