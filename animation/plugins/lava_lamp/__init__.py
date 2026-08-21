"""Thermal metaball lava with calibrated foliage and planter-bowl semantics."""

from __future__ import annotations

import math
import threading
from collections import deque
from typing import Any, Dict, Optional

import numpy as np

from animation import AnimationBase
from animation.core.plant_awareness import GLOBE_REGION_ORDER

PALETTES = {
    "classic": ((92, 8, 0), (255, 64, 4), (255, 215, 70), (255, 80, 8)),
    "ruby": ((70, 0, 14), (225, 8, 40), (255, 138, 95), (255, 24, 52)),
    "violet": ((34, 4, 70), (150, 26, 235), (255, 125, 245), (180, 38, 255)),
    "ocean": ((0, 26, 70), (0, 135, 220), (105, 255, 245), (0, 160, 255)),
    "toxic": ((12, 48, 0), (80, 220, 10), (225, 255, 80), (100, 255, 10)),
    "candy": ((65, 2, 60), (255, 42, 175), (80, 255, 240), (255, 70, 210)),
    "solar": ((80, 12, 0), (255, 115, 0), (255, 250, 150), (255, 155, 15)),
    "mono": ((24, 24, 28), (145, 150, 165), (255, 255, 255), (190, 200, 225)),
}

SEMANTIC_PALETTE_ROLES = ("secondary", "primary", "accent", "background_high")


