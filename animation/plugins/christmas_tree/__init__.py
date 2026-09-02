"""Scene v2 Christmas Tree: a bounded, opaque seasonal instrument."""
from __future__ import annotations
import math
from types import MappingProxyType
from typing import Any, Mapping
import numpy as np
from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene

class ChristmasTreeAnimation(AnimationBase):
    ANIMATION_NAME, ANIMATION_DESCRIPTION, ANIMATION_AUTHOR, ANIMATION_VERSION = "Christmas Tree", "A blinking evergreen and gentle snow", "LED Grid Team", "3.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "christmas_tree", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES, PLANT_MODIFIER_SUPPORT = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent")), frozenset()
    DEFAULTS = MappingProxyType({"season": "classic", "tree_height": 58, "snowfall": .35, "twinkle_hz": 1.0, "seed": 1225})
    COMPONENT_DESCRIPTOR = ComponentDescriptor(component_id=COMPONENT_ID, version=1, provider="python", role="animation", timing_policy="scaled_context", alpha_behavior="opaque", palette_policy="semantic", plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS)
    SEASONS=("classic","party","quiet","blizzard")
    _SEASON={"classic":((2,8,18),(16,104,54),((255,54,55),(255,202,38))),"party":((12,2,25),(16,96,72),((255,48,180),(48,220,255))),"quiet":((2,10,22),(14,82,62),((165,220,255),(235,245,255))),"blizzard":((3,12,30),(12,72,68),((80,180,255),(255,255,255)))}
    def __init__(self,controller:Any,config:Mapping[str,Any]|None=None):
        self._authored_config=dict(config or {}); super().__init__(controller,self._authored_config); self.default_params=dict(self.DEFAULTS); self.params=self._normalized_parameters(self._authored_config); self.width,self.height=self.get_strip_info(); self._pixels=np.zeros((self.get_pixel_count(),3),dtype=np.uint8); self._context=None; self._key=None
    @classmethod
    def component_descriptor(cls): return cls.COMPONENT_DESCRIPTOR
    @classmethod
    def _normalized_parameters(cls,values):
        unknown=set(values)-set(cls.DEFAULTS)
        if unknown: raise ValueError(f"Christmas Tree received non-local parameters: {sorted(unknown)!r}")
        out=dict(cls.DEFAULTS); out.update(values)
        if out["season"] not in cls.SEASONS: raise ValueError(f"season must be one of {cls.SEASONS!r}")
        if isinstance(out["tree_height"],bool) or not isinstance(out["tree_height"],int) or not 24<=out["tree_height"]<=116: raise ValueError("tree_height must be an integer from 24 to 116")
        for name,lo,hi in (("snowfall",0.,1.),("twinkle_hz",.1,3.)):
            value=out[name]
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)) or not lo<=float(value)<=hi: raise ValueError(f"{name} must be a finite number from {lo} to {hi}")
            out[name]=float(value)
        if isinstance(out["seed"],bool) or not isinstance(out["seed"],int) or not 0<=out["seed"]<=999999: raise ValueError("seed must be an integer from 0 to 999999")
        return out
    def get_parameter_schema(self): return {"season":{"type":"str","options":list(self.SEASONS),"default":"classic","description":"Winter story and ornaments"},"tree_height":{"type":"int","min":24,"max":116,"default":58,"description":"Evergreen height"},"snowfall":{"type":"float","min":0,"max":1,"default":.35,"description":"Snowflake density"},"twinkle_hz":{"type":"float","min":.1,"max":3,"default":1,"description":"Light twinkle tempo"},"seed":{"type":"int","min":0,"max":999999,"default":1225,"description":"Repeatable decoration layout"}}
    def update_parameters(self,new_params): self.params=self._normalized_parameters({**self.params,**dict(new_params)});self._key=None
    def on_presentation_context_changed(self,old,new):
        del old
        if new.descriptor.component_id!=self.COMPONENT_ID or new.palette is None: raise ValueError("Christmas Tree requires its semantic Scene v2 context")
        self._context=new
    def set_presentation_context(self,context):self.on_presentation_context_changed(self._context,context)
    def render_resolved_scene(self,context):self.set_presentation_context(context);return self.generate_frame(context.phase_time,self.frame_count)
    def generate_frame(self,time_elapsed,frame_count):
        del frame_count
        p=self._normalized_parameters(self._context.parameters if self._context else self.params); phase=max(0.,float(self._context.phase_time if self._context else time_elapsed));palette=str(self._context.palette["palette_id"]) if self._context else "neutral";key=(int(phase*20),palette,tuple(p.items()))
        if key==self._key:return RenderedFrame(self._pixels,changed=False,dirty_ranges=())
        bg,green,ornaments=self._SEASON[p["season"]];tint={"neutral":(1,1,1),"mist":(.75,.9,1),"spectrum":(1,.65,1),"ember":(1,.65,.4)}.get(palette,(1,1,1));c=self._pixels.reshape(self.width,self.height,3)[:,::-1];c[:]=bg;center=self.width//2; bottom=self.height-7; top=max(4,bottom-p["tree_height"]);rng=np.random.default_rng(p["seed"])
        for y in range(top,bottom):
            half=max(1,int((y-top+1)/(bottom-top+1)*(self.width*.43)));c[max(0,center-half):min(self.width,center+half+1),y]=np.asarray(green)*np.asarray(tint)
        c[max(0,center-1):min(self.width,center+2),bottom:min(self.height,bottom+6)]=(112,57,20); c[center, max(0,top-2):top+2]=(255,225,100)
        count=20+int(55*p["snowfall"]); xs=rng.integers(0,self.width,count); ys=rng.integers(0,self.height,count)
        for i,(x,y) in enumerate(zip(xs,ys)):
            yy=(int(y+phase*(6+12*p["snowfall"]))%self.height);c[x,yy]=(150,205,255)
        for i in range(34):
            y=int(top+5+(bottom-top-10)*(i/34));span=max(1,int((y-top+1)/(bottom-top+1)*(self.width*.43)));x=center+int(rng.integers(-span,span+1));bright=.45+.55*(.5+.5*math.sin(phase*math.tau*p["twinkle_hz"]+i));c[x,y]=np.asarray(ornaments[i%2])*bright
        self.params,self._key=p,key;return RenderedFrame(self._pixels,changed=True)
