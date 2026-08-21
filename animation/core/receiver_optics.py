"""Shared fixed-point receiver-safe color optics.

The host reference and ESP32 implementation consume the same 257-entry Q14
matrix table.  Keeping the transform entirely integer after table selection
makes host/receiver parity independent of either platform's ``libm``.
"""

from __future__ import annotations

import math
import operator
from typing import Final

import numpy as np


HUE_STRENGTH_MAX: Final = 256
HUE_MATRIX_SHIFT: Final = 14
HUE_MATRIX_SCALE: Final = 1 << HUE_MATRIX_SHIFT
HUE_MATRIX_ROUND: Final = HUE_MATRIX_SCALE // 2

# These are the established AnimationBase YIQ coefficients.  They deliberately
# remain decimal constants rather than being replaced by a different color
# space approximation during the fixed-point migration.
_RGB_TO_YIQ: Final = (
    (0.299, 0.587, 0.114),
    (0.596, -0.274, -0.322),
    (0.211, -0.523, 0.312),
)
_YIQ_TO_RGB: Final = (
    (1.0, 0.956, 0.621),
    (1.0, -0.272, -0.647),
    (1.0, -1.106, 1.703),
)


def _round_q14(value: float) -> int:
    """Quantize one coefficient to signed Q14, with ties away from zero."""

    scaled = value * HUE_MATRIX_SCALE
    if scaled >= 0.0:
        return math.floor(scaled + 0.5)
    return math.ceil(scaled - 0.5)


def _build_hue_rotation_matrix_q14(
    strength_q8_8: int,
) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    if strength_q8_8 == 0:
        # The rounded legacy YIQ bases are close to, but not algebraically, exact
        # inverses.  The disabled endpoint is an explicit byte-exact identity.
        return (
            (HUE_MATRIX_SCALE, 0, 0),
            (0, HUE_MATRIX_SCALE, 0),
            (0, 0, HUE_MATRIX_SCALE),
        )

    angle = math.pi * strength_q8_8 / HUE_STRENGTH_MAX
    cosine = math.cos(angle)
    sine = math.sin(angle)
    rotation = (
        (1.0, 0.0, 0.0),
        (0.0, cosine, -sine),
        (0.0, sine, cosine),
    )
    rotated_yiq = tuple(
        tuple(
            sum(rotation[row][inner] * _RGB_TO_YIQ[inner][column]
                for inner in range(3))
            for column in range(3)
        )
        for row in range(3)
    )
    rgb_matrix = tuple(
        tuple(
            sum(_YIQ_TO_RGB[row][inner] * rotated_yiq[inner][column]
                for inner in range(3))
            for column in range(3)
        )
        for row in range(3)
    )
    return tuple(
        tuple(_round_q14(coefficient) for coefficient in row)
        for row in rgb_matrix
    )


HUE_ROTATION_MATRICES_Q14: Final = tuple(
    _build_hue_rotation_matrix_q14(strength)
    for strength in range(HUE_STRENGTH_MAX + 1)
)

_HUE_ROTATION_MATRICES_ARRAY = np.asarray(
    HUE_ROTATION_MATRICES_Q14, dtype=np.int32
)
_HUE_ROTATION_MATRICES_ARRAY.setflags(write=False)


def _validated_strength(strength_q8_8: int) -> int:
    if isinstance(strength_q8_8, (bool, np.bool_)):
        raise TypeError("hue strength must be an integer Q8.8 value")
    try:
        strength = operator.index(strength_q8_8)
    except TypeError as exc:
        raise TypeError("hue strength must be an integer Q8.8 value") from exc
    if not 0 <= strength <= HUE_STRENGTH_MAX:
        raise ValueError(
            f"hue strength must be between 0 and {HUE_STRENGTH_MAX}"
        )
    return strength


def hue_rotation_matrix_q14(
    strength_q8_8: int,
) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    """Return the immutable signed-Q14 RGB matrix for one Q8.8 strength."""

    return HUE_ROTATION_MATRICES_Q14[_validated_strength(strength_q8_8)]


def apply_hue_shift_u8(
    pixels: np.ndarray,
    strength_q8_8: int,
    target_mask: np.ndarray,
) -> np.ndarray:
    """Apply the fixed-point hue rotation in place at selected RGB pixels.

    Each channel uses ``floor((matrix_row dot rgb + 8192) / 16384)`` and is
    then clamped to an unsigned byte.  Strength zero intentionally returns
    before inspecting either the pixel array or mask so the canonical no-op
    path performs no allocation or geometry work.
    """

    strength = _validated_strength(strength_q8_8)
    if strength == 0:
        return pixels

    if not isinstance(pixels, np.ndarray) or pixels.dtype != np.uint8:
        raise TypeError("pixels must be a numpy uint8 array")
    if pixels.ndim != 2 or pixels.shape[1] != 3:
        raise ValueError(f"pixels must have shape (pixel_count, 3), got {pixels.shape}")
    if not isinstance(target_mask, np.ndarray) or target_mask.dtype != np.bool_:
        raise TypeError("target_mask must be a numpy bool array")
    if target_mask.shape != (pixels.shape[0],):
        raise ValueError(
            "target_mask must have shape "
            f"({pixels.shape[0]},), got {target_mask.shape}"
        )
    if not np.any(target_mask):
        return pixels

    selected = pixels[target_mask].astype(np.int32)
    transformed = selected @ _HUE_ROTATION_MATRICES_ARRAY[strength].T
    transformed += HUE_MATRIX_ROUND
    np.floor_divide(transformed, HUE_MATRIX_SCALE, out=transformed)
    np.clip(transformed, 0, 255, out=transformed)
    pixels[target_mask] = transformed
    return pixels
