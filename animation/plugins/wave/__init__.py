"""Scene v2 Wave ambient instrument."""
from types import MappingProxyType
from animation.plugins.ambient_scene import AmbientSceneAnimation
class WaveAnimation(AmbientSceneAnimation):
    ANIMATION_NAME="Wave"; ANIMATION_DESCRIPTION="Traveling semantic ribbons"; ANIMATION_VERSION="2.0"; COMPONENT_ID="wave"; STYLE="wave"
    DEFAULTS=MappingProxyType({"axis":"vertical","frequency":2.,"travel":.45,"shape":.8,"direction":1,"seed":6105})
    SCHEMA={"axis":("choice",("vertical","horizontal","diagonal"),None,"Wave direction"),"frequency":("float",.25,12.,"Ribbon frequency"),"travel":("float",0.,4.,"Wave travel tempo"),"shape":("float",.05,1.,"Ribbon contrast"),"direction":("int",-1,1,"Travel direction"),"seed":("int",0,999999,"Repeatable field seed")}
