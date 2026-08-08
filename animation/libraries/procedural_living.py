"""Shared bounded timing and rendering utilities for procedural living systems."""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

from animation import AnimationBase


MOODS = {
    "dusk": ((8, 8, 18), (104, 72, 42), (236, 186, 92)),
    "moon": ((3, 7, 17), (35, 91, 115), (125, 229, 224)),
    "ember": ((12, 2, 2), (139, 31, 9), (255, 153, 37)),
    "reef": ((2, 5, 17), (20, 91, 111), (213, 65, 129)),
    "violet": ((5, 2, 18), (64, 32, 118), (186, 122, 255)),
    "daylight": ((28, 48, 72), (105, 178, 222), (244, 251, 255)),
    "pastel": ((35, 24, 58), (174, 128, 205), (255, 214, 226)),
    "synthwave": ((18, 5, 45), (193, 35, 151), (45, 224, 255)),
    "candlelight": ((30, 9, 2), (178, 74, 18), (255, 218, 128)),
    "aurora": ((3, 18, 29), (27, 154, 134), (168, 255, 221)),
}

BACKGROUND_LEVELS = {"none": 0.0, "soft": 0.35, "luminous": 0.7, "radiant": 1.0}


class ProceduralLivingBase(AnimationBase):
    """Base for a low-rate deterministic simulation with a capped source FPS."""

    SIM_HZ = 10.0
    RENDER_FPS = 30.0

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.width, self.height = self.get_strip_info()
        self.default_params.update({
            "seed": 7319,
            "motion": 1.0,
            "density": 1.0,
            "mood": "moon",
            "brightness": 0.42,
            "background": "soft",
            "background_level": 0.16,
            "simulation_hz": self.SIM_HZ,
            "render_fps": self.RENDER_FPS,
        })
        self.params = {**self.default_params, **self.config}
        self.rng = np.random.default_rng(int(self.params["seed"]))
        self._last_elapsed: Optional[float] = None
        self._last_render_elapsed: Optional[float] = None
        self._accumulator = 0.0
        self._sim_time = 0.0
        self._cached_frame = None
        self._logical_generation = 0
        self._initialize_simulation()

    def get_parameter_schema(self):
        schema = super().get_parameter_schema()
        schema.update({
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 7319,
                     "description": "Dedicated deterministic simulation seed"},
            "motion": {"type": "float", "min": 0.1, "max": 2.5, "default": 1.0,
                       "description": "Semantic motion rate"},
            "density": {"type": "float", "min": 0.2, "max": 2.0, "default": 1.0,
                        "description": "Bounded population or pattern density"},
            "mood": {"type": "str", "default": "moon", "options": list(MOODS),
                     "description": "Presentation-only color family"},
            "brightness": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.42,
                           "description": "Conservative overall LED brightness"},
            "background": {"type": "str", "options": list(BACKGROUND_LEVELS), "default": "soft",
                           "description": "Ambient light behind the living system"},
            "background_level": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.16,
                                 "description": "Strength of the ambient background"},
            "simulation_hz": {"type": "float", "min": 5.0, "max": 20.0,
                              "default": self.SIM_HZ, "description": "Fixed semantic updates per second"},
            "render_fps": {"type": "float", "min": 20.0, "max": 40.0,
                           "default": self.RENDER_FPS, "description": "Maximum source redraw rate"},
        })
        return schema

    def update_parameters(self, new_params):
        reset = bool({"seed", "density"} & new_params.keys())
        super().update_parameters(new_params)
        if reset:
            self.rng = np.random.default_rng(int(self.params["seed"]))
            self._logical_generation = 0
            self._sim_time = 0.0
            self._accumulator = 0.0
            self._initialize_simulation()
        self._last_render_elapsed = None

    def _initialize_simulation(self):
        raise NotImplementedError

    def _simulate_step(self, dt: float):
        raise NotImplementedError

    def _render_scene(self, elapsed: float):
        raise NotImplementedError

    def _advance(self, elapsed: float):
        if self._last_elapsed is None or elapsed < self._last_elapsed:
            self._last_elapsed = elapsed
            return
        delta = min(0.25, max(0.0, elapsed - self._last_elapsed))
        self._last_elapsed = elapsed
        hz = float(np.clip(self.params.get("simulation_hz", self.SIM_HZ), 5.0, 20.0))
        step = 1.0 / hz
        self._accumulator += delta * float(np.clip(self.params.get("motion", 1.0), .1, 2.5))
        count = 0
        while self._accumulator >= step and count < 4:
            self._simulate_step(step)
            self._accumulator -= step
            self._sim_time += step
            self._logical_generation += 1
            count += 1
        if count == 4:
            self._accumulator = min(self._accumulator, step)

    def generate_frame(self, time_elapsed: float, frame_count: int):
        self._advance(float(time_elapsed))
        fps = float(np.clip(self.params.get("render_fps", self.RENDER_FPS), 20.0, 40.0))
        if (self._cached_frame is not None and self._last_render_elapsed is not None
                and time_elapsed >= self._last_render_elapsed
                and time_elapsed - self._last_render_elapsed < 1.0 / fps):
            return self.rendered_frame(self._cached_frame, changed=False)
        self._cached_frame = self._render_scene(float(time_elapsed))
        self._last_render_elapsed = float(time_elapsed)
        return self.rendered_frame(self._cached_frame, changed=True)

    def _finish_canvas(self, canvas: np.ndarray):
        """Apply global brightness and map logical (height,width) to flat strips."""
        style = str(self.params.get("background", "soft"))
        level = float(np.clip(self.params.get("background_level", .16), 0.0, 1.0))
        strength = BACKGROUND_LEVELS.get(style, BACKGROUND_LEVELS["soft"]) * level
        if strength > 0.0:
            _, mid, high = (np.asarray(color, dtype=np.float32) for color in self._palette())
            color = mid * (1.0 - .38 * strength) + high * (.18 + .38 * strength)
            floor = np.clip(color * (.34 * strength), 0.0, 255.0).astype(np.uint8)
            np.maximum(canvas, floor, out=canvas)
        frame = self.next_frame_buffer(clear=False)
        flat = canvas.transpose(1, 0, 2).reshape(-1, 3)
        brightness = float(np.clip(self.params.get("brightness", .42), 0.0, 1.0))
        np.multiply(flat, brightness, out=frame, casting="unsafe")
        return frame

    def _palette(self):
        return MOODS.get(str(self.params.get("mood", "moon")), MOODS["moon"])

    def logical_state(self):
        """Stable semantic snapshot used by deterministic and presentation tests."""
        raise NotImplementedError
