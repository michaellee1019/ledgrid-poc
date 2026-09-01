"""Bounded Scene v2 Fluid Tank flow instrument."""
from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene


class FluidTankAnimation(AnimationBase):
    ANIMATION_NAME = "Fluid Tank"; ANIMATION_DESCRIPTION = "A compact water-flow instrument with bubbles and energetic surface ripples"
    ANIMATION_AUTHOR = "LED Grid Team"; ANIMATION_VERSION = "2.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "fluid_tank", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent")); PLANT_MODIFIER_SUPPORT = frozenset(); INTERACTION_TYPES = frozenset(("primary",))
    SIM_HZ, MAX_CATCH_UP_STEPS, MAX_PULSES = 20.0, 12, 8
    DEFAULTS = MappingProxyType({"flow_rate": .72, "current": .35, "bubble_lift": .42, "surface_energy": .38, "seed": 6211})
    COMPONENT_DESCRIPTOR = ComponentDescriptor(component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER, role=ROLE, timing_policy=TIMING_POLICY, alpha_behavior="opaque", palette_policy=PALETTE_POLICY, plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS)
    SEMANTIC_PALETTES = MappingProxyType({"neutral": ((2.,9.,17.),(12.,108.,178.),(126.,244.,255.)), "mist": ((2.,8.,18.),(24.,104.,170.),(164.,239.,255.)), "spectrum": ((9.,3.,25.),(35.,101.,242.),(114.,248.,230.)), "ember": ((8.,4.,3.),(20.,112.,168.),(169.,241.,190.))})

    def __init__(self, controller: Any, config: Mapping[str, Any] | None = None):
        self._authored_config=dict(config or {}); super().__init__(controller,self._authored_config); self.params=self._normalized_parameters(self._authored_config)
        self.width,self.height=self.get_strip_info(); self._pixels=np.zeros((self.get_pixel_count(),3),dtype=np.uint8); self._x=np.linspace(0.,1.,self.width,dtype=np.float32)[:,None]; self._y=np.linspace(0.,1.,self.height,dtype=np.float32)[None,:]
        self._last_tick:int|None=None; self._last_render_key:tuple[Any,...]|None=None; self._presentation_context:ResolvedScene|None=None; self._rng=np.random.default_rng(self.params["seed"]); self._phase=0.; self._pulses:list[tuple[float,float]]=[]; self._manual_events=0; self._last_manual_tick=-9999
    @classmethod
    def component_descriptor(cls)->ComponentDescriptor:return cls.COMPONENT_DESCRIPTOR
    def get_parameter_schema(self)->dict[str,dict[str,Any]]:
        return {"flow_rate":{"type":"float","min":.1,"max":2.,"default":.72,"description":"Fill and pour cadence"},"current":{"type":"float","min":0.,"max":1.,"default":.35,"description":"Sideways flow across the tank"},"bubble_lift":{"type":"float","min":0.,"max":1.,"default":.42,"description":"Column of rising bubbles"},"surface_energy":{"type":"float","min":0.,"max":1.,"default":.38,"description":"Ripple and foam response"},"seed":{"type":"int","min":0,"max":999999,"default":6211,"description":"Repeatable water rhythm"}}
    def update_parameters(self,new_params:Mapping[str,Any])->None:
        candidate=self._normalized_parameters({**self.params,**dict(new_params)}); reset=candidate["seed"]!=self.params["seed"]; self.params=candidate
        if reset:self._reset()
        self._last_render_key=None
    def on_presentation_context_changed(self,old:ResolvedScene|None,new:ResolvedScene)->None:
        del old
        if (new.descriptor.component_id,new.descriptor.version,new.descriptor.provider.value,new.descriptor.role.value)!=(self.COMPONENT_ID,1,"python","animation"):raise ValueError("Fluid Tank received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"),str):raise ValueError("Fluid Tank requires a semantic Scene v2 palette")
        self._presentation_context=new
    def set_presentation_context(self,context:ResolvedScene)->None:self.on_presentation_context_changed(self._presentation_context,context)
    def render_resolved_scene(self,context:ResolvedScene)->RenderedFrame:self.set_presentation_context(context);return self.generate_frame(context.phase_time,self.frame_count)
    def handle_interaction(self,kind:str,x:float,y:float,strength:float=1.)->bool:
        if kind!="primary" or not all(isinstance(v,(int,float)) and math.isfinite(float(v)) for v in (x,y,strength)) or not 0.<=x<self.width or not 0.<=y<self.height or not 0.<strength<=1.:return False
        tick=-1 if self._last_tick is None else self._last_tick
        if tick-self._last_manual_tick<int(self.SIM_HZ/4):return False
        self._last_manual_tick=tick;self._manual_events+=1;self._pulses.append((float(x)/max(1,self.width-1),float(strength)));self._pulses=self._pulses[-self.MAX_PULSES:];self._last_render_key=None;return True
    def generate_frame(self,time_elapsed:float,frame_count:int)->RenderedFrame:
        del frame_count
        phase_time,palette,values=(max(0.,float(time_elapsed)),"neutral",self.params) if self._presentation_context is None else (max(0.,float(self._presentation_context.phase_time)),str(self._presentation_context.palette["palette_id"]),self._presentation_context.parameters)
        candidate=self._normalized_parameters(values)
        if candidate["seed"]!=self.params["seed"]:self.params=candidate;self._reset()
        else:self.params=candidate
        tick=int(math.floor(phase_time*self.SIM_HZ+1e-9))
        if self._last_tick is None:self._last_tick=tick
        else:
            target=min(tick,self._last_tick+self.MAX_CATCH_UP_STEPS)
            while self._last_tick<target:self._step();self._last_tick+=1
        key=(self._last_tick,palette,tuple(self.params.items()),tuple(self._pulses))
        if key==self._last_render_key:return RenderedFrame(self._pixels,changed=False,dirty_ranges=())
        self._paint(palette);self._last_render_key=key;return RenderedFrame(self._pixels,changed=True)
    def cadence_snapshot(self)->Mapping[str,Any]:return MappingProxyType({"simulation_hz":self.SIM_HZ,"tick":self._last_tick,"manual_pulses":self._manual_events,"active_pulses":len(self._pulses)})
    def get_runtime_stats(self)->dict[str,Any]:return dict(self.cadence_snapshot())
    def _reset(self)->None:self._rng=np.random.default_rng(self.params["seed"]);self._phase=0.;self._pulses.clear();self._last_tick=None;self._last_manual_tick=-9999
    def _step(self)->None:
        self._phase=(self._phase+self.params["flow_rate"]/self.SIM_HZ)%1000.;self._pulses=[(x,max(0.,e-1./self.SIM_HZ)) for x,e in self._pulses if e>1./self.SIM_HZ]
        if self._rng.random()<self.params["surface_energy"]*.018:self._pulses.append((float(self._rng.random()),.35+.4*self.params["surface_energy"]))
        self._pulses=self._pulses[-self.MAX_PULSES:]
    def _paint(self,palette_id:str)->None:
        low,water,highlight=self.SEMANTIC_PALETTES.get(palette_id,self.SEMANTIC_PALETTES["neutral"]);canvas=self._pixels.reshape(self.width,self.height,3)
        phase=self._phase; current=self.params["current"]; energy=self.params["surface_energy"]; surface=.38+.10*np.sin(self._x*math.tau*(1.2+current*2.)+phase*3.)+.035*np.sin(self._x*math.tau*5.-phase*5.)*energy
        depth=np.clip((self._y-surface)/(.62+.12*np.sin(phase*.3)),0.,1.); shimmer=np.maximum(0.,np.sin(self._x*math.tau*8.+phase*11.-self._y*4.))*energy
        bubbles=np.maximum(0.,np.sin((self._x*11.+self._y*29.-phase*(7.+self.params["bubble_lift"]*12.))*math.tau))**18*self.params["bubble_lift"]
        for x,power in self._pulses:bubbles+=np.exp(-np.square((self._x-x)/.045))*np.exp(-np.square((self._y-(.42+.23*np.sin(phase*5.+x*9.)))/.3))*power
        intensity=np.clip(depth*(.52+.48*(1.-self._y))+shimmer*.33+bubbles*.55,0.,1.)
        for channel in range(3):canvas[:,:,channel]=np.clip(low[channel]+(water[channel]-low[channel])*intensity+(highlight[channel]-water[channel])*np.clip(shimmer+bubbles,0.,1.)*.72,0.,255.)
    @classmethod
    def _normalized_parameters(cls,values:Mapping[str,Any])->dict[str,Any]:
        unknown=set(values)-set(cls.DEFAULTS)
        if unknown:raise ValueError(f"Fluid Tank does not accept non-local parameters: {sorted(unknown)!r}")
        result=dict(cls.DEFAULTS);result.update(values)
        if isinstance(result["seed"],bool) or not isinstance(result["seed"],int) or not 0<=result["seed"]<=999999:raise ValueError("seed must be an integer from 0 to 999999")
        for name,low,high in (("flow_rate",.1,2.),("current",0.,1.),("bubble_lift",0.,1.),("surface_energy",0.,1.)):
            value=result[name]
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)) or not low<=float(value)<=high:raise ValueError(f"{name} must be a finite number from {low} to {high}")
            result[name]=float(value)
        return result
