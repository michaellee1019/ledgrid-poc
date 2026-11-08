"""Shipped Scene v2 gradient ambient renderer; named instruments subclass the library base."""
from animation.libraries.ambient_scene import AmbientSceneAnimation as AmbientSceneBase


class AmbientSceneAnimation(AmbientSceneBase):
    ANIMATION_NAME = "Ambient Scene"
    ANIMATION_DESCRIPTION = "A drifting semantic color field"
    ANIMATION_VERSION = "2.0"
    COMPONENT_ID = "ambient_scene"
    STYLE = "gradient"
