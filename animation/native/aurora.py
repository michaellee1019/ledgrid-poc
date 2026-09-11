"""Canonical Scene palette adapter for the receiver-native Aurora source."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from animation.core.presentation_contracts import VIBE_PALETTE_ROLES


_PALETTES = MappingProxyType({
    "neutral": MappingProxyType({
        "background_low": (2, 10, 18), "background_mid": (13, 79, 75),
        "background_high": (24, 148, 132), "primary": (24, 148, 132),
        "secondary": (87, 202, 175), "accent": (150, 255, 218),
        "hud": (150, 255, 218), "warning": (255, 202, 92),
    }),
    "mist": MappingProxyType({
        "background_low": (3, 9, 20), "background_mid": (22, 56, 81),
        "background_high": (40, 102, 142), "primary": (40, 102, 142),
        "secondary": (105, 165, 194), "accent": (170, 228, 245),
        "hud": (170, 228, 245), "warning": (255, 202, 92),
    }),
    "spectrum": MappingProxyType({
        "background_low": (15, 3, 34), "background_mid": (50, 21, 114),
        "background_high": (84, 38, 194), "primary": (84, 38, 194),
        "secondary": (69, 138, 212), "accent": (54, 238, 230),
        "hud": (54, 238, 230), "warning": (255, 202, 92),
    }),
    "ember": MappingProxyType({
        "background_low": (18, 3, 2), "background_mid": (87, 23, 8),
        "background_high": (156, 42, 14), "primary": (156, 42, 14),
        "secondary": (206, 122, 53), "accent": (255, 202, 92),
        "hud": (255, 202, 92), "warning": (255, 72, 64),
    }),
})


def canonical_palette_roles(palette_id: str) -> Mapping[str, tuple[int, int, int]]:
    """Return all ABI palette roles in canonical order for one Scene palette."""

    palette = _PALETTES.get(str(palette_id), _PALETTES["neutral"])
    return MappingProxyType({role: palette[role] for role in VIBE_PALETTE_ROLES})


__all__ = ["canonical_palette_roles"]
