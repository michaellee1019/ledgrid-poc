"""Bitmap text drawing utilities."""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

from dashboard.layout import Rect
from dashboard.renderer import FrameBuffer, Color


Glyph = Iterable[str]
FontMap = Dict[str, Tuple[str, ...]]


def glyph_size(font: FontMap) -> Tuple[int, int]:
    for rows in font.values():
        if rows:
            return len(rows[0]), len(rows)
    return 0, 0


def measure_text(text: str, font: FontMap, spacing: int = 1) -> Tuple[int, int]:
    if not text:
        return 0, 0
    width = 0
    height = 0
    for i, ch in enumerate(text):
        rows = font.get(ch) or font.get("?")
        if not rows:
            continue
        height = max(height, len(rows))
        width += len(rows[0])
        if i < len(text) - 1:
            width += max(0, spacing)
    return width, height


def draw_glyph(
    buffer: FrameBuffer,
    glyph: Glyph,
    x: int,
    y: int,
    color: Color,
    clip_rect: Rect | None = None,
) -> int:
    rows = tuple(glyph)
    if not rows:
        return 0
    glyph_w = len(rows[0])
    for gy, row in enumerate(rows):
        py = y + gy
        if clip_rect and (py < clip_rect.y or py >= clip_rect.bottom):
            continue
        for gx, cell in enumerate(row):
            if cell not in {"1", "#", "X"}:
                continue
            px = x + gx
            if clip_rect and (px < clip_rect.x or px >= clip_rect.right):
                continue
            buffer.set_pixel(px, py, color)
    return glyph_w


def draw_text(
    buffer: FrameBuffer,
    text: str,
    x: int,
    y: int,
    font: FontMap,
    color: Color,
    spacing: int = 1,
    clip_rect: Rect | None = None,
) -> None:
    cursor_x = x
    for i, ch in enumerate(text):
        glyph = font.get(ch) or font.get("?")
        if glyph:
            glyph_w = draw_glyph(buffer, glyph, cursor_x, y, color, clip_rect=clip_rect)
            cursor_x += glyph_w
        if i < len(text) - 1:
            cursor_x += max(0, spacing)
