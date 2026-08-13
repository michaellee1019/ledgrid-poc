#!/usr/bin/env python3
"""Clock faces ranging from practical timepieces to atmospheric time studies."""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np

from animation import AnimationBase
from animation.core.plant_awareness import PLANT_MODIFIER_IDS
from animation.libraries.clock_face import ClockFaceRenderer


class ClockAnimation(ClockFaceRenderer, AnimationBase):
    ANIMATION_NAME = "Clock"
    ANIMATION_DESCRIPTION = "Useful and atmospheric clocks with composable faces, palettes, and backgrounds"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    # Every global plant mode protects the informational HUD. The semantic
    # meaning of the selected mode may vary by animation, but physical foliage
    # and globes can obscure a clock under any of them.
    PLANT_MODIFIER_SUPPORT = frozenset(PLANT_MODIFIER_IDS)

    BACKGROUND_OPTIONS = (
        "solid", "gradient", "radial", "stars", "aurora",
        "scanlines", "horizon", "grid",
    )

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
        self._initialize_clock_face_state()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = {
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
        schema.update({
            key: value for key, value in super().get_parameter_schema().items()
            if key.startswith("plant_")
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int):
        context = getattr(self, "presentation_context", None)
        presentation_elapsed = (
            context.unscaled_elapsed if context is not None else time_elapsed
        )
        now = self._clock_now()
        background = self._choice("background", self.BACKGROUND_OPTIONS, "gradient")
        face = self._choice("face", self.FACE_OPTIONS, "digital")
        animated = background in {"stars", "aurora", "scanlines", "horizon", "grid"} or face in {"orbit", "hourglass"}
        tick = int(presentation_elapsed * (12 if animated else 1))
        time_key = (now.year, now.month, now.day, now.hour, now.minute, now.second if self.params.get("show_seconds", True) else 0)
        palette = self._presentation_palette()
        vibe_key = (
            (context.vibe_id, context.vibe_profile_version)
            if context is not None else ("neutral", 1)
        )
        render_key = (
            tick if animated else 0,
            time_key,
            tuple(sorted(self.params.items())),
            palette,
            vibe_key,
        )
        if render_key == self._last_render_key and self._last_frame is not None:
            return self.rendered_frame(self._last_frame, changed=False)

        self._render_background(background, palette, presentation_elapsed)
        marks = np.zeros((self.width, self.height, 3), dtype=np.float32)
        self._draw_face(marks, face, now, palette, presentation_elapsed)
        if self._plant_placement_enabled():
            marks = self._place_away_from_plants(marks)
        else:
            self._reset_clock_placement_stats()
        self._composite_glow(marks, float(self.params.get("glow", 0.45)))

        frame = self.next_frame_buffer(clear=False)
        np.clip(self._canvas, 0, 255, out=self._canvas)
        # Drawing uses conventional screen coordinates where y=0 is the visual
        # top. Physical LED 0 is mounted at the visual bottom, so convert once
        # at the render boundary. Keeping this transform out of the individual
        # faces ensures text is upright and analog hands advance clockwise.
        frame[:] = self._canvas[:, ::-1, :].reshape((-1, 3))
        self.apply_brightness_array(frame, out=frame)
        self._last_render_key, self._last_frame = render_key, frame
        return self.rendered_frame(frame)

    def on_presentation_context_changed(self, old_context, new_context) -> None:
        """Invalidate presentation only; wall time and authored state stay intact."""
        self._last_render_key = None

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
