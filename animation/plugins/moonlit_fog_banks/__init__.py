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
    COMPONENT_ID = "moonlit_fog_banks"
    COMPONENT_DEFAULTS = {
        "motion": .35, "density": .5, "mood": "moonlit", "background": "soft",
        "background_level": .18, "render_fps": 24, "seed": 7101,
    }
