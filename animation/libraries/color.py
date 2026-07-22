"""Small RGB operations shared by pixel-art animations."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple


Rgb = Tuple[int, int, int]


def parameter_rgb(
    params: Mapping[str, Any],
    prefix: str,
    default: Rgb,
    *,
    clamp_channels: bool = True,
) -> Rgb:
    """Read ``<prefix>_red/green/blue`` integer channels from parameters."""
    channels = tuple(
        int(params.get(f"{prefix}_{name}", fallback))
        for name, fallback in zip(("red", "green", "blue"), default)
    )
    if not clamp_channels:
        return channels
    return tuple(max(0, min(255, channel)) for channel in channels)


def scale_rgb(
    color: Rgb,
    scale: float,
    *,
    scale_bounds: Optional[Tuple[float, float]] = None,
    clamp_lower: bool = True,
) -> Rgb:
    """Scale RGB channels with explicit bounds and integer truncation."""
    if scale_bounds is not None:
        lower, upper = scale_bounds
        scale = max(float(lower), min(float(upper), float(scale)))
    scaled = tuple(min(255, int(channel * scale)) for channel in color)
    if clamp_lower:
        return tuple(max(0, channel) for channel in scaled)
    return scaled


def mix_rgb(base: Rgb, overlay: Rgb, mix: float) -> Rgb:
    """Linearly interpolate RGB tuples with a clamped overlay ratio."""
    ratio = max(0.0, min(1.0, float(mix)))
    return tuple(
        int(base[channel] * (1.0 - ratio) + overlay[channel] * ratio)
        for channel in range(3)
    )
