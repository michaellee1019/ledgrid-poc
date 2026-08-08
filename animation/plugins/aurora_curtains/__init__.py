"""Overlapping translucent aurora sheets."""

from animation.libraries.procedural_atmospheres import ProceduralAtmosphereBase


class AuroraCurtainsAnimation(ProceduralAtmosphereBase):
    ANIMATION_NAME = "Aurora Curtains"
    ANIMATION_DESCRIPTION = "Independent luminous curtains fold upward through faint stars"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "aurora"
    DEFAULT_MOOD = "boreal"
    DEFAULT_SEED = 4201
    PLANT_MODIFIER_SUPPORT = frozenset({"refract", "shadow", "illuminate", "emitter"})
