"""Reusable rendering and pixel-art primitives shared by animations."""

from .color import mix_rgb, parameter_rgb, scale_rgb
from .mask_effects import (
    build_halo_weights,
    dilate_8,
    indices_from_payload,
    logical_mask,
    mask_boundary,
)
from .palette_field import AnimatedPaletteField
from .spatial import normalized_axis_positions

__all__ = [
    "AnimatedPaletteField",
    "build_halo_weights",
    "dilate_8",
    "indices_from_payload",
    "logical_mask",
    "mask_boundary",
    "mix_rgb",
    "normalized_axis_positions",
    "parameter_rgb",
    "scale_rgb",
]
