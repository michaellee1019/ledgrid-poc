"""Bounded Scene v2 Flame Burst ignition instrument."""
from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene


class FlameBurstAnimation(AnimationBase):
    ANIMATION_NAME = "Flame Burst"; ANIMATION_DESCRIPTION = "A tactile ignition instrument with warm expanding flame shells"
    ANIMATION_AUTHOR = "LED Grid Team"; ANIMATION_VERSION = "2.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "flame_burst", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent")); PLANT_MODIFIER_SUPPORT = frozenset(); INTERACTION_TYPES = frozenset(("primary",))
    SIM_HZ, MAX_CATCH_UP_STEPS, MAX_IGNITIONS = 30.0, 12, 10
    DEFAULTS = MappingProxyType({"ignition_cadence": .9, "flare_size": .42, "ember_linger": .45, "flicker": .35, "seed": 6201})
    COMPONENT_DESCRIPTOR = ComponentDescriptor(component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER, role=ROLE, timing_policy=TIMING_POLICY, alpha_behavior="opaque", palette_policy=PALETTE_POLICY, plant_capabilities=("effect_intent",), fidelity_exceptions=(), defaults=DEFAULTS)
    SEMANTIC_PALETTES = MappingProxyType({"neutral": ((8.,2.,1.),(238.,55.,9.),(255.,209.,78.)), "mist": ((5.,5.,12.),(248.,73.,18.),(255.,221.,140.)), "spectrum": ((16.,2.,20.),(250.,45.,104.),(255.,185.,64.)), "ember": ((15.,2.,1.),(255.,64.,8.),(255.,225.,108.))})

    def __init__(self, controller: Any, config: Mapping[str, Any] | None = None):
        self._authored_config = dict(config or {}); super().__init__(controller, self._authored_config); self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info(); self._pixels = np.zeros((self.get_pixel_count(), 3), dtype=np.uint8)
        self._x = np.linspace(-1., 1., self.width, dtype=np.float32)[:, None]; self._y = np.linspace(-1., 1., self.height, dtype=np.float32)[None, :]
        self._last_tick: int | None = None; self._last_render_key: tuple[Any, ...] | None = None; self._presentation_context: ResolvedScene | None = None; self._rng = np.random.default_rng(self.params["seed"])
        self._ignitions: list[tuple[float,float,float,float]] = []; self._cadence_phase, self._manual_events, self._last_manual_tick = 1., 0, -9999

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor: return cls.COMPONENT_DESCRIPTOR
    def get_parameter_schema(self) -> dict[str, dict[str, Any]]:
        return {"ignition_cadence":{"type":"float","min":.12,"max":4.,"default":.9,"description":"How often the instrument lights a new burst"}, "flare_size":{"type":"float","min":.12,"max":.9,"default":.42,"description":"Reach of each expanding flame shell"}, "ember_linger":{"type":"float","min":.08,"max":1.,"default":.45,"description":"How long the warm ember trail remains"}, "flicker":{"type":"float","min":0.,"max":1.,"default":.35,"description":"Texture and shimmer in the shell"}, "seed":{"type":"int","min":0,"max":999999,"default":6201,"description":"Repeatable ignition sequence"}}
    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        candidate = self._normalized_parameters({**self.params, **dict(new_params)}); changed_seed = candidate["seed"] != self.params["seed"]; self.params = candidate
        if changed_seed: self._reset()
        self._last_render_key = None
    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        if (new.descriptor.component_id,new.descriptor.version,new.descriptor.provider.value,new.descriptor.role.value)!=(self.COMPONENT_ID,1,"python","animation"): raise ValueError("Flame Burst received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"),str): raise ValueError("Flame Burst requires a semantic Scene v2 palette")
        self._presentation_context = new
    def set_presentation_context(self, context: ResolvedScene) -> None: self.on_presentation_context_changed(self._presentation_context, context)
    def render_resolved_scene(self, context: ResolvedScene) -> RenderedFrame: self.set_presentation_context(context); return self.generate_frame(context.phase_time, self.frame_count)
    def handle_interaction(self, kind: str, x: float, y: float, strength: float = 1.) -> bool:
        if kind != "primary" or not all(isinstance(v,(int,float)) and math.isfinite(float(v)) for v in (x,y,strength)) or not 0. <= x < self.width or not 0. <= y < self.height or not 0. < strength <= 1.: return False
        tick = -1 if self._last_tick is None else self._last_tick
        if tick-self._last_manual_tick < int(self.SIM_HZ/5): return False
        self._last_manual_tick=tick; self._manual_events+=1; self._ignite(float(x)/max(1,self.width-1)*2-1,float(y)/max(1,self.height-1)*2-1,.7+.3*float(strength)); self._last_render_key=None; return True
    def generate_frame(self, time_elapsed: float, frame_count: int) -> RenderedFrame:
        del frame_count
        phase_time,palette,values=(max(0.,float(time_elapsed)),"neutral",self.params) if self._presentation_context is None else (max(0.,float(self._presentation_context.phase_time)),str(self._presentation_context.palette["palette_id"]),self._presentation_context.parameters)
        candidate=self._normalized_parameters(values)
        if candidate["seed"] != self.params["seed"]: self.params=candidate; self._reset()
        else: self.params=candidate
        tick=int(math.floor(phase_time*self.SIM_HZ+1e-9))
        if self._last_tick is None: self._last_tick=tick
        else:
            target=min(tick,self._last_tick+self.MAX_CATCH_UP_STEPS)
            while self._last_tick<target: self._step(); self._last_tick+=1
        key=(self._last_tick,palette,tuple(self.params.items()),tuple(self._ignitions))
        if key==self._last_render_key: return RenderedFrame(self._pixels,changed=False,dirty_ranges=())
        self._paint(palette); self._last_render_key=key; return RenderedFrame(self._pixels,changed=True)
    def cadence_snapshot(self) -> Mapping[str,Any]: return MappingProxyType({"simulation_hz":self.SIM_HZ,"tick":self._last_tick,"active_ignitions":len(self._ignitions),"manual_ignitions":self._manual_events})
    def get_runtime_stats(self) -> dict[str,Any]: return dict(self.cadence_snapshot())
    def _reset(self) -> None: self._rng=np.random.default_rng(self.params["seed"]); self._ignitions.clear(); self._cadence_phase=1.; self._last_tick=None; self._last_manual_tick=-9999
    def _ignite(self,x:float|None=None,y:float|None=None,energy:float=1.) -> None:
        if x is None: x,y=float(self._rng.uniform(-.78,.78)),float(self._rng.uniform(-.58,.58))
        self._ignitions.append((float(x),float(y),0.,float(energy))); self._ignitions=self._ignitions[-self.MAX_IGNITIONS:]
    def _step(self) -> None:
        self._cadence_phase+=self.params["ignition_cadence"]/self.SIM_HZ
        if self._cadence_phase>=1.: self._cadence_phase-=1.; self._ignite()
        life=.32+self.params["ember_linger"]*1.5; self._ignitions=[(x,y,age+1./self.SIM_HZ,e) for x,y,age,e in self._ignitions if age+1./self.SIM_HZ<life]
    def _paint(self,palette_id:str)->None:
        low,hot,white=self.SEMANTIC_PALETTES.get(palette_id,self.SEMANTIC_PALETTES["neutral"]); canvas=self._pixels.reshape(self.width,self.height,3); canvas[:]=np.asarray(low,dtype=np.uint8); field=np.zeros((self.width,self.height),dtype=np.float32); life=.32+self.params["ember_linger"]*1.5
        for x,y,age,energy in self._ignitions:
            progress=age/life; radius=(.04+progress*self.params["flare_size"]*1.15)*energy; distance=np.hypot(self._x-x,self._y-y); field+=np.exp(-np.square((distance-radius)/(.035+self.params["flare_size"]*.10)))*(1.-progress)*1.35+np.exp(-distance/(.09+self.params["flare_size"]*.18))*(1.-progress)*self.params["ember_linger"]*.48
        field*=1.-self.params["flicker"]+self.params["flicker"]*(.73+.27*np.sin(self._x*23.+self._y*17.+(self._last_tick or 0)*.71)); np.clip(field,0.,1.,out=field)
        for channel in range(3): canvas[:,:,channel]=np.clip(low[channel]+(hot[channel]-low[channel])*field+(white[channel]-hot[channel])*np.square(field),0.,255.)
    @classmethod
    def _normalized_parameters(cls,values:Mapping[str,Any])->dict[str,Any]:
        unknown=set(values)-set(cls.DEFAULTS)
        if unknown: raise ValueError(f"Flame Burst does not accept non-local parameters: {sorted(unknown)!r}")
        result=dict(cls.DEFAULTS); result.update(values)
        if isinstance(result["seed"],bool) or not isinstance(result["seed"],int) or not 0<=result["seed"]<=999999: raise ValueError("seed must be an integer from 0 to 999999")
        for name,low,high in (("ignition_cadence",.12,4.),("flare_size",.12,.9),("ember_linger",.08,1.),("flicker",0.,1.)):
            value=result[name]
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)) or not low<=float(value)<=high: raise ValueError(f"{name} must be a finite number from {low} to {high}")
            result[name]=float(value)
        return result
