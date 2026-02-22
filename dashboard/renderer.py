"""Frame composition helpers for dashboard rendering."""

from __future__ import annotations

from typing import List, Tuple


Color = Tuple[int, int, int]


def _clamp_channel(value: int) -> int:
    return max(0, min(255, int(value)))


class FrameBuffer:
    """Simple 2D frame buffer mapped to strip-major LED output."""

    def __init__(self, width: int, height: int):
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self._pixels: List[List[Color]] = [
            [(0, 0, 0) for _ in range(self.width)]
            for _ in range(self.height)
        ]

    def clear(self, color: Color = (0, 0, 0)) -> None:
        fill = (_clamp_channel(color[0]), _clamp_channel(color[1]), _clamp_channel(color[2]))
        for y in range(self.height):
            row = self._pixels[y]
            for x in range(self.width):
                row[x] = fill

    def set_pixel(self, x: int, y: int, color: Color) -> None:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        self._pixels[y][x] = (
            _clamp_channel(color[0]),
            _clamp_channel(color[1]),
            _clamp_channel(color[2]),
        )

    def get_pixel(self, x: int, y: int) -> Color:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return (0, 0, 0)
        return self._pixels[y][x]

    def to_frame(self, *, reverse_y: bool = True, serpentine: bool = False) -> List[Color]:
        """Flatten logical XY pixels into strip-major frame data."""
        frame: List[Color] = [(0, 0, 0)] * (self.width * self.height)
        for x in range(self.width):
            for y in range(self.height):
                physical_y = (self.height - 1 - y) if reverse_y else y
                if serpentine and (x % 2 == 1):
                    physical_y = self.height - 1 - physical_y
                idx = x * self.height + physical_y
                frame[idx] = self._pixels[y][x]
        return frame
