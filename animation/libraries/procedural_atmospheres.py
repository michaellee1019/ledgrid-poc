"""Shared allocation-conscious renderer for tall procedural atmospheres."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import numpy as np

from animation import AnimationBase


MOOD_PALETTES: Mapping[str, Mapping[str, tuple[float, float, float]]] = {
    "moonlit": {"low": (1, 5, 14), "mid": (8, 38, 64), "high": (76, 178, 205)},
    "boreal": {"low": (1, 8, 13), "mid": (7, 61, 52), "high": (60, 214, 151)},
    "violet": {"low": (6, 2, 18), "mid": (48, 17, 80), "high": (191, 94, 219)},
    "ember": {"low": (12, 3, 1), "mid": (91, 28, 8), "high": (238, 126, 42)},
    "garden": {"low": (2, 8, 9), "mid": (16, 45, 31), "high": (129, 176, 116)},
}


class ProceduralAtmosphereBase(AnimationBase):
    """Source-rate cached analytic simulation composed for a 32x138 wall."""

    SCENE = ""
    DEFAULT_MOOD = "moonlit"
    DEFAULT_SEED = 1701
    PLANT_MODIFIER_SUPPORT = frozenset()

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.width, self.height = self.get_strip_info()
        self.default_params.update({
            "motion": 0.42,
            "density": 0.46,
            "mood": self.DEFAULT_MOOD,
            "brightness": 0.46,
            "source_fps": 30.0,
            "seed": self.DEFAULT_SEED,
        })
        self.params = {**self.default_params, **self.config}
        self._x = np.linspace(0.0, 1.0, self.width, dtype=np.float32)[:, None]
        self._y = np.linspace(0.0, 1.0, self.height, dtype=np.float32)[None, :]
        self._rgb = np.empty((self.width, self.height, 3), dtype=np.float32)
        self._field = np.empty((self.width, self.height), dtype=np.float32)
        self._cached_frame = None
        self._last_source_tick = None
        self._last_elapsed = None
        self._simulation_time = 0.0
        self._reset_seeded_state()

    def _reset_seeded_state(self) -> None:
        rng = np.random.default_rng(int(self.params.get("seed", self.DEFAULT_SEED)))
        self._phase = rng.uniform(0.0, 2.0 * np.pi, 16).astype(np.float32)
        self._offset = rng.uniform(0.0, 1.0, 16).astype(np.float32)
        self._frequency = rng.uniform(0.65, 2.4, 16).astype(np.float32)
        self._last_source_tick = None
        self._last_elapsed = None
        self._simulation_time = 0.0

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.pop("color_saturation", None)
        schema.pop("color_value", None)
        schema["brightness"].update({"default": 0.46, "max": 0.8,
                                      "description": "Conservative installation luminance"})
        schema.update({
            "motion": {"type": "float", "min": 0.0, "max": 2.0, "default": 0.42,
                       "description": "Macro advection and fine-motion rate"},
            "density": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.46,
                        "description": "Scene coverage or event population"},
            "mood": {"type": "str", "options": list(MOOD_PALETTES), "default": self.DEFAULT_MOOD,
                     "description": "Color and light atmosphere"},
            "source_fps": {"type": "float", "min": 20.0, "max": 40.0, "default": 30.0,
                           "description": "Bounded source redraw cadence"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": self.DEFAULT_SEED,
                     "description": "Deterministic long-form scene seed"},
        })
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        old_seed = int(self.params.get("seed", self.DEFAULT_SEED))
        super().update_parameters(new_params)
        if "seed" in new_params and int(new_params["seed"]) != old_seed:
            self._reset_seeded_state()
        self._last_source_tick = None

    def generate_frame(self, time_elapsed: float, frame_count: int):
        fps = float(np.clip(self.params.get("source_fps", 30.0), 20.0, 40.0))
        tick = int(max(0.0, float(time_elapsed)) * fps + 1.0e-7)
        if self._last_source_tick == tick and self._cached_frame is not None:
            return self.rendered_frame(self._cached_frame, changed=False)

        elapsed = max(0.0, float(time_elapsed))
        if self._last_elapsed is not None:
            # A stalled manager cannot create an unbounded simulation leap.
            dt = min(0.1, max(0.0, elapsed - self._last_elapsed))
            self._simulation_time += dt * float(np.clip(self.params.get("motion", .42), 0.0, 2.0))
        self._last_elapsed = elapsed
        self._last_source_tick = tick
        self._render_scene(self._simulation_time)
        self._apply_plant_modifiers()
        self._rgb *= float(np.clip(self.params.get("brightness", .46), 0.0, .8))
        np.clip(self._rgb, 0.0, 255.0, out=self._rgb)
        frame = self.next_frame_buffer(clear=False)
        np.copyto(frame, self._rgb.reshape((-1, 3)), casting="unsafe")
        self._cached_frame = frame
        return self.rendered_frame(frame, changed=True)

    def _palette(self):
        palette = MOOD_PALETTES.get(str(self.params.get("mood", self.DEFAULT_MOOD)),
                                    MOOD_PALETTES[self.DEFAULT_MOOD])
        return tuple(np.asarray(palette[key], dtype=np.float32) for key in ("low", "mid", "high"))

    def _paint(self, field: np.ndarray, accent: Optional[np.ndarray] = None) -> None:
        low, mid, high = self._palette()
        f = np.clip(field, 0.0, 1.0)
        lower = np.minimum(f * 2.0, 1.0)[..., None]
        upper = np.maximum(f * 2.0 - 1.0, 0.0)[..., None]
        self._rgb[:] = low + (mid - low) * lower + (high - mid) * upper
        if accent is not None:
            self._rgb += np.maximum(accent, 0.0)[..., None] * high * 0.55

    def _render_scene(self, t: float) -> None:
        density = float(np.clip(self.params.get("density", .46), 0.0, 1.0))
        if self.SCENE == "rain":
            self._rain(t, density)
        elif self.SCENE == "aurora":
            self._aurora(t, density)
        elif self.SCENE == "cloud":
            self._cloud(t, density)
        elif self.SCENE == "waterfall":
            self._waterfall(t, density)
        elif self.SCENE == "tidal":
            self._tidal(t, density)
        else:
            raise ValueError(f"unknown atmosphere scene {self.SCENE!r}")

    def _rain(self, t: float, density: float) -> None:
        city = .08 + .08 * np.sin(17.0 * self._x + self._phase[0]) * (self._y > .52)
        field = city + .05 * (1.0 - self._y)
        count = 3 + int(11 * density)
        trails = np.zeros_like(self._field)
        for i in range(count):
            x0 = self._offset[i] + .055 * np.sin(t * (.4 + self._frequency[i]) + self._phase[i])
            head = np.mod(self._offset[(i + 5) % 16] + t * (.13 + .09 * self._frequency[i]), 1.18) - .08
            dx = np.minimum(np.abs(self._x - x0), 1.0 - np.abs(self._x - x0))
            track = np.exp(-((dx / (.018 + .012 * density)) ** 2))
            tail = np.exp(-np.maximum(0.0, head - self._y) * (20.0 - 8.0 * density))
            trails += track * tail * (self._y <= head + .018)
        self._paint(np.clip(field + trails * (.14 + .16 * density), 0, 1), trails * .12)

    def _aurora(self, t: float, density: float) -> None:
        field = np.full_like(self._field, .025)
        curtains = 2 + int(4 * density)
        for i in range(curtains):
            center = self._offset[i] + .16 * np.sin(self._y * (5.0 + self._frequency[i]) + t * .33 + self._phase[i])
            width = .025 + .025 * density
            sheet = np.exp(-((self._x - center) / width) ** 2)
            reach = np.clip((1.1 - self._y) * (1.5 + density), 0.0, 1.0)
            fold = .55 + .45 * np.sin(self._y * 15.0 + t * .7 + self._phase[(i + 7) % 16]) ** 2
            field += sheet * reach * fold * (.16 + .09 * density)
        stars = (np.sin(self._x * 173.0 + self._y * 311.0 + self._phase[12]) > .997).astype(np.float32)
        self._paint(np.clip(field + stars * (.08 + .12 * density), 0, 1), field * .12)

    def _cloud(self, t: float, density: float) -> None:
        n1 = np.sin(self._x * 7.0 + self._y * 9.0 - t * .18 + self._phase[0])
        n2 = np.sin(self._x * 15.0 - self._y * 5.0 + t * .11 + self._phase[1])
        n3 = np.sin(self._x * 27.0 + self._y * 21.0 - t * .07 + self._phase[2])
        fog = np.clip(.5 + .25 * n1 + .16 * n2 + .09 * n3 - (.72 - density), 0, 1)
        canyon = np.exp(-((self._x - (.5 + .12 * np.sin(self._y * 4.0 + t * .08))) / .13) ** 2)
        light = canyon * (1.0 - fog) * (.25 + .3 * (1.0 - self._y))
        edge = np.maximum(0.0, fog - np.roll(fog, 1, axis=1))
        self._paint(.035 + fog * (.12 + .24 * density) + light, edge * .5)

    def _waterfall(self, t: float, density: float) -> None:
        field = .025 + .035 * (1.0 - self._y)
        streams = np.zeros_like(self._field)
        count = 3 + int(10 * density)
        for i in range(count):
            bend = self._offset[i] + .045 * np.sin(self._y * (7 + i % 4) + self._phase[i])
            dx = np.abs(self._x - bend)
            pulse = .55 + .45 * np.sin(self._y * 31.0 + t * (1.2 + self._frequency[i])) ** 2
            streams += np.exp(-((dx / (.012 + .01 * density)) ** 2)) * pulse
        ledges = (np.sin(self._x * 19.0 + self._y * 48.0 + self._phase[10]) > .94).astype(np.float32)
        mist = np.exp(-((self._y - (.72 + .08 * np.sin(self._x * 11.0))) / .06) ** 2) * density
        self._paint(np.clip(field + streams * .23 + ledges * .05 + mist * .1, 0, 1), mist * .22)

    def _tidal(self, t: float, density: float) -> None:
        surface = .47 + .035 * np.sin(self._x * 8.0 - t * .42) + .018 * np.sin(self._x * 19.0 + t * .25)
        depth = np.clip((self._y - surface) * 2.0, 0.0, 1.0)
        ocean = (self._y >= surface) * (.035 + .09 * (1.0 - depth))
        wakes = np.zeros_like(self._field)
        count = 3 + int(9 * density)
        for i in range(count):
            cx = np.mod(self._offset[i] + t * (.018 + .015 * self._frequency[i]), 1.0)
            cy = .52 + self._offset[(i + 6) % 16] * .43
            radius = np.sqrt((self._x - cx) ** 2 + ((self._y - cy) * .52) ** 2)
            wakes += np.exp(-radius * (30.0 - 10.0 * density)) * (.5 + .5 * np.sin(radius * 95.0 - t * 2.0))
        plankton = (np.sin(self._x * 157.0 + self._y * 263.0 + self._phase[9]) > (.994 - .02 * density)).astype(np.float32)
        glow = (wakes * .28 + plankton * .18) * (self._y >= surface)
        self._paint(np.clip(ocean + glow, 0, 1), glow * .65)

    def _apply_plant_modifiers(self) -> None:
        strengths = {name: self.plant_modifier_strength(name) for name in self.PLANT_MODIFIER_SUPPORT}
        if not any(value > 0.0 for value in strengths.values()):
            return
        masks = self.get_plant_masks()
        obstacle = masks.obstacle_flat.reshape(self.width, self.height)
        edge = masks.obstacle_edge.reshape(self.width, self.height)
        if strengths.get("shadow", 0.0) > 0.0:
            self._rgb[obstacle] *= 1.0 - .82 * strengths["shadow"]
        if strengths.get("refract", 0.0) > 0.0:
            amount = strengths["refract"]
            halo = np.exp(-masks.distance.reshape(self.width, self.height) / (1.0 + 4.0 * amount))
            self._rgb += halo[..., None] * np.roll(self._rgb, 1, axis=0) * (.08 * amount)
        if strengths.get("illuminate", 0.0) > 0.0:
            self._rgb[edge] += 38.0 * strengths["illuminate"]
        if strengths.get("emitter", 0.0) > 0.0:
            pulse = .5 + .5 * np.sin(self._simulation_time * 2.0 + self._x * 9.0 + self._y * 13.0)
            self._rgb[edge] += pulse[edge, None] * (42.0 * strengths["emitter"])

    def get_runtime_stats(self) -> Dict[str, Any]:
        return {"scene": self.SCENE, "source_tick": self._last_source_tick,
                "simulation_time": self._simulation_time,
                "source_fps": float(self.params.get("source_fps", 30.0))}
