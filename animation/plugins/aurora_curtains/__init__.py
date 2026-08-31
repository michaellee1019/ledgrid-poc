"""A context-native, source-cadenced Aurora Curtains background.

Aurora is deliberately a Scene-v1 component rather than a compatibility
adapter. It consumes already-scaled presentation time and semantic palette
roles, then leaves presentation luminance and wall output brightness to the
scene pipeline that owns them.
"""

from __future__ import annotations

import copy
from types import MappingProxyType
from typing import Any, Mapping, Optional

import numpy as np

from animation.core.base import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene


PaletteRoles = Mapping[str, tuple[float, float, float]]


SEMANTIC_PALETTES: Mapping[str, PaletteRoles] = MappingProxyType({
    "neutral": MappingProxyType({
        "background_low": (2.0, 10.0, 18.0),
        "primary": (24.0, 148.0, 132.0),
        "accent": (150.0, 255.0, 218.0),
    }),
    "mist": MappingProxyType({
        "background_low": (3.0, 9.0, 20.0),
        "primary": (40.0, 102.0, 142.0),
        "accent": (170.0, 228.0, 245.0),
    }),
    "spectrum": MappingProxyType({
        "background_low": (15.0, 3.0, 34.0),
        "primary": (84.0, 38.0, 194.0),
        "accent": (54.0, 238.0, 230.0),
    }),
    "ember": MappingProxyType({
        "background_low": (18.0, 3.0, 2.0),
        "primary": (156.0, 42.0, 14.0),
        "accent": (255.0, 202.0, 92.0),
    }),
})


