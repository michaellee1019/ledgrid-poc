"""Migrating herds and hunters for the installed plant wall."""
from __future__ import annotations

import math
from functools import lru_cache
import numpy as np
from animation.core.component_catalog import ComponentDescriptor
from animation.core.plant_awareness import InstallationGeometryContact, PlantModifierState

from animation.libraries.procedural_sculptures import CadencedSculpture


class LivingEcosystemAnimation(CadencedSculpture):
    ANIMATION_NAME = "Living Ecosystem"
    ANIMATION_DESCRIPTION = "Migrating herds cross a growing wetland beneath a living canopy"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"
    COMPONENT_ID = "living_ecosystem"
    SOURCE_FPS = 20.0
    # ``habitat`` is manager-global intent, never an authored ecosystem
    # parameter.  It turns the provider-owned canopy/landmark plan on only for
    # a normal source tick.
    PLANT_MODIFIER_SUPPORT = frozenset(("habitat",))
    INSTALLATION_GEOMETRY_CONTACT = True
    COMPONENT_DEFAULTS = {"motion": .55, "density": .62, "background_level": .22, "seed": 7319, "migration": .55, "predator_pressure": .38, "canopy_density": .58, "mutation": .10, "night_life": .42}
    LEGACY_PRESET_KEYS = frozenset(("lifecycle_minutes", "day_length_seconds", "creature_density", "predator_ratio", "tree_density", "creature_size", "mutation_rate", "grass_regrowth", "predation_pressure", "pack_cohesion", "palette", "night_brightness", "glow_strength", "firefly_density", "water_shimmer", "color_saturation", "color_value", "output_gamma", "render_fps", "simulation_hz", "show_water"))

    def __init__(self, controller, config=None):
        super().__init__(controller, config); self._init_world()
        self._geometry_foliage = np.zeros(self._shape, dtype=bool)
        self._geometry_clearance = np.zeros(self._shape, dtype=bool)
        self._geometry_cores = np.zeros(self._shape, dtype=bool)
        self._geometry_escape_x = np.zeros(self._shape, dtype=np.float32)
        self._geometry_escape_y = np.zeros(self._shape, dtype=np.float32)
        self._geometry_identity = None
        self._geometry_strength = 0.0
        self._pending_geometry = None
        self._geometry_activation_tick = 0

    @classmethod
    @lru_cache(maxsize=None)
    def component_descriptor(cls):
        return ComponentDescriptor(
            component_id=cls.COMPONENT_ID, version=1, provider="python",
            role="animation", timing_policy="scaled_context",
            alpha_behavior="opaque", palette_policy="semantic",
            plant_capabilities=("effect_intent", "simulation_inputs"),
            fidelity_exceptions=(),
            optional_simulation_inputs=("installation_geometry_contact",),
            defaults=cls.COMPONENT_DEFAULTS,
            parameter_normalizer=cls._normalized_parameters,
        )

    def set_presentation_context(self, context):
        """Stage provider geometry without re-seeding or moving the habitat."""
        super().set_presentation_context(context)
        state = PlantModifierState.from_payload(
            context.canonical_scene.get("plants", {}).get("effects", {})
        )
        strength = state.strength("habitat", self.PLANT_MODIFIER_SUPPORT)
        contact = context.installation_geometry
        identity = (
            (contact.identity, contact.available, contact.status)
            if isinstance(contact, InstallationGeometryContact) else None
        )
        effective = strength if identity is not None and contact.available else 0.0
        key = (identity, effective)
        if key == self._geometry_identity:
            return
        foliage = np.zeros(self._shape, dtype=bool)
        clearance = np.zeros(self._shape, dtype=bool)
        cores = np.zeros(self._shape, dtype=bool)
        if effective and contact.foliage.shape == self._shape:
            # All simulation arrays remain strip-major like the provider.
            foliage[:] = contact.foliage
            clearance[:] = contact.clearance
            cores[:] = contact.exact_globe_cores
        # Foliage is a soft clearing preference.  This derived gradient is a
        # plan, not a contact rule; only exact globe cores reject a move.
        canopy = foliage.astype(np.float32) + clearance.astype(np.float32) * .35
        escape_x = np.roll(canopy, 1, 0) - np.roll(canopy, -1, 0)
        escape_y = np.roll(canopy, 1, 1) - np.roll(canopy, -1, 1)
        self._pending_geometry = (foliage, clearance, cores, escape_x, escape_y, effective)
        self._geometry_identity = key
        self._geometry_activation_tick = max(0, self._last_sim_tick + 1)
        # Keep the current source-frame cache intact.  The staged plan is
        # installed and painted by _step at the next ordinary source boundary.

    def _activate_geometry(self, tick):
        if self._pending_geometry is None or tick < self._geometry_activation_tick:
            return
        (self._geometry_foliage, self._geometry_clearance, self._geometry_cores,
         self._geometry_escape_x, self._geometry_escape_y, self._geometry_strength) = self._pending_geometry
        self._pending_geometry = None

    def _init_world(self):
        count = max(12, min(72, int(22 + 52 * self.params["density"])))
        self.herd = np.column_stack((self.rng.uniform(0, self._shape[0], count), self.rng.uniform(0, self._shape[1], count))).astype(np.float32)
        self.heading = self.rng.uniform(-math.pi, math.pi, count).astype(np.float32)
        hunters = max(2, int(count * self.params["predator_pressure"] * .18))
        self.hunters = np.column_stack((self.rng.uniform(0, self._shape[0], hunters), self.rng.uniform(0, self._shape[1], hunters))).astype(np.float32)
        trees = max(8, int(12 + 56 * self.params["canopy_density"]))
        self.trees = np.column_stack((self.rng.uniform(0, self._shape[0], trees), self.rng.uniform(0, self._shape[1], trees))).astype(np.float32)
        self.season = float(self.rng.random())

    def reset_simulation(self): super().reset_simulation(); self._init_world()

    def get_parameter_schema(self):
        schema = super().get_parameter_schema(); schema.update({
            "migration": {"type":"float", "min":0., "max":1., "default":.55, "description":"How decisively herds cross the habitat"},
            "predator_pressure": {"type":"float", "min":0., "max":1., "default":.38, "description":"Visible hunter pressure around herds"},
            "canopy_density": {"type":"float", "min":.1, "max":1., "default":.58, "description":"Woodland cover and clearings"},
            "mutation": {"type":"float", "min":0., "max":.5, "default":.10, "description":"Variation in each migration route"},
            "night_life": {"type":"float", "min":0., "max":1., "default":.42, "description":"Firefly activity in the wetland"},
        }); return schema

    @classmethod
    def _validate_local_parameters(cls, values):
        super()._validate_local_parameters(values)
        for key, low, high in (("migration",0.,1.), ("predator_pressure",0.,1.), ("canopy_density",.1,1.), ("mutation",0.,.5), ("night_life",0.,1.)):
            if not low <= float(values[key]) <= high: raise ValueError(f"{key} is out of range")

    def _step(self, tick):
        self._activate_geometry(tick)
        phase = tick * (.012 + self.params["motion"] * .025)
        target = np.arctan2(np.sin(self.herd[:, 1] * .06 + phase), np.cos(self.herd[:, 0] * .11 - phase))
        self.heading += np.sin(target - self.heading) * (.09 + .19 * self.params["migration"])
        if self._geometry_strength:
            ix = np.mod(np.rint(self.herd[:, 0]).astype(int), self._shape[0])
            iy = np.mod(np.rint(self.herd[:, 1]).astype(int), self._shape[1])
            # Use clearance to begin steering before a hard landmark contact.
            pressure = (
                self._geometry_foliage[ix, iy].astype(np.float32)
                + self._geometry_clearance[ix, iy].astype(np.float32) * .35
            )
            turn = (self._geometry_escape_y[ix, iy] * np.cos(self.heading)
                    - self._geometry_escape_x[ix, iy] * np.sin(self.heading))
            self.heading += turn * pressure * self._geometry_strength * .18
        self.heading += self.rng.normal(0., .012 + self.params["mutation"] * .06, self.heading.size)
        next_x = np.mod(self.herd[:, 0] + np.cos(self.heading) * (.10 + .26 * self.params["migration"]), self._shape[0])
        next_y = np.mod(self.herd[:, 1] + np.sin(self.heading) * .18 + .12, self._shape[1])
        if self._geometry_strength:
            hit = self._geometry_cores[np.rint(next_x).astype(int) % self._shape[0], np.rint(next_y).astype(int) % self._shape[1]]
            next_x[hit], next_y[hit] = self.herd[hit, 0], self.herd[hit, 1]
            self.heading[hit] += np.pi * .63
        self.herd[:, 0], self.herd[:, 1] = next_x, next_y
        old_hunters = self.hunters.copy()
        nearest = self.herd[np.arange(self.hunters.shape[0]) % self.herd.shape[0]]
        self.hunters += np.sign(nearest - self.hunters) * (.04 + self.params["predator_pressure"] * .12)
        self.hunters[:, 0] %= self._shape[0]; self.hunters[:, 1] %= self._shape[1]
        if self._geometry_strength:
            hx = np.rint(self.hunters[:, 0]).astype(int) % self._shape[0]
            hy = np.rint(self.hunters[:, 1]).astype(int) % self._shape[1]
            # A landmark is hard for every animal: retain the previous herd
            # target rather than allowing hunters to tunnel through its core.
            hit = self._geometry_cores[hx, hy]
            self.hunters[hit] = old_hunters[hit]
        self.season = (self.season + .0015 + self.params["motion"] * .002) % 1.

    def generate_frame(self, time_elapsed, frame_count):
        tick, cached = self.begin_frame(time_elapsed)
        if cached: return cached
        self.advance_bounded(tick, self._step, 10)
        x, y = self._x, self._y; river = np.exp(-np.square((x - .18 * np.sin(y * 4 + tick*.02)) / .20))
        field = np.clip(.18 + .28*np.sin(y*4 + self.season*math.tau) + .16*np.cos(x*6-y*2), 0., 1.)
        value, accent = np.maximum(field, river*.62), river*.35
        for tx, ty in self.trees: accent = np.maximum(accent, np.exp(-((x-(tx/self._shape[0]*2-1))**2 + (y-(ty/self._shape[1]*2-1))**2)/.008) * .62)
        for hx, hy in self.herd: accent[int(np.clip(round(hx),0,self._shape[0]-1)), int(np.clip(round(hy),0,self._shape[1]-1))] = 1.
        for hx, hy in self.hunters: value[int(np.clip(round(hx),0,self._shape[0]-1)), int(np.clip(round(hy),0,self._shape[1]-1))] = 1.
        fireflies = np.maximum(0., np.sin(x*31 + y*17 + tick*.17))**28 * self.params["night_life"]
        return self.finish_frame(tick, self.colorize(value, np.maximum(accent, fireflies)))

    def logical_state(self): return self.herd.tobytes(), self.hunters.tobytes(), round(self.season, 6)
