"""Layered moonlit fog and mountain silhouettes."""

from animation.libraries.atmospheric_palette import (
    resolve_atmospheric_palette,
    semantic_atmospheric_palette_active,
)
from animation.libraries.procedural_longform import LongformSceneBase


class MoonlitFogBanksAnimation(LongformSceneBase):
    ANIMATION_NAME = "Moonlit Fog Banks"
    ANIMATION_DESCRIPTION = "Slow fog banks reveal a hidden moon above dark ridges"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "fog"
    DEFAULT_MOOD = "moonlit"
    MOODS = ("moonlit", "predawn", "sleeper")

    def palette(self):
        return resolve_atmospheric_palette(self, super().palette())

    def on_presentation_context_changed(self, old_context, new_context) -> None:
        super().on_presentation_context_changed(old_context, new_context)
        if semantic_atmospheric_palette_active(self):
            self._last_key = None
