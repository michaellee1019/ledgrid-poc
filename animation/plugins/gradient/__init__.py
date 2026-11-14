"""Scene v2 Gradient ambient instrument."""
from types import MappingProxyType
import numpy as np
from animation.libraries.ambient_scene import AmbientSceneAnimation
class GradientAnimation(AmbientSceneAnimation):
    SUGGESTED_SCENE_PACE = 6.0
    PREFERRED_PARAMETERS = ('drift', 'motion')
    PARAMETER_CONTROLS = {'seed': {'control': 'fixed'}}
    INTERACTION_TYPES = frozenset({"primary"})
    POINT_ACTION_LABELS = {"primary": "Sweep a color band"}
    NIMBUS_POINT_BINDINGS = {"primary": ("a", 3)}
    POINT_PULSE_SECONDS = .9

    ANIMATION_NAME="Gradient"; ANIMATION_DESCRIPTION="A drifting semantic color field"; ANIMATION_VERSION="2.0"; COMPONENT_ID="gradient"; STYLE="gradient"
    DEFAULTS=MappingProxyType({"direction":"vertical","drift":.22,"motion":.72,"seed":6101})
    SCHEMA={"direction":("choice",("vertical","horizontal","diagonal"),None,"Band direction"),"drift":("float",.0,2.,"Color drift tempo"),"motion":("float",0.,1.,"How much the field travels"),"seed":("int",0,999999,"Repeatable field seed")}

    def point_field(self, field, t, params, point, progress):
        across = point.x / max(1, self.width - 1)
        height = point.y / max(1, self.height - 1)
        direction = params["direction"]
        axis = self._x if direction == "horizontal" else self._y if direction == "vertical" else (self._x + self._y) * .5
        origin = across if direction == "horizontal" else height if direction == "vertical" else (across + height) * .5
        band = np.maximum(1.0 - abs(axis - origin - .46 * progress) / .13, 0.0)
        amount = band * (1.0 - progress) * point.strength
        return np.clip(field + amount * (.55 - .9 * field), 0, 1)
