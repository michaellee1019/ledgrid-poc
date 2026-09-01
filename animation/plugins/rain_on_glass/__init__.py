"""Optically wet window with falling beads and persistent tracks."""

from animation.libraries.procedural_atmospheres import ProceduralAtmosphereBase


class RainOnGlassAnimation(ProceduralAtmosphereBase):
    ANIMATION_NAME = "Rain on Glass"
    ANIMATION_DESCRIPTION = "Refractive beads descend through a dim moonlit or city-lit window"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "rain"
    DEFAULT_MOOD = "moonlit"
    DEFAULT_SEED = 4101
    PLANT_MODIFIER_SUPPORT = frozenset({"shadow", "refract", "emitter"})
    COMPONENT_ID = "rain_on_glass"
    COMPONENT_DEFAULTS = {
        "motion": .42, "density": .46, "mood": "moonlit", "background": "soft",
        "background_level": .18, "source_fps": 30.0, "seed": 4101,
    }
