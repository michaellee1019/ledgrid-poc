"""Scene v2 Wave ambient instrument."""
from types import MappingProxyType
import numpy as np
from animation.libraries.ambient_scene import AmbientSceneAnimation
class WaveAnimation(AmbientSceneAnimation):
    PREFERRED_PARAMETERS = ('frequency', 'travel', 'shape')
    PARAMETER_CONTROLS = {
        'direction': {'options': [-1, 0, 1], 'option_labels': {'-1': 'Reverse', '0': 'Still', '1': 'Forward'}},
        'seed': {'control': 'fixed'},
    }
    INTERACTION_TYPES = frozenset({"primary"})
    POINT_ACTION_LABELS = {"primary": "Send a ribbon crest"}
    NIMBUS_POINT_BINDINGS = {"primary": ("a", 3)}
    POINT_PULSE_SECONDS = .8

    ANIMATION_NAME="Wave"; ANIMATION_DESCRIPTION="Traveling semantic ribbons"; ANIMATION_VERSION="2.0"; COMPONENT_ID="wave"; STYLE="wave"
    DEFAULTS=MappingProxyType({"axis":"vertical","frequency":2.,"travel":.45,"shape":.8,"direction":1,"seed":6105})
    SCHEMA={"axis":("choice",("vertical","horizontal","diagonal"),None,"Wave direction"),"frequency":("float",.25,12.,"Ribbon frequency"),"travel":("float",0.,4.,"Wave travel tempo"),"shape":("float",.05,1.,"Ribbon contrast"),"direction":("int",-1,1,"Travel direction"),"seed":("int",0,999999,"Repeatable field seed")}

    def point_field(self, field, t, params, point, progress):
        dx = self._x * (self.width - 1) - point.x
        dy = self._y * (self.height - 1) - point.y
        radius = np.hypot(dx, dy * .5)
        crest = np.maximum(1.0 - abs(radius - 8 * progress) / 2.6, 0.0)
        return np.clip(field + crest * (1.0 - progress) * point.strength * .48, 0, 1)
