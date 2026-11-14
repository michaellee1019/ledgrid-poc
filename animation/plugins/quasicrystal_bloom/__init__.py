"""Non-periodic rotational symmetries made from interfering plane waves."""
import numpy as np
from animation.libraries.procedural_sculptures import CadencedSculpture
from animation.libraries.point_pulse import AimedPoint, PointPulse


class QuasicrystalBloomAnimation(CadencedSculpture):
    PREFERRED_PARAMETERS = ('spatial_scale', 'warp', 'motion', 'density', 'background_level')
    INTERACTION_TYPES = frozenset({"primary"})
    POINT_ACTION_LABELS = {"primary": "Pull the next rosette"}
    NIMBUS_POINT_BINDINGS = {"primary": ("a", 3)}

    ANIMATION_NAME="Quasicrystal Bloom"
    ANIMATION_DESCRIPTION="Five- to twelve-fold non-repeating rosettes open and tunnel in place"
    PLANT_MODIFIER_SUPPORT=frozenset(("refract","shadow","illuminate"))
    SOURCE_FPS=24.0
    COMPONENT_ID = "quasicrystal_bloom"
    COMPONENT_DEFAULTS = {"motion": .42, "density": .56, "background_level": .16, "seed": 2701,
                          "symmetry": 10, "spatial_scale": 2.4, "warp": .18}

    def __init__(self,controller,config=None):
        super().__init__(controller,config)
        self._pulse = PointPulse(1.2)

    def handle_interaction(self, kind, x, y, strength=1.0):
        point = AimedPoint.from_input(kind, x, y, strength, *self._shape)
        return point is not None and self._pulse.trigger(point)

    @classmethod
    def get_parameter_schema(cls):
        s=super().get_parameter_schema();s.update({
            "symmetry":{"type":"int","options":[5,8,10,12],"default":10,"description":"Rotational plane-wave order"},
            "spatial_scale":{"type":"float","min":0.8,"max":5,"default":2.4,"description":"Rosette spatial frequency"},
            "warp":{"type":"float","min":0,"max":1,"default":0.18,"description":"Radial phase modulation"},
        });return s

    @classmethod
    def _validate_local_parameters(cls, values):
        super()._validate_local_parameters(values)
        if int(values["symmetry"]) not in {5, 8, 10, 12}: raise ValueError("symmetry is not supported")
        if not .8 <= float(values["spatial_scale"]) <= 5: raise ValueError("spatial_scale is out of range")
        if not 0 <= float(values["warp"]) <= 1: raise ValueError("warp is out of range")

    def generate_frame(self,time_elapsed,frame_count):
        point, progress = self._pulse.sample(self.source_tick(time_elapsed) / self.SOURCE_FPS)
        key = (point, progress) if point is not None else ()
        tick,cached=self.begin_frame(time_elapsed, extras=key)
        if cached:return cached
        n=int(self.params["symmetry"]); t=tick/self.SOURCE_FPS*float(self.params["motion"])*.22
        x, y = self._x, self._y
        if point is not None:
            n = {5: 8, 8: 10, 10: 12, 12: 5}[n]
            amount = (1.0 - progress) * point.strength
            x = x - (2.0 * point.x / max(1, self._shape[0] - 1) - 1) * amount
            y = y - (2.0 * point.y / max(1, self._shape[1] - 1) - 1) * amount
        radius = np.hypot(x, y)
        field=np.zeros(self._shape,np.float32); scale=float(self.params["spatial_scale"])*5
        for k in range(n):
            angle=2*np.pi*k/n
            phase=t*np.sin(angle*2.17+k*.71)+float(self.params["warp"])*np.sin(radius*5-t)
            field+=np.cos(scale*(x*np.cos(angle)+y*np.sin(angle))+phase)
        value=.5+.5*np.cos(field/n*5.5+t*.3)
        # Nonlinear bands expose global symmetry without a periodic scrolling wave.
        value=np.clip((value-.18)/.82,0,1)**1.35
        accent=np.zeros_like(value)
        return self.finish_frame(tick,self.colorize(value,accent), extras=key)
