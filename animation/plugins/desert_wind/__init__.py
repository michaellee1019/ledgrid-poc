"""Evolving dune crests and sparse windblown grains."""

from animation.libraries.atmospheric_palette import (
    resolve_atmospheric_palette,
    semantic_atmospheric_palette_active,
)
from animation.libraries.procedural_longform import LongformSceneBase


class DesertWindAnimation(LongformSceneBase):
    ANIMATION_NAME = "Desert Wind"
    ANIMATION_DESCRIPTION = "Layered dunes erode slowly while grains skim luminous crests"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "desert"
    DEFAULT_MOOD = "ochre"
    MOODS = ("ochre", "mars", "predawn")

    def palette(self):
        return resolve_atmospheric_palette(self, super().palette())

    def on_presentation_context_changed(self, old_context, new_context) -> None:
        super().on_presentation_context_changed(old_context, new_context)
        if semantic_atmospheric_palette_active(self):
            self._last_key = None
