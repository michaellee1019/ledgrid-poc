"""Topology-neutral renderer for an already-activated canonical Scene v1.

The Composer and activation boundary own normalization and identity.  This
module deliberately consumes that completed basis; it neither accepts a
Composer request nor changes the scene JSON while turning its two supported
Python component kinds into one host RGB frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

import numpy as np

from animation.core.base import RenderedFrame
from animation.core.component_catalog import (
    ComponentCatalog,
    ComponentDescriptor,
    ComponentProvider,
    ComponentRole,
    TimingPolicy,
)
from animation.core.compositing import BaseFrame, HostSceneCompositor, OverlayFrame, PlacedOverlay
from animation.core.presentation_contracts import ResolvedScene
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
from animation.plugins.clock_overlay import ClockOverlayAnimation
from animation.plugins.conway_life import ConwayLifeAnimation
from ipc.scene_contract import (
    CanonicalScene,
    SceneIdentity,
    canonical_json_bytes,
)


class CanonicalSceneRuntimeError(ValueError):
    """An activated scene is outside the intentionally closed host runtime."""


@dataclass(frozen=True)
class RuntimeFrame:
    """One composed RGB frame, explicitly tied to its activated Scene basis."""

    pixels: np.ndarray
    basis: SceneIdentity
    changed: bool
    dirty_ranges: tuple[tuple[int, int], ...] | None = None


BackgroundFactory = Callable[[Any, Mapping[str, Any]], AuroraCurtainsAnimation]
ClockFactory = Callable[[Any, Mapping[str, Any], Callable[[], datetime]], ClockOverlayAnimation]
ConwayFactory = Callable[[Any, Mapping[str, Any]], ConwayLifeAnimation]


class _RuntimeClockOverlay(ClockOverlayAnimation):
    """Default Clock adapter whose wall-time source belongs to the runtime."""

    def __init__(
        self,
        controller: Any,
        config: Mapping[str, Any],
        wall_time_source: Callable[[], datetime],
    ) -> None:
        self._runtime_wall_time_source = wall_time_source
        super().__init__(controller, config)

    def _clock_now(self) -> datetime:
        return self._runtime_wall_time_source() + timedelta(
            minutes=int(self.params["clock_offset_minutes"])
        )


@dataclass
class _BackgroundSlot:
    component_key: tuple[str, str, int, str]
    semantic_seed: int
    animation: AuroraCurtainsAnimation
    parameters: Mapping[str, Any]


@dataclass
class _OverlaySlot:
    component_key: tuple[str, str, int, str]
    component_id: str
    animation: ClockOverlayAnimation | ConwayLifeAnimation
    parameters: Mapping[str, Any]


class CanonicalSceneRuntime:
    """Render activated Aurora-plus-Conway-and-Clock Scene v1 bases on a host canvas.

    Only integrated Python ``aurora_curtains`` backgrounds and
    ``conway_life`` and ``clock_overlay`` planes are supported in this vertical
    slice. Factories and a wall-time source are explicit inputs so lifecycle
    and timing tests do not need globals or real hardware.
    """

    def __init__(
        self,
        controller: Any,
        catalog: ComponentCatalog,
        *,
        background_factory: Optional[BackgroundFactory] = None,
        clock_factory: Optional[ClockFactory] = None,
        conway_factory: Optional[ConwayFactory] = None,
        wall_time_source: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if not isinstance(catalog, ComponentCatalog):
            raise TypeError("catalog must be a ComponentCatalog")
        self.controller = controller
        self.catalog = catalog
        self.strip_count, self.leds_per_strip = self._controller_geometry(controller)
        self._compositor = HostSceneCompositor(self.strip_count, self.leds_per_strip)
        self._background_factory = background_factory or self._default_background_factory
        self._clock_factory = clock_factory or self._default_clock_factory
        self._conway_factory = conway_factory or self._default_conway_factory
        self._wall_time_source = wall_time_source or (lambda: datetime.now().astimezone())
        self._background: _BackgroundSlot | None = None
        self._overlays: dict[str, _OverlaySlot] = {}
        self._canonical: CanonicalScene | None = None
        self._frame_count = 0
        self._output_buffers = tuple(
            np.empty((self.strip_count * self.leds_per_strip, 3), dtype=np.uint8)
            for _ in range(2)
        )
        self._output_index = -1
        self._scale_work = np.empty((self.strip_count * self.leds_per_strip, 3), dtype=np.float32)
        self._last_presentation_signature: tuple[float, float] | None = None
        self._last_output: np.ndarray | None = None

    @staticmethod
    def _controller_geometry(controller: Any) -> tuple[int, int]:
        strips = getattr(controller, "strip_count", None)
        leds = getattr(controller, "leds_per_strip", None)
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (strips, leds)):
            raise ValueError("controller must expose positive strip_count and leds_per_strip")
        total = getattr(controller, "total_leds", strips * leds)
        if total != strips * leds:
            raise ValueError("controller total_leds does not match strip geometry")
        return strips, leds

    @staticmethod
    def _default_background_factory(controller: Any, parameters: Mapping[str, Any]) -> AuroraCurtainsAnimation:
        return AuroraCurtainsAnimation(controller, parameters)

    @staticmethod
    def _default_clock_factory(
        controller: Any,
        parameters: Mapping[str, Any],
        wall_time_source: Callable[[], datetime],
    ) -> ClockOverlayAnimation:
        return _RuntimeClockOverlay(controller, parameters, wall_time_source)

    @staticmethod
    def _default_conway_factory(
        controller: Any, parameters: Mapping[str, Any],
    ) -> ConwayLifeAnimation:
        return ConwayLifeAnimation(controller, parameters)

    @property
    def desired_identity(self) -> SceneIdentity | None:
        """Identity of the basis requested for all subsequent render calls."""
        return None if self._canonical is None else self._canonical.identity

    def activate(self, canonical: CanonicalScene) -> SceneIdentity:
        """Accept one already-canonical scene without reinterpreting its identity."""
        raise CanonicalSceneRuntimeError(
            "the Scene v1 host runtime is retired; use the Scene v2 compositor"
        )
        self._require_canonical_basis(canonical)
        self._sync_slots(canonical.scene)
        self._canonical = canonical
        return canonical.identity

    # A named alias makes the activation boundary clear to control-plane callers.
    activate_scene = activate

    def render(self, monotonic_elapsed: float) -> RuntimeFrame:
        """Render the desired basis at host monotonic time and injected wall time."""
        canonical = self._canonical
        if canonical is None:
            raise CanonicalSceneRuntimeError("no canonical Scene v1 has been activated")
        elapsed = self._finite_nonnegative(monotonic_elapsed, "monotonic_elapsed")
        assert self._background is not None

        context = self._background_context(canonical, elapsed)
        background = self._render_background(self._background.animation, context)
        overlays = self._render_overlays(canonical.scene, elapsed)
        composed = self._compositor.compose(background, overlays)
        presentation_signature = (
            float(canonical.scene["presentation_luminance"]),
            float(canonical.scene["master_brightness"]),
        )
        presentation_changed = presentation_signature != self._last_presentation_signature
        changed = composed.changed or presentation_changed
        if changed or self._last_output is None:
            self._output_index = (self._output_index + 1) % len(self._output_buffers)
            output = self._output_buffers[self._output_index]
            np.multiply(composed.pixels, presentation_signature[0] * presentation_signature[1], out=self._scale_work)
            np.rint(self._scale_work, out=self._scale_work)
            np.clip(self._scale_work, 0.0, 255.0, out=self._scale_work)
            np.copyto(output, self._scale_work, casting="unsafe")
            self._last_output = output
            self._last_presentation_signature = presentation_signature
        assert self._last_output is not None
        self._frame_count += 1
        return RuntimeFrame(
            pixels=self._last_output,
            basis=canonical.identity,
            changed=changed,
            dirty_ranges=composed.dirty_ranges if changed and not presentation_changed else None,
        )

    def _sync_slots(self, scene: Mapping[str, Any]) -> None:
        background = scene["background"]
        background_descriptor = self._require_component(background, ComponentRole.BACKGROUND)
        if background_descriptor.component_id != AuroraCurtainsAnimation.COMPONENT_ID:
            raise CanonicalSceneRuntimeError("only Python Aurora Curtains backgrounds are integrated")
        parameters = _freeze_mapping(background["parameters"])
        key = self._component_key(background)
        seed = int(parameters["seed"])
        if (
            self._background is None
            or self._background.component_key != key
            or self._background.semantic_seed != seed
        ):
            self._background = _BackgroundSlot(
                component_key=key,
                semantic_seed=seed,
                animation=self._background_factory(self.controller, parameters),
                parameters=parameters,
            )
        elif self._background.parameters != parameters:
            self._background.animation.update_parameters(parameters)
            self._background.parameters = parameters

        active_slots: set[str] = set()
        for overlay in scene["overlays"]:
            slot_id = overlay["slot_id"]
            active_slots.add(slot_id)
            descriptor = self._require_component(overlay["component"], None)
            parameters = _freeze_mapping(overlay["component"]["parameters"])
            key = self._component_key(overlay["component"])
            existing = self._overlays.get(slot_id)
            if existing is None or existing.component_key != key:
                if descriptor.component_id == ClockOverlayAnimation.COMPONENT_ID:
                    animation = self._clock_factory(self.controller, parameters, self._wall_time_source)
                elif descriptor.component_id == ConwayLifeAnimation.COMPONENT_ID:
                    animation = self._conway_factory(self.controller, parameters)
                else:
                    raise CanonicalSceneRuntimeError("only Python Conway Life and Clock Overlay planes are integrated")
                self._overlays[slot_id] = _OverlaySlot(
                    component_key=key,
                    component_id=descriptor.component_id,
                    animation=animation,
                    parameters=parameters,
                )
            elif existing.parameters != parameters:
                existing.animation.update_parameters(parameters)
                existing.parameters = parameters
        for slot_id in set(self._overlays) - active_slots:
            del self._overlays[slot_id]

    def _background_context(self, canonical: CanonicalScene, elapsed: float) -> ResolvedScene:
        assert self._background is not None
        scene = canonical.scene
        descriptor = self._require_component(scene["background"], ComponentRole.BACKGROUND)
        return ResolvedScene(
            canonical_scene=_freeze_mapping(scene),
            canonical_bytes=canonical.canonical_bytes,
            digest=canonical.identity.digest,
            descriptor=descriptor,
            parameters=self._background.parameters,
            palette=MappingProxyType({"palette_id": scene["palette_id"]}),
            phase_time=elapsed * float(scene["wall_pace"]),
            plant_inputs=MappingProxyType({}),
        )

    def _render_background(self, animation: AuroraCurtainsAnimation, context: ResolvedScene) -> BaseFrame:
        animation.set_presentation_context(context)
        rendered = animation.generate_frame(context.phase_time, self._frame_count)
        pixels, changed, dirty_ranges = _rendered_rgb(rendered)
        return BaseFrame(pixels, changed=changed, dirty_ranges=dirty_ranges)

    def _render_overlays(self, scene: Mapping[str, Any], elapsed: float) -> tuple[PlacedOverlay, ...]:
        placed: list[PlacedOverlay] = []
        for overlay in scene["overlays"]:
            slot = self._overlays[overlay["slot_id"]]
            if slot.component_id == ConwayLifeAnimation.COMPONENT_ID:
                animation = slot.animation
                if not isinstance(animation, ConwayLifeAnimation):
                    raise CanonicalSceneRuntimeError("Conway Life slot has an incompatible animation")
                descriptor = self._require_component(overlay["component"], None)
                context = self._overlay_context(scene, descriptor, slot.parameters, elapsed)
                animation.set_presentation_context(context)
                frame = animation.generate_frame(context.phase_time, self._frame_count)
            else:
                frame = slot.animation.generate_frame(elapsed, self._frame_count)
            if not isinstance(frame, OverlayFrame):
                raise CanonicalSceneRuntimeError("integrated overlay must render an OverlayFrame")
            placement = overlay["placement"]
            placed.append(PlacedOverlay(
                frame=frame,
                strip_offset=placement["strip_translation"],
                led_offset=placement["led_translation"],
                opacity=overlay["opacity"],
                enabled=overlay["enabled"],
            ))
        return tuple(placed)

    @staticmethod
    def _overlay_context(
        scene: Mapping[str, Any],
        descriptor: ComponentDescriptor,
        parameters: Mapping[str, Any],
        elapsed: float,
    ) -> ResolvedScene:
        canonical_bytes = canonical_json_bytes(scene)
        return ResolvedScene(
            canonical_scene=_freeze_mapping(scene),
            canonical_bytes=canonical_bytes,
            digest=hashlib.sha256(canonical_bytes).hexdigest(),
            descriptor=descriptor,
            parameters=parameters,
            palette=MappingProxyType({"palette_id": scene["palette_id"]}),
            phase_time=elapsed * float(scene["wall_pace"]),
            plant_inputs=MappingProxyType({}),
        )

    def _require_component(self, component: Mapping[str, Any], role: ComponentRole | None) -> ComponentDescriptor:
        try:
            descriptor = self.catalog.require(
                provider=component["provider"], component_id=component["component_id"], version=component["version"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CanonicalSceneRuntimeError("canonical scene component is absent from this catalog") from exc
        if descriptor.provider is not ComponentProvider.PYTHON or descriptor.role is not role:
            raise CanonicalSceneRuntimeError("canonical scene component is outside the supported Python runtime")
        if role is ComponentRole.BACKGROUND and descriptor.timing_policy is not TimingPolicy.SCALED_CONTEXT:
            raise CanonicalSceneRuntimeError("canonical scene component is outside the supported Python runtime")
        if role is None and (
            (descriptor.component_id == ClockOverlayAnimation.COMPONENT_ID and descriptor.timing_policy is not TimingPolicy.WALL_CLOCK)
            or (descriptor.component_id == ConwayLifeAnimation.COMPONENT_ID and descriptor.timing_policy is not TimingPolicy.SCALED_CONTEXT)
            or descriptor.component_id not in {ClockOverlayAnimation.COMPONENT_ID, ConwayLifeAnimation.COMPONENT_ID}
        ):
            raise CanonicalSceneRuntimeError("canonical scene component is outside the supported Python runtime")
        return descriptor

    @staticmethod
    def _component_key(component: Mapping[str, Any]) -> tuple[str, str, int, str]:
        return (component["provider"], component["component_id"], component["version"], component["role"])

    @staticmethod
    def _finite_nonnegative(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
            raise ValueError(f"{name} must be finite and non-negative")
        return float(value)

    @staticmethod
    def _require_canonical_basis(canonical: CanonicalScene) -> None:
        if not isinstance(canonical, CanonicalScene):
            raise TypeError("canonical must be a CanonicalScene")
        if canonical.identity.revision != 1:
            raise CanonicalSceneRuntimeError("canonical scene revision is not current Scene v1")
        bytes_value = canonical_json_bytes(canonical.scene)
        if bytes_value != canonical.canonical_bytes:
            raise CanonicalSceneRuntimeError("canonical scene bytes do not match its scene value")
        if hashlib.sha256(bytes_value).hexdigest() != canonical.identity.digest:
            raise CanonicalSceneRuntimeError("canonical scene digest does not match its bytes")


def _rendered_rgb(rendered: Any) -> tuple[np.ndarray, bool, tuple[tuple[int, int], ...] | None]:
    if isinstance(rendered, RenderedFrame):
        return rendered.pixels, rendered.changed, rendered.dirty_ranges
    if isinstance(rendered, np.ndarray):
        return rendered, True, None
    raise CanonicalSceneRuntimeError("Aurora Curtains must render RGB pixels")


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


__all__ = ["CanonicalSceneRuntime", "CanonicalSceneRuntimeError", "RuntimeFrame"]
