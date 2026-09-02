"""Scene v2 Emoji Animation, deliberately separate from the message Widget."""
from __future__ import annotations
import math
from types import MappingProxyType
from typing import Any, Mapping
import numpy as np
from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene
from animation.libraries.pixel_art import EMOJI_PATTERNS

class EmojiAnimation(AnimationBase):
    ANIMATION_NAME, ANIMATION_DESCRIPTION, ANIMATION_AUTHOR, ANIMATION_VERSION = "Emoji", "A breathing pixel face, not a text message", "LED Grid Team", "2.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "emoji", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES, PLANT_MODIFIER_SUPPORT = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent")), frozenset()
    DEFAULTS = MappingProxyType({"face": "smile", "mood": "golden", "pulse_hz": .8, "scale": 1.0, "seed": 2026})
    COMPONENT_DESCRIPTOR = ComponentDescriptor(component_id=COMPONENT_ID, version=1, provider="python", role="animation", timing_policy="scaled_context", alpha_behavior="opaque", palette_policy="semantic", plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS)
    FACES, MOODS = ("smile", "heart"), ("golden", "neon", "rose", "ice")
    _MOOD = {"golden": ((3, 7, 18), (255, 202, 45), (230, 60, 70)), "neon": ((8, 1, 24), (78, 245, 238), (255, 46, 205)), "rose": ((20, 1, 10), (255, 105, 158), (255, 235, 245)), "ice": ((1, 12, 25), (118, 210, 255), (240, 250, 255))}
    def __init__(self, controller: Any, config: Mapping[str, Any] | None = None):
        self._authored_config=dict(config or {}); super().__init__(controller, self._authored_config); self.default_params=dict(self.DEFAULTS); self.params=self._normalized_parameters(self._authored_config); self.width,self.height=self.get_strip_info(); self._pixels=np.zeros((self.get_pixel_count(),3),dtype=np.uint8); self._context=None; self._key=None
    @classmethod
    def component_descriptor(cls): return cls.COMPONENT_DESCRIPTOR
    @classmethod
    def _normalized_parameters(cls, values):
        unknown=set(values)-set(cls.DEFAULTS)
        if unknown: raise ValueError(f"Emoji received non-local parameters: {sorted(unknown)!r}")
        result=dict(cls.DEFAULTS); result.update(values)
        if result["face"] not in cls.FACES: raise ValueError(f"face must be one of {cls.FACES!r}")
        if result["mood"] not in cls.MOODS: raise ValueError(f"mood must be one of {cls.MOODS!r}")
        for name,lo,hi in (("pulse_hz",.1,3.),("scale",.55,1.8)):
            value=result[name]
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)) or not lo<=float(value)<=hi: raise ValueError(f"{name} must be a finite number from {lo} to {hi}")
            result[name]=float(value)
        if isinstance(result["seed"],bool) or not isinstance(result["seed"],int) or not 0<=result["seed"]<=999999: raise ValueError("seed must be an integer from 0 to 999999")
        return result
    def get_parameter_schema(self): return {"face":{"type":"str","options":list(self.FACES),"default":"smile","description":"Pixel face type"},"mood":{"type":"str","options":list(self.MOODS),"default":"golden","description":"Emotion and local colors"},"pulse_hz":{"type":"float","min":.1,"max":3,"default":.8,"description":"Breathing tempo"},"scale":{"type":"float","min":.55,"max":1.8,"default":1,"description":"Face scale"},"seed":{"type":"int","min":0,"max":999999,"default":2026,"description":"Repeatable sparkle phase"}}
    def update_parameters(self,new_params): self.params=self._normalized_parameters({**self.params,**dict(new_params)}); self._key=None
    def on_presentation_context_changed(self,old,new):
        del old
        if new.descriptor.component_id!=self.COMPONENT_ID or new.palette is None: raise ValueError("Emoji requires its semantic Scene v2 context")
        self._context=new
    def set_presentation_context(self,context): self.on_presentation_context_changed(self._context,context)
    def render_resolved_scene(self,context): self.set_presentation_context(context); return self.generate_frame(context.phase_time,self.frame_count)
    def generate_frame(self,time_elapsed,frame_count):
        del frame_count
        params=self._normalized_parameters(self._context.parameters if self._context else self.params); phase=max(0.,float(self._context.phase_time if self._context else time_elapsed)); palette=str(self._context.palette["palette_id"]) if self._context else "neutral"; key=(round(phase*20),palette,tuple(params.items()))
        if key==self._key:return RenderedFrame(self._pixels,changed=False,dirty_ranges=())
        bg,primary,accent=self._MOOD[params["mood"]]; tint={"neutral":(1,1,1),"mist":(.72,.9,1),"spectrum":(1,.6,1),"ember":(1,.62,.35)}.get(palette,(1,1,1)); canvas=self._pixels.reshape(self.width,self.height,3)[:,::-1]; canvas[:]=bg
        pattern=EMOJI_PATTERNS["heart" if params["face"]=="heart" else "smile"]; h,w=len(pattern),len(pattern[0]); unit=max(1,int(min(self.width/(w+3),self.height/(h+10))*params["scale"])); left=(self.width-w*unit)//2; bottom=(self.height-h*unit)//2; pulse=.72+.28*(.5+.5*math.sin(phase*math.tau*params["pulse_hz"]));
        for row,line in enumerate(pattern):
            for col,cell in enumerate(line):
                if cell==".":continue
                color=np.asarray(accent if cell in "EM" else primary,dtype=np.float32)*pulse*np.asarray(tint)
                canvas[max(0,left+col*unit):min(self.width,left+(col+1)*unit),max(0,bottom+row*unit):min(self.height,bottom+(row+1)*unit)]=np.clip(color,0,255)
        self.params,self._key=params,key; return RenderedFrame(self._pixels,changed=True)
