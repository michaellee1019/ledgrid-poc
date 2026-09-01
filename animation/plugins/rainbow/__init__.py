"""Scene v2 Rainbow ambient instrument."""
from types import MappingProxyType
from animation.plugins.ambient_scene import AmbientSceneAnimation
class RainbowAnimation(AmbientSceneAnimation):
    ANIMATION_NAME="Rainbow"; ANIMATION_DESCRIPTION="Prismatic bands with a local travel dial"; ANIMATION_VERSION="2.0"; COMPONENT_ID="rainbow"; STYLE="rainbow"
    DEFAULTS=MappingProxyType({"bands":1.4,"travel":.65,"direction":1,"seed":6102})
    SCHEMA={"bands":("float",.25,4.,"Rainbow bands across the wall"),"travel":("float",0.,4.,"Prism travel tempo"),"direction":("int",-1,1,"Travel direction"),"seed":("int",0,999999,"Repeatable field seed")}
