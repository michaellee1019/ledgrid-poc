"""Scene v2 Plant Glow: semantic light shaped by installed plant geometry."""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from animation import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.compositing import OverlayFrame, coverage_dirty_union
from animation.core.plant_awareness import INSTALLATION_GEOMETRY_CONTACT_INPUT
from animation.core.presentation_contracts import ResolvedScene
from animation.libraries.mask_effects import build_halo_weights


class PlantGlowAnimation(AnimationBase):
    """Illuminate exact foliage/globe cores and their local exterior halos.

    The installation profile owns the masks. Plant Glow only derives a bounded
    presentation halo from the provider-qualified contact; it never authors or
    persists calibration geometry.
    """

    ANIMATION_NAME = "Plant Glow"
    ANIMATION_DESCRIPTION = "Breathing semantic light traces the wall's foliage and rooting globes"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "3.0"

    COMPONENT_ID, COMPONENT_VERSION = "plant_glow", 1
    PROVIDER, ROLE = "python", "animation"
    FRAME_FORMAT = "rgba_uint8_premultiplied_strip_major"
    TIMING_POLICY, PALETTE_POLICY = "scaled_context", "semantic"
    CAPABILITIES = frozenset(
        (
            "semantic_palette_roles",
            "source_cadence",
            "scaled_context",
            "installation_geometry_contact",
        )
    )
    PLANT_MODIFIER_SUPPORT = frozenset()
    INSTALLATION_GEOMETRY_CONTACT = True
    SOURCE_FPS = 30.0

    DEFAULTS = MappingProxyType(
        {
            "glow_radius": 2,
            "glow_strength": 0.72,
            "glow_falloff": 1.4,
            "breath_speed": 0.18,
            "breath_depth": 0.24,
            "shimmer": 0.10,
            "foliage_intensity": 1.0,
            "globe_intensity": 1.0,
        }
    )
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        provider=PROVIDER,
        role=ROLE,
        timing_policy=TIMING_POLICY,
        alpha_behavior="premultiplied_rgba",
        palette_policy=PALETTE_POLICY,
        plant_capabilities=("effect_intent", "simulation_inputs"),
        fidelity_exceptions=(),
        optional_simulation_inputs=(INSTALLATION_GEOMETRY_CONTACT_INPUT,),
        defaults=DEFAULTS,
        parameter_normalizer=lambda values: PlantGlowAnimation._normalized_parameters(values),
    )
    SEMANTIC_PALETTES = MappingProxyType(
        {
            "neutral": {
                "foliage": (54, 255, 132),
                "foliage_halo": (18, 110, 255),
                "globe": (255, 72, 224),
                "globe_halo": (108, 34, 255),
            },
            "mist": {
                "foliage": (112, 231, 255),
                "foliage_halo": (55, 122, 255),
                "globe": (232, 247, 255),
                "globe_halo": (142, 171, 255),
            },
            "spectrum": {
                "foliage": (53, 255, 160),
                "foliage_halo": (34, 157, 255),
                "globe": (255, 55, 224),
                "globe_halo": (141, 46, 255),
            },
            "ember": {
                "foliage": (255, 126, 30),
                "foliage_halo": (255, 58, 9),
                "globe": (255, 232, 105),
                "globe_halo": (255, 103, 18),
            },
        }
    )

    def __init__(self, controller: Any, config: Mapping[str, Any] | None = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self._buffers = tuple(
            np.zeros((self.get_pixel_count(), 4), dtype=np.uint8) for _ in range(2)
        )
        self._last_pixels, self._last_key = self._buffers[0], None
        self._revision = 0
        self._presentation_context: ResolvedScene | None = None
        self._geometry_key: tuple[Any, ...] | None = None
        self._foliage_core = np.zeros(self.get_pixel_count(), dtype=np.bool_)
        self._globe_core = np.zeros(self.get_pixel_count(), dtype=np.bool_)
        self._foliage_halo = np.zeros(self.get_pixel_count(), dtype=np.float32)
        self._globe_halo = np.zeros(self.get_pixel_count(), dtype=np.float32)
        self._phase = (
            np.arange(self.get_pixel_count(), dtype=np.float32) * np.float32(2.3999632)
        ) % np.float32(math.tau)

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        supplied = dict(values)
        unknown = sorted(set(supplied) - set(cls.DEFAULTS))
        if unknown:
            raise ValueError(f"Plant Glow does not accept non-local parameters: {unknown!r}")
        result = dict(cls.DEFAULTS)
        result.update(supplied)
        radius = result["glow_radius"]
        if isinstance(radius, bool) or not isinstance(radius, int) or not 0 <= radius <= 5:
            raise ValueError("glow_radius must be an integer from 0 to 5")
        bounds = (
            ("glow_strength", 0.0, 1.5),
            ("glow_falloff", 0.1, 4.0),
            ("breath_speed", 0.0, 2.0),
            ("breath_depth", 0.0, 0.8),
            ("shimmer", 0.0, 0.5),
            ("foliage_intensity", 0.0, 1.0),
            ("globe_intensity", 0.0, 1.5),
        )
        for name, low, high in bounds:
            value = result[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not low <= float(value) <= high
            ):
                raise ValueError(f"{name} must be a finite number from {low} to {high}")
            result[name] = float(value)
        return result

    def get_parameter_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "glow_radius": {
                "type": "int", "min": 0, "max": 5, "default": 2,
                "description": "Exterior halo radius in logical pixels",
            },
            "glow_strength": {
                "type": "float", "min": 0.0, "max": 1.5, "default": 0.72,
                "description": "Halo intensity around the exact plant cores",
            },
            "glow_falloff": {
                "type": "float", "min": 0.1, "max": 4.0, "default": 1.4,
                "description": "How quickly exterior light fades",
            },
            "breath_speed": {
                "type": "float", "min": 0.0, "max": 2.0, "default": 0.18,
                "description": "Breathing cycles per scaled Scene second",
            },
            "breath_depth": {
                "type": "float", "min": 0.0, "max": 0.8, "default": 0.24,
                "description": "Depth of the breathing pulse",
            },
            "shimmer": {
                "type": "float", "min": 0.0, "max": 0.5, "default": 0.10,
                "description": "Fine spatial variation along halo edges",
            },
            "foliage_intensity": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 1.0,
                "description": "Relative foliage core and halo light",
            },
            "globe_intensity": {
                "type": "float", "min": 0.0, "max": 1.5, "default": 1.0,
                "description": "Relative rooting-globe core and halo light",
            },
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        old_geometry = (self.params["glow_radius"], self.params["glow_falloff"])
        self.params = self._normalized_parameters({**self.params, **dict(new_params)})
        if old_geometry != (self.params["glow_radius"], self.params["glow_falloff"]):
            self._geometry_key = None
        self._last_key = None

    def on_presentation_context_changed(
        self, old: ResolvedScene | None, new: ResolvedScene
    ) -> None:
        del old
        descriptor = new.descriptor
        identity = (
            descriptor.component_id,
            descriptor.version,
            descriptor.provider.value,
            descriptor.role.value,
        )
        if identity != (self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE):
            raise ValueError("Plant Glow received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str):
            raise ValueError("Plant Glow requires a semantic Scene v2 palette")
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> OverlayFrame:
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def _ensure_geometry(self, context: ResolvedScene | None) -> tuple[Any, ...]:
        contact = None if context is None else context.installation_geometry
        contact_identity = None if contact is None else contact.identity
        available = bool(contact is not None and contact.available)
        key = (
            contact_identity,
            available,
            self.params["glow_radius"],
            self.params["glow_falloff"],
            self.width,
            self.height,
        )
        if key == self._geometry_key:
            return key
        if available and contact.geometry.foliage.shape == (self.width, self.height):
            foliage = contact.geometry.foliage_flat & ~contact.geometry.globes_flat
            globes = contact.geometry.globes_flat
            self._foliage_core, self._foliage_halo = build_halo_weights(
                np.flatnonzero(foliage), self.width, self.height,
                self.params["glow_radius"], self.params["glow_falloff"],
            )
            self._globe_core, self._globe_halo = build_halo_weights(
                np.flatnonzero(globes), self.width, self.height,
                self.params["glow_radius"], self.params["glow_falloff"],
            )
        else:
            self._foliage_core.fill(False)
            self._globe_core.fill(False)
            self._foliage_halo.fill(0.0)
            self._globe_halo.fill(0.0)
        self._geometry_key = key
        return key

    @staticmethod
    def _composite_layer(
        output: np.ndarray, color: tuple[int, int, int], opacity: np.ndarray
    ) -> None:
        indices = np.flatnonzero(opacity > 0.0)
        if not indices.size:
            return
        alpha = np.rint(np.clip(opacity[indices], 0.0, 1.0) * 255.0).astype(np.uint16)
        source = (np.asarray(color, dtype=np.uint16)[None, :] * alpha[:, None] + 127) // 255
        inverse = 255 - alpha
        destination = output[indices].astype(np.uint16)
        output[indices, :3] = np.minimum(
            255, source + (destination[:, :3] * inverse[:, None] + 127) // 255
        ).astype(np.uint8)
        output[indices, 3] = np.minimum(
            255, alpha + (destination[:, 3] * inverse + 127) // 255
        ).astype(np.uint8)

    def generate_frame(self, time_elapsed: float, frame_count: int) -> OverlayFrame:
        del frame_count
        context = self._presentation_context
        if context is None:
            phase_time = max(0.0, float(time_elapsed))
            palette_id, parameters = "neutral", self.params
        else:
            phase_time = max(0.0, float(context.phase_time))
            palette_id = str(context.palette["palette_id"])
            parameters = context.parameters
        self.params = self._normalized_parameters(parameters)
        geometry_key = self._ensure_geometry(context)
        tick = int(math.floor(phase_time * self.SOURCE_FPS + 1.0e-9))
        key = (tick, palette_id, tuple(self.params.items()), geometry_key)
        if key == self._last_key:
            return OverlayFrame(
                self._last_pixels, revision=self._revision,
                changed=False, dirty_ranges=(),
            )

        output = self._buffers[1] if self._last_pixels is self._buffers[0] else self._buffers[0]
        output.fill(0)
        palette = self.SEMANTIC_PALETTES.get(palette_id, self.SEMANTIC_PALETTES["neutral"])
        phase = tick / self.SOURCE_FPS * self.params["breath_speed"] * math.tau
        depth = self.params["breath_depth"]
        foliage_breath = 1.0 - depth + depth * (0.5 + 0.5 * math.sin(phase))
        globe_breath = 1.0 - depth + depth * (0.5 + 0.5 * math.sin(phase + 1.7))
        shimmer = 1.0 + self.params["shimmer"] * np.sin(self._phase + phase * 1.9)
        foliage_intensity = self.params["foliage_intensity"]
        globe_intensity = self.params["globe_intensity"]

        self._composite_layer(
            output, palette["foliage_halo"],
            self._foliage_halo * shimmer * self.params["glow_strength"]
            * foliage_breath * foliage_intensity,
        )
        self._composite_layer(
            output, palette["globe_halo"],
            self._globe_halo * shimmer * self.params["glow_strength"]
            * globe_breath * globe_intensity,
        )
        self._composite_layer(
            output, palette["foliage"],
            self._foliage_core.astype(np.float32) * foliage_breath * foliage_intensity,
        )
        self._composite_layer(
            output, palette["globe"],
            self._globe_core.astype(np.float32) * globe_breath * globe_intensity,
        )

        dirty = coverage_dirty_union(self._last_pixels, output)
        changed = bool(dirty)
        if changed:
            self._revision += 1
        self._last_pixels, self._last_key = output, key
        return OverlayFrame(output, revision=self._revision, changed=changed, dirty_ranges=dirty)

    def semantic_snapshot(self) -> Mapping[str, Any]:
        """Plant Glow has no simulation or RNG state for geometry changes to reset."""
        return MappingProxyType({})

    def get_runtime_stats(self) -> dict[str, Any]:
        return {
            "geometry_role": "exact-core illumination with exterior presentation halos",
            "geometry_available": bool(self._geometry_key and self._geometry_key[1]),
            "foliage_pixels": int(np.count_nonzero(self._foliage_core)),
            "globe_pixels": int(np.count_nonzero(self._globe_core)),
            "foliage_halo_pixels": int(np.count_nonzero(self._foliage_halo)),
            "globe_halo_pixels": int(np.count_nonzero(self._globe_halo)),
            "source_fps": self.SOURCE_FPS,
        }