class AuroraCurtainsAnimation(AnimationBase):
    """Overlapping analytic light curtains for the opaque background layer."""

    ANIMATION_NAME = "Aurora Curtains"
    ANIMATION_DESCRIPTION = "Slow semantic-palette curtains folding through a dark sky"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"

    COMPONENT_ID = "aurora_curtains"
    COMPONENT_VERSION = 1
    PROVIDER = "python"
    ROLE = "background"
    FRAME_FORMAT = "rgb_uint8_strip_major"
    CADENCE_POLICY = "source_rate_cached"
    TIMING_POLICY = "scaled_context"
    PALETTE_POLICY = "semantic"
    PALETTE_ROLES = ("background_low", "primary", "accent")
    CAPABILITIES = frozenset({"semantic_palette_roles", "source_cadence", "scaled_context"})

    # Aurora has no semantic plant behaviour in this packet. It intentionally
    # does not inherit the old plant_aware compatibility bridge.
    PLANT_MODIFIER_SUPPORT = frozenset()

    DEFAULTS = MappingProxyType({
        "curtain_density": 0.56,
        "fold_depth": 0.58,
        "glow_intensity": 0.62,
        "source_fps": 30.0,
        "seed": 4201,
    })

    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        provider=PROVIDER,
        role=ROLE,
        palette_policy=PALETTE_POLICY,
        timing_policy=TIMING_POLICY,
        intensity_parameter="glow_intensity",
        defaults=DEFAULTS,
    )

    def __init__(self, controller, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)

        # Deliberately replace AnimationBase's compatibility parameter surface.
        # Component luminosity and pace belong to the resolved presentation
        # context, not this renderer.
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self._x = np.linspace(0.0, 1.0, self.width, dtype=np.float32)[:, None]
        self._y = np.linspace(0.0, 1.0, self.height, dtype=np.float32)[None, :]
        self._field = np.empty((self.width, self.height), dtype=np.float32)
        self._rgb = np.empty((self.width, self.height, 3), dtype=np.float32)
        self._cached_frame: Optional[np.ndarray] = None
        self._cached_key: Optional[tuple[Any, ...]] = None
        self._last_source_tick: Optional[int] = None
        self._active_seed = int(self.params["seed"])
        self._active_source_fps = float(self.params["source_fps"])
        self._presentation_context: Optional[ResolvedScene] = None
        self._event_count = 0
        self._seed_state()

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        """Return the provider-qualified Scene-v1 declaration for Aurora."""
        return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def palette_roles(cls, palette_id: str) -> PaletteRoles:
        """Resolve a complete semantic role palette with a stable fallback."""
        return SEMANTIC_PALETTES.get(str(palette_id), SEMANTIC_PALETTES["neutral"])

    def get_parameter_schema(self) -> dict[str, dict[str, Any]]:
        """Expose only component-local artistic controls and source cadence."""
        return {
            "curtain_density": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 0.56,
                "description": "Number and width of the translucent curtain sheets",
            },
            "fold_depth": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 0.58,
                "description": "Vertical ripple depth inside each curtain",
            },
            "glow_intensity": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 0.62,
                "description": "Component-local curtain emission before scene luminance",
            },
            "source_fps": {
                "type": "float", "min": 20.0, "max": 40.0, "default": 30.0,
                "description": "Independent bounded source redraw cadence",
            },
            "seed": {
                "type": "int", "min": 0, "max": 999999, "default": 4201,
                "description": "Deterministic curtain layout seed",
            },
        }

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        """Update local controls without treating presentation values as config."""
        unknown = set(new_params) - set(self.DEFAULTS)
        if unknown:
            raise ValueError(f"Aurora Curtains does not accept non-local parameters: {sorted(unknown)!r}")
        old_seed = self.params["seed"]
        self.params = self._normalized_parameters({**self.params, **dict(new_params)})
        if self.params["seed"] != old_seed:
            self._seed_state(int(self.params["seed"]))
        self._active_source_fps = float(self.params["source_fps"])
        self._cached_key = None

    def on_presentation_context_changed(
        self, old: Optional[ResolvedScene], new: ResolvedScene
    ) -> None:
        """Accept a new immutable context without mutating semantic state.

        Palette and vibe changes intentionally do not reseed, advance a source
        tick, consume RNG, or emit events. The cache key includes the resolved
        visual inputs, so the next render refreshes only its presentation.
        """
        del old
        self._validate_context(new)
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        """Compatibility-friendly public context hook for direct/headless use."""
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> np.ndarray:
        """Render raw component RGB for Scene-v1's outer presentation pipeline."""
        self.set_presentation_context(context)
        rendered = self._render(
            phase_time=context.phase_time,
            palette_id=self._palette_id(context),
            parameters=context.parameters,
        )
        return rendered.pixels

    def generate_frame(self, time_elapsed: float, frame_count: int) -> RenderedFrame:
        """Preserve the frame-plugin API using direct neutral headless context.

        Managed Scene-v1 rendering calls :meth:`render_resolved_scene` with
        its already-scaled phase time. The direct path is deliberately neutral
        and does not synthesize pace or luminance controls.
        """
        del frame_count
        if self._presentation_context is not None:
            context = self._presentation_context
            return self._render(
                phase_time=context.phase_time,
                palette_id=self._palette_id(context),
                parameters=context.parameters,
            )
        return self._render(
            phase_time=max(0.0, float(time_elapsed)),
            palette_id="neutral",
            parameters=self.params,
        )

    def semantic_snapshot(self) -> Mapping[str, Any]:
        """Stable proof surface for state/RNG/event parity tests."""
        return MappingProxyType({
            "seed": self._active_seed,
            "phase": tuple(float(value) for value in self._phase),
            "offset": tuple(float(value) for value in self._offset),
            "frequency": tuple(float(value) for value in self._frequency),
            "rng_state": copy.deepcopy(self._rng_state),
            "events": self._event_count,
        })

    def cadence_snapshot(self) -> Mapping[str, Any]:
        """The source cadence policy, separate from transient cached tick state."""
        return MappingProxyType({
            "source_fps": self._active_source_fps,
        })

    def get_runtime_stats(self) -> dict[str, Any]:
        return {
            "provider": self.PROVIDER,
            "role": self.ROLE,
            "cadence_policy": self.CADENCE_POLICY,
            "source_fps": self._active_source_fps,
            "source_tick": self._last_source_tick,
            "events": self._event_count,
        }

    def _seed_state(self, seed: Optional[int] = None) -> None:
        """Initialize deterministic semantic state for one resolved seed only."""
        self._active_seed = int(self.params["seed"] if seed is None else seed)
        rng = np.random.default_rng(self._active_seed)
        self._phase = rng.uniform(0.0, 2.0 * np.pi, 12).astype(np.float32)
        self._offset = rng.uniform(0.0, 1.0, 12).astype(np.float32)
        self._frequency = rng.uniform(0.72, 2.2, 12).astype(np.float32)
        self._rng_state = copy.deepcopy(rng.bit_generator.state)
        self._cached_key = None
        self._last_source_tick = None

    def _render(
        self,
        *,
        phase_time: float,
        palette_id: str,
        parameters: Mapping[str, Any],
    ) -> RenderedFrame:
        local = self._normalized_parameters(parameters)
        source_fps = float(local["source_fps"])
        if int(local["seed"]) != self._active_seed:
            self._seed_state(int(local["seed"]))
        self._active_source_fps = source_fps
        tick = int(max(0.0, float(phase_time)) * source_fps + 1.0e-7)
        key = (
            tick, palette_id, local["curtain_density"], local["fold_depth"],
            local["glow_intensity"], local["source_fps"], local["seed"],
        )
        if self._cached_key == key and self._cached_frame is not None:
            return self.rendered_frame(self._cached_frame, changed=False)

        # phase_time is already scaled by wall pace in resolve_scene(). No
        # component speed factor appears here, so pace is applied exactly once.
        quantized_time = tick / source_fps
        self._paint_curtains(quantized_time, local)
        self._paint_palette(self.palette_roles(palette_id), float(local["glow_intensity"]))
        frame = self.next_frame_buffer(clear=False)
        np.copyto(frame, self._rgb.reshape((-1, 3)), casting="unsafe")
        self._cached_frame = frame
        self._cached_key = key
        self._last_source_tick = tick
        return self.rendered_frame(frame, changed=True)

    def _paint_curtains(self, phase_time: float, local: Mapping[str, float | int]) -> None:
        density = float(local["curtain_density"])
        fold_depth = float(local["fold_depth"])
        self._field.fill(0.018)
        curtains = 2 + int(round(4.0 * density))
        for index in range(curtains):
            phase = self._phase[index]
            center = self._offset[index] + .15 * np.sin(
                self._y * (4.8 + self._frequency[index]) + phase_time * .33 + phase
            )
            width = .024 + .03 * density
            sheet = np.exp(-((self._x - center) / width) ** 2)
            reach = np.clip((1.08 - self._y) * (1.12 + density), 0.0, 1.0)
            fold = 1.0 - fold_depth + fold_depth * (
                .34 + .66 * np.sin(
                    self._y * 15.0 + phase_time * .7 + self._phase[(index + 5) % 12]
                ) ** 2
            )
            self._field += sheet * reach * fold * (.18 + .13 * density)
        stars = (
            np.sin(self._x * 173.0 + self._y * 311.0 + self._phase[10]) > .997
        ).astype(np.float32)
        self._field += stars * (.035 + .05 * density)
        np.clip(self._field, 0.0, 1.0, out=self._field)

    def _paint_palette(self, roles: PaletteRoles, glow_intensity: float) -> None:
        low = np.asarray(roles["background_low"], dtype=np.float32)
        primary = np.asarray(roles["primary"], dtype=np.float32)
        accent = np.asarray(roles["accent"], dtype=np.float32)
        field = self._field
        lower = np.minimum(field * 2.0, 1.0)[..., None]
        upper = np.maximum(field * 2.0 - 1.0, 0.0)[..., None]
        self._rgb[:] = low + (primary - low) * lower + (accent - primary) * upper
        self._rgb += np.maximum(field - .12, 0.0)[..., None] * accent * (.34 * glow_intensity)
        np.clip(self._rgb, 0.0, 255.0, out=self._rgb)

    def _normalized_parameters(self, values: Mapping[str, Any]) -> dict[str, float | int]:
        unknown = set(values) - set(self.DEFAULTS)
        if unknown:
            raise ValueError(
                f"Aurora Curtains does not accept non-local parameters: {sorted(unknown)!r}"
            )
        result: dict[str, float | int] = dict(self.DEFAULTS)
        for key in self.DEFAULTS:
            if key in values:
                result[key] = values[key]
        for key in ("curtain_density", "fold_depth", "glow_intensity"):
            value = result[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a number from 0 to 1")
            result[key] = float(np.clip(float(value), 0.0, 1.0))
        fps = result["source_fps"]
        if isinstance(fps, bool) or not isinstance(fps, (int, float)):
            raise ValueError("source_fps must be a number from 20 to 40")
        result["source_fps"] = float(np.clip(float(fps), 20.0, 40.0))
        seed = result["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 999999:
            raise ValueError("seed must be an integer from 0 to 999999")
        result["seed"] = seed
        return result

    def _validate_context(self, context: ResolvedScene) -> None:
        descriptor = context.descriptor
        if (
            descriptor.component_id != self.COMPONENT_ID
            or descriptor.version != self.COMPONENT_VERSION
            or descriptor.provider.value != self.PROVIDER
            or descriptor.role.value != self.ROLE
        ):
            raise ValueError("Aurora Curtains received a context for another component")
        if context.palette is None:
            raise ValueError("Aurora Curtains requires a semantic palette context")

    @staticmethod
    def _palette_id(context: ResolvedScene) -> str:
        assert context.palette is not None
        return str(context.palette["palette_id"])
