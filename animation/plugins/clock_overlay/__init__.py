"""Wall-clock-driven premultiplied Clock plane for host composition.

This is intentionally a small bridge between the existing full-scene Clock
and the host compositor.  It owns only an informational plane: Aurora (or a
future opaque renderer) remains responsible for all background pixels.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional

import numpy as np

from animation import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.compositing import OverlayFrame
from animation.plugins.clock import ClockAnimation


def _dirty_ranges(previous: np.ndarray, current: np.ndarray) -> tuple[tuple[int, int], ...]:
    """Return exact flat-pixel ranges changed between two overlay buffers."""
    changed = np.flatnonzero(np.any(previous != current, axis=1))
    if not changed.size:
        return ()
    starts = changed[np.r_[True, np.diff(changed) != 1]]
    ends = changed[np.r_[np.diff(changed) != 1, True]] + 1
    return tuple((int(start), int(end)) for start, end in zip(starts, ends))


class ClockOverlayAnimation(AnimationBase):
    """A cached, current-time digital clock in canonical RGBA8 overlay form."""

    ANIMATION_NAME = "Clock Overlay"
    ANIMATION_DESCRIPTION = "Current wall time as a transparent composed layer"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    COMPONENT_ID = "clock_overlay"
    COMPONENT_VERSION = 1
    PROVIDER = "python"
    ROLE = "overlay"
    FRAME_FORMAT = "rgba_uint8_premultiplied_strip_major"
    TIMING_POLICY = "wall_clock"

    # This overlay deliberately replaces AnimationBase's compatibility
    # parameters.  Pace, brightness, plant geometry, and color grading belong
    # to their owning background/composition/presentation stages, never here.
    DEFAULTS = MappingProxyType({
        "format_24h": False,
        "show_seconds": True,
        "clock_offset_minutes": 0,
        "color": (255, 224, 128),
    })
    # Catalog defaults cross the Composer JSON boundary, so their color uses
    # a JSON array. Runtime defaults stay immutable tuples and are normalized
    # independently below; no mutable descriptor value becomes runtime state.
    DESCRIPTOR_DEFAULTS = MappingProxyType({
        "format_24h": False,
        "show_seconds": True,
        "clock_offset_minutes": 0,
        "color": [255, 224, 128],
    })

    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        provider=PROVIDER,
        role=ROLE,
        timing_policy=TIMING_POLICY,
        defaults=DESCRIPTOR_DEFAULTS,
    )

    # Reuse the current Clock's established 3x5 visual-wall glyphs without
    # importing its full-scene background or preset family.
    FONT = ClockAnimation.FONT

    def __init__(self, controller, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        pixel_count = self.get_pixel_count()
        self._buffers = tuple(np.zeros((pixel_count, 4), dtype=np.uint8) for _ in range(2))
        self._last_pixels = self._buffers[0]
        self._last_key: Optional[tuple[Any, ...]] = None
        self._revision = 0

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        return {
            "format_24h": {"type": "bool", "default": False, "description": "Use 24-hour time"},
            "show_seconds": {"type": "bool", "default": True, "description": "Show seconds and update each second"},
            "clock_offset_minutes": {"type": "int", "min": -720, "max": 840, "default": 0, "description": "Offset from local wall time"},
            "color": {"type": "color", "default": [255, 224, 128], "description": "Opaque clock-mark color"},
        }

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        """Return the provider-qualified wall-clock overlay declaration."""
        return cls.COMPONENT_DESCRIPTOR

    def _clock_now(self) -> datetime:
        """Isolated wall-time source so tests and callers can provide fixed time."""
        return datetime.now().astimezone() + timedelta(minutes=int(self.params["clock_offset_minutes"]))

    @staticmethod
    def _time_key(now: datetime, show_seconds: bool) -> tuple[int, ...]:
        key = (now.year, now.month, now.day, now.hour, now.minute)
        return (*key, now.second) if show_seconds else key

    def generate_frame(self, time_elapsed: float, frame_count: int) -> OverlayFrame:
        """Render from wall time only; vibe/manager pace cannot advance this plane."""
        del time_elapsed, frame_count
        now = self._clock_now()
        show_seconds = bool(self.params["show_seconds"])
        key = (
            self._time_key(now, show_seconds), show_seconds,
            bool(self.params["format_24h"]), self._color_key(),
        )
        if key == self._last_key:
            return OverlayFrame(self._last_pixels, revision=self._revision, changed=False)

        output = self._buffers[1] if self._last_pixels is self._buffers[0] else self._buffers[0]
        output.fill(0)
        self._paint_digital(now, output)
        self._last_key = key
        if np.array_equal(output, self._last_pixels):
            return OverlayFrame(self._last_pixels, revision=self._revision, changed=False)

        ranges = _dirty_ranges(self._last_pixels, output)
        self._last_pixels = output
        self._revision += 1
        return OverlayFrame(output, revision=self._revision, changed=True, dirty_ranges=ranges)

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        """Accept only local overlay controls and invalidate its cached plane."""
        unknown = set(new_params) - set(self.DEFAULTS)
        if unknown:
            raise ValueError(
                f"Clock Overlay does not accept non-local parameters: {sorted(unknown)!r}"
            )
        self.params = self._normalized_parameters({**self.params, **dict(new_params)})
        self._last_key = None

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(
                f"Clock Overlay does not accept non-local parameters: {sorted(unknown)!r}"
            )
        result = dict(cls.DEFAULTS)
        result.update(values)
        for key in ("format_24h", "show_seconds"):
            if not isinstance(result[key], bool):
                raise ValueError(f"{key} must be a bool")
        offset = result["clock_offset_minutes"]
        if isinstance(offset, bool) or not isinstance(offset, int) or not -720 <= offset <= 840:
            raise ValueError("clock_offset_minutes must be an integer from -720 to 840")
        color = result["color"]
        if (
            not isinstance(color, (list, tuple))
            or len(color) != 3
            or any(isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 255 for channel in color)
        ):
            raise ValueError("color must be three integer channels from 0 to 255")
        result["color"] = tuple(color)
        return result

    def _color_key(self) -> tuple[int, int, int]:
        return self.params["color"]

    def _paint_digital(self, now: datetime, output: np.ndarray) -> None:
        visual = output.reshape(self.width, self.height, 4)[:, ::-1]
        hour = now.hour if self.params["format_24h"] else (now.hour % 12 or 12)
        text = f"{hour:02d}:{now.minute:02d}"
        x = (self.width - self._text_width(text)) // 2
        y = self.height // 2 - 3
        color = self._color_key()
        self._draw_text(visual, text, x, y, color)
        if self.params["show_seconds"]:
            seconds = f"{now.second:02d}"
            self._draw_text(visual, seconds, (self.width - self._text_width(seconds)) // 2, y + 8, color)

    @staticmethod
    def _text_width(text: str) -> int:
        return max(0, len(text) * 4 - 1)

    def _draw_text(self, canvas: np.ndarray, text: str, x: int, y: int, color: tuple[int, int, int]) -> None:
        cursor = x
        for character in text:
            for row, bits in enumerate(self.FONT.get(character, self.FONT[" "])):
                for column in range(3):
                    if bits & (1 << (2 - column)):
                        xx, yy = cursor + column, y + row
                        if 0 <= xx < self.width and 0 <= yy < self.height:
                            canvas[xx, yy, :3] = color
                            canvas[xx, yy, 3] = 255
            cursor += 4


__all__ = ["ClockOverlayAnimation"]
