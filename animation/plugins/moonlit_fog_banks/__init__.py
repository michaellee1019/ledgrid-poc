"""Layered moonlit fog and mountain silhouettes."""
from animation.libraries.procedural_longform import LongformSceneBase

class MoonlitFogBanksAnimation(LongformSceneBase):
    ANIMATION_NAME = "Moonlit Fog Banks"
    ANIMATION_DESCRIPTION = "Slow fog banks reveal a hidden moon above dark ridges"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "fog"
    DEFAULT_MOOD = "moonlit"
    MOODS = ("moonlit", "predawn", "sleeper")

