"""Coherent gust fronts bending a field of procedural reed chains."""

from __future__ import annotations

import numpy as np

from animation.libraries.procedural_living import ProceduralLivingBase


class WindInTheReedsAnimation(ProceduralLivingBase):
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
