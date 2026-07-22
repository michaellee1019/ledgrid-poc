"""Cached logical coordinate fields shared by dense animations."""

from __future__ import annotations

from functools import lru_cache

import numpy as np


@lru_cache(maxsize=64)
def normalized_axis_positions(
    strip_count: int,
    leds_per_strip: int,
    axis: str,
) -> np.ndarray:
    """Return a read-only flat 0..1 coordinate field for a logical LED axis."""
    strip_count = int(strip_count)
    leds_per_strip = int(leds_per_strip)
    if strip_count <= 0 or leds_per_strip <= 0:
        raise ValueError("grid dimensions must be positive")
    if axis not in {"horizontal", "vertical", "diagonal"}:
        raise ValueError(f"unsupported axis: {axis}")

    x = np.repeat(
        np.linspace(0.0, 1.0, strip_count, dtype=np.float32),
        leds_per_strip,
    )
    y = np.tile(
        np.linspace(1.0, 0.0, leds_per_strip, dtype=np.float32),
        strip_count,
    )
    if axis == "horizontal":
        result = x
    elif axis == "vertical":
        result = y
    else:
        result = (x + y) * np.float32(0.5)
    result.flags.writeable = False
    return result
