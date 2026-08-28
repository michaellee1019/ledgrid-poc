"""Dependency-neutral component-parameter ownership boundaries."""

from __future__ import annotations


SCENE_EXTERNAL_COMPONENT_PARAMETERS = frozenset((
    "plant_aware", "plant_modifiers", "vibe", "output",
))
