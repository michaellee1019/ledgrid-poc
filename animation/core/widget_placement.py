"""Deterministic, installation-owned placement for Scene v2 Widgets.

Widget source planes are full-wall RGBA buffers. Their visible pixels may be
translated by the aggregate foreground compositor, so placement is resolved
from the plane's alpha footprint rather than from any look-owned calibration
data. Callers provide the installed wall's ``safe_flat`` view (normally
``PlantMaskGeometry.safe_flat``); the scene stores only auto or manual intent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from animation.core.compositing import OverlayFrame


@dataclass(frozen=True)
class WidgetPlacementResolution:
    """The effective foreground translation and any operator-facing caveat."""

    strip_translation: int
    led_translation: int
    clamped: bool = False
    used_fallback: bool = False
    overlap_pixels: int = 0
    plant_overlap_pixels: int = 0
    widget_overlap_pixels: int = 0
    warning: str | None = None


def resolve_widget_placement(
    placement: Mapping[str, Any], frame: OverlayFrame, *,
    strip_count: int, leds_per_strip: int, safe_flat: np.ndarray,
    reserved_flat: np.ndarray | None = None,
) -> WidgetPlacementResolution:
    """Resolve one Widget against installation-owned safe space.

    Manual positions are clamped to retain every visible pixel on the wall.
    Auto positions avoid both clearance geometry and footprints reserved by
    earlier Widgets. Stable scene order therefore gives each Widget a stable
    non-overlapping opportunity before the deterministic fallback is used.
    """

    safe = _safe_canvas(safe_flat, strip_count, leds_per_strip)
    reserved = _reserved_canvas(reserved_flat, strip_count, leds_per_strip)
    occupied = frame.pixels.reshape(strip_count, leds_per_strip, 4)[..., 3] != 0
    if not np.any(occupied):
        return WidgetPlacementResolution(0, 0)
    strip_positions, led_positions = np.nonzero(occupied)
    min_strip, max_strip = int(strip_positions.min()), int(strip_positions.max())
    min_led, max_led = int(led_positions.min()), int(led_positions.max())
    strip_bounds = (-min_strip, strip_count - 1 - max_strip)
    led_bounds = (-min_led, leds_per_strip - 1 - max_led)

    if placement["mode"] == "manual":
        requested_strip = int(placement["strip_translation"])
        requested_led = int(placement["led_translation"])
        strip = min(max(requested_strip, strip_bounds[0]), strip_bounds[1])
        led = min(max(requested_led, led_bounds[0]), led_bounds[1])
        clamped = (strip, led) != (requested_strip, requested_led)
        plant_overlap, widget_overlap = _overlap_counts(
            occupied, safe, reserved, min_strip, max_strip, min_led, max_led, strip, led,
        )
        overlap = plant_overlap + widget_overlap
        warnings: list[str] = []
        if clamped:
            warnings.append("Widget position was clamped to keep its visible content on the wall.")
        if plant_overlap:
            warnings.append("Widget position overlaps installation clearance geometry.")
        if widget_overlap:
            warnings.append("Widget position overlaps an earlier Widget.")
        return WidgetPlacementResolution(
            strip, led, clamped=clamped, overlap_pixels=overlap,
            plant_overlap_pixels=plant_overlap, widget_overlap_pixels=widget_overlap,
            warning=" ".join(warnings) or None,
        )
    if placement["mode"] != "auto":
        raise ValueError("widget placement mode must be auto or manual")

    candidates = (
        (strip, led)
        for strip in range(strip_bounds[0], strip_bounds[1] + 1)
        for led in range(led_bounds[0], led_bounds[1] + 1)
    )

    def score(candidate: tuple[int, int]) -> tuple[int, int, int, int, int, int]:
        strip, led = candidate
        plant_overlap, widget_overlap = _overlap_counts(
            occupied, safe, reserved, min_strip, max_strip, min_led, max_led, strip, led,
        )
        # Plant clearance is an installation constraint, so prefer preserving
        # it before resolving an otherwise equivalent Widget collision.
        return (plant_overlap, widget_overlap, abs(strip) + abs(led), abs(led), strip, led)

    strip, led = min(candidates, key=score)
    plant_overlap, widget_overlap = _overlap_counts(
        occupied, safe, reserved, min_strip, max_strip, min_led, max_led, strip, led,
    )
    overlap = plant_overlap + widget_overlap
    warnings = ["No fully clear placement is available; using the least-overlapping position."] if overlap else []
    if plant_overlap:
        warnings.append("Placement overlaps installation clearance geometry.")
    if widget_overlap:
        warnings.append("Placement overlaps an earlier Widget.")
    return WidgetPlacementResolution(
        strip, led, used_fallback=bool(overlap), overlap_pixels=overlap,
        plant_overlap_pixels=plant_overlap, widget_overlap_pixels=widget_overlap,
        warning=" ".join(warnings) or None,
    )


def _safe_canvas(safe_flat: np.ndarray, strip_count: int, leds_per_strip: int) -> np.ndarray:
    if not isinstance(safe_flat, np.ndarray) or safe_flat.dtype != np.bool_:
        raise TypeError("widget safe geometry must be a boolean numpy array")
    total = strip_count * leds_per_strip
    if safe_flat.shape != (total,) or not safe_flat.flags.c_contiguous:
        raise ValueError(f"widget safe geometry must be C-contiguous bool ({total},)")
    return safe_flat.reshape(strip_count, leds_per_strip)


def _reserved_canvas(reserved_flat: np.ndarray | None, strip_count: int, leds_per_strip: int) -> np.ndarray:
    if reserved_flat is None:
        return np.zeros((strip_count, leds_per_strip), dtype=np.bool_)
    return _safe_canvas(reserved_flat, strip_count, leds_per_strip)


def _overlap_counts(occupied: np.ndarray, safe: np.ndarray, reserved: np.ndarray,
                    min_strip: int, max_strip: int, min_led: int, max_led: int,
                    strip: int, led: int) -> tuple[int, int]:
    safe_destination = safe[
        min_strip + strip:max_strip + strip + 1,
        min_led + led:max_led + led + 1,
    ]
    reserved_destination = reserved[
        min_strip + strip:max_strip + strip + 1,
        min_led + led:max_led + led + 1,
    ]
    footprint = occupied[min_strip:max_strip + 1, min_led:max_led + 1]
    return (
        int(np.count_nonzero(footprint & ~safe_destination)),
        int(np.count_nonzero(footprint & reserved_destination)),
    )


def translated_widget_coverage(frame: OverlayFrame, *, strip_count: int, leds_per_strip: int,
                               strip_translation: int, led_translation: int) -> np.ndarray:
    """Return the clipped flat alpha footprint reserved by one placed Widget."""
    occupied = frame.pixels.reshape(strip_count, leds_per_strip, 4)[..., 3] != 0
    strips, leds = np.nonzero(occupied)
    destination_strips = strips + strip_translation
    destination_leds = leds + led_translation
    valid = (
        (destination_strips >= 0) & (destination_strips < strip_count)
        & (destination_leds >= 0) & (destination_leds < leds_per_strip)
    )
    coverage = np.zeros(strip_count * leds_per_strip, dtype=np.bool_)
    coverage[destination_strips[valid] * leds_per_strip + destination_leds[valid]] = True
    return coverage


__all__ = ["WidgetPlacementResolution", "resolve_widget_placement", "translated_widget_coverage"]
