"""Vectorized Gray-Scott chemistry with persistent pattern age."""

from __future__ import annotations

import math
from functools import lru_cache
from threading import Lock

import numpy as np

from animation.core.component_catalog import ComponentDescriptor
from animation.core.plant_awareness import InstallationGeometryContact, PlantModifierState
from animation.libraries.procedural_living import ProceduralLivingBase


class _LegacyReactionDiffusionGardenAnimation(ProceduralLivingBase):
    ANIMATION_NAME = "Reaction-Diffusion Garden"
    ANIMATION_DESCRIPTION = "Gray-Scott chemistry grows luminous coral, spots, and fingerprints"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    PLANT_MODIFIER_SUPPORT = frozenset(("habitat", "obstacle", "hazard", "emitter", "illuminate"))
    SIM_HZ = 12.0
    REGIMES = {"coral": (.0545, .062), "spots": (.035, .065), "fingerprints": (.037, .060)}

    def __init__(self, controller, config=None):
        super().__init__(controller, config)
        self.default_params.update({"morphology":"coral", "growth_rate":1.0, "seeding_mode":"scattered",
                                    "edge_glow":.65, "color_by_age":.6, "perturbation_interval":24.0})
        self.params = {**self.default_params, **self.config}
        self.rng = np.random.default_rng(int(self.params["seed"]))
        self._initialize_simulation()

    def get_parameter_schema(self):
        s = super().get_parameter_schema()
        s.update({
            "morphology":{"type":"str","default":"coral","options":list(self.REGIMES),"description":"Gray-Scott feed/kill regime"},
            "growth_rate":{"type":"float","min":.25,"max":2.0,"default":1.0,"description":"Chemistry integration rate"},
            "seeding_mode":{"type":"str","default":"scattered","options":["scattered","column","center"],"description":"Initial reagent layout"},
            "edge_glow":{"type":"float","min":0.0,"max":1.5,"default":.65,"description":"Presentation-only reaction-front glow"},
            "color_by_age":{"type":"float","min":0.0,"max":1.0,"default":.6,"description":"Presentation-only history color"},
            "perturbation_interval":{"type":"float","min":8.0,"max":120.0,"default":24.0,"description":"Seconds between bounded seed disturbances"},
        })
        return s

    def update_parameters(self, new_params):
        structural = bool({"morphology", "seeding_mode"} & new_params.keys())
        super().update_parameters(new_params)
        if structural:
            self.rng = np.random.default_rng(int(self.params["seed"]))
            self._initialize_simulation()

    def _initialize_simulation(self):
        self.u = np.ones((self.height, self.width), dtype=np.float32)
        self.v = np.zeros_like(self.u)
        self.age = np.zeros_like(self.u)
        count = max(3, min(48, int(12 * float(np.clip(self.params.get("density", 1), .2, 2)))))
        mode = self.params.get("seeding_mode", "scattered")
        if mode == "center":
            xs = np.full(count, self.width // 2)
            ys = np.linspace(self.height // 3, self.height * 2 // 3, count).astype(int)
        elif mode == "column":
            xs = self.rng.integers(self.width // 3, max(self.width // 3 + 1, self.width * 2 // 3), count)
            ys = self.rng.integers(2, self.height - 2, count)
        else:
            xs = self.rng.integers(1, self.width - 1, count)
            ys = self.rng.integers(1, self.height - 1, count)
        self.v[ys, xs] = self.rng.uniform(.72, 1.0, count)
        self.u[ys, xs] = .2
        self._next_perturbation = float(self.params.get("perturbation_interval", 24.0))

    @staticmethod
    def _lap(field):
        return (-field + .2 * (np.roll(field,1,0)+np.roll(field,-1,0)+np.roll(field,1,1)+np.roll(field,-1,1))
                + .05 * (np.roll(np.roll(field,1,0),1,1)+np.roll(np.roll(field,1,0),-1,1)
                           +np.roll(np.roll(field,-1,0),1,1)+np.roll(np.roll(field,-1,0),-1,1)))

    def _simulate_step(self, dt):
        f, k = self.REGIMES.get(self.params.get("morphology", "coral"), self.REGIMES["coral"])
        habitat = self.plant_modifier_strength("habitat")
        obstacle = self.plant_modifier_strength("obstacle")
        hazard = self.plant_modifier_strength("hazard")
        masks = self.get_plant_masks() if max(habitat, obstacle, hazard) > 0 else None
        feed = f
        if habitat > 0:
            feed = f + masks.foliage.T.astype(np.float32) * (.008 * habitat)
        uvv = self.u * self.v * self.v
        rate = float(np.clip(self.params.get("growth_rate", 1), .25, 2)) * dt * 10.0
        nu = self.u + (1.0 * self._lap(self.u) - uvv + feed * (1-self.u)) * rate
        nv = self.v + (.5 * self._lap(self.v) + uvv - (feed+k)*self.v) * rate
        np.clip(nu, 0, 1, out=nu); np.clip(nv, 0, 1, out=nv)
        if obstacle > 0:
            core = masks.obstacle.T
            nu[core] = self.u[core]; nv[core] = self.v[core]
        if hazard > 0:
            nv[masks.obstacle.T] *= max(0.0, 1.0 - .8 * hazard)
        self.u, self.v = nu, nv
        self.age += (self.v > .18).astype(np.float32) * dt
        emitter = self.plant_modifier_strength("emitter")
        interval = float(self.params.get("perturbation_interval", 24.0))
        if self._sim_time >= self._next_perturbation:
            if emitter > 0:
                edge = np.flatnonzero(self.get_plant_masks().obstacle_edge.T)
                if edge.size:
                    chosen = self.rng.choice(edge, size=min(12, edge.size, 2 + int(10*emitter)), replace=False)
                    self.v.ravel()[chosen] = .9
            else:
                n = max(1, int(2 * float(self.params.get("density", 1))))
                ys = self.rng.integers(1, self.height-1, n); xs = self.rng.integers(1, self.width-1, n)
                self.v[ys, xs] = .9
            self._next_perturbation += interval

    def _render_scene(self, elapsed):
        dark, mid, light = (np.asarray(c, dtype=np.float32) for c in self._palette())
        edge = np.clip(np.abs(self._lap(self.v)) * 5.0, 0, 1)
        body = np.clip(self.v * 1.4, 0, 1)
        history = np.clip(self.age / 25.0, 0, 1) * float(self.params.get("color_by_age", .6))
        canvas = dark + body[...,None] * (mid-dark) + (edge * float(self.params.get("edge_glow",.65)))[...,None] * (light-mid)
        canvas += history[...,None] * (light-dark) * .18
        illuminate = self.plant_modifier_strength("illuminate")
        if illuminate > 0:
            front = self.get_plant_masks().obstacle_edge.T
            canvas[front] = np.maximum(canvas[front], light * (.12 + .25*illuminate))
        return self._finish_canvas(np.clip(canvas,0,255).astype(np.uint8))

    def _palette(self):
        context = self.presentation_context
        if context is None or context.vibe_id == "neutral":
            return super()._palette()
        roles = context.palette_roles
        return tuple(
            np.asarray(roles[role], dtype=np.float32)
            for role in ("background_low", "primary", "accent")
        )

    def on_presentation_context_changed(self, _old, _new):
        self._last_render_elapsed = None
        self._cached_frame = None

    def logical_state(self):
        return (self.u.tobytes(), self.v.tobytes(), self.age.tobytes(), round(self._next_perturbation, 5))


_LegacyReactionDiffusionGardenAnimation.__module__ = "animation.plugins._legacy_reaction_diffusion_garden"

from animation.libraries.procedural_sculptures import CadencedSculpture


class ReactionDiffusionGardenAnimation(CadencedSculpture):
    ANIMATION_NAME="Reaction-Diffusion Garden"; ANIMATION_DESCRIPTION="Luminous chemistry grows coral, spots, and fingerprints"; ANIMATION_AUTHOR="LED Grid Team"; ANIMATION_VERSION="2.0"
    COMPONENT_ID="reaction_diffusion_garden"; SOURCE_FPS=20.; REGIMES={"coral":(.0545,.062),"spots":(.035,.065),"fingerprints":(.037,.060)}
    INTERACTION_TYPES=frozenset(("primary",))
    COMPOSER_INTERACTIONS={"point":{"kind":"primary","label":"Seed local chemistry"}}
    PLANT_MODIFIER_SUPPORT=frozenset(("habitat",))
    INSTALLATION_GEOMETRY_CONTACT=True
    COMPONENT_DEFAULTS={"motion":.52,"density":.58,"background_level":.14,"seed":9101,"morphology":"coral","growth_rate":1.,"seeding_mode":"scattered","edge_glow":.65,"color_by_age":.6,"perturbation_interval":24.}
    LEGACY_PRESET_KEYS=frozenset(("render_fps","simulation_hz"))
    def __init__(self,controller,config=None):
        super().__init__(controller,config)
        self._interaction_lock=Lock();self._pending_primary_perturbation=None
        self._primary_interactions_received=0;self._primary_interactions_applied=0;self._primary_interactions_rejected=0
        self._init_garden()
        self._geometry_foliage=np.zeros(self._shape,dtype=bool);self._geometry_cores=np.zeros(self._shape,dtype=bool);self._geometry_edge=np.zeros(self._shape,dtype=bool)
        self._geometry_identity=None;self._geometry_strength=0.;self._pending_geometry=None;self._geometry_activation_tick=0
    @classmethod
    @lru_cache(maxsize=None)
    def component_descriptor(cls):
        return ComponentDescriptor(component_id=cls.COMPONENT_ID,version=1,provider="python",role="animation",timing_policy="scaled_context",alpha_behavior="opaque",palette_policy="semantic",plant_capabilities=("effect_intent","simulation_inputs"),fidelity_exceptions=(),optional_simulation_inputs=("installation_geometry_contact",),defaults=cls.COMPONENT_DEFAULTS,parameter_normalizer=cls._normalized_parameters)
    def set_presentation_context(self,context):
        super().set_presentation_context(context)
        state=PlantModifierState.from_payload(context.canonical_scene.get("plants",{}).get("effects",{}));strength=state.strength("habitat",self.PLANT_MODIFIER_SUPPORT);contact=context.installation_geometry
        identity=(contact.identity,contact.available,contact.status) if isinstance(contact,InstallationGeometryContact) else None;effective=strength if identity is not None and contact.available else 0.;key=(identity,effective)
        if key==self._geometry_identity:return
        foliage=np.zeros(self._shape,dtype=bool);cores=np.zeros(self._shape,dtype=bool);edge=np.zeros(self._shape,dtype=bool)
        if effective and contact.foliage.shape==self._shape: foliage[:]=contact.foliage;cores[:]=contact.exact_globe_cores;edge[:]=contact.geometry.globe_edge
        self._pending_geometry=(foliage,cores,edge,effective);self._geometry_identity=key;self._geometry_activation_tick=max(0,self._last_sim_tick+1)
    def _activate_geometry(self,tick):
        if self._pending_geometry is None or tick<self._geometry_activation_tick:return
        self._geometry_foliage,self._geometry_cores,self._geometry_edge,self._geometry_strength=self._pending_geometry;self._pending_geometry=None
    def _init_garden(self):
        self.u=np.ones(self._shape,np.float32);self.v=np.zeros(self._shape,np.float32);self.age=np.zeros(self._shape,np.float32);n=max(4,int(10+36*self.params["density"]));
        if self.params["seeding_mode"]=="center":xs=np.full(n,self._shape[0]//2);ys=np.linspace(2,self._shape[1]-3,n).astype(int)
        elif self.params["seeding_mode"]=="column":xs=self.rng.integers(self._shape[0]//3,self._shape[0]*2//3,n);ys=self.rng.integers(2,self._shape[1]-2,n)
        else:xs=self.rng.integers(1,self._shape[0]-1,n);ys=self.rng.integers(2,self._shape[1]-2,n)
        self.v[xs,ys]=self.rng.uniform(.7,1.,n);self.u[xs,ys]=.2
    def reset_simulation(self):
        super().reset_simulation();self._init_garden()
        with self._interaction_lock:self._pending_primary_perturbation=None
    def handle_interaction(self,kind,x,y,strength=1.0):
        """Queue one fixed chemistry seed for the next 20 Hz semantic tick."""
        values=(x,y,strength)
        if kind!="primary" or any(isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)) for value in values):
            self._primary_interactions_rejected+=1;return False
        x_value,y_value,strength_value=map(float,values)
        if not (0.<=x_value<self._shape[0] and 0.<=y_value<self._shape[1] and 0.<strength_value<=1.):
            self._primary_interactions_rejected+=1;return False
        # Keep the authored 3x3 reagent disk fully on the field even when the
        # gesture lands at an edge.  Its chemistry is intentionally fixed; the
        # input strength is admission-only so it cannot alter scene semantics.
        center=(min(self._shape[0]-2,max(1,int(x_value))),min(self._shape[1]-2,max(1,int(y_value))))
        with self._interaction_lock:
            if self._pending_primary_perturbation is not None:
                self._primary_interactions_rejected+=1;return False
            self._pending_primary_perturbation=center;self._primary_interactions_received+=1
        return True
    def _consume_primary_perturbation(self):
        with self._interaction_lock:
            center=self._pending_primary_perturbation;self._pending_primary_perturbation=None
        if center is None:return
        x,y=center
        self.u[x-1:x+2,y-1:y+2]=.2;self.v[x-1:x+2,y-1:y+2]=.9
        self._primary_interactions_applied+=1
    def get_parameter_schema(self):
        s=super().get_parameter_schema();s.update({"morphology":{"type":"str","options":list(self.REGIMES),"default":"coral","description":"Reaction-front character"},"growth_rate":{"type":"float","min":.25,"max":2.,"default":1.,"description":"Chemistry growth rate"},"seeding_mode":{"type":"str","options":["scattered","column","center"],"default":"scattered","description":"Initial reaction seed"},"edge_glow":{"type":"float","min":0.,"max":1.5,"default":.65,"description":"Luminous front edge"},"color_by_age":{"type":"float","min":0.,"max":1.,"default":.6,"description":"Pattern-history coloring"},"perturbation_interval":{"type":"float","min":8.,"max":120.,"default":24.,"description":"Bounded reseed interval"}});return s
    @classmethod
    def _validate_local_parameters(cls,v):
        super()._validate_local_parameters(v)
        if v["morphology"] not in cls.REGIMES or v["seeding_mode"] not in {"scattered","column","center"}:raise ValueError("morphology or seeding_mode is invalid")
        for key,lo,hi in (("growth_rate",.25,2.),("edge_glow",0.,1.5),("color_by_age",0.,1.),("perturbation_interval",8.,120.)):
            if not lo<=float(v[key])<=hi:raise ValueError(f"{key} is out of range")
    @staticmethod
    def _lap(a):return -a+.2*(np.roll(a,1,0)+np.roll(a,-1,0)+np.roll(a,1,1)+np.roll(a,-1,1))+.05*(np.roll(np.roll(a,1,0),1,1)+np.roll(np.roll(a,1,0),-1,1)+np.roll(np.roll(a,-1,0),1,1)+np.roll(np.roll(a,-1,0),-1,1))
    def _step(self,tick):
        self._activate_geometry(tick)
        f,k=self.REGIMES[self.params["morphology"]]
        feed=f+self._geometry_foliage.astype(np.float32)*(.006*self._geometry_strength) if self._geometry_strength else f
        uvv=self.u*self.v*self.v;rate=.45*self.params["growth_rate"]*(.4+self.params["motion"]);next_u=np.clip(self.u+(self._lap(self.u)-uvv+feed*(1-self.u))*rate,0,1);next_v=np.clip(self.v+(.5*self._lap(self.v)+uvv-(feed+k)*self.v)*rate,0,1)
        if self._geometry_strength:next_u[self._geometry_cores]=self.u[self._geometry_cores];next_v[self._geometry_cores]=self.v[self._geometry_cores]
        self.u,self.v=next_u,next_v;self.age+=self.v>.18
        if tick and tick%max(8,int(self.params["perturbation_interval"]*self.SOURCE_FPS))==0:
            x=self.rng.integers(1,self._shape[0]-1,4);y=self.rng.integers(2,self._shape[1]-2,4);self.v[x,y]=.9
        self._consume_primary_perturbation()
    def generate_frame(self,time_elapsed,frame_count):
        tick,cached=self.begin_frame(time_elapsed)
        if cached:return cached
        self.advance_bounded(tick,self._step,10);value=np.clip(self.v*1.8,0,1);edge=np.clip(np.abs(self._lap(self.v))*self.params["edge_glow"]*3,0,1);age=np.clip(self.age/60,0,1)*self.params["color_by_age"]
        if self._geometry_strength: edge=np.maximum(edge,self._geometry_edge.astype(np.float32)*(.22*self._geometry_strength))
        return self.finish_frame(tick,self.colorize(np.maximum(value,age*.45),np.maximum(edge,age*.35)))
    def logical_state(self):return self.u.tobytes(),self.v.tobytes(),self.age.tobytes()
    def get_runtime_stats(self):
        with self._interaction_lock:pending=self._pending_primary_perturbation is not None
        return {"primary_interactions_received":self._primary_interactions_received,"primary_interactions_applied":self._primary_interactions_applied,"primary_interactions_rejected":self._primary_interactions_rejected,"primary_interaction_pending":pending}
