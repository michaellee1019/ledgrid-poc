"""Browser-safe frame contracts and coverage tracking for overlay plugins."""

import numpy as np

from animation.core.presentation_contracts import BaseFrame, OverlayFrame


def _coverage_ranges(premultiplied_rgba):
    if not isinstance(premultiplied_rgba, np.ndarray):
        raise TypeError("premultiplied_rgba must be a numpy.ndarray")
    if (
        premultiplied_rgba.dtype != np.uint8
        or premultiplied_rgba.ndim != 2
        or premultiplied_rgba.shape[1:] != (4,)
    ):
        raise ValueError(
            "premultiplied_rgba must have dtype uint8 and shape (total_leds, 4); "
            f"got dtype={premultiplied_rgba.dtype}, shape={premultiplied_rgba.shape}"
        )
    return premultiplied_rgba[:, 3] != 0


def coverage_dirty_union(previous_rgba, current_rgba):
    """Return compact ranges covering pixels visible before or after a frame."""
    if not isinstance(previous_rgba, np.ndarray) or not isinstance(current_rgba, np.ndarray):
        raise TypeError("previous_rgba and current_rgba must be numpy.ndarray values")
    if previous_rgba.shape != current_rgba.shape:
        raise ValueError(
            "previous_rgba and current_rgba must have identical shapes; "
            f"got {previous_rgba.shape} and {current_rgba.shape}"
        )
    covered = np.flatnonzero(
        _coverage_ranges(previous_rgba) | _coverage_ranges(current_rgba)
    )
    if covered.size == 0:
        return ()
    starts = covered[np.r_[True, np.diff(covered) != 1]]
    ends = covered[np.r_[np.diff(covered) != 1, True]] + 1
    return tuple((int(start), int(end)) for start, end in zip(starts, ends))


__all__ = ("BaseFrame", "OverlayFrame", "coverage_dirty_union")
