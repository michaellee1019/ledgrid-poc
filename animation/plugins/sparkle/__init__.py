"""Scene v2 Sparkle ambient instrument."""
from types import MappingProxyType
import numpy as np
from animation.libraries.ambient_scene import AmbientSceneAnimation
class SparkleAnimation(AmbientSceneAnimation):
    PREFERRED_PARAMETERS = ('linger', 'density', 'twinkle', 'night')
    INTERACTION_TYPES = frozenset({"primary"})
    POINT_ACTION_LABELS = {"primary": "Scatter a star cluster"}
    NIMBUS_POINT_BINDINGS = {"primary": ("a", 3)}
    POINT_PULSE_SECONDS = .65

    ANIMATION_NAME="Sparkle"; ANIMATION_DESCRIPTION="A seeded constellation of semantic glitter"; ANIMATION_VERSION="2.0"; COMPONENT_ID="sparkle"; STYLE="sparkle"
    # Local controls preserve existing stars, their hues, and the RNG stream.
    LIVE_TUNABLE_PARAMETERS = frozenset({"density", "linger", "twinkle", "night"})
    SIMULATION_HZ=120.0
    DEFAULTS=MappingProxyType({"density":.20,"linger":.65,"twinkle":.72,"night":.08,"seed":6104})
    SCHEMA={"density":("float",0.,1.,"Automatic stars, zero keeps manual clusters"),"linger":("float",.05,1.,"Trail persistence"),"twinkle":("float",0.,1.,"Spark contrast"),"night":("float",0.,.55,"Night-field depth"),"seed":("int",0,999999,"Repeatable star field")}

    def point_rgb(self, rgb, palette_id, point, progress):
        spark = np.asarray(self.PALETTES.get(palette_id, self.PALETTES["neutral"])[2], dtype=np.float32)
        for dx, dy, gain in ((0, 0, 1), (-2, -4, .6), (2, 3, .5), (3, -2, .4), (-3, 3, .65), (0, 6, .3)):
            x, y = point.x + dx, point.y + dy
            if 0 <= x < self.width and 0 <= y < self.height:
                amount = gain * (1.0 - progress) * point.strength
                rgb[x, y] = rgb[x, y] * (1.0 - amount) + spark * amount
        return rgb
