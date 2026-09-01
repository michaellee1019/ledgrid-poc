"""Slowly drifting Voronoi panes with transmitted light and leadwork."""
import numpy as np

from animation.libraries.procedural_sculptures import CadencedSculpture


class LivingStainedGlassAnimation(CadencedSculpture):
    ANIMATION_NAME="Living Stained Glass"
    ANIMATION_DESCRIPTION="Broad colored panes drift gently while their lead topology heals"
    PLANT_MODIFIER_SUPPORT=frozenset(("shadow","illuminate","refract"))
    SOURCE_FPS=20.0
    COMPONENT_ID = "living_stained_glass"
    COMPONENT_DEFAULTS = {"motion": .34, "density": .52, "background_level": .18, "seed": 2201,
                          "lead_width": .30, "light_direction": .40}

    def __init__(self,controller,config=None):
        super().__init__(controller,config); self._init_panes()

    def _init_panes(self):
        n=max(5,int(7+19*float(self.params["density"])))
        self.seeds=np.column_stack((self.rng.uniform(-1,1,n),self.rng.uniform(-1,1,n))).astype(np.float32)
        self.seed_phase=self.rng.uniform(0,6.283,n).astype(np.float32)

    def get_parameter_schema(self):
        s=super().get_parameter_schema(); s.update({
            "lead_width":{"type":"float","min":0.05,"max":1,"default":0.3,"description":"Dark lead seam thickness"},
            "light_direction":{"type":"float","min":-1,"max":1,"default":0.4,"description":"Direction of passing transmitted light"},
        }); return s

    def update_parameters(self,p):
        old=float(self.params.get("density",.5)); super().update_parameters(p)
        if "density" in p and float(p["density"])!=old:self._init_panes()

    @classmethod
    def _validate_local_parameters(cls, values):
        super()._validate_local_parameters(values)
        if not .05 <= float(values["lead_width"]) <= 1: raise ValueError("lead_width is out of range")
        if not -1 <= float(values["light_direction"]) <= 1: raise ValueError("light_direction is out of range")

    def reset_simulation(self):super().reset_simulation();self._init_panes()

    def palette(self, mood=None):
        return super().palette(mood)

    def on_presentation_context_changed(self, _old, _new):
        self._render_key = None
        self._cached_pixels = None

    def generate_frame(self,time_elapsed,frame_count):
        tick,cached=self.begin_frame(time_elapsed)
        if cached:return cached
        t=tick/self.SOURCE_FPS*float(self.params["motion"])
        sx=self.seeds[:,0]+.035*np.sin(t*.08+self.seed_phase); sy=self.seeds[:,1]+.025*np.cos(t*.065+self.seed_phase*1.3)
        d=(self._x[...,None]-sx)**2+(self._y[...,None]-sy)**2
        nearest=np.argmin(d,axis=2); sorted_d=np.partition(d,1,axis=2)
        seam=np.clip((sorted_d[...,1]-sorted_d[...,0])*45/(.2+float(self.params["lead_width"])),0,1)
        cloud=.68+.18*np.sin(self._x*2+self._y*float(self.params["light_direction"])*2+t*.12)
        pane=(.42+.46*((nearest*0.61803398875)%1))*cloud*seam
        rgb=self.colorize(np.clip(pane,0,1),1-seam)
        self.pane_ids=nearest
        return self.finish_frame(tick,rgb.astype(np.float32))
