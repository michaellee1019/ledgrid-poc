"""Current-only Scene v2 compositor runtime.

The receiver owns exactly one qualified native Background.  One Python
Animation and ordered Python Widgets are folded into the established aggregate
premultiplied-RGBA foreground transport before final RGB output.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

import numpy as np

from animation.core.base import RenderedFrame
from animation.core.component_catalog import AlphaBehavior, ComponentCatalog, ComponentDescriptor, PalettePolicy
from animation.core.compositing import BaseFrame, HostSceneCompositor, OverlayFrame, PlacedOverlay
from animation.core.presentation_contracts import NEUTRAL_PLANT_INPUTS, ResolvedScene
from animation.core.widget_placement import (
    WidgetPlacementResolution, resolve_widget_placement, translated_widget_coverage,
)
from ipc.scene_contract import (
    CanonicalScene, SCENE_V2_REVISION, SceneIdentity, canonical_json_bytes,
    normalize_composer_scene,
)


class CanonicalSceneRuntimeError(ValueError):
    """An activated scene cannot be rendered by the current Scene v2 runtime."""


@dataclass(frozen=True)
class RuntimeFrame:
    pixels: np.ndarray
    basis: SceneIdentity
    changed: bool
    dirty_ranges: tuple[tuple[int, int], ...] | None = None
    foreground: OverlayFrame | None = None
    stage_trace: tuple[str, ...] = (
        "native_background", "animation", "widgets", "plant_optics",
        "look_presentation", "output_master_brightness",
    )
    widget_placements: Mapping[str, WidgetPlacementResolution] = MappingProxyType({})


BackgroundRenderer = Callable[[ResolvedScene, int], BaseFrame]
ComponentFactory = Callable[[ComponentDescriptor, Any, Mapping[str, Any]], Any]
PlantInputResolver = Callable[[Mapping[str, Any], ComponentDescriptor], Mapping[str, float]]
PlantOptics = Callable[[np.ndarray, Mapping[str, Any]], np.ndarray]
WidgetPlacementResolver = Callable[[Mapping[str, Any], int, int, int], tuple[int, int]]
WidgetSafeGeometry = Callable[[Mapping[str, Any], int, int], np.ndarray]


@dataclass
class _ComponentSlot:
    component_key: tuple[str, str, int, str]
    instance: Any
    parameters: Mapping[str, Any]
    plane_revision: int = 0
    opaque_pixels: np.ndarray | None = None


class CanonicalSceneRuntime:
    """Render a canonical Scene v2 without changing its identity.

    ``background_renderer`` is the receiver-native preview seam.  Plant
    calibration is supplied by runtime-owned callbacks, never saved in a look.
    """

    def __init__(
        self, controller: Any, catalog: ComponentCatalog, *,
        background_renderer: Optional[BackgroundRenderer] = None,
        animation_factory: Optional[ComponentFactory] = None,
        widget_factory: Optional[ComponentFactory] = None,
        plant_input_resolver: Optional[PlantInputResolver] = None,
        plant_optics: Optional[PlantOptics] = None,
        widget_placement_resolver: Optional[WidgetPlacementResolver] = None,
        widget_safe_geometry: Optional[WidgetSafeGeometry] = None,
        master_brightness: float = 1.0,
    ) -> None:
        if not isinstance(catalog, ComponentCatalog):
            raise TypeError("catalog must be a ComponentCatalog")
        self.controller = controller
        self.catalog = catalog
        self.strip_count, self.leds_per_strip = self._controller_geometry(controller)
        self._compositor = HostSceneCompositor(self.strip_count, self.leds_per_strip)
        self._background_renderer = background_renderer or self._black_native_preview
        self._animation_factory = animation_factory or self._default_component_factory
        self._widget_factory = widget_factory or self._default_component_factory
        self._plant_input_resolver = plant_input_resolver or self._neutral_plant_inputs
        self._plant_optics = plant_optics or self._identity_plant_optics
        # The legacy resolver remains an opt-in compatibility seam. New
        # callers bind calibrated ``PlantMaskGeometry.safe_flat`` here so
        # placements use installation authority rather than scene data.
        self._widget_placement_resolver = widget_placement_resolver
        self._widget_safe_geometry = widget_safe_geometry or self._all_safe_widget_geometry
        self._master_brightness = self._factor(master_brightness, "master_brightness")
        self._animation: _ComponentSlot | None = None
        self._widgets: dict[str, _ComponentSlot] = {}
        self._canonical: CanonicalScene | None = None
        self._frame_count = 0
        total = self.strip_count * self.leds_per_strip
        self._output_buffers = tuple(np.empty((total, 3), dtype=np.uint8) for _ in range(2))
        self._output_index = -1
        self._scale_work = np.empty((total, 3), dtype=np.float32)
        self._last_output: np.ndarray | None = None
        self._last_pipeline_signature: tuple[Any, ...] | None = None

    @staticmethod
    def _controller_geometry(controller: Any) -> tuple[int, int]:
        strips, leds = getattr(controller, "strip_count", None), getattr(controller, "leds_per_strip", None)
        if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in (strips, leds)):
            raise ValueError("controller must expose positive strip_count and leds_per_strip")
        if getattr(controller, "total_leds", strips * leds) != strips * leds:
            raise ValueError("controller total_leds does not match strip geometry")
        return strips, leds

    @property
    def desired_identity(self) -> SceneIdentity | None:
        return None if self._canonical is None else self._canonical.identity

    @property
    def master_brightness(self) -> float:
        """Installation-owned factor; never part of a Scene v2 look."""
        return self._master_brightness

    def set_master_brightness(self, value: float) -> float:
        self._master_brightness = self._factor(value, "master_brightness")
        return self._master_brightness

    def activate(self, canonical: CanonicalScene) -> SceneIdentity:
        self._require_canonical_basis(canonical)
        self._sync_slots(canonical.scene)
        self._canonical = canonical
        return canonical.identity

    activate_scene = activate

    def render(self, monotonic_elapsed: float) -> RuntimeFrame:
        canonical = self._canonical
        if canonical is None:
            raise CanonicalSceneRuntimeError("no canonical Scene v2 has been activated")
        elapsed = self._finite_nonnegative(monotonic_elapsed, "monotonic_elapsed")
        scene = canonical.scene
        bg_context = self._context(canonical, self._descriptor(scene["background"]), scene["background"]["parameters"], elapsed)
        background = self._background_renderer(bg_context, self._frame_count)
        if not isinstance(background, BaseFrame):
            raise CanonicalSceneRuntimeError("receiver-native background renderer must return BaseFrame")
        self._require_geometry(background.pixels, 3, "receiver-native background")

        assert self._animation is not None
        animation_descriptor = self._descriptor(scene["animation"])
        animation_context = self._context(canonical, animation_descriptor, self._animation.parameters, elapsed)
        layers = [PlacedOverlay(self._render_plane(self._animation, animation_descriptor, animation_context))]
        placements: dict[str, WidgetPlacementResolution] = {}
        reserved_widgets = np.zeros(self.strip_count * self.leds_per_strip, dtype=np.bool_)
        for index, widget in enumerate(scene["widgets"]):
            if not widget["visible"]:
                continue
            descriptor, slot = self._descriptor(widget["component"]), self._widgets[widget["id"]]
            context = self._context(canonical, descriptor, slot.parameters, elapsed)
            plane = self._render_plane(slot, descriptor, context)
            placement = self._placement(widget, index, plane, scene["plants"], reserved_widgets)
            placements[widget["id"]] = placement
            layers.append(PlacedOverlay(plane, placement.strip_translation, placement.led_translation))
            reserved_widgets |= translated_widget_coverage(
                plane, strip_count=self.strip_count, leds_per_strip=self.leds_per_strip,
                strip_translation=placement.strip_translation, led_translation=placement.led_translation,
            )

        composed = self._compositor.compose(background, tuple(layers))
        foreground = self._compositor.aggregate_foreground()
        pipeline_signature = (scene["plants"], scene["look"]["presentation_brightness"], self._master_brightness)
        pipeline_changed = pipeline_signature != self._last_pipeline_signature
        changed = composed.changed or pipeline_changed or self._last_output is None
        if changed:
            final = self._validated_rgb(self._plant_optics(composed.pixels, scene["plants"]), "plant optics")
            np.multiply(final, float(scene["look"]["presentation_brightness"]), out=self._scale_work)
            np.multiply(self._scale_work, self._master_brightness, out=self._scale_work)
            np.rint(self._scale_work, out=self._scale_work)
            np.clip(self._scale_work, 0.0, 255.0, out=self._scale_work)
            self._output_index = (self._output_index + 1) % len(self._output_buffers)
            self._last_output = self._output_buffers[self._output_index]
            np.copyto(self._last_output, self._scale_work, casting="unsafe")
            self._last_pipeline_signature = pipeline_signature
        assert self._last_output is not None
        self._frame_count += 1
        return RuntimeFrame(self._last_output, canonical.identity, changed,
                            composed.dirty_ranges if changed and not pipeline_changed else None, foreground,
                            widget_placements=MappingProxyType(placements))

    def _sync_slots(self, scene: Mapping[str, Any]) -> None:
        component = scene["animation"]
        animation = self._candidate_slot(self._animation, self._descriptor(component), component["parameters"], self._animation_factory)
        widgets: dict[str, _ComponentSlot] = {}
        active: set[str] = set()
        for widget in scene["widgets"]:
            widget_id, component = widget["id"], widget["component"]
            active.add(widget_id)
            widgets[widget_id] = self._candidate_slot(self._widgets.get(widget_id), self._descriptor(component), component["parameters"], self._widget_factory)
        # Nothing above mutates a published slot. Only exchange the complete
        # candidate set after every constructor/validation path has succeeded.
        self._animation = animation
        self._widgets = widgets

    def _candidate_slot(self, current: _ComponentSlot | None, descriptor: ComponentDescriptor,
                        parameters: Mapping[str, Any], factory: ComponentFactory) -> _ComponentSlot:
        frozen = _freeze_mapping(parameters)
        key = (descriptor.provider.value, descriptor.component_id, descriptor.version, descriptor.role.value)
        if current is not None and current.component_key == key and current.parameters == frozen:
            return current
        # Reconstruct changed components rather than live-mutating the active
        # instance. That makes a later failing Widget factory unable to leak a
        # partial Animation update into the last valid scene.
        return _ComponentSlot(key, factory(descriptor, self.controller, frozen), frozen)

    def _context(self, canonical: CanonicalScene, descriptor: ComponentDescriptor, parameters: Mapping[str, Any], elapsed: float) -> ResolvedScene:
        supplied = self._plant_input_resolver(canonical.scene["plants"], descriptor)
        if not isinstance(supplied, Mapping):
            raise CanonicalSceneRuntimeError("plant input resolver must return a mapping")
        plant_inputs: dict[str, float] = {}
        for name in descriptor.required_simulation_inputs:
            if name not in supplied:
                raise CanonicalSceneRuntimeError(f"required plant simulation input {name!r} is missing")
            value = supplied[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise CanonicalSceneRuntimeError(f"plant simulation input {name!r} must be finite")
            plant_inputs[name] = float(value)
        for name in descriptor.optional_simulation_inputs:
            value = supplied.get(name, NEUTRAL_PLANT_INPUTS.get(name, 0.0))
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise CanonicalSceneRuntimeError(f"plant simulation input {name!r} must be finite")
            plant_inputs[name] = float(value)
        palette = _freeze_mapping({"palette_id": canonical.scene["look"]["palette_id"]}) if descriptor.palette_policy is PalettePolicy.SEMANTIC else None
        return ResolvedScene(_freeze_mapping(canonical.scene), canonical.canonical_bytes, canonical.identity.digest,
                             descriptor, parameters, palette, elapsed * float(canonical.scene["look"]["pace"]), _freeze_mapping(plant_inputs))

    def _render_plane(self, slot: _ComponentSlot, descriptor: ComponentDescriptor, context: ResolvedScene) -> OverlayFrame:
        instance = slot.instance
        set_context = getattr(instance, "set_presentation_context", None)
        if set_context is not None:
            set_context(context)
        resolved_renderer = getattr(instance, "render_resolved_scene", None)
        if resolved_renderer is not None:
            rendered = resolved_renderer(context)
        else:
            generator = getattr(instance, "generate_frame", None)
            if generator is None:
                raise CanonicalSceneRuntimeError(f"{descriptor.component_id} has no frame renderer")
            rendered = generator(context.phase_time if descriptor.timing_policy.value == "scaled_context" else 0.0, self._frame_count)
        if isinstance(rendered, OverlayFrame):
            if descriptor.alpha_behavior is AlphaBehavior.OPAQUE and np.any(rendered.pixels[:, 3] != 255):
                raise CanonicalSceneRuntimeError("opaque Animation must produce alpha 255")
            return rendered
        pixels, changed, dirty = _rendered_pixels(rendered)
        if descriptor.alpha_behavior is not AlphaBehavior.OPAQUE:
            raise CanonicalSceneRuntimeError("premultiplied_rgba component must return OverlayFrame")
        rgb = self._validated_rgb(pixels, descriptor.component_id)
        if slot.opaque_pixels is None:
            slot.opaque_pixels = np.empty((rgb.shape[0], 4), dtype=np.uint8)
            refresh = True
        else:
            refresh = changed
        if refresh:
            slot.opaque_pixels[:, :3], slot.opaque_pixels[:, 3] = rgb, 255
            slot.plane_revision += 1
        return OverlayFrame(slot.opaque_pixels, revision=slot.plane_revision, changed=changed, dirty_ranges=dirty)

    def _placement(self, widget: Mapping[str, Any], index: int, frame: OverlayFrame,
                   plants: Mapping[str, Any], reserved_widgets: np.ndarray) -> WidgetPlacementResolution:
        if self._widget_placement_resolver is not None:
            resolved = self._widget_placement_resolver(widget, index, self.strip_count, self.leds_per_strip)
            if not isinstance(resolved, tuple) or len(resolved) != 2 or any(isinstance(item, bool) or not isinstance(item, int) for item in resolved):
                raise CanonicalSceneRuntimeError("widget placement resolver must return integer offsets")
            return WidgetPlacementResolution(*resolved)
        try:
            safe_flat = self._widget_safe_geometry(plants, self.strip_count, self.leds_per_strip)
            return resolve_widget_placement(
                widget["placement"], frame, strip_count=self.strip_count,
                leds_per_strip=self.leds_per_strip, safe_flat=safe_flat,
                reserved_flat=reserved_widgets,
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise CanonicalSceneRuntimeError(f"widget placement could not be resolved: {exc}") from exc

    def _descriptor(self, component: Mapping[str, Any]) -> ComponentDescriptor:
        try:
            return self.catalog.require(provider=component["provider"], component_id=component["component_id"], version=component["version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CanonicalSceneRuntimeError("canonical scene component is absent from this catalog") from exc

    @staticmethod
    def _default_component_factory(descriptor: ComponentDescriptor, controller: Any, parameters: Mapping[str, Any]) -> Any:
        if descriptor.component_id == "aurora_curtains":
            from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
            return AuroraCurtainsAnimation(controller, parameters)
        if descriptor.component_id == "conway_life":
            from animation.plugins.conway_life import ConwayLifeAnimation
            return ConwayLifeAnimation(controller, parameters)
        if descriptor.component_id == "clock_overlay":
            from animation.plugins.clock_overlay import ClockOverlayAnimation
            return ClockOverlayAnimation(controller, parameters)
        raise CanonicalSceneRuntimeError(f"no runtime factory is registered for {descriptor.component_id}")

    def _black_native_preview(self, context: ResolvedScene, frame_count: int) -> BaseFrame:
        del context, frame_count
        return BaseFrame(np.zeros((self.strip_count * self.leds_per_strip, 3), dtype=np.uint8), changed=False)

    @staticmethod
    def _neutral_plant_inputs(plants: Mapping[str, Any], descriptor: ComponentDescriptor) -> Mapping[str, float]:
        del plants, descriptor
        return NEUTRAL_PLANT_INPUTS

    @staticmethod
    def _identity_plant_optics(pixels: np.ndarray, plants: Mapping[str, Any]) -> np.ndarray:
        del plants
        return pixels

    @staticmethod
    def _all_safe_widget_geometry(plants: Mapping[str, Any], strips: int, leds: int) -> np.ndarray:
        """Headless default; installed runtimes inject ``PlantMaskGeometry.safe_flat``."""
        del plants
        return np.ones(strips * leds, dtype=np.bool_)

    @staticmethod
    def _factor(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 2.0:
            raise ValueError(f"{name} must be a finite number from 0 to 2")
        return float(value)

    def _require_geometry(self, pixels: np.ndarray, channels: int, name: str) -> None:
        if pixels.dtype != np.uint8 or pixels.shape != (self.strip_count * self.leds_per_strip, channels) or not pixels.flags.c_contiguous:
            raise CanonicalSceneRuntimeError(f"{name} must be C-contiguous uint8 ({self.strip_count * self.leds_per_strip}, {channels})")

    def _validated_rgb(self, pixels: Any, name: str) -> np.ndarray:
        if not isinstance(pixels, np.ndarray):
            raise CanonicalSceneRuntimeError(f"{name} must render numpy pixels")
        self._require_geometry(pixels, 3, name)
        return pixels

    @staticmethod
    def _finite_nonnegative(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
            raise ValueError(f"{name} must be finite and non-negative")
        return float(value)

    def _require_canonical_basis(self, canonical: CanonicalScene) -> None:
        if not isinstance(canonical, CanonicalScene):
            raise TypeError("canonical must be a CanonicalScene")
        if canonical.identity.revision != SCENE_V2_REVISION:
            raise CanonicalSceneRuntimeError("canonical scene revision is not current Scene v2")
        bytes_value = canonical_json_bytes(canonical.scene)
        if bytes_value != canonical.canonical_bytes:
            raise CanonicalSceneRuntimeError("canonical scene bytes do not match its scene value")
        if hashlib.sha256(bytes_value).hexdigest() != canonical.identity.digest:
            raise CanonicalSceneRuntimeError("canonical scene digest does not match its bytes")
        try:
            normalized = normalize_composer_scene({"origin": "composer", "scene": canonical.scene}, self.catalog)
        except (TypeError, ValueError) as exc:
            raise CanonicalSceneRuntimeError("canonical scene no longer satisfies the Scene v2 catalog") from exc
        if normalized.canonical_bytes != canonical.canonical_bytes or normalized.identity != canonical.identity:
            raise CanonicalSceneRuntimeError("canonical scene is not a validated Scene v2 basis")


def _rendered_pixels(rendered: Any) -> tuple[Any, bool, tuple[tuple[int, int], ...] | None]:
    if isinstance(rendered, RenderedFrame):
        return rendered.pixels, rendered.changed, rendered.dirty_ranges
    if isinstance(rendered, np.ndarray):
        return rendered, True, None
    raise CanonicalSceneRuntimeError("component renderer must return RGB pixels, RenderedFrame, or OverlayFrame")


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


__all__ = ["CanonicalSceneRuntime", "CanonicalSceneRuntimeError", "RuntimeFrame"]
