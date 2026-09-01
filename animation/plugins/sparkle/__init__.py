"""Scene v2 Sparkle ambient instrument."""
from types import MappingProxyType
from animation.plugins.ambient_scene import AmbientSceneAnimation
class SparkleAnimation(AmbientSceneAnimation):
    ANIMATION_NAME="Sparkle"; ANIMATION_DESCRIPTION="A seeded constellation of semantic glitter"; ANIMATION_VERSION="2.0"; COMPONENT_ID="sparkle"; STYLE="sparkle"
    DEFAULTS=MappingProxyType({"density":.20,"linger":.65,"twinkle":.72,"night":.08,"seed":6104})
    SCHEMA={"density":("float",.01,1.,"How many stars arrive"),"linger":("float",.05,1.,"Trail persistence"),"twinkle":("float",0.,1.,"Spark contrast"),"night":("float",0.,.55,"Night-field depth"),"seed":("int",0,999999,"Repeatable star field")}
