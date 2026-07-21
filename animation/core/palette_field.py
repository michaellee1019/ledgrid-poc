"""Reusable, allocation-free animated palette fields for LED backgrounds."""

from __future__ import annotations

from typing import Optional

import numpy as np


class AnimatedPaletteField:
    """Render a moving indexed-color interference field from cached geometry."""

    def __init__(
        self,
        width: int,
        height: int,
        palette: np.ndarray,
        *,
        x_scale: float = 13.0,
        y_scale: float = 3.7,
        radial_scale: float = 17.0,
        radial_y_scale: float = 0.22,
        center_y: float = 0.52,
    ):
        palette = np.asarray(palette, dtype=np.uint8)
        if palette.shape != (256, 3):
            raise ValueError("palette must be uint8 with shape (256, 3)")

        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.palette = palette

        grid_y, grid_x = np.indices((self.height, self.width), dtype=np.float32)
        radial = np.hypot(
            grid_x - self.width * 0.5,
            (grid_y - self.height * center_y) * radial_y_scale,
        )
        self._phase = np.remainder(
            grid_x * x_scale + grid_y * y_scale + radial * radial_scale,
            256.0,
        ).astype(np.uint16)
        self._indices = np.empty((self.height, self.width), dtype=np.uint16)
        self._layer = np.empty((self.height, self.width, 3), dtype=np.uint8)

    def render(
        self,
        time_elapsed: float,
        *,
        ticks_per_second: float = 100.0,
        out: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Return the field at ``time_elapsed`` without per-frame allocations."""
        tick = int(max(0.0, time_elapsed) * ticks_per_second) & 255
        np.add(self._phase, tick, out=self._indices)
        np.bitwise_and(self._indices, 255, out=self._indices)
        target = self._layer if out is None else out
        if target.shape != self._layer.shape or target.dtype != np.uint8:
            raise ValueError("output must match the field dimensions and use uint8")
        np.take(self.palette, self._indices, axis=0, out=target)
        return target
