"""Small allocation-conscious helpers for ambient procedural light sculptures."""

from __future__ import annotations

from abc import ABC
from typing import Any, Dict

import numpy as np

from animation import AnimationBase


BACKGROUND_LEVELS = {"none": 0.0, "soft": 0.35, "luminous": 0.7, "radiant": 1.0}


class CadencedSculpture(AnimationBase, ABC):
    """Abstract base providing deterministic source ticks and logical canvases."""

    SOURCE_FPS = 24.0

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "brightness": 0.32, "motion": 0.45, "density": 0.5,
            "mood": "quiet", "seed": 1729, "background": "soft",
            "background_level": 0.16,
        })
        self.params = {**self.default_params, **(config or {})}
        self._revision = 0
        self._render_key = None
        self._cached_pixels = None
        self._last_sim_tick = -1
        strips, leds = self.get_strip_info()
        self._shape = (strips, leds)
        self._x, self._y = np.meshgrid(
            np.linspace(-1.0, 1.0, strips, dtype=np.float32),
            np.linspace(-1.0, 1.0, leds, dtype=np.float32), indexing="ij",
        )
        self.rng = np.random.default_rng(int(self.params["seed"]))

    def get_parameter_schema(self):
        schema = super().get_parameter_schema()
        schema.update({
            "motion": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.45,
                       "description": "Structural motion and evolution rate"},
            "density": {"type": "float", "min": 0.05, "max": 1.0, "default": 0.5,
                        "description": "Amount of visual structure"},
            "mood": {"type": "str", "options": ["quiet", "showcase", "night", "bright", "pastel", "synthwave", "candlelight", "aurora"],
                     "default": "quiet", "description": "Curated color and contrast family"},
            "background": {"type": "str", "options": list(BACKGROUND_LEVELS), "default": "soft",
                           "description": "Ambient light behind the sculpture"},
            "background_level": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.16,
                                 "description": "Strength of the ambient background"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 1729,
                     "description": "Deterministic sculpture seed"},
        })
        return schema

    def update_parameters(self, new_params):
        old_seed = int(self.params.get("seed", 1729))
        super().update_parameters(new_params)
        self._revision += 1
        if "seed" in new_params and int(new_params["seed"]) != old_seed:
            self.rng = np.random.default_rng(int(new_params["seed"]))
            self.reset_simulation()

    def reset_simulation(self):
        self._last_sim_tick = -1

    def source_tick(self, time_elapsed: float) -> int:
        speed = float(self.params.get("speed", 1.0))
        return max(0, int(max(0.0, float(time_elapsed)) * self.SOURCE_FPS * speed))

    def begin_frame(self, time_elapsed: float):
        tick = self.source_tick(time_elapsed)
        key = (tick, self._revision)
        if key == self._render_key and self._cached_pixels is not None:
            return tick, self.rendered_frame(self._cached_pixels, changed=False)
        return tick, None

    def finish_frame(self, tick: int, logical_rgb: np.ndarray):
        frame = self.next_frame_buffer(clear=False)
        style = str(self.params.get("background", "soft"))
        level = float(np.clip(self.params.get("background_level", .16), 0.0, 1.0))
        strength = BACKGROUND_LEVELS.get(style, BACKGROUND_LEVELS["soft"]) * level
        if strength > 0.0:
            palette = self.palette(str(self.params.get("mood", "quiet")))
            color = palette[1] * (1.0 - .38 * strength) + palette[2] * (.18 + .38 * strength)
            logical_rgb += color * (.14 * strength)
        np.clip(logical_rgb, 0.0, 255.0, out=logical_rgb)
        np.multiply(logical_rgb,
                    max(0.0, float(self.params.get("brightness", 0.32))),
                    out=logical_rgb)
        np.copyto(frame, np.ascontiguousarray(logical_rgb).reshape((-1, 3)), casting="unsafe")
        self._cached_pixels = frame
        self._render_key = (tick, self._revision)
        return self.rendered_frame(frame)

    def advance_bounded(self, tick: int, callback, max_steps: int = 12):
        """Advance fixed state without an unbounded post-stall catch-up."""
        if self._last_sim_tick < 0:
            self._last_sim_tick = tick - 1
        start = max(self._last_sim_tick + 1, tick - max_steps + 1)
        for step in range(start, tick + 1):
            callback(step)
        self._last_sim_tick = tick

    @staticmethod
    def palette(mood: str):
        return {
            "quiet": np.array([[8, 18, 30], [50, 125, 170], [185, 225, 220]], np.float32),
            "showcase": np.array([[18, 5, 35], [35, 185, 220], [245, 125, 190]], np.float32),
            "night": np.array([[1, 2, 8], [18, 38, 78], [85, 125, 155]], np.float32),
            "bright": np.array([[28, 48, 72], [105, 178, 222], [244, 251, 255]], np.float32),
            "pastel": np.array([[35, 24, 58], [174, 128, 205], [255, 214, 226]], np.float32),
            "synthwave": np.array([[18, 5, 45], [193, 35, 151], [45, 224, 255]], np.float32),
            "candlelight": np.array([[30, 9, 2], [178, 74, 18], [255, 218, 128]], np.float32),
            "aurora": np.array([[3, 18, 29], [27, 154, 134], [168, 255, 221]], np.float32),
        }[mood]

    def colorize(self, value: np.ndarray, accent: np.ndarray | None = None):
        p = self.palette(str(self.params.get("mood", "quiet")))
        v = np.clip(value, 0.0, 1.0)[..., None]
        mid = np.minimum(v * 2.0, 1.0)
        high = np.maximum(v * 2.0 - 1.0, 0.0)
        rgb = p[0] + (p[1] - p[0]) * mid + (p[2] - p[1]) * high
        if accent is not None:
            rgb += accent[..., None] * p[2] * 0.35
        return rgb.astype(np.float32, copy=False)
