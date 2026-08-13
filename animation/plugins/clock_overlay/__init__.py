#!/usr/bin/env python3
"""Sparse premultiplied-alpha clock overlay for composed host scenes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Dict, Optional

import numpy as np

from animation import AnimationBase
from animation.core.compositing import coverage_dirty_union
from animation.core.plant_awareness import PLANT_MODIFIER_IDS
from animation.core.presentation_contracts import OverlayFrame, TimingAdapter
from animation.libraries.clock_face import ClockFaceRenderer


def _cache_value(value: Any) -> Any:
    """Return a stable equality key for authored JSON-like parameters."""
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _cache_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_cache_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(_cache_value(item) for item in value))
    return value


class ClockOverlayAnimation(ClockFaceRenderer, AnimationBase):
    """Clock marks and glow over transparent pixels, with no owned background."""

    ANIMATION_NAME = "Clock Overlay"
    ANIMATION_DESCRIPTION = "Plant-aware clock marks for composed backgrounds"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    PLANT_MODIFIER_SUPPORT = frozenset(PLANT_MODIFIER_IDS)
    TIMING_ADAPTER = TimingAdapter.WALL_CLOCK
    VIBE_CAPABILITIES = frozenset(("palette_roles", "luminance"))
    VIBE_COLOR_POLICY = "semantic"

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "face": "digital",
            "palette": "amber",
            "format_24h": False,
            "show_seconds": True,
            "clock_offset_minutes": 0,
            "position_y": 0.5,
            "scale": 1,
            "glow": 0.45,
            # Brightness changes straight color only. Opacity changes both RGB
            # and alpha, preserving premultiplication and allowing opaque black.
            "brightness": 1.0,
            "opacity": 1.0,
        })
        self.params = {**self.default_params, **self.config}
        self.width, self.height = self.get_strip_info()
        self._marks = np.zeros((self.width, self.height, 3), dtype=np.float32)
        self._straight_rgb = np.zeros_like(self._marks)
        self._halo_rgb = np.zeros_like(self._marks)
        self._alpha = np.zeros((self.width, self.height), dtype=np.float32)
        pixel_count = self.get_pixel_count()
        self._overlay_buffers = [
            np.zeros((pixel_count, 4), dtype=np.uint8),
            np.zeros((pixel_count, 4), dtype=np.uint8),
        ]
        self._last_overlay_pixels = np.zeros((pixel_count, 4), dtype=np.uint8)
        self._last_overlay_key = None
        self._revision = 0
        self._initialize_clock_face_state()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = {
            "face": {
                "type": "str", "default": "digital",
                "options": list(self.FACE_OPTIONS),
                "description": "Clock face geometry",
            },
            "palette": {
                "type": "str", "default": "amber",
                "options": list(self.PALETTES),
                "description": "Semantic clock-mark palette",
            },
            "format_24h": {
                "type": "bool", "default": False,
                "description": "Use 24-hour time",
            },
            "show_seconds": {
                "type": "bool", "default": True,
                "description": "Show seconds and use one-second cadence",
            },
            "clock_offset_minutes": {
                "type": "int", "min": -720, "max": 840, "default": 0,
                "description": "Offset from the controller's local wall clock",
            },
            "position_y": {
                "type": "float", "min": 0.08, "max": 0.92, "default": 0.5,
                "description": "Vertical position of the complete clock face",
            },
            "scale": {
                "type": "int", "min": 1, "max": 3, "default": 1,
                "description": "Face scale where geometry permits",
            },
            "glow": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 0.45,
                "description": "Transparent halo strength around clock marks",
            },
            "brightness": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 1.0,
                "description": "Clock color intensity; alpha coverage is unchanged",
            },
            "opacity": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 1.0,
                "description": "Whole-overlay opacity applied to RGB and alpha",
            },
        }
        schema.update({
            key: value for key, value in super().get_parameter_schema().items()
            if key.startswith("plant_")
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> OverlayFrame:
        del time_elapsed, frame_count  # wall-clock faces do not consume scene time
        now = self._clock_now()
        show_seconds = bool(self.params.get("show_seconds", True))
        time_key = self._clock_time_key(now, show_seconds)
        context = getattr(self, "presentation_context", None)
        presentation_key = context.presentation_identity if context is not None else None
        render_key = (
            time_key,
            _cache_value(self.params),
            presentation_key,
        )
        if render_key == self._last_overlay_key:
            return OverlayFrame(
                pixels=self._last_overlay_pixels,
                revision=self._revision,
                changed=False,
            )

        # Seconds-off mode is semantically minute-driven for every face. It
        # removes abstract second tracks and prevents residual sub-minute hand
        # interpolation from multiplying render cadence.
        face_now = now if show_seconds else now.replace(second=0, microsecond=0)
        face = self._choice("face", self.FACE_OPTIONS, "digital")
        palette = self._presentation_palette()
        self._marks.fill(0.0)
        self._draw_face(self._marks, face, face_now, palette, 0.0)
        marks = self._marks
        if self._plant_placement_enabled():
            marks = self._place_away_from_plants(marks)
        else:
            self._reset_clock_placement_stats()

        output = self._next_overlay_buffer()
        self._render_premultiplied_overlay(marks, output)
        self._last_overlay_key = render_key

        previous = self._last_overlay_pixels
        if np.array_equal(previous, output):
            return OverlayFrame(
                pixels=previous,
                revision=self._revision,
                changed=False,
            )

        dirty_ranges = coverage_dirty_union(previous, output)
        self._revision += 1
        self._last_overlay_pixels = output
        return OverlayFrame(
            pixels=output,
            revision=self._revision,
            changed=True,
            dirty_ranges=dirty_ranges,
        )

    def _next_overlay_buffer(self) -> np.ndarray:
        # An invalidated key may rerender byte-identical content. Keep using the
        # non-current scratch buffer until content truly changes so the cached
        # plane is never cleared or overwritten during that comparison.
        output = (
            self._overlay_buffers[1]
            if self._last_overlay_pixels is self._overlay_buffers[0]
            else self._overlay_buffers[0]
        )
        output.fill(0)
        return output

    def _render_premultiplied_overlay(
        self, marks: np.ndarray, output: np.ndarray
    ) -> None:
        """Convert visual RGB marks into physical premultiplied RGBA8."""
        np.copyto(self._straight_rgb, marks)
        self._halo_rgb.fill(0.0)
        glow = float(np.clip(self.params.get("glow", 0.45), 0.0, 1.0))
        if glow > 0.0:
            # Deliberately do not use np.roll: glow at an edge must clip rather
            # than wrap around to the opposite side of the physical wall.
            np.maximum(self._halo_rgb[1:], marks[:-1], out=self._halo_rgb[1:])
            np.maximum(self._halo_rgb[:-1], marks[1:], out=self._halo_rgb[:-1])
            np.maximum(self._halo_rgb[:, 1:], marks[:, :-1], out=self._halo_rgb[:, 1:])
            np.maximum(self._halo_rgb[:, :-1], marks[:, 1:], out=self._halo_rgb[:, :-1])
            np.maximum(
                self._straight_rgb,
                self._halo_rgb * (glow * 0.13),
                out=self._straight_rgb,
            )

        core = np.any(marks > 0.0, axis=2)
        halo = np.any(self._halo_rgb > 0.0, axis=2) & ~core
        self._alpha.fill(0.0)
        self._alpha[core] = 255.0
        self._alpha[halo] = 255.0 * glow * 0.35

        opacity = float(np.clip(self.params.get("opacity", 1.0), 0.0, 1.0))
        brightness = float(np.clip(self.params.get("brightness", 1.0), 0.0, 1.0))
        final_alpha = self._alpha * opacity
        premultiplied = (
            self._straight_rgb
            * brightness
            * (final_alpha[:, :, None] / 255.0)
        )

        visual = output.reshape((self.width, self.height, 4))[:, ::-1]
        np.rint(premultiplied, out=premultiplied)
        np.clip(premultiplied, 0.0, 255.0, out=premultiplied)
        np.copyto(visual[:, :, :3], premultiplied, casting="unsafe")
        rounded_alpha = np.rint(final_alpha)
        np.copyto(visual[:, :, 3], rounded_alpha, casting="unsafe")
        # Float rounding and future palette additions must never violate the
        # premultiplied contract checked by OverlayFrame.
        np.minimum(visual[:, :, :3], visual[:, :, 3:4], out=visual[:, :, :3])

    def _clock_now(self) -> datetime:
        context = getattr(self, "presentation_context", None)
        if context is None:
            return super()._clock_now()
        return self._apply_clock_offset(
            datetime.fromtimestamp(context.wall_time).astimezone()
        )

    def _abstract_seconds_enabled(self) -> bool:
        return bool(self.params.get("show_seconds", True))

    def update_parameters(self, new_params: Dict[str, Any]):
        super().update_parameters(new_params)
        self._last_overlay_key = None

    def on_presentation_context_changed(self, old_context, new_context) -> None:
        """Invalidate presentation only; wall time and authored state stay intact."""
        self._last_overlay_key = None


__all__ = ["ClockOverlayAnimation"]
