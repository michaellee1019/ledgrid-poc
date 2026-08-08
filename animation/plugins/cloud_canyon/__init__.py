"""Slow fog banks enclosing a vertical moonlit canyon."""

from animation.libraries.procedural_atmospheres import ProceduralAtmosphereBase


class CloudCanyonAnimation(ProceduralAtmosphereBase):
    ANIMATION_NAME = "Cloud Canyon"
    ANIMATION_DESCRIPTION = "Billowing cloud banks occlude broad shafts leaking through a tall canyon"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "cloud"
    DEFAULT_MOOD = "moonlit"
    DEFAULT_SEED = 4301
    PLANT_MODIFIER_SUPPORT = frozenset({"shadow", "refract", "emitter"})
