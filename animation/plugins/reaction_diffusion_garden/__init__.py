"""Vectorized Gray-Scott chemistry with persistent pattern age."""

from __future__ import annotations

import numpy as np

from animation.libraries.procedural_living import ProceduralLivingBase


class ReactionDiffusionGardenAnimation(ProceduralLivingBase):
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
