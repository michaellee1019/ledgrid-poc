"""Scene v2 Sparkle ambient instrument."""
from types import MappingProxyType
from animation.libraries.ambient_scene import AmbientSceneAnimation
class SparkleAnimation(AmbientSceneAnimation):
    PREFERRED_PARAMETERS = ('linger', 'density', 'twinkle', 'night')

    ANIMATION_NAME="Sparkle"; ANIMATION_DESCRIPTION="A seeded constellation of semantic glitter"; ANIMATION_VERSION="2.0"; COMPONENT_ID="sparkle"; STYLE="sparkle"
    # Local controls preserve existing stars, their hues, and the RNG stream.
    LIVE_TUNABLE_PARAMETERS = frozenset({"density", "linger", "twinkle", "night"})
    SIMULATION_HZ=120.0
    DEFAULTS=MappingProxyType({"density":.20,"linger":.65,"twinkle":.72,"night":.08,"seed":6104})
    SCHEMA={"density":("float",.01,1.,"How many stars arrive"),"linger":("float",.05,1.,"Trail persistence"),"twinkle":("float",0.,1.,"Spark contrast"),"night":("float",0.,.55,"Night-field depth"),"seed":("int",0,999999,"Repeatable star field")}
