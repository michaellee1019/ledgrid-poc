"""Coherent gust fronts bending a field of procedural reed chains."""

from __future__ import annotations

import math
import numpy as np

from animation.libraries.procedural_living import ProceduralLivingBase


class _LegacyWindInTheReedsAnimation(ProceduralLivingBase):
    ANIMATION_NAME = "Wind in the Reeds"
    ANIMATION_DESCRIPTION = "Inverse-kinematic reeds sway in coherent, slow gust fronts"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    PLANT_MODIFIER_SUPPORT = frozenset(("shadow", "habitat", "slow_zone", "emitter", "illuminate"))
    SIM_HZ = 12.0

    def __init__(self, controller, config=None):
        super().__init__(controller, config)
        self.default_params.update({
            "wind": .65, "gustiness": .55, "stem_density": 1.0,
            "season": "late_summer", "motes": .45, "silhouette_strength": .5,
        })
        self.params = {**self.default_params, **self.config}
        self.rng = np.random.default_rng(int(self.params["seed"]))
        self._initialize_simulation()

    def get_parameter_schema(self):
        schema = super().get_parameter_schema()
        schema.update({
            "wind": {"type":"float","min":0.0,"max":2.0,"default":.65,"description":"Steady wind bend"},
            "gustiness": {"type":"float","min":0.0,"max":2.0,"default":.55,"description":"Coherent gust-front strength"},
            "stem_density": {"type":"float","min":.25,"max":2.0,"default":1.0,"description":"Number of stems"},
            "season": {"type":"str","default":"late_summer","options":["spring","late_summer","winter"],"description":"Stem presentation"},
            "motes": {"type":"float","min":0.0,"max":2.0,"default":.45,"description":"Presentation-only drifting motes"},
            "silhouette_strength": {"type":"float","min":0.0,"max":1.0,"default":.5,"description":"Dark foreground depth"},
        })
        return schema

    def _initialize_simulation(self):
        density = float(np.clip(self.params.get("density", 1.0), .2, 2.0))
        stem_density = float(np.clip(self.params.get("stem_density", 1.0), .25, 2.0))
        n = max(5, min(96, int(self.width * 1.6 * density * stem_density)))
        self.base_x = self.rng.uniform(0, self.width - 1, n).astype(np.float32)
        self.lengths = self.rng.uniform(self.height * .16, self.height * .52, n).astype(np.float32)
        self.flex = self.rng.uniform(.65, 1.25, n).astype(np.float32)
        self.phases = self.rng.uniform(0, np.pi * 2, n).astype(np.float32)
        self.bend = np.zeros(n, dtype=np.float32)
        self.gust_phase = 0.0
        self.pollen_x = np.empty(0, dtype=np.float32)
        self.pollen_y = np.empty(0, dtype=np.float32)
        self.pollen_life = np.empty(0, dtype=np.float32)

    def _simulate_step(self, dt):
        self.gust_phase = (self.gust_phase + dt * (.16 + .08 * float(self.params.get("wind", .65)))) % (np.pi * 2)
        wind = float(np.clip(self.params.get("wind", .65), 0, 2))
        gustiness = float(np.clip(self.params.get("gustiness", .55), 0, 2))
        front = np.sin(self.gust_phase - self.base_x * .19)
        lull = np.maximum(0.0, np.sin(self.gust_phase * .37 + .8))
        target = self.flex * (wind * .36 + gustiness * .48 * front * lull)
        slow = self.plant_modifier_strength("slow_zone")
        habitat = self.plant_modifier_strength("habitat")
        if slow > 0 or habitat > 0:
            masks = self.get_plant_masks()
            bx = np.clip(np.rint(self.base_x).astype(int), 0, self.width - 1)
            by = np.full_like(bx, self.height - 1)
            lee = masks.clearance.T[by, bx].astype(np.float32)
            target *= 1.0 - lee * (.7 * slow)
            if habitat > 0:
                sheltered = masks.foliage_edge.T[by, bx].astype(np.float32)
                target *= 1.0 - sheltered * (.35 * habitat)
        self.bend += (target - self.bend) * min(1.0, dt * 3.2)
        if self.pollen_x.size:
            self.pollen_x = np.mod(self.pollen_x + dt * (1.2 + wind), self.width)
            self.pollen_y -= dt * (.8 + gustiness * .25)
            self.pollen_life -= dt
            alive = (self.pollen_life > 0) & (self.pollen_y >= 0)
            self.pollen_x, self.pollen_y, self.pollen_life = self.pollen_x[alive], self.pollen_y[alive], self.pollen_life[alive]
        emitter = self.plant_modifier_strength("emitter")
        if emitter > 0 and self._logical_generation % max(8, int(40 - 24 * emitter)) == 0:
            edge = np.flatnonzero(self.get_plant_masks().foliage_edge.T)
            if edge.size:
                count = min(6, edge.size, 48 - self.pollen_x.size)
                if count > 0:
                    chosen = self.rng.choice(edge, count, replace=False)
                    ey, ex = np.unravel_index(chosen, (self.height, self.width))
                    self.pollen_x = np.concatenate((self.pollen_x, ex.astype(np.float32)))
                    self.pollen_y = np.concatenate((self.pollen_y, ey.astype(np.float32)))
                    self.pollen_life = np.concatenate((self.pollen_life, self.rng.uniform(2, 5, count).astype(np.float32)))

    def _render_scene(self, elapsed):
        canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        dark, mid, light = self._palette()
        canvas[:] = np.asarray(dark, dtype=np.uint8)
        season = self.params.get("season", "late_summer")
        tint = np.array((39, 112, 55) if season == "spring" else (mid if season != "winter" else (42, 61, 71)), dtype=np.float32)
        tip = np.array(light, dtype=np.float32)
        for i in range(self.base_x.size):
            segments = max(5, int(self.lengths[i] / 3))
            t = np.linspace(0, 1, segments)
            y = np.rint(self.height - 1 - t * self.lengths[i]).astype(int)
            x = np.rint(self.base_x[i] + self.bend[i] * (t ** 2) * self.width * .22
                         + np.sin(t * 3 + self.phases[i]) * t * .35).astype(int)
            valid = (x >= 0) & (x < self.width) & (y >= 0) & (y < self.height)
            color = tint[None, :] * (.25 + .75 * t[:, None])
            canvas[y[valid], x[valid]] = np.clip(color[valid], 0, 255).astype(np.uint8)
            if valid.any():
                j = np.flatnonzero(valid)[-1]
                canvas[y[j], x[j]] = tip
        shadow = self.plant_modifier_strength("shadow")
        illuminate = self.plant_modifier_strength("illuminate")
        if shadow > 0 or illuminate > 0:
            masks = self.get_plant_masks()
            if shadow > 0:
                foliage = masks.foliage.T
                canvas[foliage] = (canvas[foliage].astype(np.float32) * (1 - .9 * shadow)).astype(np.uint8)
            if illuminate > 0:
                edge = masks.obstacle_edge.T
                canvas[edge] = np.maximum(canvas[edge], (np.asarray(light) * (.12 + .3 * illuminate)).astype(np.uint8))
        motes = int(min(24, max(0, self.params.get("motes", .45) * 8)))
        for i in range(motes):
            x = int((i * 7.13 + elapsed * (1.4 + i % 3)) % self.width)
            y = int((i * 19.7 - elapsed * (.5 + i % 2)) % self.height)
            canvas[y, x] = np.maximum(canvas[y, x], (np.asarray(light) * .35).astype(np.uint8))
        if self.pollen_x.size:
            px = np.mod(self.pollen_x.astype(int), self.width)
            py = np.clip(self.pollen_y.astype(int), 0, self.height - 1)
            canvas[py, px] = np.maximum(canvas[py, px], (np.asarray(light) * .55).astype(np.uint8))
        return self._finish_canvas(canvas)

    def logical_state(self):
        return (round(self.gust_phase, 6), self.bend.tobytes(), self.pollen_x.tobytes(), self.pollen_y.tobytes(), self.pollen_life.tobytes())


