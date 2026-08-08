"""Reusable geometry helpers for mask-driven LED animations.

Logical masks use the controller's canonical flat index convention:
``index = strip * leds_per_strip + led``.  All neighborhood operations happen
in that logical 2-D space, so halos stop at panel edges instead of wrapping to
the next strip.
"""

from typing import Any, Iterable, Mapping, Sequence, Set, Tuple

import numpy as np


def indices_from_payload(
    payload: Mapping[str, Any],
    total_leds: int,
    keys: Sequence[str],
) -> Set[int]:
    """Return validated flat indices from the first list-valued payload key."""
    for key in keys:
        raw_indices = payload.get(key)
        if not isinstance(raw_indices, list):
            continue
        result: Set[int] = set()
        for raw_index in raw_indices:
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if 0 <= index < total_leds:
                result.add(index)
        return result
    return set()


def logical_mask(
    indices: Iterable[int], strip_count: int, leds_per_strip: int
) -> np.ndarray:
    """Build a logical ``(strip_count, leds_per_strip)`` boolean mask."""
    total_leds = strip_count * leds_per_strip
    mask = np.zeros(total_leds, dtype=bool)
    valid = [int(index) for index in indices if 0 <= int(index) < total_leds]
    if valid:
        mask[np.asarray(valid, dtype=np.intp)] = True
    return mask.reshape(strip_count, leds_per_strip)


def dilate_8(mask: np.ndarray) -> np.ndarray:
    """Dilate a 2-D mask by one pixel with an 8-connected neighborhood."""
    if mask.ndim != 2:
        raise ValueError("mask must be two-dimensional")
    padded = np.pad(mask.astype(bool, copy=False), 1, mode="constant")
    height, width = mask.shape
    dilated = np.zeros_like(mask, dtype=bool)
    for row_offset in range(3):
        for column_offset in range(3):
            dilated |= padded[
                row_offset : row_offset + height,
                column_offset : column_offset + width,
            ]
    return dilated


def mask_boundary(mask: np.ndarray) -> np.ndarray:
    """Return core pixels touching the exterior of a 2-D mask."""
    if mask.ndim != 2:
        raise ValueError("mask must be two-dimensional")
    source = mask.astype(bool, copy=False)
    padded = np.pad(source, 1, mode="constant")
    height, width = source.shape
    eroded = np.ones_like(source, dtype=bool)
    for row_offset in range(3):
        for column_offset in range(3):
            eroded &= padded[
                row_offset : row_offset + height,
                column_offset : column_offset + width,
            ]
    return source & ~eroded


def build_halo_weights(
    indices: Iterable[int],
    strip_count: int,
    leds_per_strip: int,
    radius: int = 2,
    falloff: float = 1.4,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return flat core mask and weighted exterior halo for a logical mask.

    The halo contains ``radius`` Chebyshev-distance rings. Core pixels always
    have a halo weight of zero so callers can color cores independently.
    """
    radius = max(0, int(radius))
    falloff = max(0.05, float(falloff))
    core = logical_mask(indices, strip_count, leds_per_strip)
    halo = np.zeros(core.shape, dtype=np.float32)
    frontier = core.copy()
    reached = core.copy()
    for distance in range(1, radius + 1):
        expanded = dilate_8(frontier)
        ring = expanded & ~reached
        weight = (1.0 - distance / (radius + 1.0)) ** falloff
        halo[ring] = np.float32(weight)
        reached |= expanded
        frontier = expanded
    halo[core] = 0.0
    return core.ravel(), halo.ravel()
