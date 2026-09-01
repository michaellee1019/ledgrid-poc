"""Scene orchestration for dashboard rendering."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from dashboard.layout import DashboardLayout, Rect
from dashboard.renderer import FrameBuffer
from dashboard.widgets.clock import ClockWidget


class DashboardScene:
    """Coordinates widget state updates and rendering."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.layout = DashboardLayout(width, height)
        self.clock = ClockWidget()
        self._snapshot: Dict[str, Any] = {}

    def update_settings(
        self,
        *,
        use_24h: bool,
        show_seconds: bool,
        text_color,
        safe_margin_x: int = 0,
        safe_margin_y: int = 0,
    ) -> None:
        self.layout = DashboardLayout(
            self.width,
            self.height,
            safe_margin_x=safe_margin_x,
            safe_margin_y=safe_margin_y,
        )
        self.clock.update_settings(
            use_24h=use_24h,
            show_seconds=show_seconds,
            color=text_color,
        )

    def consume(self, now: datetime) -> None:
        self._snapshot = {"clock_now": now}
        self.clock.consume(self._snapshot, now)

    def render(self, buffer: FrameBuffer) -> None:
        regions = self.layout.regions()
        header = regions.get("header", Rect(0, 0, self.width, self.height))
        if header.height <= 0:
            header = Rect(0, 0, self.width, self.height)
        self.clock.render(buffer, header)
