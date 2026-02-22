"""Widget contract for dashboard components."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict

from dashboard.layout import Rect
from dashboard.renderer import FrameBuffer


class Widget(ABC):
    """Base class for dashboard widgets."""

    @abstractmethod
    def consume(self, data_snapshot: Dict[str, Any], now: datetime) -> None:
        """Update internal state from the latest data snapshot."""

    @abstractmethod
    def render(self, buffer: FrameBuffer, layout_slot: Rect) -> None:
        """Render widget contents into the provided layout slot."""
