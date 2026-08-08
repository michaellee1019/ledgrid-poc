"""Tall parallax night landscape viewed from a train."""
from animation.libraries.procedural_longform import LongformSceneBase

class NightTrainWindowsAnimation(LongformSceneBase):
    ANIMATION_NAME = "Night Train Windows"
    ANIMATION_DESCRIPTION = "Moonlit terrain, poles, and warm towns pass at layered speeds"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "train"
    DEFAULT_MOOD = "sleeper"
    MOODS = ("sleeper", "moonlit", "ember")

