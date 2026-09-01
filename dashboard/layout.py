"""Dashboard layout helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def inset(self, margin_x: int = 0, margin_y: int = 0) -> "Rect":
        nx = self.x + max(0, margin_x)
        ny = self.y + max(0, margin_y)
        nw = max(0, self.width - (2 * max(0, margin_x)))
        nh = max(0, self.height - (2 * max(0, margin_y)))
        return Rect(nx, ny, nw, nh)


class DashboardLayout:
    """Computes top-level dashboard regions with safe margins."""

    def __init__(self, width: int, height: int, safe_margin_x: int = 0, safe_margin_y: int = 0):
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.safe_margin_x = max(0, int(safe_margin_x))
        self.safe_margin_y = max(0, int(safe_margin_y))

    def safe_bounds(self) -> Rect:
        return Rect(0, 0, self.width, self.height).inset(self.safe_margin_x, self.safe_margin_y)

    def regions(self) -> Dict[str, Rect]:
        safe = self.safe_bounds()
        if safe.height < 10:
            return {
                "header": Rect(safe.x, safe.y, safe.width, 0),
                "body": safe,
                "footer": Rect(safe.x, safe.bottom, safe.width, 0),
            }

        header_h = max(1, safe.height // 5)
        footer_h = max(1, safe.height // 6)
        body_h = max(0, safe.height - header_h - footer_h)

        header = Rect(safe.x, safe.y, safe.width, header_h)
        body = Rect(safe.x, header.bottom, safe.width, body_h)
        footer = Rect(safe.x, body.bottom, safe.width, footer_h)
        return {"header": header, "body": body, "footer": footer}
