"""Scene v2 Night Train Windows: a small parallax route through the dark."""
from __future__ import annotations
import math
from types import MappingProxyType
from typing import Any, Mapping
import numpy as np
from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene

class NightTrainWindowsAnimation(AnimationBase):
    ANIMATION_NAME, ANIMATION_DESCRIPTION, ANIMATION_AUTHOR, ANIMATION_VERSION = "Night Train Windows", "Moonlit terrain and warm towns slip past the carriage", "LED Grid Team", "2.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "night_train_windows",1,"python","animation"
    FRAME_FORMAT,TIMING_POLICY,PALETTE_POLICY="rgb_uint8_strip_major","scaled_context","semantic"
    CAPABILITIES,PLANT_MODIFIER_SUPPORT=frozenset(("semantic_palette_roles","scaled_context","effect_intent")),frozenset()
    DEFAULTS=MappingProxyType({"route":"sleeper","travel_speed":1.0,"window_glow":.65,"star_density":.35,"seed":1984})
    COMPONENT_DESCRIPTOR=ComponentDescriptor(component_id=COMPONENT_ID,version=1,provider="python",role="animation",timing_policy="scaled_context",alpha_behavior="opaque",palette_policy="semantic",plant_capabilities=("effect_intent",),fidelity_exceptions=(),defaults=DEFAULTS)
    ROUTES=("sleeper","moonlit","ember","synthwave")
    _ROUTE={"sleeper":((2,5,18),(35,56,105),(255,190,98)),"moonlit":((4,12,34),(65,105,158),(210,235,255)),"ember":((16,3,4),(78,35,45),(255,128,55)),"synthwave":((20,2,32),(92,32,136),(80,230,255))}
    def __init__(self,controller:Any,config:Mapping[str,Any]|None=None):
        self._authored_config=dict(config or {});super().__init__(controller,self._authored_config);self.default_params=dict(self.DEFAULTS);self.params=self._normalized_parameters(self._authored_config);self.width,self.height=self.get_strip_info();self._pixels=np.zeros((self.get_pixel_count(),3),dtype=np.uint8);self._context=None;self._key=None
    @classmethod
    def component_descriptor(cls):return cls.COMPONENT_DESCRIPTOR
    @classmethod
    def _normalized_parameters(cls,values):
        unknown=set(values)-set(cls.DEFAULTS)
        if unknown:raise ValueError(f"Night Train Windows received non-local parameters: {sorted(unknown)!r}")
        out=dict(cls.DEFAULTS);out.update(values)
        if out["route"] not in cls.ROUTES:raise ValueError(f"route must be one of {cls.ROUTES!r}")
        for name,lo,hi in (("travel_speed",.25,3.),("window_glow",.1,1.),("star_density",.05,1.)):
            v=out[name]
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(float(v)) or not lo<=float(v)<=hi:raise ValueError(f"{name} must be a finite number from {lo} to {hi}")
            out[name]=float(v)
        if isinstance(out["seed"],bool) or not isinstance(out["seed"],int) or not 0<=out["seed"]<=999999:raise ValueError("seed must be an integer from 0 to 999999")
        return out
    def get_parameter_schema(self):return {"route":{"type":"str","options":list(self.ROUTES),"default":"sleeper","description":"Night route narrative"},"travel_speed":{"type":"float","min":.25,"max":3,"default":1,"description":"Parallax travel pace"},"window_glow":{"type":"float","min":.1,"max":1,"default":.65,"description":"Warm carriage-window glow"},"star_density":{"type":"float","min":.05,"max":1,"default":.35,"description":"Visible star count"},"seed":{"type":"int","min":0,"max":999999,"default":1984,"description":"Repeatable landscape"}}
    def update_parameters(self,new_params):self.params=self._normalized_parameters({**self.params,**dict(new_params)});self._key=None
    def on_presentation_context_changed(self,old,new):
        del old
        if new.descriptor.component_id!=self.COMPONENT_ID or new.palette is None:raise ValueError("Night Train Windows requires its semantic Scene v2 context")
        self._context=new
    def set_presentation_context(self,context):self.on_presentation_context_changed(self._context,context)
    def render_resolved_scene(self,context):self.set_presentation_context(context);return self.generate_frame(context.phase_time,self.frame_count)
    def generate_frame(self,time_elapsed,frame_count):
        del frame_count
        p=self._normalized_parameters(self._context.parameters if self._context else self.params);phase=max(0.,float(self._context.phase_time if self._context else time_elapsed));palette=str(self._context.palette["palette_id"]) if self._context else "neutral";key=(int(phase*20),palette,tuple(p.items()))
        if key==self._key:return RenderedFrame(self._pixels,changed=False,dirty_ranges=())
        sky,hill,warm=self._ROUTE[p["route"]];tint={"neutral":(1,1,1),"mist":(.72,.88,1),"spectrum":(1,.62,1),"ember":(1,.62,.4)}.get(palette,(1,1,1));c=self._pixels.reshape(self.width,self.height,3)[:,::-1];c[:]=sky;rng=np.random.default_rng(p["seed"]);stars=int(self.width*self.height*.008*p["star_density"])
        for x,y in zip(rng.integers(0,self.width,stars),rng.integers(0,int(self.height*.7),stars)):c[x,y]=(145,180,220)
        y=np.arange(self.height);shift=int(phase*7*p["travel_speed"])
        for x in range(self.width):
            ridge=int(self.height*.58+6*math.sin((x+shift*.25)*.45)+3*math.sin((x+shift*.08)*.16));c[x,max(0,ridge):]=np.asarray(hill)*np.asarray(tint)
        for x in range((shift%9),self.width,9):c[x:min(self.width,x+4),int(self.height*.48):int(self.height*.58)]=np.asarray(warm)*p["window_glow"]
        pole=(int(phase*15*p["travel_speed"])%self.width);c[pole:min(self.width,pole+1),:]=(12,12,22)
        self.params,self._key=p,key;return RenderedFrame(self._pixels,changed=True)
