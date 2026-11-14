"""Layered moonlit fog and mountain silhouettes."""
from animation.libraries.procedural_longform import LongformSceneBase

class MoonlitFogBanksAnimation(LongformSceneBase):
    PREFERRED_PARAMETERS = ('density', 'motion', 'background_level')
    INTERACTION_TYPES = frozenset({"primary"})
    POINT_ACTION_LABELS = {"primary": "Part the fog"}
    NIMBUS_POINT_BINDINGS = {"primary": ("a", 3)}
    POINT_PULSE_SECONDS = 1.2

    ANIMATION_NAME = "Moonlit Fog Banks"
    ANIMATION_DESCRIPTION = "Slow fog banks reveal a hidden moon above dark ridges"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "fog"
    DEFAULT_MOOD = "moonlit"
    MOODS = ("moonlit", "predawn", "sleeper")
    COMPONENT_ID = "moonlit_fog_banks"
    SCENE_SEMANTIC_PALETTE = True
    COMPONENT_DEFAULTS = {
        "motion": .35, "density": .5, "mood": "moonlit", "background": "soft",
        "background_level": .18, "render_fps": 24, "seed": 7101,
    }