class LavaLampAnimation(AnimationBase):
    ANIMATION_NAME = "Lava Lamp"
    ANIMATION_DESCRIPTION = "Thermal wax blobs rise, merge, cool, and flow around the living wall"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SOURCE_FPS = 100.0
    PHYSICS_DT = 0.01
    MAX_BLOBS = 16
    INTERACTION_TYPES = frozenset(("primary",))
    PLANT_MODIFIER_SUPPORT = frozenset((
        "illuminate", "shadow", "refract", "attractor", "repulsor",
        "slow_zone", "obstacle", "bumper", "portal", "hazard",
        "habitat", "emitter",
    ))

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "speed": 1.0,
            "brightness": 0.72,
            "blob_count": 7,
            "blob_scale": 1.0,
            "viscosity": 0.68,
            "heat": 0.72,
            "turbulence": 0.24,
            "glow": 0.58,
            "palette": "classic",
            "background": "glass",
            "seed": 1977,
            "interaction_radius": 8.0,
            "interaction_strength": 1.0,
        })
        self.params = {**self.default_params, **(config or {})}
        self.width, self.height = self.get_strip_info()
        self.rng = np.random.default_rng(int(self.params["seed"]))
        self._interaction_lock = threading.Lock()
        self._interactions = deque(maxlen=16)
        self._allocate_geometry()
        self._reset_simulation()

    def _allocate_geometry(self) -> None:
        self._rows, self._cols = np.indices((self.height, self.width), dtype=np.float32)
        self._field = np.zeros((self.height, self.width), dtype=np.float32)
        self._temperature_field = np.zeros_like(self._field)
        self._canvas_float = np.zeros((self.height, self.width, 3), dtype=np.float32)
        self._canvas_u8 = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self._foliage = np.zeros((self.height, self.width), dtype=bool)
        self._globes = np.zeros_like(self._foliage)
        self._globe_edge = np.zeros_like(self._foliage)
        self._obstacle_edge = np.zeros_like(self._foliage)
        self._distance = np.full_like(self._field, float(max(self.width, self.height)))
        self._normal_x = np.zeros_like(self._field)
        self._normal_y = np.zeros_like(self._field)
        self._regions: Dict[str, np.ndarray] = {}
        self._region_centers: Dict[str, tuple[float, float]] = {}
        self._plant_key = None
        self._plant_error = ""

    def _reset_simulation(self) -> None:
        self.x = np.zeros(self.MAX_BLOBS, dtype=np.float32)
        self.y = np.zeros(self.MAX_BLOBS, dtype=np.float32)
        self.vx = np.zeros(self.MAX_BLOBS, dtype=np.float32)
        self.vy = np.zeros(self.MAX_BLOBS, dtype=np.float32)
        self.radius = np.zeros(self.MAX_BLOBS, dtype=np.float32)
        self.temperature = np.zeros(self.MAX_BLOBS, dtype=np.float32)
        self.cooldown = np.zeros(self.MAX_BLOBS, dtype=np.float32)
        self.phase = np.zeros(self.MAX_BLOBS, dtype=np.float32)
        self.active = np.zeros(self.MAX_BLOBS, dtype=bool)
        count = max(3, min(12, int(self.params.get("blob_count", 7))))
        scale = float(self.params.get("blob_scale", 1.0))
        for index in range(count):
            self.active[index] = True
            self.x[index] = (index + 0.5) * self.width / count
            self.x[index] += self.rng.uniform(-1.2, 1.2)
            self.y[index] = self.height - 7.0 - self.rng.uniform(0.0, 20.0)
            self.radius[index] = scale * self.rng.uniform(3.0, 5.0)
            self.temperature[index] = self.rng.uniform(0.48, 0.92)
            self.vx[index] = self.rng.uniform(-0.5, 0.5)
            self.vy[index] = self.rng.uniform(-1.8, 0.2)
            self.phase[index] = self.rng.uniform(0.0, math.tau)
        self.previous_x = self.x.copy()
        self.previous_y = self.y.copy()
        self.previous_radius = self.radius.copy()
        self.simulation_time = 0.0
        self._accumulator = 0.0
        self._last_source_time: Optional[float] = None
        self._last_semantic_time: Optional[float] = None
        self._last_render_tick: Optional[int] = None
        self._cached_frame: Optional[np.ndarray] = None
        self._steps = 0
        self._dropped_steps = 0
        self._midline_up = 0
        self._midline_down = 0
        self._splits = 0
        self._merges = 0
        self._interactions_applied = 0
        self._plant_contacts = 0
        self._portal_transfers = 0
        self._hazard_recycles = 0
        self._emissions = 0
        self._emitter_clock = 0.0
        self._emitter_region = 0
        self._initial_wax_area = self.wax_area

    @property
    def wax_area(self) -> float:
        return float(np.sum(np.square(self.radius[self.active], dtype=np.float64)))

    def start(self):
        super().start()
        self.width, self.height = self.get_strip_info()
        self.rng = np.random.default_rng(int(self.params.get("seed", 1977)))
        self._allocate_geometry()
        self._reset_simulation()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            "speed": {"type": "float", "min": 0.1, "max": 4.0, "default": 1.0,
                      "description": "Simulation-time scale; render cadence remains 100 FPS."},
            "blob_count": {"type": "int", "min": 3, "max": 12, "default": 7,
                           "description": "Target number of thermal wax bodies."},
            "blob_scale": {"type": "float", "min": 0.6, "max": 1.8, "default": 1.0,
                           "description": "Typical wax-blob radius."},
            "viscosity": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.68,
                          "description": "Drag and resistance to rapid motion."},
            "heat": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.72,
                     "description": "Bottom-heater and buoyancy strength."},
            "turbulence": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.24,
                           "description": "Slow lateral thermal currents."},
            "glow": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.58,
                     "description": "Wax halo and glass bloom."},
            "palette": {"type": "str", "options": list(PALETTES), "default": "classic",
                        "description": "Hot/cool wax color family."},
            "background": {"type": "str", "options": ["black", "glass", "gradient", "ember"],
                           "default": "glass", "description": "Lamp vessel atmosphere."},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 1977,
                     "description": "Repeatable initial blob arrangement."},
            "interaction_radius": {"type": "float", "min": 2.0, "max": 16.0, "default": 8.0,
                                   "description": "Click-to-stir influence radius."},
            "interaction_strength": {"type": "float", "min": 0.1, "max": 2.0, "default": 1.0,
                                     "description": "Vortex and heat impulse multiplier."},
        })
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        old_seed = int(self.params.get("seed", 1977))
        old_count = int(self.params.get("blob_count", 7))
        super().update_parameters(new_params)
        if {
            "plant_aware", "plant_modifiers", "plant_clearance",
            "plant_mask_path", "plant_globe_mask_path",
        } & new_params.keys():
            self._plant_key = None
        if int(self.params.get("seed", 1977)) != old_seed:
            self.rng = np.random.default_rng(int(self.params["seed"]))
            self._reset_simulation()
        elif "blob_count" in new_params and int(self.params["blob_count"]) != old_count:
            self._reconcile_blob_count()
        self._last_render_tick = None

    def on_presentation_context_changed(self, old_context, new_context) -> None:
        """Refresh presentation without resetting wax state or consuming RNG."""
        self._last_render_tick = None

    def _presentation_palette(self):
        """Resolve wax colors without changing authored palette identity.

        Neutral and direct/headless rendering deliberately return the original
        authored palette.  Active semantic profiles replace the four visual
        palette slots only; simulation state, timing, parameters, and RNG are
        not consulted or mutated here.
        """
        authored = PALETTES[
            str(getattr(self, "effective_params", self.params).get("palette", "classic"))
        ]
        context = self.presentation_context
        if (
            context is None
            or context.vibe_id == "neutral"
            or self.VIBE_COLOR_POLICY != "semantic"
            or "palette_roles" not in self.VIBE_CAPABILITIES
        ):
            return authored
        return tuple(
            context.palette_roles.get(role, authored[index])
            for index, role in enumerate(SEMANTIC_PALETTE_ROLES)
        )

    def _reconcile_blob_count(self) -> None:
        target = max(3, min(12, int(self.params.get("blob_count", 7))))
        while int(np.count_nonzero(self.active)) < target:
            slot = self._free_slot()
            if slot is None:
                break
            source = int(np.argmax(np.where(self.active, self.radius, -1.0)))
            if not self._split_blob(source, slot=slot):
                break
        while int(np.count_nonzero(self.active)) > target:
            indices = np.flatnonzero(self.active)
            left, right = int(indices[0]), int(indices[-1])
            if left == right:
                break
            self._merge_pair(left, right)

    def handle_interaction(
        self, kind: str, x: float, y: float, strength: float = 1.0
    ) -> bool:
        if kind != "primary":
            return False
        with self._interaction_lock:
            self._interactions.append((float(x), float(y), float(strength)))
        return True

    def _active_modifier(self, name: str) -> float:
        strength = self.plant_modifier_strength(name)
        return strength if strength > 0.0 else 0.0

    def _plant_effects_active(self) -> bool:
        return any(self._active_modifier(name) > 0.0 for name in self.PLANT_MODIFIER_SUPPORT)

    def _refresh_plant_geometry(self) -> None:
        strengths = tuple(
            (name, self._active_modifier(name)) for name in sorted(self.PLANT_MODIFIER_SUPPORT)
        )
        masks = self.get_plant_masks()
        key = (id(masks), strengths)
        if key == self._plant_key:
            return
        self._foliage[:] = masks.foliage.T[::-1]
        self._globes[:] = masks.globes.T[::-1]
        self._globe_edge[:] = masks.globe_edge.T[::-1]
        self._obstacle_edge[:] = masks.obstacle_edge.T[::-1]
        self._distance[:] = masks.distance.T[::-1]
        self._normal_x[:] = masks.normal_x.T[::-1]
        self._normal_y[:] = -masks.normal_y.T[::-1]
        self._regions.clear()
        self._region_centers.clear()
        for name in GLOBE_REGION_ORDER:
            source = masks.globe_region_masks.get(name)
            if source is None:
                continue
            region = source.T[::-1].copy()
            if not np.any(region):
                continue
            self._regions[name] = region
            rows, cols = np.nonzero(region)
            self._region_centers[name] = (float(cols.mean()), float(rows.mean()))
        self._plant_error = masks.error
        self._plant_key = key

    def _free_slot(self) -> Optional[int]:
        free = np.flatnonzero(~self.active)
        return int(free[0]) if free.size else None

    def _split_blob(self, index: int, slot: Optional[int] = None) -> bool:
        if not self.active[index] or self.radius[index] < 2.2:
            return False
        slot = self._free_slot() if slot is None else slot
        if slot is None:
            return False
        new_radius = float(self.radius[index]) / math.sqrt(2.0)
        offset = min(new_radius * 0.65, 2.5)
        self.radius[index] = new_radius
        self.active[slot] = True
        self.radius[slot] = new_radius
        self.x[slot] = np.clip(self.x[index] + offset, new_radius, self.width - 1 - new_radius)
        self.x[index] = np.clip(self.x[index] - offset, new_radius, self.width - 1 - new_radius)
        self.y[slot] = self.y[index]
        self.vx[slot] = self.vx[index] + 1.2
        self.vx[index] -= 1.2
        self.vy[slot] = self.vy[index] - 0.4
        self.temperature[slot] = self.temperature[index]
        self.cooldown[slot] = self.cooldown[index] = 0.8
        self.phase[slot] = self.phase[index] + math.pi
        self.previous_x[slot] = self.x[slot]
        self.previous_y[slot] = self.y[slot]
        self.previous_radius[slot] = self.radius[slot]
        self._splits += 1
        return True

    def _merge_pair(self, left: int, right: int) -> None:
        left_area = float(self.radius[left] ** 2)
        right_area = float(self.radius[right] ** 2)
        total = max(1e-6, left_area + right_area)
        self.x[left] = (self.x[left] * left_area + self.x[right] * right_area) / total
        self.y[left] = (self.y[left] * left_area + self.y[right] * right_area) / total
        self.vx[left] = (self.vx[left] * left_area + self.vx[right] * right_area) / total
        self.vy[left] = (self.vy[left] * left_area + self.vy[right] * right_area) / total
        self.temperature[left] = (
            self.temperature[left] * left_area + self.temperature[right] * right_area
        ) / total
        self.radius[left] = math.sqrt(total)
        self.active[right] = False
        self.radius[right] = 0.0
        self._merges += 1

    def _drain_interactions(self) -> None:
        with self._interaction_lock:
            events = list(self._interactions)
            self._interactions.clear()
        for click_x, click_y, input_strength in events:
            indices = np.flatnonzero(self.active)
            if not indices.size:
                continue
            dx = self.x[indices] - click_x
            dy = self.y[indices] - click_y
            distances = np.hypot(dx, dy)
            radius = float(self.params.get("interaction_radius", 8.0))
            weights = np.clip(1.0 - distances / max(0.1, radius), 0.0, 1.0)
            if not np.any(weights > 0.0):
                nearest = int(np.argmin(distances))
                weights[nearest] = 0.25
            strength = float(self.params.get("interaction_strength", 1.0)) * input_strength
            inverse = 1.0 / np.maximum(distances, 0.75)
            self.vx[indices] += (-dy * inverse) * weights * 9.0 * strength
            self.vy[indices] += (dx * inverse) * weights * 9.0 * strength
            self.temperature[indices] = np.clip(
                self.temperature[indices] + weights * 0.32 * strength, 0.0, 1.0
            )
            target = int(indices[int(np.argmax(weights * self.radius[indices]))])
            if weights[int(np.argmax(weights * self.radius[indices]))] > 0.45:
                self._split_blob(target)
            self._interactions_applied += 1

    def _sample(self, field: np.ndarray, index: int) -> float:
        row = max(0, min(self.height - 1, int(round(float(self.y[index])))))
        col = max(0, min(self.width - 1, int(round(float(self.x[index])))))
        return float(field[row, col])

    def _region_at(self, index: int) -> Optional[str]:
        row = max(0, min(self.height - 1, int(round(float(self.y[index])))))
        col = max(0, min(self.width - 1, int(round(float(self.x[index])))))
        for name in GLOBE_REGION_ORDER:
            region = self._regions.get(name)
            if region is not None and region[row, col]:
                return name
        return None

    def _apply_plant_dynamics(self, index: int, dt: float) -> None:
        row = max(0, min(self.height - 1, int(round(float(self.y[index])))))
        col = max(0, min(self.width - 1, int(round(float(self.x[index])))))
        distance = float(self._distance[row, col])
        influence = max(0.0, 1.0 - distance / 7.0)
        nx = float(self._normal_x[row, col])
        ny = float(self._normal_y[row, col])
        attractor = self._active_modifier("attractor")
        repulsor = self._active_modifier("repulsor")
        if attractor:
            self.vx[index] -= nx * influence * attractor * 4.0 * dt
            self.vy[index] -= ny * influence * attractor * 4.0 * dt
        if repulsor:
            self.vx[index] += nx * influence * repulsor * 6.0 * dt
            self.vy[index] += ny * influence * repulsor * 6.0 * dt
        slow = self._active_modifier("slow_zone")
        if slow and influence:
            factor = max(0.25, 1.0 - slow * influence * 0.75)
            self.vx[index] *= factor
            self.vy[index] *= factor

        in_globe = bool(self._globes[row, col])
        if not in_globe:
            return
        self._plant_contacts += 1
        portal = self._active_modifier("portal")
        if portal and self.cooldown[index] <= 0.0:
            source = self._region_at(index)
            if source and len(self._region_centers) > 1:
                start = GLOBE_REGION_ORDER.index(source)
                for offset in range(1, len(GLOBE_REGION_ORDER) + 1):
                    target = GLOBE_REGION_ORDER[(start + offset) % len(GLOBE_REGION_ORDER)]
                    if target in self._region_centers:
                        self.x[index], self.y[index] = self._region_centers[target]
                        self.cooldown[index] = 1.0 + (1.0 - portal)
                        self._portal_transfers += 1
                        return
        hazard = self._active_modifier("hazard")
        if hazard:
            self.y[index] = self.height - self.radius[index] - 1.0
            self.x[index] = 1.0 + ((self.x[index] + 7.0) % max(2.0, self.width - 2.0))
            self.temperature[index] = 0.2 + 0.35 * (1.0 - hazard)
            self.vy[index] = 0.0
            self.cooldown[index] = 0.8
            self._hazard_recycles += 1
            return
        habitat = self._active_modifier("habitat")
        if habitat:
            self.vx[index] *= max(0.1, 1.0 - 0.9 * habitat)
            self.vy[index] *= max(0.1, 1.0 - 0.9 * habitat)
            self.temperature[index] = max(0.0, self.temperature[index] - 0.35 * habitat * dt)
            return
        obstacle = self._active_modifier("obstacle")
        bumper = self._active_modifier("bumper")
        if obstacle or bumper:
            push = max(obstacle, bumper)
            self.x[index] += nx * (0.8 + push)
            self.y[index] += ny * (0.8 + push)
            normal_velocity = self.vx[index] * nx + self.vy[index] * ny
            if normal_velocity < 0.0:
                bounce = 1.2 if bumper else 0.65
                self.vx[index] -= (1.0 + bounce) * normal_velocity * nx
                self.vy[index] -= (1.0 + bounce) * normal_velocity * ny
            if bumper:
                self.temperature[index] = min(1.0, self.temperature[index] + 0.18 * bumper)
                if bumper > 0.7 and self.cooldown[index] <= 0.0:
                    self._split_blob(index)

    def _emit_from_bowl(self) -> None:
        strength = self._active_modifier("emitter")
        if not strength or not self._region_centers:
            return
        interval = 2.2 - 1.7 * strength
        if self.simulation_time - self._emitter_clock < interval:
            return
        indices = np.flatnonzero(self.active)
        if not indices.size:
            return
        source = int(indices[int(np.argmax(self.radius[indices]))])
        slot = self._free_slot()
        if slot is None or not self._split_blob(source, slot=slot):
            return
        names = [name for name in GLOBE_REGION_ORDER if name in self._region_centers]
        name = names[self._emitter_region % len(names)]
        self._emitter_region += 1
        center_x, center_y = self._region_centers[name]
        self.x[slot] = center_x
        self.y[slot] = max(self.radius[slot], center_y - 3.0)
        self.vy[slot] = -2.5 - 2.5 * strength
        self.temperature[slot] = 0.8 + 0.2 * strength
        self.cooldown[slot] = 0.7
        self._emitter_clock = self.simulation_time
        self._emissions += 1

    def _merge_and_restore(self) -> None:
        indices = list(map(int, np.flatnonzero(self.active)))
        target = max(3, min(12, int(self.params.get("blob_count", 7))))
        for position, left in enumerate(indices):
            if not self.active[left] or self.y[left] < self.height * 0.78 or self.temperature[left] > 0.42:
                continue
            for right in indices[position + 1:]:
                if not self.active[right] or self.temperature[right] > 0.42:
                    continue
                if math.hypot(float(self.x[left] - self.x[right]), float(self.y[left] - self.y[right])) < 0.45 * (self.radius[left] + self.radius[right]):
                    self._merge_pair(left, right)
                    return
        count = int(np.count_nonzero(self.active))
        if count < target:
            candidates = np.flatnonzero(self.active & (self.temperature > 0.72) & (self.cooldown <= 0.0))
            if candidates.size:
                source = int(candidates[int(np.argmax(self.radius[candidates]))])
                self._split_blob(source)

    def _step(self, dt: float) -> None:
        self.previous_x[:] = self.x
        self.previous_y[:] = self.y
        self.previous_radius[:] = self.radius
        self._drain_interactions()
        viscosity = float(self.params.get("viscosity", 0.68))
        heat = float(self.params.get("heat", 0.72))
        turbulence = float(self.params.get("turbulence", 0.24))
        damping = math.exp(-(0.12 + 0.75 * viscosity) * dt)
        plant_active = self._plant_effects_active()
        if plant_active:
            self._refresh_plant_geometry()
        for index in map(int, np.flatnonzero(self.active)):
            old_y = float(self.y[index])
            bottom = self.y[index] > self.height * 0.78
            top = self.y[index] < self.height * 0.28
            if bottom:
                self.temperature[index] += heat * 0.8 * dt
            self.temperature[index] -= (0.025 + (0.3 if top else 0.0)) * dt
            self.temperature[index] = np.clip(self.temperature[index], 0.0, 1.0)
            buoyancy = 7.0 - (20.0 * heat * self.temperature[index])
            lateral = math.sin(self.phase[index] + self.simulation_time * 0.7 + self.y[index] * 0.045)
            self.vx[index] += lateral * turbulence * 5.5 * dt
            self.vy[index] += buoyancy * dt
            self.vx[index] *= damping
            self.vy[index] *= damping
            self.x[index] += self.vx[index] * dt
            self.y[index] += self.vy[index] * dt
            radius = float(self.radius[index])
            if self.x[index] < radius:
                self.x[index] = radius
                self.vx[index] = abs(self.vx[index]) * 0.7
            elif self.x[index] > self.width - 1 - radius:
                self.x[index] = self.width - 1 - radius
                self.vx[index] = -abs(self.vx[index]) * 0.7
            if self.y[index] < radius:
                self.y[index] = radius
                self.vy[index] = abs(self.vy[index]) * 0.5
                self.temperature[index] *= 0.96
            elif self.y[index] > self.height - 1 - radius:
                self.y[index] = self.height - 1 - radius
                self.vy[index] = -abs(self.vy[index]) * 0.35
                self.temperature[index] = min(1.0, self.temperature[index] + heat * 0.015)
            if old_y >= self.height * 0.5 > self.y[index]:
                self._midline_up += 1
            elif old_y <= self.height * 0.5 < self.y[index]:
                self._midline_down += 1
            self.cooldown[index] = max(0.0, self.cooldown[index] - dt)
            if plant_active:
                self._apply_plant_dynamics(index, dt)
        if plant_active:
            self._emit_from_bowl()
        self._merge_and_restore()
        self.simulation_time += dt
        self._steps += 1

    def _background(self, elapsed: float) -> None:
        style = str(self.params.get("background", "glass"))
        self._canvas_float.fill(0.0)
        normalized_y = self._rows / max(1.0, self.height - 1.0)
        if style == "glass":
            edge = np.clip(1.0 - np.minimum(self._cols, self.width - 1 - self._cols) / 5.0, 0.0, 1.0)
            value = 2.0 + 8.0 * edge + 3.0 * normalized_y
            self._canvas_float[:, :, 0] = value * 0.55
            self._canvas_float[:, :, 1] = value * 0.75
            self._canvas_float[:, :, 2] = value
        elif style == "gradient":
            self._canvas_float[:, :, 0] = 2.0 + 8.0 * normalized_y
            self._canvas_float[:, :, 1] = 2.0 + 3.0 * normalized_y
            self._canvas_float[:, :, 2] = 8.0 + 12.0 * (1.0 - normalized_y)
        elif style == "ember":
            pulse = 0.5 + 0.5 * np.sin(self._cols * 0.8 + elapsed * 0.7)
            self._canvas_float[:, :, 0] = 3.0 + 13.0 * normalized_y * pulse
            self._canvas_float[:, :, 1] = 1.0 + 3.0 * normalized_y

    def _render(self, elapsed: float, alpha: float) -> np.ndarray:
        self._field.fill(0.0)
        self._temperature_field.fill(0.0)
        interp_x = self.previous_x + (self.x - self.previous_x) * alpha
        interp_y = self.previous_y + (self.y - self.previous_y) * alpha
        interp_radius = self.previous_radius + (self.radius - self.previous_radius) * alpha
        for index in map(int, np.flatnonzero(self.active)):
            dx = self._cols - interp_x[index]
            dy = (self._rows - interp_y[index]) * 0.78
            contribution = (interp_radius[index] ** 2) / (dx * dx + dy * dy + 1.0)
            self._field += contribution
            self._temperature_field += contribution * self.temperature[index]
        temperature = np.divide(
            self._temperature_field, self._field,
            out=np.zeros_like(self._field), where=self._field > 1e-6,
        )
        wax = np.clip((self._field - 0.48) / 0.78, 0.0, 1.0)
        wax *= wax * (3.0 - 2.0 * wax)
        glow_strength = float(self.params.get("glow", 0.58))
        halo = np.clip((self._field - 0.12) / 0.52, 0.0, 1.0) * glow_strength
        cool, warm, hot, glow_color = (
            np.asarray(color, dtype=np.float32)
            for color in self._presentation_palette()
        )
        heat_mix = np.clip(temperature, 0.0, 1.0)[..., None]
        wax_color = cool + (warm - cool) * np.minimum(1.0, heat_mix * 1.5)
        hot_mix = np.clip((heat_mix - 0.55) / 0.45, 0.0, 1.0)
        wax_color += (hot - wax_color) * hot_mix
        self._background(elapsed)
        self._canvas_float += glow_color * halo[..., None] * (1.0 - wax[..., None]) * 0.34
        self._canvas_float *= 1.0 - wax[..., None]
        self._canvas_float += wax_color * wax[..., None]

        if self._plant_effects_active():
            self._refresh_plant_geometry()
            refract = self._active_modifier("refract")
            if refract:
                displacement = 1.0 + 2.0 * refract
                source_rows = np.clip(
                    np.rint(self._rows + self._normal_y * displacement), 0, self.height - 1
                ).astype(np.intp)
                source_cols = np.clip(
                    np.rint(self._cols + self._normal_x * displacement), 0, self.width - 1
                ).astype(np.intp)
                influence = np.clip(1.0 - self._distance / 6.0, 0.0, 1.0)[..., None] * refract
                sampled = self._canvas_float[source_rows, source_cols]
                self._canvas_float[:] = self._canvas_float * (1.0 - influence) + sampled * influence
            shadow = self._active_modifier("shadow")
            if shadow:
                self._canvas_float[self._foliage] *= 1.0 - 0.58 * shadow
                self._canvas_float[self._globes] *= 1.0 - 0.78 * shadow
            illuminate = self._active_modifier("illuminate")
            if illuminate:
                self._canvas_float[self._obstacle_edge] += hot * (0.28 + 0.72 * illuminate)

        np.clip(self._canvas_float, 0.0, 255.0, out=self._canvas_float)
        np.copyto(self._canvas_u8, self._canvas_float, casting="unsafe")
        frame = self.next_frame_buffer(clear=False)
        logical = frame.reshape(self.width, self.height, 3)
        logical[:] = self._canvas_u8[::-1].transpose(1, 0, 2)
        self.apply_brightness_array(frame, out=frame)
        return frame

    def generate_frame(self, time_elapsed: float, frame_count: int):
        context = getattr(self, "presentation_context", None)
        cadence_elapsed = (
            context.unscaled_elapsed if context is not None else time_elapsed
        )
        semantic_elapsed = (
            context.scaled_elapsed if context is not None else time_elapsed
        )
        source_tick = int(math.floor(max(0.0, cadence_elapsed) * self.SOURCE_FPS + 1e-9))
        if self._last_render_tick == source_tick and self._cached_frame is not None:
            return self.rendered_frame(self._cached_frame, changed=False)
        if self._last_semantic_time is None:
            real_delta = self.PHYSICS_DT
        else:
            real_delta = max(0.0, min(0.1, semantic_elapsed - self._last_semantic_time))
        self._last_source_time = cadence_elapsed
        self._last_semantic_time = semantic_elapsed
        speed = (
            1.0 if context is not None
            else max(0.1, min(4.0, float(self.params.get("speed", 1.0))))
        )
        self._accumulator += real_delta * speed
        steps = 0
        while self._accumulator + 1e-12 >= self.PHYSICS_DT and steps < 8:
            self._step(self.PHYSICS_DT)
            self._accumulator -= self.PHYSICS_DT
            steps += 1
        if self._accumulator >= self.PHYSICS_DT:
            self._dropped_steps += int(self._accumulator / self.PHYSICS_DT)
            self._accumulator = math.fmod(self._accumulator, self.PHYSICS_DT)
        alpha = max(0.0, min(1.0, self._accumulator / self.PHYSICS_DT))
        self._cached_frame = self._render(semantic_elapsed, alpha)
        self._last_render_tick = source_tick
        return self.rendered_frame(self._cached_frame, changed=True)

    def get_runtime_stats(self) -> Dict[str, Any]:
        masks = self.get_plant_masks() if self._plant_effects_active() else None
        return {
            "source_fps": self.SOURCE_FPS,
            "simulation_time": self.simulation_time,
            "simulation_speed": float(self.params.get("speed", 1.0)),
            "physics_steps": self._steps,
            "dropped_physics_steps": self._dropped_steps,
            "blob_count": int(np.count_nonzero(self.active)),
            "wax_area": self.wax_area,
            "initial_wax_area": self._initial_wax_area,
            "midline_up_crossings": self._midline_up,
            "midline_down_crossings": self._midline_down,
            "splits": self._splits,
            "merges": self._merges,
            "interactions_applied": self._interactions_applied,
            "plant_contacts": self._plant_contacts,
            "portal_transfers": self._portal_transfers,
            "hazard_recycles": self._hazard_recycles,
            "emissions": self._emissions,
            "plant_mask_error": masks.error if masks is not None else "",
        }
