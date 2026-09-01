"""Small Scene v2 renderer foundation for the five ambient instruments."""
from __future__ import annotations
import math
from types import MappingProxyType
from typing import Any, Mapping
import numpy as np
from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene

class AmbientSceneAnimation(AnimationBase):
    """Opaque semantic field renderer; subclasses only declare local controls."""
    COMPONENT_VERSION, PROVIDER, ROLE = 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES, PLANT_MODIFIER_SUPPORT = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent")), frozenset()
    # This module remains a shipped gradient ambient renderer in addition to
    # providing the shared base for the named instruments. Its direct plugin
    # contract is therefore the same complete local control set as Gradient;
    # subclasses replace both mappings with their own controls.
    DEFAULTS: Mapping[str, Any] = MappingProxyType({
        "direction": "vertical", "drift": .22, "motion": .72, "seed": 6101,
    })
    SCHEMA: Mapping[str, tuple[Any, ...]] = MappingProxyType({
        "direction": ("choice", ("vertical", "horizontal", "diagonal"), None, "Band direction"),
        "drift": ("float", .0, 2., "Color drift tempo"),
        "motion": ("float", 0., 1., "How much the field travels"),
        "seed": ("int", 0, 999999, "Repeatable field seed"),
    })
    STYLE = "gradient"
    PALETTES = MappingProxyType({"neutral": ((4.,11.,19.),(32.,164.,148.),(186.,255.,220.)),"mist": ((3.,8.,22.),(61.,146.,220.),(236.,245.,255.)),"spectrum": ((19.,3.,35.),(235.,42.,203.),(48.,240.,224.)),"ember": ((24.,3.,2.),(244.,76.,16.),(255.,209.,78.))})
    def __init__(self, controller: Any, config: Mapping[str, Any] | None = None):
        self._authored_config = dict(config or {}); super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS); self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info(); self._pixels = np.zeros((self.get_pixel_count(), 3), dtype=np.uint8)
        self._context: ResolvedScene | None = None; self._last_key = None; self._last_tick = None
        self._x = np.linspace(0.,1.,self.width,dtype=np.float32)[:,None]; self._y = np.linspace(0.,1.,self.height,dtype=np.float32)[None,:]
        self._rng = np.random.default_rng(int(self.params.get("seed",6101))); self._spark = np.zeros((self.width,self.height),dtype=np.float32)
    @classmethod
    def component_descriptor(cls):
        return ComponentDescriptor(component_id=cls.COMPONENT_ID,version=1,provider="python",role="animation",timing_policy="scaled_context",alpha_behavior="opaque",palette_policy="semantic",plant_capabilities=("effect_intent",),fidelity_exceptions=(),defaults=cls.DEFAULTS)
    @classmethod
    def _normalized_parameters(cls, values):
        supplied=dict(values); unknown=sorted(set(supplied)-set(cls.DEFAULTS))
        if unknown: raise ValueError(f"{cls.ANIMATION_NAME} does not accept non-local parameters: {unknown!r}")
        result=dict(cls.DEFAULTS); result.update(supplied)
        for name,spec in cls.SCHEMA.items():
            value=result[name]; kind,low,high=spec[:3]
            if kind=="choice":
                if value not in low: raise ValueError(f"{name} must be one of {list(low)!r}")
            elif kind=="bool":
                if not isinstance(value,bool): raise ValueError(f"{name} must be a boolean")
            elif kind=="int":
                if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high: raise ValueError(f"{name} must be an integer from {low} to {high}")
            elif isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)) or not low<=float(value)<=high: raise ValueError(f"{name} must be a finite number from {low} to {high}")
            elif kind=="float": result[name]=float(value)
        return result
    def get_parameter_schema(self):
        return {name:{"type":"str" if s[0]=="choice" else s[0],"default":self.DEFAULTS[name],**({"options":list(s[1])} if s[0]=="choice" else ({"min":s[1],"max":s[2]} if s[0] in {"int","float"} else {})),"description":s[3]} for name,s in self.SCHEMA.items()}
    def update_parameters(self, values): self.params=self._normalized_parameters({**self.params,**dict(values)}); self._last_key=None
    def on_presentation_context_changed(self, old, new):
        del old
        if new.descriptor.component_id!=self.COMPONENT_ID: raise ValueError("ambient renderer received another component context")
        self._context=new
    def set_presentation_context(self, context): self.on_presentation_context_changed(self._context,context)
    def render_resolved_scene(self, context): self.set_presentation_context(context); return self.generate_frame(context.phase_time,self.frame_count)
    def generate_frame(self,time_elapsed,frame_count):
        del frame_count; context=self._context; p=self._normalized_parameters(context.parameters if context else self.params); palette_id=str(context.palette["palette_id"]) if context else "neutral"; t=max(0.,float(context.phase_time if context else time_elapsed)); self.params=p
        tick=int(t*24.); key=(tick,palette_id,tuple(p.items()))
        if key==self._last_key: return RenderedFrame(self._pixels,changed=False,dirty_ranges=())
        low,primary,accent=(np.asarray(item,dtype=np.float32) for item in self.PALETTES.get(palette_id,self.PALETTES["neutral"])); field=self._field(t,p,tick)
        rgb=low+(primary-low)*field[...,None]+(accent-primary)*np.square(field[...,None])*.62; self._pixels[:]=np.clip(rgb,0,255).astype(np.uint8).reshape((-1,3)); self._last_key=key
        return RenderedFrame(self._pixels,changed=True)
    def _field(self,t,p,tick):
        if self.STYLE=="gradient":
            base=self._y if p["direction"]=="vertical" else self._x if p["direction"]=="horizontal" else (self._x+self._y)*.5; return np.broadcast_to(np.clip(base+(.5-.5*np.cos(t*p["drift"]*math.tau))*p["motion"],0.,1.),(self.width,self.height))
        if self.STYLE=="rainbow": return .5+.5*np.sin((self._y*p["bands"]+self._x*.35+t*p["travel"]*p["direction"])*math.tau)
        if self.STYLE=="solid": return np.full((self.width,self.height),p["glow"]*(.62+.38*(.5+.5*math.sin(t*p["breath"]*math.tau))))
        if self.STYLE=="sparkle":
            if self._last_tick is None: self._last_tick=tick
            while self._last_tick<min(tick,self._last_tick+10): self._spark*=.55+.4*p["linger"]; self._spark[self._rng.random(self._spark.shape)<p["density"]*.08]=1.; self._last_tick+=1
            return np.clip(p["night"]+self._spark*(.55+.45*p["twinkle"]),0.,1.)
        axis=self._y if p["axis"]=="vertical" else self._x if p["axis"]=="horizontal" else (self._x+self._y)*.5; return np.broadcast_to(np.clip(.5+.5*np.sin((axis*p["frequency"]+t*p["travel"]*p["direction"])*math.tau)*p["shape"],0.,1.),(self.width,self.height))
    def cadence_snapshot(self): return MappingProxyType({"simulation_hz":24.,"tick":self._last_tick})
