"""Evolving dune crests and sparse windblown grains."""
from animation.libraries.procedural_longform import LongformSceneBase

class DesertWindAnimation(LongformSceneBase):
    ANIMATION_NAME = "Desert Wind"
    ANIMATION_DESCRIPTION = "Layered dunes erode slowly while grains skim luminous crests"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "desert"
    DEFAULT_MOOD = "ochre"
    MOODS = ("ochre", "mars", "predawn")

