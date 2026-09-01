"""A scrolling wall-height history of a one-dimensional cellular automaton."""
import numpy as np
from animation.libraries.procedural_sculptures import CadencedSculpture


class CellularTapestryAnimation(CadencedSculpture):
    ANIMATION_NAME="Cellular Tapestry"
    ANIMATION_DESCRIPTION="Each new automaton row pushes a woven historical record down the wall"
    PLANT_MODIFIER_SUPPORT=frozenset(("obstacle","habitat","emitter"))
    SOURCE_FPS=20.0
    COMPONENT_ID = "cellular_tapestry"
    COMPONENT_DEFAULTS = {"motion": .48, "density": .54, "background_level": .16, "seed": 2801,
                          "rule": 90, "mutation": .01, "wrap": True, "row_interval": .55}

    def __init__(self,controller,config=None):
        super().__init__(controller,config); self._init_history()

    def _init_history(self):
        self.history=np.zeros((self._shape[1],self._shape[0]),bool)
        self.current=np.zeros(self._shape[0],bool); self.current[self._shape[0]//2]=True
        self.rows_written=0

    def get_parameter_schema(self):
        s=super().get_parameter_schema();s.update({
            "rule":{"type":"int","min":0,"max":255,"default":90,"description":"Elementary cellular automaton rule"},
            "mutation":{"type":"float","min":0,"max":0.15,"default":0.01,"description":"Rare deterministic bit mutation rate"},
            "wrap":{"type":"bool","default":True,"description":"Join automaton boundary columns"},
            "row_interval":{"type":"float","min":0.1,"max":1,"default":0.55,"description":"Seconds between born rows"},
        });return s

    @classmethod
    def _validate_local_parameters(cls, values):
        super()._validate_local_parameters(values)
        if not 0 <= int(values["rule"]) <= 255: raise ValueError("rule is out of range")
        for name, low, high in (("mutation", 0., .15), ("row_interval", .1, 1.)):
            if not low <= float(values[name]) <= high: raise ValueError(f"{name} is out of range")

    def reset_simulation(self):super().reset_simulation();self._init_history()

    def _step(self,tick):
        every=max(2,int(float(self.params["row_interval"])*self.SOURCE_FPS/(.25+float(self.params["motion"]))))
        if tick%every:return
        left=np.roll(self.current,1);right=np.roll(self.current,-1)
        if not self.params["wrap"]:left[0]=False;right[-1]=False
        code=(left.astype(np.uint8)<<2)|(self.current.astype(np.uint8)<<1)|right.astype(np.uint8)
        nxt=((int(self.params["rule"])>>code)&1).astype(bool)
        mutation=float(self.params["mutation"])*float(self.params["density"])
        if mutation>0:nxt ^= self.rng.random(nxt.size)<mutation
        self.history[1:]=self.history[:-1];self.history[0]=nxt;self.current=nxt;self.rows_written+=1

    def generate_frame(self,time_elapsed,frame_count):
        tick,cached=self.begin_frame(time_elapsed)
        if cached:return cached
        self.advance_bounded(tick,self._step)
        ages=np.linspace(1,.25,self._shape[1],dtype=np.float32)[:,None]
        value=(self.history*ages).T
        return self.finish_frame(tick,self.colorize(value,np.roll(value,1,axis=1)*.3))
