"""Clock widget implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from dashboard.fonts import FONT_3X5, FONT_5X7
from dashboard.layout import Rect
from dashboard.renderer import Color, FrameBuffer
from dashboard.text import draw_text, measure_text
from dashboard.widgets.base import Widget


class ClockWidget(Widget):
    def __init__(
        self,
        *,
        use_24h: bool = True,
        show_seconds: bool = True,
        color: Color = (255, 255, 255),
        spacing: int = 1,
    ):
        self.use_24h = use_24h
        self.show_seconds = show_seconds
        self.color = color
        self.spacing = max(0, int(spacing))
        self._text = "--:--"
        self._clock_now = datetime.now()

    def update_settings(
        self,
        *,
        use_24h: bool | None = None,
        show_seconds: bool | None = None,
        color: Color | None = None,
        spacing: int | None = None,
    ) -> None:
        if use_24h is not None:
            self.use_24h = bool(use_24h)
        if show_seconds is not None:
            self.show_seconds = bool(show_seconds)
        if color is not None:
            self.color = color
        if spacing is not None:
            self.spacing = max(0, int(spacing))

    def consume(self, data_snapshot: Dict[str, Any], now: datetime) -> None:
        clock_now = data_snapshot.get("clock_now", now)
        if not isinstance(clock_now, datetime):
            clock_now = now
        self._clock_now = clock_now

        if self.use_24h:
            fmt = "%H:%M:%S" if self.show_seconds else "%H:%M"
            self._text = clock_now.strftime(fmt)
            return

        fmt = "%I:%M:%S %p" if self.show_seconds else "%I:%M %p"
        self._text = clock_now.strftime(fmt).lstrip("0")

    def render(self, buffer: FrameBuffer, layout_slot: Rect) -> None:
        if layout_slot.width <= 0 or layout_slot.height <= 0:
            return

        display_text, font = self._select_render_text(layout_slot)
        text_w, text_h = measure_text(display_text, font, spacing=self.spacing)

        start_x = layout_slot.x + max(0, (layout_slot.width - text_w) // 2)
        start_y = layout_slot.y + max(0, (layout_slot.height - text_h) // 2)
        draw_text(
            buffer,
            display_text,
            start_x,
            start_y,
            font,
            self.color,
            spacing=self.spacing,
            clip_rect=layout_slot,
        )

    def _select_render_text(self, layout_slot: Rect):
        fonts = [FONT_5X7, FONT_3X5] if layout_slot.height >= 8 else [FONT_3X5, FONT_5X7]
        for candidate in self._text_candidates():
            for font in fonts:
                width, _ = measure_text(candidate, font, spacing=self.spacing)
                if width <= layout_slot.width:
                    return candidate, font
        return self._text_candidates()[-1], FONT_3X5

    def _text_candidates(self):
        now = self._clock_now
        if self.use_24h:
            candidates = []
            if self.show_seconds:
                candidates.append(now.strftime("%H:%M:%S"))
            candidates.append(now.strftime("%H:%M"))
            return candidates

        candidates = []
        if self.show_seconds:
            candidates.append(now.strftime("%I:%M:%S %p").lstrip("0"))
        candidates.append(now.strftime("%I:%M %p").lstrip("0"))
        candidates.append(now.strftime("%I:%M").lstrip("0"))
        return candidates
