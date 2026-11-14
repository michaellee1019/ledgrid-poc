"""Scene v2 Rainbow ambient instrument."""
from types import MappingProxyType
import math
import numpy as np
from animation.libraries.ambient_scene import AmbientSceneAnimation
class RainbowAnimation(AmbientSceneAnimation):
    SUGGESTED_SCENE_PACE = 6.0
    PREFERRED_PARAMETERS = ('bands', 'travel')
    PARAMETER_CONTROLS = {
        'direction': {'options': [-1, 0, 1], 'option_labels': {'-1': 'Reverse', '0': 'Still', '1': 'Forward'}},
        'seed': {'control': 'fixed'},
    }
    INTERACTION_TYPES = frozenset({"primary"})
    POINT_ACTION_LABELS = {"primary": "Pull the rainbow phase"}
    POINT_ACTION_AXES = {"primary": ("x",)}
    NIMBUS_POINT_BINDINGS = {"primary": ("a", 3)}
    POINT_PULSE_SECONDS = 1.15

    ANIMATION_NAME="Rainbow"; ANIMATION_DESCRIPTION="Prismatic bands with a local travel dial"; ANIMATION_VERSION="2.0"; COMPONENT_ID="rainbow"; STYLE="rainbow"
    DEFAULTS=MappingProxyType({"bands":1.4,"travel":.65,"direction":1,"seed":6102})
    SCHEMA={"bands":("float",.25,4.,"Rainbow bands across the wall"),"travel":("float",0.,4.,"Prism travel tempo"),"direction":("int",-1,1,"Travel direction"),"seed":("int",0,999999,"Repeatable field seed")}

    def point_field(self, field, t, params, point, progress):
        sweep = point.x / max(1, self.width - 1) - .5
        current = t * params["travel"] * params["direction"]
        reverse = -2 * progress * self.POINT_PULSE_SECONDS * params["travel"] * params["direction"]
        offset = (.25 + sweep * .8 + reverse) * (1.0 - progress) * point.strength
        return .5 + .5 * np.sin((self._y * params["bands"] + self._x * .35 + current + offset) * math.tau)
