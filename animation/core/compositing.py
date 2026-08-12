"""Version 1 reference math for animation layers and logical coordinates.

This module is deliberately independent of :mod:`animation.core.manager`.  It
freezes the byte-level contract shared by future host composition and receiver
firmware without activating either path.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Optional, Tuple

import numpy as np


RGBA8_MAX = 255
DirtyRange = Tuple[int, int]
DirtyRanges = Tuple[DirtyRange, ...]


def _require_u8(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer from 0 to 255")
    result = int(value)
    if not 0 <= result <= RGBA8_MAX:
        raise ValueError(f"{name} must be from 0 to 255, got {result}")
    return result


def _require_pixel(name: str, value: Sequence[int], channels: int) -> Tuple[int, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be a {channels}-channel integer sequence")
    if len(value) != channels:
        raise ValueError(f"{name} must have {channels} channels, got {len(value)}")
    return tuple(_require_u8(f"{name}[{index}]", channel) for index, channel in enumerate(value))


def _require_premultiplied(name: str, rgba: Sequence[int]) -> Tuple[int, int, int, int]:
    red, green, blue, alpha = _require_pixel(name, rgba, 4)
    if max(red, green, blue) > alpha:
        raise ValueError(
            f"{name} must be premultiplied RGBA8; RGB channels must not exceed "
            f"alpha ({alpha}), got {(red, green, blue)}"
        )
    return red, green, blue, alpha


def round_u8_product(value: int, factor: int) -> int:
    """Scale two RGBA8 quantities using version 1 round-half-up semantics."""

    value = _require_u8("value", value)
    factor = _require_u8("factor", factor)
    return (value * factor + 127) // RGBA8_MAX


def scale_premultiplied_rgba(rgba: Sequence[int], opacity: int) -> Tuple[int, int, int, int]:
    """Apply scene opacity to premultiplied RGBA, scaling all four channels."""

    pixel = _require_premultiplied("rgba", rgba)
    opacity = _require_u8("opacity", opacity)
    return tuple(round_u8_product(channel, opacity) for channel in pixel)  # type: ignore[return-value]


def source_over_rgb(base_rgb: Sequence[int], overlay_rgba: Sequence[int]) -> Tuple[int, int, int]:
    """Blend one premultiplied overlay pixel over an opaque RGB base pixel."""

    base = _require_pixel("base_rgb", base_rgb, 3)
    overlay = _require_premultiplied("overlay_rgba", overlay_rgba)
    inverse_alpha = RGBA8_MAX - overlay[3]
    return tuple(
        min(RGBA8_MAX, overlay[channel] + round_u8_product(base[channel], inverse_alpha))
        for channel in range(3)
    )  # type: ignore[return-value]


def source_over_rgba(
    bottom_rgba: Sequence[int], top_rgba: Sequence[int]
) -> Tuple[int, int, int, int]:
    """Fold premultiplied ``top_rgba`` over ``bottom_rgba`` with one rounding step."""

    bottom = _require_premultiplied("bottom_rgba", bottom_rgba)
    top = _require_premultiplied("top_rgba", top_rgba)
    inverse_alpha = RGBA8_MAX - top[3]
    return tuple(
        min(RGBA8_MAX, top[channel] + round_u8_product(bottom[channel], inverse_alpha))
        for channel in range(4)
    )  # type: ignore[return-value]


def fold_overlays(overlays: Iterable[Sequence[int]]) -> Tuple[int, int, int, int]:
    """Fold logical overlays in declared bottom-to-top order."""

    aggregate = (0, 0, 0, 0)
    for overlay in overlays:
        aggregate = source_over_rgba(aggregate, overlay)
    return aggregate


def normalize_dirty_ranges(
    ranges: Iterable[Sequence[int]], pixel_count: int
) -> DirtyRanges:
    """Validate, sort, and coalesce half-open canonical flat-index ranges."""

    if isinstance(pixel_count, bool) or not isinstance(pixel_count, int):
        raise TypeError("pixel_count must be a non-negative integer")
    if pixel_count < 0:
        raise ValueError(f"pixel_count must be non-negative, got {pixel_count}")

    validated = []
    for index, item in enumerate(ranges):
        if isinstance(item, (str, bytes)) or not isinstance(item, Sequence) or len(item) != 2:
            raise ValueError(f"dirty range {index} must be a two-item (start, end) sequence")
        start, end = item
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, np.integer))
            or isinstance(end, bool)
            or not isinstance(end, (int, np.integer))
        ):
            raise TypeError(f"dirty range {index} bounds must be integers")
        start, end = int(start), int(end)
        if not 0 <= start < end <= pixel_count:
            raise ValueError(
                f"dirty range {index} must satisfy 0 <= start < end <= {pixel_count}; "
                f"got ({start}, {end})"
            )
        validated.append((start, end))

    result: list[DirtyRange] = []
    for start, end in sorted(validated):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return tuple(result)


def union_dirty_ranges(
    *range_groups: Iterable[Sequence[int]], pixel_count: int
) -> DirtyRanges:
    """Return the canonical union used for movement and complete clearing."""

    return normalize_dirty_ranges(
        (item for group in range_groups for item in group), pixel_count
    )


def alpha_coverage_ranges(premultiplied_rgba: np.ndarray) -> DirtyRanges:
    """Convert non-zero alpha coverage into compact canonical ranges."""

    if not isinstance(premultiplied_rgba, np.ndarray):
        raise TypeError("premultiplied_rgba must be a numpy.ndarray")
    if premultiplied_rgba.dtype != np.uint8 or premultiplied_rgba.ndim != 2 or premultiplied_rgba.shape[1:] != (4,):
        raise ValueError(
            "premultiplied_rgba must have dtype uint8 and shape (total_leds, 4); "
            f"got dtype={premultiplied_rgba.dtype}, shape={premultiplied_rgba.shape}"
        )
    covered = np.flatnonzero(premultiplied_rgba[:, 3])
    if covered.size == 0:
        return ()
    starts = covered[np.r_[True, np.diff(covered) != 1]]
    ends = covered[np.r_[np.diff(covered) != 1, True]] + 1
    return tuple((int(start), int(end)) for start, end in zip(starts, ends))


def coverage_dirty_union(
    previous_rgba: np.ndarray, current_rgba: np.ndarray
) -> DirtyRanges:
    """Union previous/new coverage so movement and alpha-zero clears are complete."""

    if not isinstance(previous_rgba, np.ndarray) or not isinstance(current_rgba, np.ndarray):
        raise TypeError("previous_rgba and current_rgba must be numpy.ndarray values")
    if previous_rgba.shape != current_rgba.shape:
        raise ValueError(
            "previous_rgba and current_rgba must have identical shapes; "
            f"got {previous_rgba.shape} and {current_rgba.shape}"
        )
    previous = alpha_coverage_ranges(previous_rgba)
    current = alpha_coverage_ranges(current_rgba)
    return union_dirty_ranges(previous, current, pixel_count=previous_rgba.shape[0])


def logical_flat_index(
    strip: int, led: int, *, strip_count: int, leds_per_strip: int
) -> int:
    """Map global logical ``(strip, led)`` to canonical strip-major index."""

    _validate_geometry(strip_count, leds_per_strip)
    strip = _require_coordinate("strip", strip, strip_count)
    led = _require_coordinate("led", led, leds_per_strip)
    return strip * leds_per_strip + led


def canvas_to_logical_flat_index(
    row: int, column: int, *, strip_count: int, leds_per_strip: int
) -> int:
    """Map image-style ``(row, column)`` to logical ``(led, strip)`` order."""

    return logical_flat_index(
        column, row, strip_count=strip_count, leds_per_strip=leds_per_strip
    )


def receiver_local_index(
    global_strip: int,
    led: int,
    *,
    global_strip_offset: int,
    local_strip_count: int,
    leds_per_strip: int,
) -> int:
    """Map a global logical coordinate into one receiver's local strip-major plane."""

    _validate_geometry(local_strip_count, leds_per_strip)
    if isinstance(global_strip_offset, bool) or not isinstance(global_strip_offset, int):
        raise TypeError("global_strip_offset must be a non-negative integer")
    if global_strip_offset < 0:
        raise ValueError("global_strip_offset must be non-negative")
    global_strip = _require_coordinate(
        "global_strip",
        global_strip,
        global_strip_offset + local_strip_count,
        minimum=global_strip_offset,
    )
    led = _require_coordinate("led", led, leds_per_strip)
    return (global_strip - global_strip_offset) * leds_per_strip + led


