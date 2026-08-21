"""Semantic palette resolution shared only by atmospheric plugin families."""

from __future__ import annotations

from typing import Any, Sequence, TypeAlias

import numpy as np

ATMOSPHERIC_SEMANTIC_ROLES = (
    "background_low",
    "primary",
    "accent",
)

PaletteColor: TypeAlias = Sequence[int | float] | np.ndarray
AtmosphericPalette: TypeAlias = tuple[PaletteColor, PaletteColor, PaletteColor]


def resolve_atmospheric_palette(
    animation: Any,
    authored_palette: AtmosphericPalette,
) -> AtmosphericPalette:
    """Return authored colors for neutral, semantic roles for an active vibe.

    The policy/capability guard is deliberate: shared renderers also serve
    grade-only plugins, whose authored RGB must remain available for the single
    framework grade pass.  Palette selection is presentation-only and performs
    no parameter, RNG, clock, or simulation mutation.
    """

    context = animation.presentation_context
    if (
        context is None
        or context.vibe_id == "neutral"
        or animation.VIBE_COLOR_POLICY != "semantic"
        or "palette_roles" not in animation.VIBE_CAPABILITIES
    ):
        return authored_palette

    resolved = []
    for index, role in enumerate(ATMOSPHERIC_SEMANTIC_ROLES):
        color = context.palette_roles.get(role, authored_palette[index])
        resolved.append(np.asarray(color, dtype=np.float32))
    return (resolved[0], resolved[1], resolved[2])


def semantic_atmospheric_palette_active(animation: Any) -> bool:
    """Return whether this loaded component owns semantic palette rendering."""

    return (
        animation.VIBE_COLOR_POLICY == "semantic"
        and "palette_roles" in animation.VIBE_CAPABILITIES
    )