_LegacyWindInTheReedsAnimation.__module__ = "animation.plugins._legacy_wind_in_the_reeds"

from animation.libraries.procedural_sculptures import CadencedSculpture


class WindInTheReedsAnimation(CadencedSculpture):
    ANIMATION_NAME="Wind in the Reeds"; ANIMATION_DESCRIPTION="A tactile field of reeds bends beneath travelling gust fronts"; ANIMATION_AUTHOR="LED Grid Team"; ANIMATION_VERSION="2.0"
    COMPONENT_ID="wind_in_the_reeds"; SOURCE_FPS=24.
    COMPONENT_DEFAULTS={"motion":.5,"density":.58,"background_level":.18,"seed":6101,"wind":.65,"gustiness":.55,"stem_density":1.,"season":"late_summer","motes":.45,"silhouette_strength":.5}
    def __init__(self,controller,config=None):super().__init__(controller,config);self._init_reeds()
    def _init_reeds(self):
        n=max(8,min(96,int((8+72*self.params["density"])*self.params["stem_density"])));self.base_x=self.rng.uniform(0,self._shape[0],n);self.lengths=self.rng.uniform(self._shape[1]*.12,self._shape[1]*.5,n);self.phases=self.rng.uniform(0,math.tau,n);self.bend=np.zeros(n);self.gust_phase=0.
    def reset_simulation(self):super().reset_simulation();self._init_reeds()
    def get_parameter_schema(self):
        s=super().get_parameter_schema();s.update({"wind":{"type":"float","min":0.,"max":2.,"default":.65,"description":"Steady reed bend"},"gustiness":{"type":"float","min":0.,"max":2.,"default":.55,"description":"Coherent travelling gusts"},"stem_density":{"type":"float","min":.25,"max":1.5,"default":1.,"description":"Reed field density"},"season":{"type":"str","options":["spring","late_summer","winter"],"default":"late_summer","description":"Stem character"},"motes":{"type":"float","min":0.,"max":2.,"default":.45,"description":"Floating seed motes"},"silhouette_strength":{"type":"float","min":0.,"max":1.,"default":.5,"description":"Foreground depth"}});return s
    @classmethod
    def _validate_local_parameters(cls,v):
        super()._validate_local_parameters(v)
        if v["season"] not in {"spring","late_summer","winter"}:raise ValueError("season is invalid")
        for key,lo,hi in (("wind",0.,2.),("gustiness",0.,2.),("stem_density",.25,1.5),("motes",0.,2.),("silhouette_strength",0.,1.)):
            if not lo<=float(v[key])<=hi:raise ValueError(f"{key} is out of range")
    def _step(self,tick):
        self.gust_phase=(self.gust_phase+.025+.028*self.params["motion"])%math.tau;target=(self.params["wind"]*.28+self.params["gustiness"]*.44*np.sin(self.gust_phase-self.base_x*.22))*np.sin(self.gust_phase*.43+.7);self.bend+=(target-self.bend)*.18
    def generate_frame(self,time_elapsed,frame_count):
        tick,cached=self.begin_frame(time_elapsed)
        if cached:return cached
        self.advance_bounded(tick,self._step,12);value=np.zeros(self._shape,np.float32);accent=np.zeros_like(value)
        for bx,length,bend,phase in zip(self.base_x,self.lengths,self.bend,self.phases):
            t=np.linspace(0,1,max(4,int(length/4)));x=np.clip(np.rint(bx+bend*t*t*self._shape[0]*.18+np.sin(t*3+phase)*.25),0,self._shape[0]-1).astype(int);y=np.clip(np.rint(self._shape[1]-1-t*length),0,self._shape[1]-1).astype(int);value[x,y]=np.maximum(value[x,y],.45+.5*t);accent[x[-1],y[-1]]=1.
        motes=np.maximum(0,np.sin(self._x*19+self._y*27+tick*.12))**30*self.params["motes"]*.35;value=np.maximum(value,motes);return self.finish_frame(tick,self.colorize(value*(1-self.params["silhouette_strength"]*.35),accent))
    def logical_state(self):return round(self.gust_phase,6),self.bend.tobytes()
