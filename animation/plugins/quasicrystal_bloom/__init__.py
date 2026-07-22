"""Non-periodic rotational symmetries made from interfering plane waves."""
import numpy as np
from animation.libraries.procedural_sculptures import CadencedSculpture


class QuasicrystalBloomAnimation(CadencedSculpture):
    ANIMATION_NAME="Quasicrystal Bloom"
    ANIMATION_DESCRIPTION="Five- to twelve-fold non-repeating rosettes open and tunnel in place"
    PLANT_MODIFIER_SUPPORT=frozenset(("refract","shadow","illuminate"))
    SOURCE_FPS=24.0

    def __init__(self,controller,config=None):
        super().__init__(controller,config)
        self.default_params.update({"symmetry":10,"spatial_scale":2.4,"warp":0.18})
        self.params={**self.default_params,**(config or {})}

    def get_parameter_schema(self):
        s=super().get_parameter_schema();s.update({
            "symmetry":{"type":"int","options":[5,8,10,12],"default":10,"description":"Rotational plane-wave order"},
            "spatial_scale":{"type":"float","min":0.8,"max":5,"default":2.4,"description":"Rosette spatial frequency"},
            "warp":{"type":"float","min":0,"max":1,"default":0.18,"description":"Radial phase modulation"},
        });return s

    def generate_frame(self,time_elapsed,frame_count):
        tick,cached=self.begin_frame(time_elapsed)
        if cached:return cached
        n=int(self.params["symmetry"]); t=tick/self.SOURCE_FPS*float(self.params["motion"])*.22
        x=self._x; y=self._y; radius=np.hypot(x,y)
        phase_offset=0
        refract=self.plant_modifier_strength("refract")
        if refract>0:phase_offset=np.exp(-self.get_plant_masks().distance/3)*refract*1.5
        field=np.zeros(self._shape,np.float32); scale=float(self.params["spatial_scale"])*5
        for k in range(n):
            angle=2*np.pi*k/n
            phase=t*np.sin(angle*2.17+k*.71)+float(self.params["warp"])*np.sin(radius*5-t)
            field+=np.cos(scale*(x*np.cos(angle)+y*np.sin(angle))+phase+phase_offset)
        value=.5+.5*np.cos(field/n*5.5+t*.3)
        # Nonlinear bands expose global symmetry without a periodic scrolling wave.
        value=np.clip((value-.18)/.82,0,1)**1.35
        shadow=self.plant_modifier_strength("shadow")
        if shadow>0:value*=1-self.get_plant_masks().obstacle*shadow
        accent=np.zeros_like(value); illum=self.plant_modifier_strength("illuminate")
        if illum>0:accent=self.get_plant_masks().obstacle_edge.astype(np.float32)*illum
        return self.finish_frame(tick,self.colorize(value,accent))
