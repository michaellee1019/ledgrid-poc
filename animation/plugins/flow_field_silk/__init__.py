"""Coherent luminous filaments advected through an analytic curl field."""
import numpy as np
from animation.libraries.procedural_sculptures import CadencedSculpture


class FlowFieldSilkAnimation(CadencedSculpture):
    ANIMATION_NAME = "Flow-Field Silk"
    ANIMATION_DESCRIPTION = "Fine advected threads braid and fray in a slow underwater vector field"
    PLANT_MODIFIER_SUPPORT = frozenset(("refract", "shadow", "emitter"))
    SOURCE_FPS = 30.0
    COMPONENT_ID = "flow_field_silk"
    COMPONENT_DEFAULTS = {"motion": .52, "density": .50, "background_level": .14, "seed": 1901,
                          "turbulence": .35, "persistence": .80}

    def __init__(self, controller, config=None):
        super().__init__(controller, config); self._init_threads()

    def _init_threads(self):
        n = max(4, int(8 + 28 * float(self.params["density"])))
        length = 22
        heads = np.column_stack((self.rng.uniform(0, self._shape[0], n), self.rng.uniform(0, self._shape[1], n)))
        self.filaments = np.repeat(heads[:, None, :], length, axis=1).astype(np.float32)

    def get_parameter_schema(self):
        s=super().get_parameter_schema(); s.update({
            "turbulence":{"type":"float","min":0,"max":1,"default":0.35,"description":"Curl-field complexity"},
            "persistence":{"type":"float","min":0.1,"max":1,"default":0.8,"description":"Visible filament trail length"},
        }); return s

    def update_parameters(self, params):
        old=float(self.params.get("density",.5)); super().update_parameters(params)
        if "density" in params and float(params["density"]) != old: self._init_threads()

    @classmethod
    def _validate_local_parameters(cls, values):
        super()._validate_local_parameters(values)
        for name, low, high in (("turbulence", 0., 1.), ("persistence", .1, 1.)):
            if not low <= float(values[name]) <= high: raise ValueError(f"{name} is out of range")

    def _step(self, tick):
        self.filaments[:,1:] = self.filaments[:,:-1]
        p=self.filaments[:,0]; x=p[:,0]/self._shape[0]*6.28; y=p[:,1]/self._shape[1]*6.28
        phase=tick*.018*float(self.params["motion"]); turb=float(self.params["turbulence"])
        vx=np.sin(y*.75+phase)+turb*np.cos(x*1.7-phase*.6)
        vy=.8+0.6*np.cos(x*.8-phase)+turb*np.sin(y*1.3+phase)
        p[:,0]=(p[:,0]+vx*.16*(.3+float(self.params["motion"])))%self._shape[0]
        p[:,1]=(p[:,1]+vy*.16*(.3+float(self.params["motion"])))%self._shape[1]

    def reset_simulation(self): super().reset_simulation(); self._init_threads()

    def generate_frame(self,time_elapsed,frame_count):
        tick,cached=self.begin_frame(time_elapsed)
        if cached:return cached
        self.advance_bounded(tick,self._step)
        value=np.zeros(self._shape,np.float32); accent=np.zeros_like(value)
        keep=max(3,int(self.filaments.shape[1]*float(self.params["persistence"])))
        for age in range(keep):
            pts=self.filaments[:,age]; ix=np.clip(pts[:,0].astype(int),0,self._shape[0]-1); iy=np.clip(pts[:,1].astype(int),0,self._shape[1]-1)
            np.maximum.at(value,(ix,iy),(1-age/keep)*.9); np.maximum.at(accent,(ix,iy),(1-age/keep)*.6)
        return self.finish_frame(tick,self.colorize(value,accent))
