"""Scene v2 Emoji Message Widget.

The message owns only a small transparent glyph plane. Palette resolution,
scaled time, plant presentation, and output brightness remain host concerns.
"""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping, Optional

import numpy as np

from animation import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.compositing import OverlayFrame
from animation.core.presentation_contracts import ResolvedScene
from animation.libraries.pixel_art import EMOJI_PATTERNS


def _dirty_ranges(previous: np.ndarray, current: np.ndarray) -> tuple[tuple[int, int], ...]:
    changed = np.flatnonzero(np.any(previous != current, axis=1))
    if not changed.size:
        return ()
    starts = changed[np.r_[True, np.diff(changed) != 1]]
    ends = changed[np.r_[np.diff(changed) != 1, True]] + 1
    return tuple((int(start), int(end)) for start, end in zip(starts, ends))


class EmojiArrangerAnimation(AnimationBase):
    """A deterministic, transparent, semantic-palette Emoji Message plane."""

    ANIMATION_NAME = "Emoji Message"
    ANIMATION_DESCRIPTION = "A compact scrolling emoji message composed over the current scene"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"

    COMPONENT_ID = "emoji_arranger"
    COMPONENT_VERSION = 1
    PROVIDER = "python"
    ROLE = "widget"
    FRAME_FORMAT = "rgba_uint8_premultiplied_strip_major"
    TIMING_POLICY = "scaled_context"
    PALETTE_POLICY = "semantic"
    PALETTE_ROLES = ("message", "highlight", "ink")
    DEFAULTS = MappingProxyType({
        "text": "HI🔥", "x_offset": 8, "y_offset": 3,
        "char_spacing": 1, "line_spacing": 1,
        "scroll_speed": 0.0, "pulse_speed": 0.5,
    })
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID, version=COMPONENT_VERSION,
        provider=PROVIDER, role=ROLE, timing_policy=TIMING_POLICY,
        alpha_behavior="premultiplied_rgba", palette_policy=PALETTE_POLICY,
        plant_capabilities=("effect_intent",), fidelity_exceptions=(),
        defaults=DEFAULTS,
    )
    SEMANTIC_PALETTES = MappingProxyType({
        "neutral": MappingProxyType({"message": (150, 255, 218), "highlight": (225, 255, 244), "ink": (8, 22, 19)}),
        "mist": MappingProxyType({"message": (170, 228, 245), "highlight": (235, 252, 255), "ink": (9, 25, 38)}),
        "spectrum": MappingProxyType({"message": (54, 238, 230), "highlight": (231, 181, 255), "ink": (17, 5, 36)}),
        "ember": MappingProxyType({"message": (255, 202, 92), "highlight": (255, 239, 179), "ink": (48, 10, 4)}),
    })

    def __init__(self, controller: Any, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self._buffers = tuple(np.zeros((self.get_pixel_count(), 4), dtype=np.uint8) for _ in range(2))
        self._last_pixels = self._buffers[0]
        self._last_key: tuple[Any, ...] | None = None
        self._revision = 0
        self._presentation_context: ResolvedScene | None = None

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    def get_parameter_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "text": {"type": "str", "default": "HI🔥", "description": "Letters, numbers, spaces, and supported emoji"},
            "x_offset": {"type": "int", "min": -137, "max": 137, "default": 8, "description": "Horizontal message position"},
            "y_offset": {"type": "int", "min": -32, "max": 32, "default": 3, "description": "Vertical message position"},
            "char_spacing": {"type": "int", "min": 0, "max": 8, "default": 1, "description": "Space between glyphs"},
            "line_spacing": {"type": "int", "min": 0, "max": 8, "default": 1, "description": "Space between wrapped lines"},
            "scroll_speed": {"type": "float", "min": 0.0, "max": 24.0, "default": 0.0, "description": "Message scroll pixels per scaled second"},
            "pulse_speed": {"type": "float", "min": 0.0, "max": 3.0, "default": 0.5, "description": "Message pulse cycles per scaled second"},
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        unknown = set(new_params) - set(self.DEFAULTS)
        if unknown:
            raise ValueError(f"Emoji Message does not accept non-local parameters: {sorted(unknown)!r}")
        self.params = self._normalized_parameters({**self.params, **dict(new_params)})
        self._last_key = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        descriptor = new.descriptor
        if (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value) != (
            self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE,
        ):
            raise ValueError("Emoji Message received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str):
            raise ValueError("Emoji Message requires a semantic Scene v2 palette")
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> OverlayFrame:
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> OverlayFrame:
        del frame_count
        if self._presentation_context is None:
            phase_time, palette_id, parameters = max(0.0, float(time_elapsed)), "neutral", self.params
        else:
            phase_time = max(0.0, float(self._presentation_context.phase_time))
            palette_id = str(self._presentation_context.palette["palette_id"])
            parameters = self._presentation_context.parameters
        self.params = self._normalized_parameters(parameters)
        tick = int(math.floor(phase_time * 60.0 + 1e-9))
        key = (tuple(self.params.items()), palette_id, tick)
        if key == self._last_key:
            return OverlayFrame(self._last_pixels, revision=self._revision, changed=False)
        output = self._buffers[1] if self._last_pixels is self._buffers[0] else self._buffers[0]
        self._paint(output, phase_time, palette_id)
        ranges = _dirty_ranges(self._last_pixels, output)
        self._last_pixels = output
        self._last_key = key
        self._revision += 1
        return OverlayFrame(output, revision=self._revision, changed=True, dirty_ranges=ranges)

    def _paint(self, output: np.ndarray, phase_time: float, palette_id: str) -> None:
        canvas = output.reshape(self.width, self.height, 4)
        canvas.fill(0)
        palette = self.SEMANTIC_PALETTES.get(palette_id, self.SEMANTIC_PALETTES["neutral"])
        pulse = .82 + .18 * ((math.sin(phase_time * self.params["pulse_speed"] * math.tau) + 1.0) * .5)
        scroll = int(math.floor(phase_time * self.params["scroll_speed"] + 1e-9))
        lines = self._arrange_text(self.params["text"], self.height, self.params["char_spacing"])
        y = self.params["y_offset"]
        for line in lines:
            x = self.params["x_offset"] - scroll
            for character in line:
                self._paint_glyph(canvas, character, x, y, palette, pulse)
                x += self._glyph_width(character) + self.params["char_spacing"]
            y += 7 + self.params["line_spacing"]
            if y >= self.width:
                break

    @staticmethod
    def _glyph_width(character: str) -> int:
        pattern = EMOJI_PATTERNS.get(character, ())
        return len(pattern[0]) if pattern else 3

    @classmethod
    def _arrange_text(cls, text: str, max_width: int, spacing: int) -> list[list[str]]:
        lines: list[list[str]] = []
        line: list[str] = []
        used = 0
        for character in text:
            width = cls._glyph_width(character)
            needed = width + (spacing if line else 0)
            if line and used + needed > max_width:
                lines.append(line)
                line, used = [], 0
            if character != " ":
                line.append(character)
            used += needed if line else width
        if line:
            lines.append(line)
        return lines

    @staticmethod
    def _paint_glyph(canvas: np.ndarray, character: str, x: int, y: int,
                     palette: Mapping[str, tuple[int, int, int]], pulse: float) -> None:
        role_for = {"F": "message", "H": "highlight", "E": "ink", "M": "ink"}
        for row, pattern_row in enumerate(EMOJI_PATTERNS[character]):
            strip = y + row
            if not 0 <= strip < canvas.shape[0]:
                continue
            for column, cell in enumerate(pattern_row):
                role = role_for.get(cell)
                led = x + column
                if role is None or not 0 <= led < canvas.shape[1]:
                    continue
                alpha = 235 if role == "highlight" else int(round(220 * pulse))
                rgb = np.asarray(palette[role], dtype=np.uint16)
                canvas[strip, led, :3] = ((rgb * alpha + 127) // 255).astype(np.uint8)
                canvas[strip, led, 3] = alpha

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(f"Emoji Message does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS)
        result.update(values)
        text = result["text"]
        if not isinstance(text, str) or not text or len(text) > 64:
            raise ValueError("text must contain 1 to 64 supported characters")
        unsupported = sorted({character for character in text if character != " " and character not in EMOJI_PATTERNS})
        if unsupported:
            raise ValueError(f"text contains unsupported characters: {''.join(unsupported)!r}")
        for name, minimum, maximum in (("x_offset", -137, 137), ("y_offset", -32, 32), ("char_spacing", 0, 8), ("line_spacing", 0, 8)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
        for name, minimum, maximum in (("scroll_speed", 0.0, 24.0), ("pulse_speed", 0.0, 3.0)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not minimum <= float(value) <= maximum:
                raise ValueError(f"{name} must be a finite number from {minimum} to {maximum}")
            result[name] = float(value)
        return result


__all__ = ["EmojiArrangerAnimation"]
