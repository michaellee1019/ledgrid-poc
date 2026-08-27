"""Portable presentation contracts required by browser animation plugins.

The browser bundle intentionally carries the repository plugin and rendering
code, but not the host's scene manager or receiver protocol graph.  This file
keeps the two contracts imported by ``clock_overlay`` byte-for-byte compatible
at their boundary: the timing enum and validated premultiplied RGBA frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Tuple

import numpy as np


FRAME_CONTRACT_SCHEMA = "ledgrid.layer-frame"
FRAME_CONTRACT_VERSION = 1


class TimingAdapter(str, Enum):
    LEGACY_SPEED_PARAM = "legacy_speed_param"
    SCALED_CONTEXT = "scaled_context"
    WALL_CLOCK = "wall_clock"


def _normalize_dirty_ranges(
    ranges: Optional[Tuple[Tuple[int, int], ...]], pixel_count: int
) -> Optional[Tuple[Tuple[int, int], ...]]:
    if ranges is None:
        return None
    validated = []
    for index, item in enumerate(ranges):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f"dirty range {index} must contain (start, end)")
        start, end = item
        if isinstance(start, bool) or isinstance(end, bool):
            raise TypeError(f"dirty range {index} bounds must be integers")
        start, end = int(start), int(end)
        if not 0 <= start < end <= pixel_count:
            raise ValueError(
                f"dirty range {index} must satisfy 0 <= start < end <= {pixel_count}"
            )
        validated.append((start, end))
    normalized = []
    for start, end in sorted(validated):
        if normalized and start <= normalized[-1][1]:
            normalized[-1] = (normalized[-1][0], max(normalized[-1][1], end))
        else:
            normalized.append((start, end))
    return tuple(normalized)


@dataclass(frozen=True)
class OverlayFrame:
    """Canonical, C-contiguous, premultiplied RGBA8 overlay plane."""

    pixels: np.ndarray
    revision: int
    changed: bool = True
    dirty_ranges: Optional[Tuple[Tuple[int, int], ...]] = None
    contract_version: int = FRAME_CONTRACT_VERSION
    schema: str = FRAME_CONTRACT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != FRAME_CONTRACT_SCHEMA:
            raise ValueError(f"frame schema must be {FRAME_CONTRACT_SCHEMA!r}")
        if self.contract_version != FRAME_CONTRACT_VERSION:
            raise ValueError(
                f"frame contract_version must be {FRAME_CONTRACT_VERSION}"
            )
        if not isinstance(self.changed, bool):
            raise TypeError("changed must be a bool")
        if (
            not isinstance(self.pixels, np.ndarray)
            or self.pixels.dtype != np.uint8
            or self.pixels.ndim != 2
            or self.pixels.shape[1:] != (4,)
            or self.pixels.shape[0] <= 0
            or not self.pixels.flags.c_contiguous
        ):
            raise ValueError(
                "pixels must be a non-empty C-contiguous uint8 array with shape (N, 4)"
            )
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("revision must be a non-negative integer")
        if self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        alpha = self.pixels[:, 3:4]
        invalid = np.argwhere(self.pixels[:, :3] > alpha)
        if invalid.size:
            pixel, channel = (int(value) for value in invalid[0])
            raise ValueError(
                "OverlayFrame pixels must be premultiplied RGBA8; "
                f"pixel {pixel} channel {channel} exceeds alpha {int(alpha[pixel, 0])}"
            )
        dirty_ranges = _normalize_dirty_ranges(
            self.dirty_ranges, self.pixels.shape[0]
        )
        if not self.changed and dirty_ranges:
            raise ValueError("changed=False cannot carry non-empty dirty_ranges")
        object.__setattr__(self, "dirty_ranges", dirty_ranges)


__all__ = ["OverlayFrame", "TimingAdapter"]