def board_flat_slices(
    *, global_strip_count: int, leds_per_strip: int, strips_per_board: int
) -> Tuple[Tuple[int, int], ...]:
    """Return exact half-open canonical slices for equal consecutive receivers."""

    _validate_geometry(global_strip_count, leds_per_strip)
    if isinstance(strips_per_board, bool) or not isinstance(strips_per_board, int):
        raise TypeError("strips_per_board must be a positive integer")
    if strips_per_board <= 0 or global_strip_count % strips_per_board:
        raise ValueError(
            "strips_per_board must be positive and evenly divide global_strip_count"
        )
    pixels_per_board = strips_per_board * leds_per_strip
    return tuple(
        (start, start + pixels_per_board)
        for start in range(0, global_strip_count * leds_per_strip, pixels_per_board)
    )


def _validate_geometry(strip_count: int, leds_per_strip: int) -> None:
    for name, value in (("strip_count", strip_count), ("leds_per_strip", leds_per_strip)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be a positive integer")
        if value <= 0:
            raise ValueError(f"{name} must be positive, got {value}")


def _require_coordinate(name: str, value: int, upper: int, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value < upper:
        raise ValueError(f"{name} must satisfy {minimum} <= {name} < {upper}, got {value}")
    return value


def normalize_optional_dirty_ranges(
    ranges: Optional[Iterable[Sequence[int]]], pixel_count: int
) -> Optional[DirtyRanges]:
    """Normalize frame metadata while preserving ``None`` as 'unknown/all'."""

    if ranges is None:
        return None
    return normalize_dirty_ranges(ranges, pixel_count)
