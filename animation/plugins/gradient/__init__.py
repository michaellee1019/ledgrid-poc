"""Scene v2 Gradient ambient instrument."""
from types import MappingProxyType
from animation.plugins.ambient_scene import AmbientSceneAnimation
class GradientAnimation(AmbientSceneAnimation):
    ANIMATION_NAME="Gradient"; ANIMATION_DESCRIPTION="A drifting semantic color field"; ANIMATION_VERSION="2.0"; COMPONENT_ID="gradient"; STYLE="gradient"
    DEFAULTS=MappingProxyType({"direction":"vertical","drift":.22,"motion":.72,"seed":6101})
    SCHEMA={"direction":("choice",("vertical","horizontal","diagonal"),None,"Band direction"),"drift":("float",.0,2.,"Color drift tempo"),"motion":("float",0.,1.,"How much the field travels"),"seed":("int",0,999999,"Repeatable field seed")}
