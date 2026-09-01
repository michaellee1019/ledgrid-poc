"""Vectorized Gray-Scott chemistry with persistent pattern age."""

from __future__ import annotations

import numpy as np

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

    def logical_state(self):
        return (self.u.tobytes(), self.v.tobytes(), self.age.tobytes(), round(self._next_perturbation, 5))


_LegacyReactionDiffusionGardenAnimation.__module__ = "animation.plugins._legacy_reaction_diffusion_garden"

from animation.libraries.procedural_sculptures import CadencedSculpture


class ReactionDiffusionGardenAnimation(CadencedSculpture):
    ANIMATION_NAME="Reaction-Diffusion Garden"; ANIMATION_DESCRIPTION="Luminous chemistry grows coral, spots, and fingerprints"; ANIMATION_AUTHOR="LED Grid Team"; ANIMATION_VERSION="2.0"
    COMPONENT_ID="reaction_diffusion_garden"; SOURCE_FPS=20.; REGIMES={"coral":(.0545,.062),"spots":(.035,.065),"fingerprints":(.037,.060)}
    COMPONENT_DEFAULTS={"motion":.52,"density":.58,"background_level":.14,"seed":9101,"morphology":"coral","growth_rate":1.,"seeding_mode":"scattered","edge_glow":.65,"color_by_age":.6,"perturbation_interval":24.}
    LEGACY_PRESET_KEYS=frozenset(("render_fps","simulation_hz"))
    def __init__(self,controller,config=None):super().__init__(controller,config);self._init_garden()
    def _init_garden(self):
        self.u=np.ones(self._shape,np.float32);self.v=np.zeros(self._shape,np.float32);self.age=np.zeros(self._shape,np.float32);n=max(4,int(10+36*self.params["density"]));
        if self.params["seeding_mode"]=="center":xs=np.full(n,self._shape[0]//2);ys=np.linspace(2,self._shape[1]-3,n).astype(int)
        elif self.params["seeding_mode"]=="column":xs=self.rng.integers(self._shape[0]//3,self._shape[0]*2//3,n);ys=self.rng.integers(2,self._shape[1]-2,n)
        else:xs=self.rng.integers(1,self._shape[0]-1,n);ys=self.rng.integers(2,self._shape[1]-2,n)
        self.v[xs,ys]=self.rng.uniform(.7,1.,n);self.u[xs,ys]=.2
    def reset_simulation(self):super().reset_simulation();self._init_garden()
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
        f,k=self.REGIMES[self.params["morphology"]];uvv=self.u*self.v*self.v;rate=.45*self.params["growth_rate"]*(.4+self.params["motion"]);self.u=np.clip(self.u+(self._lap(self.u)-uvv+f*(1-self.u))*rate,0,1);self.v=np.clip(self.v+(.5*self._lap(self.v)+uvv-(f+k)*self.v)*rate,0,1);self.age+=self.v>.18
        if tick and tick%max(8,int(self.params["perturbation_interval"]*self.SOURCE_FPS))==0:
            x=self.rng.integers(1,self._shape[0]-1,4);y=self.rng.integers(2,self._shape[1]-2,4);self.v[x,y]=.9
    def generate_frame(self,time_elapsed,frame_count):
        tick,cached=self.begin_frame(time_elapsed)
        if cached:return cached
        self.advance_bounded(tick,self._step,10);value=np.clip(self.v*1.8,0,1);edge=np.clip(np.abs(self._lap(self.v))*self.params["edge_glow"]*3,0,1);age=np.clip(self.age/60,0,1)*self.params["color_by_age"]
        return self.finish_frame(tick,self.colorize(np.maximum(value,age*.45),np.maximum(edge,age*.35)))
    def logical_state(self):return self.u.tobytes(),self.v.tobytes(),self.age.tobytes()
