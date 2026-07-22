"""Dark tide with sparse plankton wakes."""

from animation.libraries.procedural_atmospheres import ProceduralAtmosphereBase


class TidalBioluminescenceAnimation(ProceduralAtmosphereBase):
    ANIMATION_NAME = "Tidal Bioluminescence"
    ANIMATION_DESCRIPTION = "Slow swells disturb sparse plankton into curling cyan wakes"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "tidal"
    DEFAULT_MOOD = "moonlit"
    DEFAULT_SEED = 4501
    PLANT_MODIFIER_SUPPORT = frozenset({"refract", "illuminate", "emitter"})
