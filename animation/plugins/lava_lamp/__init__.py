"""Thermal metaball lava with calibrated foliage and planter-bowl semantics."""

from __future__ import annotations

import math
import threading
from collections import deque
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.plant_awareness import GLOBE_REGION_ORDER
from animation.core.presentation_contracts import ResolvedScene


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


class LavaLampAnimation(AnimationBase):
    ANIMATION_NAME = "Lava Lamp"
    ANIMATION_DESCRIPTION = "Thermal wax blobs rise, merge, cool, and flow around the living wall"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "lava_lamp", 1, "python", "animation"
    FRAME_FORMAT, TIMING_POLICY, PALETTE_POLICY = "rgb_uint8_strip_major", "scaled_context", "semantic"
    CAPABILITIES = frozenset(("semantic_palette_roles", "scaled_context", "effect_intent"))
    SOURCE_FPS = 100.0
    PHYSICS_DT = 0.01
    MAX_BLOBS = 16
    INTERACTION_TYPES = frozenset(("primary",))
    # Scene v2 owns installation optics and plant effects at composition.  The
    # lamp remains an opaque thermal instrument, never a second authority for
    # masks, output calibration, or brightness.
    PLANT_MODIFIER_SUPPORT = frozenset()
    DEFAULTS = MappingProxyType({
        "blob_count": 7, "blob_scale": 1.0, "viscosity": .68,
        "heat": .72, "turbulence": .24, "glow": .58, "seed": 1977,
        "interaction_radius": 8.0, "interaction_strength": 1.0,
    })
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER,
        role=ROLE, timing_policy=TIMING_POLICY, alpha_behavior="opaque",
        palette_policy=PALETTE_POLICY, plant_capabilities=("effect_intent",),
        fidelity_exceptions=(), defaults=DEFAULTS,
    )
    SCENE_PALETTES = MappingProxyType({
        "neutral": "classic", "mist": "ocean", "spectrum": "violet", "ember": "solar",
    })

    def __init__(self, controller, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self.rng = np.random.default_rng(int(self.params["seed"]))
        self._interaction_lock = threading.Lock()
        self._interactions = deque(maxlen=16)
        self._presentation_context: ResolvedScene | None = None
        self._allocate_geometry()
        self._reset_simulation()

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

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
        return {
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
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 1977,
                     "description": "Repeatable initial blob arrangement."},
            "interaction_radius": {"type": "float", "min": 2.0, "max": 16.0, "default": 8.0,
                                   "description": "Click-to-stir influence radius."},
            "interaction_strength": {"type": "float", "min": 0.1, "max": 2.0, "default": 1.0,
                                     "description": "Vortex and heat impulse multiplier."},
        }

    def update_parameters(self, new_params: Mapping[str, Any]):
        candidate = self._normalized_parameters({**self.params, **dict(new_params)})
        old_seed = int(self.params["seed"])
        old_count = int(self.params["blob_count"])
        self.params = candidate
        if int(self.params.get("seed", 1977)) != old_seed:
            self.rng = np.random.default_rng(int(self.params["seed"]))
            self._reset_simulation()
        elif "blob_count" in new_params and int(self.params["blob_count"]) != old_count:
            self._reconcile_blob_count()
        self._last_render_tick = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        descriptor = new.descriptor
        identity = (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value)
        if identity != (self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE):
            raise ValueError("Lava Lamp received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str):
            raise ValueError("Lava Lamp requires a semantic Scene v2 palette")
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> RenderedFrame:
        """Render from the strict Scene v2 context used by the live manager."""
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

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

    def _render(self, elapsed: float, alpha: float, palette_id: str) -> np.ndarray:
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
        palette = self.SCENE_PALETTES.get(palette_id, self.SCENE_PALETTES["neutral"])
        cool, warm, hot, glow_color = (
            np.asarray(color, dtype=np.float32)
            for color in PALETTES[palette]
        )
        heat_mix = np.clip(temperature, 0.0, 1.0)[..., None]
        wax_color = cool + (warm - cool) * np.minimum(1.0, heat_mix * 1.5)
        hot_mix = np.clip((heat_mix - 0.55) / 0.45, 0.0, 1.0)
        wax_color += (hot - wax_color) * hot_mix
        self._background(elapsed)
        self._canvas_float += glow_color * halo[..., None] * (1.0 - wax[..., None]) * 0.34
        self._canvas_float *= 1.0 - wax[..., None]
        self._canvas_float += wax_color * wax[..., None]

        np.clip(self._canvas_float, 0.0, 255.0, out=self._canvas_float)
        np.copyto(self._canvas_u8, self._canvas_float, casting="unsafe")
        frame = self.next_frame_buffer(clear=False)
        logical = frame.reshape(self.width, self.height, 3)
        logical[:] = self._canvas_u8[::-1].transpose(1, 0, 2)
        return frame

    def generate_frame(self, time_elapsed: float, frame_count: int) -> RenderedFrame:
        del frame_count
        if self._presentation_context is None:
            phase_time, palette_id, parameters = max(0.0, float(time_elapsed)), "neutral", self.params
        else:
            phase_time = max(0.0, float(self._presentation_context.phase_time))
            palette_id = str(self._presentation_context.palette["palette_id"])
            parameters = self._presentation_context.parameters
        candidate = self._normalized_parameters(parameters)
        if candidate != self.params:
            self.update_parameters(candidate)
        source_tick = int(math.floor(phase_time * self.SOURCE_FPS + 1e-9))
        if self._last_render_tick == source_tick and self._cached_frame is not None:
            return self.rendered_frame(self._cached_frame, changed=False)
        if self._last_source_time is None:
            real_delta = self.PHYSICS_DT
        else:
            real_delta = max(0.0, min(0.1, phase_time - self._last_source_time))
        self._last_source_time = phase_time
        self._accumulator += real_delta
        steps = 0
        while self._accumulator + 1e-12 >= self.PHYSICS_DT and steps < 8:
            self._step(self.PHYSICS_DT)
            self._accumulator -= self.PHYSICS_DT
            steps += 1
        if self._accumulator >= self.PHYSICS_DT:
            self._dropped_steps += int(self._accumulator / self.PHYSICS_DT)
            self._accumulator = math.fmod(self._accumulator, self.PHYSICS_DT)
        alpha = max(0.0, min(1.0, self._accumulator / self.PHYSICS_DT))
        self._cached_frame = self._render(phase_time, alpha, palette_id)
        self._last_render_tick = source_tick
        return self.rendered_frame(self._cached_frame, changed=True)

    def get_runtime_stats(self) -> Dict[str, Any]:
        return {
            "source_fps": self.SOURCE_FPS,
            "simulation_time": self.simulation_time,
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
        }

    def cadence_snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType({"simulation_hz": 1.0 / self.PHYSICS_DT, "source_fps": self.SOURCE_FPS, "tick": self._last_render_tick})

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        # The generic legacy preview manager still attaches its empty plant
        # bridge.  Consume that transport residue here without treating it as a
        # Lava Lamp control or allowing it into Scene v2 state.
        values = {key: value for key, value in values.items() if key not in {"plant_aware", "plant_modifiers"}}
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(f"Lava Lamp does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS); result.update(values)
        count, seed = result["blob_count"], result["seed"]
        if isinstance(count, bool) or not isinstance(count, int) or not 3 <= count <= 12:
            raise ValueError("blob_count must be an integer from 3 to 12")
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 999999:
            raise ValueError("seed must be an integer from 0 to 999999")
        for name, low, high in (("blob_scale", .6, 1.8), ("viscosity", 0., 1.), ("heat", 0., 1.), ("turbulence", 0., 1.), ("glow", 0., 1.), ("interaction_radius", 2., 16.), ("interaction_strength", .1, 2.)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not low <= float(value) <= high:
                raise ValueError(f"{name} must be a finite number from {low} to {high}")
            result[name] = float(value)
        return result
