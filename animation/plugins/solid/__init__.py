"""Scene v2 Solid ambient instrument."""
from types import MappingProxyType
from animation.plugins.ambient_scene import AmbientSceneAnimation
class SolidColorAnimation(AmbientSceneAnimation):
    ANIMATION_NAME="Solid"; ANIMATION_DESCRIPTION="A calm semantic wash with optional breath"; ANIMATION_VERSION="2.0"; COMPONENT_ID="solid"; STYLE="solid"
    DEFAULTS=MappingProxyType({"glow":.68,"breath":.0,"seed":6103})
    SCHEMA={"glow":("float",.05,1.,"Field fullness"),"breath":("float",0.,3.,"Gentle breathing tempo"),"seed":("int",0,999999,"Repeatable field seed")}
