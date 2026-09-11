"""Inert, continuously-cadenced final preview for Composer Scene v2.

This is deliberately a presentation seam, not a second scene model.  It owns
only the verified host peer for the receiver-native background plus calibrated
final plant optics.  CanonicalSceneRuntime continues to own ordering, alpha
composition, resolved palette/pace, and the single output-brightness boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

import numpy as np

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from animation.core.compositing import BaseFrame
from animation.core.manager import PreviewLEDController
from animation.core.installation_profile_runtime import InstallationProfileRuntimeView
from animation.core.plant_awareness import (
    INSTALLATION_GEOMETRY_CONTACT_INPUT,
    InstallationGeometryContact,
    PlantMaskCache,
)
from animation.core.scene_runtime import (
    CanonicalSceneRuntime, RuntimeFrame, ScenePresentationContext,
)
from animation.native.managed_preview import ManagedNativeHostPreview
from animation.native.aurora import canonical_palette_roles
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
from animation.plugins.canopy_cup import CanopyCupAnimation
from animation.plugins.ascii_drop import AsciiDropAnimation
from animation.plugins.christmas_tree import ChristmasTreeAnimation
from animation.plugins.clock_overlay import ClockOverlayAnimation
from animation.plugins.conway_life import ConwayLifeAnimation
from animation.plugins.emoji_arranger import EmojiArrangerAnimation
from animation.plugins.emoji import EmojiAnimation
from animation.plugins.firefly_synchrony import FireflySynchronyAnimation
from animation.plugins.fireworks import FireworksAnimation
from animation.plugins.flame_burst import FlameBurstAnimation
from animation.plugins.fluid_tank import FluidTankAnimation
from animation.plugins.cyclic_reef import CyclicReefAnimation
from animation.plugins.lava_lamp import LavaLampAnimation
from animation.plugins.maze_chase import MazeChaseAnimation
from animation.plugins.night_train_windows import NightTrainWindowsAnimation
from animation.plugins.pinball import PinballAnimation
from animation.plugins.pixel_quest import PixelQuestAnimation
from animation.plugins.snake import SnakeAnimation
from animation.plugins.tetris import TetrisAnimation
from animation.plugins.gradient import GradientAnimation
from animation.plugins.rainbow import RainbowAnimation
from animation.plugins.solid import SolidColorAnimation
from animation.plugins.sparkle import SparkleAnimation
from animation.plugins.wave import WaveAnimation
from animation.plugins.circadian_window import CircadianWindowAnimation
from animation.plugins.cloud_canyon import CloudCanyonAnimation
from animation.plugins.desert_wind import DesertWindAnimation
from animation.plugins.moonlit_fog_banks import MoonlitFogBanksAnimation
from animation.plugins.rain_on_glass import RainOnGlassAnimation
from animation.plugins.tidal_bioluminescence import TidalBioluminescenceAnimation
from animation.plugins.waterfall_veil import WaterfallVeilAnimation
from animation.plugins.cellular_tapestry import CellularTapestryAnimation
from animation.plugins.flow_field_silk import FlowFieldSilkAnimation
from animation.plugins.frostwork import FrostworkAnimation
from animation.plugins.living_stained_glass import LivingStainedGlassAnimation
from animation.plugins.quasicrystal_bloom import QuasicrystalBloomAnimation
from animation.plugins.living_ecosystem import LivingEcosystemAnimation
from animation.plugins.physarum_network import PhysarumNetworkAnimation
from animation.plugins.reaction_diffusion_garden import ReactionDiffusionGardenAnimation
from animation.plugins.wind_in_the_reeds import WindInTheReedsAnimation
from ipc.scene_contract import CanonicalScene


NATIVE_AURORA_COMPONENT_ID = "native_aurora"
NATIVE_AURORA_BUNDLE_DIGEST = "024522f7ab0a2bad9eb9aaaa34ae47b11c41f05cab16bc4d434a58f41d39e8ce"
NATIVE_AURORA_PAYLOAD_DIGEST = "f3fa66249f42a4e8751279d19a4f3613d3bdffdb9d39043680742940c84f5c37"
NATIVE_AURORA_HOST_ARTIFACT_DIGEST = "a546fc1ff316e0db63316a2ab7512552f986721de1d43a7aa373361cae8f7da0"


def native_aurora_descriptor() -> ComponentDescriptor:
    """The integrity declaration for the installed native ambient renderer."""

    return ComponentDescriptor(
        component_id=NATIVE_AURORA_COMPONENT_ID,
        version=1,
        provider="receiver_native",
        role="background",
        timing_policy="scaled_context",
        alpha_behavior="none",
        palette_policy="semantic",
        plant_capabilities=("final_optics",),
        fidelity_exceptions=(),
        intensity_parameter="gain",
        defaults={
            "bundle_digest": NATIVE_AURORA_BUNDLE_DIGEST,
            "gain": 0.72,
            "source_fps": 30.0,
            "seed": 8012,
        },
    )


def current_component_descriptors() -> tuple[ComponentDescriptor, ...]:
    """Return the complete, deliberately small Scene v2 component packet.

    This is the sole Composer assembly seam. Retaining a demonstrated
    component means adding its fully qualified descriptor here; policy stays
    with the component declaration rather than being reimplemented by callers.
    """

    return (
        native_aurora_descriptor(),
        AuroraCurtainsAnimation.component_descriptor(),
        CanopyCupAnimation.component_descriptor(),
        AsciiDropAnimation.component_descriptor(),
        EmojiAnimation.component_descriptor(),
        ChristmasTreeAnimation.component_descriptor(),
        NightTrainWindowsAnimation.component_descriptor(),
        ConwayLifeAnimation.component_descriptor(),
        TetrisAnimation.component_descriptor(),
        FireflySynchronyAnimation.component_descriptor(),
        FireworksAnimation.component_descriptor(),
        FlameBurstAnimation.component_descriptor(),
        FluidTankAnimation.component_descriptor(),
        CyclicReefAnimation.component_descriptor(),
        LavaLampAnimation.component_descriptor(),
        SnakeAnimation.component_descriptor(),
        MazeChaseAnimation.component_descriptor(),
        PinballAnimation.component_descriptor(),
        PixelQuestAnimation.component_descriptor(),
        GradientAnimation.component_descriptor(),
        RainbowAnimation.component_descriptor(),
        SolidColorAnimation.component_descriptor(),
        SparkleAnimation.component_descriptor(),
        WaveAnimation.component_descriptor(),
        CircadianWindowAnimation.component_descriptor(),
        CloudCanyonAnimation.component_descriptor(),
        DesertWindAnimation.component_descriptor(),
        MoonlitFogBanksAnimation.component_descriptor(),
        RainOnGlassAnimation.component_descriptor(),
        TidalBioluminescenceAnimation.component_descriptor(),
        WaterfallVeilAnimation.component_descriptor(),
        CellularTapestryAnimation.component_descriptor(),
        FlowFieldSilkAnimation.component_descriptor(),
        FrostworkAnimation.component_descriptor(),
        LivingStainedGlassAnimation.component_descriptor(),
        QuasicrystalBloomAnimation.component_descriptor(),
        LivingEcosystemAnimation.component_descriptor(),
        PhysarumNetworkAnimation.component_descriptor(),
        ReactionDiffusionGardenAnimation.component_descriptor(),
        WindInTheReedsAnimation.component_descriptor(),
        ClockOverlayAnimation.component_descriptor(),
        EmojiArrangerAnimation.component_descriptor(),
    )


def current_component_catalog() -> ComponentCatalog:
    """Build the current Composer catalog from its finite packet."""

    return ComponentCatalog(current_component_descriptors())


class _NativeAuroraPreview:
    """Scene adapter over the verified host build of the receiver source."""

    def __init__(self, project_root: Path) -> None:
        self._native = ManagedNativeHostPreview(
            project_root, NATIVE_AURORA_COMPONENT_ID, NATIVE_AURORA_BUNDLE_DIGEST
        )

    def render(self, context: Any, frame_count: int) -> BaseFrame:
        parameters = context.parameters
        pace = float(context.canonical_scene["look"]["pace"])
        unscaled = context.phase_time / pace if pace > 0.0 else 0.0
        roles = canonical_palette_roles(context.palette["palette_id"])
        return self._native.render(
            parameters=parameters,
            palette=tuple(roles.values()),
            scaled_scene_time=context.phase_time,
            unscaled_scene_time=unscaled,
            frame_index=frame_count,
        )


@dataclass
class _PlantGeometryOwner:
    strip_count: int
    leds_per_strip: int
    project_root: Path

    @property
    def params(self) -> Mapping[str, Any]:
        return {
            "plant_clearance": 1,
            "plant_mask_path": str(self.project_root / "config" / "plant_pixel_map_32x138.json"),
            "plant_globe_mask_path": str(self.project_root / "config" / "plant_globe_map_32x138.json"),
        }

    def get_strip_info(self) -> tuple[int, int]:
        return self.strip_count, self.leds_per_strip

    def get_pixel_count(self) -> int:
        return self.strip_count * self.leds_per_strip


class InstalledFinalSceneRuntime:
    """The shared installed-final Scene v2 host runtime.

    Both a live host and Composer Preview create independent instances of this
    runtime.  They consume the same :class:`ScenePresentationContext`, which
    fixes the canonical basis, monotonic time, wall clock, component ordering,
    calibrated plant inputs, and final optics in one boundary.
    """

    def __init__(
        self,
        catalog: Any,
        project_root: Path,
        *,
        controller: Any | None = None,
        foreground_only: bool = False,
    ) -> None:
        self.controller = controller or PreviewLEDController(strips=33, leds_per_strip=138)
        if (getattr(self.controller, "strip_count", None), getattr(self.controller, "leds_per_strip", None)) != (33, 138):
            raise ValueError("installed Scene v2 presentation requires a 33x138 controller")
        self.foreground_only = bool(foreground_only)
        self._wall_time = datetime.now().astimezone()
        self._native = None if self.foreground_only else _NativeAuroraPreview(project_root)
        background_renderer = (
            self._render_foreground_only_background
            if self.foreground_only
            else self._native.render
        )
        self._geometry = PlantMaskCache(_PlantGeometryOwner(33, 138, project_root))
        self._installation_profile_view: InstallationProfileRuntimeView | None = None
        self._geometry_contact: InstallationGeometryContact | None = None
        self._geometry_contact_source: int | None = None
        self._runtime = CanonicalSceneRuntime(
            self.controller,
            catalog,
            background_renderer=background_renderer,
            animation_factory=self._animation_factory,
            widget_factory=self._widget_factory,
            plant_input_resolver=self._plant_inputs,
            plant_optics=self._plant_optics,
            widget_safe_geometry=self._widget_safe_geometry,
            wall_time_consumer=self._set_wall_time,
        )
        self._active_digest: str | None = None
        self._lock = RLock()

    def _render_foreground_only_background(
        self, _context: Any, _frame_count: int
    ) -> BaseFrame:
        """Supply an inert base only for receiver foreground extraction.

        The resulting RGB frame is not an installed-final preview. Live receiver
        activation consumes only ``RuntimeFrame.foreground`` and combines it with
        the separately verified receiver-native background on the receiver.
        """

        return BaseFrame(
            np.zeros((self.controller.total_leds, 3), dtype=np.uint8),
            changed=True,
        )

    def render(self, presentation: ScenePresentationContext) -> RuntimeFrame:
        """Render one installed-final frame without mutating publication state."""

        if not isinstance(presentation, ScenePresentationContext):
            raise TypeError("presentation must be a ScenePresentationContext")
        with self._lock:
            canonical = presentation.canonical
            if canonical.identity.digest != self._active_digest:
                self._runtime.activate(canonical)
                self._active_digest = canonical.identity.digest
            return self._runtime.render_presentation(presentation)

    def set_installation_profile(
        self, view: InstallationProfileRuntimeView | None
    ) -> None:
        """Swap immutable runtime-owned geometry without touching Scene state."""

        if view is not None and not isinstance(view, InstallationProfileRuntimeView):
            raise TypeError("installation profile view must be immutable runtime geometry")
        if view is self._installation_profile_view:
            return
        self._installation_profile_view = view
        self._geometry_contact = None
        self._geometry_contact_source = None
        self._runtime.invalidate_installation_geometry()

    def _installation_geometry(self):
        view = self._installation_profile_view
        return view.plant_masks if isinstance(view, InstallationProfileRuntimeView) else self._geometry.get()

    def _set_wall_time(self, wall_time: Any) -> None:
        if not isinstance(wall_time, datetime) or wall_time.tzinfo is None:
            raise ValueError("Scene v2 presentation wall_time must be a timezone-aware datetime")
        self._wall_time = wall_time

    def dispatch_animation_interaction(
        self, canonical: CanonicalScene, kind: str, x: float, y: float,
        strength: float = 1.0,
    ) -> bool:
        """Apply a gesture to this exact published final-preview basis only."""

        with self._lock:
            if canonical.identity.digest != self._active_digest:
                self._runtime.activate(canonical)
                self._active_digest = canonical.identity.digest
            return self._runtime.dispatch_animation_interaction(kind, x, y, strength)

    def _animation_factory(self, descriptor: ComponentDescriptor, controller: Any, parameters: Mapping[str, Any]) -> Any:
        if descriptor.component_id == AuroraCurtainsAnimation.COMPONENT_ID:
            return AuroraCurtainsAnimation(controller, parameters)
        if descriptor.component_id == ConwayLifeAnimation.COMPONENT_ID:
            return ConwayLifeAnimation(controller, parameters)
        if descriptor.component_id == TetrisAnimation.COMPONENT_ID:
            return TetrisAnimation(controller, parameters)
        if descriptor.component_id == CanopyCupAnimation.COMPONENT_ID:
            return CanopyCupAnimation(controller, parameters)
        if descriptor.component_id == AsciiDropAnimation.COMPONENT_ID:
            return AsciiDropAnimation(controller, parameters)
        if descriptor.component_id == EmojiAnimation.COMPONENT_ID:
            return EmojiAnimation(controller, parameters)
        if descriptor.component_id == ChristmasTreeAnimation.COMPONENT_ID:
            return ChristmasTreeAnimation(controller, parameters)
        if descriptor.component_id == NightTrainWindowsAnimation.COMPONENT_ID:
            return NightTrainWindowsAnimation(controller, parameters)
        if descriptor.component_id == FireflySynchronyAnimation.COMPONENT_ID:
            return FireflySynchronyAnimation(controller, parameters)
        if descriptor.component_id == FireworksAnimation.COMPONENT_ID:
            return FireworksAnimation(controller, parameters)
        if descriptor.component_id == FlameBurstAnimation.COMPONENT_ID:
            return FlameBurstAnimation(controller, parameters)
        if descriptor.component_id == FluidTankAnimation.COMPONENT_ID:
            return FluidTankAnimation(controller, parameters)
        if descriptor.component_id == CyclicReefAnimation.COMPONENT_ID:
            return CyclicReefAnimation(controller, parameters)
        if descriptor.component_id == LavaLampAnimation.COMPONENT_ID:
            return LavaLampAnimation(controller, parameters)
        if descriptor.component_id == SnakeAnimation.COMPONENT_ID:
            return SnakeAnimation(controller, parameters)
        if descriptor.component_id == MazeChaseAnimation.COMPONENT_ID:
            return MazeChaseAnimation(controller, parameters)
        if descriptor.component_id == PinballAnimation.COMPONENT_ID:
            return PinballAnimation(controller, parameters)
        if descriptor.component_id == PixelQuestAnimation.COMPONENT_ID:
            return PixelQuestAnimation(controller, parameters)
        if descriptor.component_id == GradientAnimation.COMPONENT_ID:
            return GradientAnimation(controller, parameters)
        if descriptor.component_id == RainbowAnimation.COMPONENT_ID:
            return RainbowAnimation(controller, parameters)
        if descriptor.component_id == SolidColorAnimation.COMPONENT_ID:
            return SolidColorAnimation(controller, parameters)
        if descriptor.component_id == SparkleAnimation.COMPONENT_ID:
            return SparkleAnimation(controller, parameters)
        if descriptor.component_id == WaveAnimation.COMPONENT_ID:
            return WaveAnimation(controller, parameters)
        if descriptor.component_id == CircadianWindowAnimation.COMPONENT_ID:
            return CircadianWindowAnimation(controller, parameters)
        if descriptor.component_id == CloudCanyonAnimation.COMPONENT_ID:
            return CloudCanyonAnimation(controller, parameters)
        if descriptor.component_id == DesertWindAnimation.COMPONENT_ID:
            return DesertWindAnimation(controller, parameters)
        if descriptor.component_id == MoonlitFogBanksAnimation.COMPONENT_ID:
            return MoonlitFogBanksAnimation(controller, parameters)
        if descriptor.component_id == RainOnGlassAnimation.COMPONENT_ID:
            return RainOnGlassAnimation(controller, parameters)
        if descriptor.component_id == TidalBioluminescenceAnimation.COMPONENT_ID:
            return TidalBioluminescenceAnimation(controller, parameters)
        if descriptor.component_id == WaterfallVeilAnimation.COMPONENT_ID:
            return WaterfallVeilAnimation(controller, parameters)
        if descriptor.component_id == CellularTapestryAnimation.COMPONENT_ID:
            return CellularTapestryAnimation(controller, parameters)
        if descriptor.component_id == FlowFieldSilkAnimation.COMPONENT_ID:
            return FlowFieldSilkAnimation(controller, parameters)
        if descriptor.component_id == FrostworkAnimation.COMPONENT_ID:
            return FrostworkAnimation(controller, parameters)
        if descriptor.component_id == LivingStainedGlassAnimation.COMPONENT_ID:
            return LivingStainedGlassAnimation(controller, parameters)
        if descriptor.component_id == QuasicrystalBloomAnimation.COMPONENT_ID:
            return QuasicrystalBloomAnimation(controller, parameters)
        if descriptor.component_id == LivingEcosystemAnimation.COMPONENT_ID:
            return LivingEcosystemAnimation(controller, parameters)
        if descriptor.component_id == PhysarumNetworkAnimation.COMPONENT_ID:
            return PhysarumNetworkAnimation(controller, parameters)
        if descriptor.component_id == ReactionDiffusionGardenAnimation.COMPONENT_ID:
            return ReactionDiffusionGardenAnimation(controller, parameters)
        if descriptor.component_id == WindInTheReedsAnimation.COMPONENT_ID:
            return WindInTheReedsAnimation(controller, parameters)
        raise ValueError(f"Composer preview cannot render Animation {descriptor.component_id!r}")

    def _widget_factory(self, descriptor: ComponentDescriptor, controller: Any, parameters: Mapping[str, Any]) -> Any:
        if descriptor.component_id == ClockOverlayAnimation.COMPONENT_ID:
            clock = ClockOverlayAnimation(controller, parameters)
            # The component deliberately owns wall-clock cadence.  The source is
            # injected at this preview boundary so deterministic requests neither
            # read the host clock nor alter the shared clock implementation.
            clock._clock_now = lambda: self._wall_time  # type: ignore[method-assign]
            return clock
        if descriptor.component_id == EmojiArrangerAnimation.COMPONENT_ID:
            return EmojiArrangerAnimation(controller, parameters)
        raise ValueError(f"Composer preview cannot render Widget {descriptor.component_id!r}")

    def _plant_inputs(self, _plants: Mapping[str, Any], descriptor: ComponentDescriptor) -> Mapping[str, Any]:
        geometry = self._installation_geometry()
        total = float(self.controller.total_leds)
        inputs: dict[str, Any] = {
            "foliage_density": geometry.foliage_count / total,
            "globe_proximity": geometry.globe_count / total,
            "occlusion": (geometry.foliage_count + geometry.globe_count) / total,
        }
        if descriptor.accepts_installation_geometry_contact:
            view = self._installation_profile_view
            if isinstance(view, InstallationProfileRuntimeView):
                self._geometry_contact = view.geometry_contact
                self._geometry_contact_source = id(geometry)
            elif self._geometry_contact_source != id(geometry):
                self._geometry_contact = InstallationGeometryContact.from_geometry(
                    geometry,
                    identity=("composer-final-preview", id(geometry), 33, 138),
                )
                self._geometry_contact_source = id(geometry)
            inputs[INSTALLATION_GEOMETRY_CONTACT_INPUT] = self._geometry_contact
        return inputs

    def _widget_safe_geometry(self, _plants: Mapping[str, Any], _strips: int, _leds: int) -> np.ndarray:
        """Bind Widget placement to the calibrated installation clearance map."""

        return self._installation_geometry().safe_flat

    def _plant_optics(self, pixels: np.ndarray, plants: Mapping[str, Any]) -> np.ndarray:
        """Apply calibrated foliage/globe presentation once after composition."""

        geometry = self._installation_geometry()
        output = pixels.copy()
        effects = plants["effects"]
        strengths = effects["strengths"]
        # The calibrated leaves and globes are visible installation optics even
        # when an effect is disabled; effect choices adjust that final treatment.
        foliage_factor = .70 - .25 * float(strengths.get("shadow", 0.0))
        globe_factor = .58 - .18 * float(strengths.get("shadow", 0.0))
        # Boolean/fancy indexing returns a copy, so ``out=output[mask]`` would
        # attenuate only that temporary selection. Write the computed pixels
        # back explicitly so Shadow reaches the installed-final frame.
        output[geometry.foliage_flat] = output[geometry.foliage_flat] * foliage_factor
        output[geometry.globes_flat] = output[geometry.globes_flat] * globe_factor
        illuminate = float(strengths.get("illuminate", 0.0))
        if illuminate:
            edge = geometry.obstacle_edge.ravel()
            lifted = output[edge].astype(np.float32) + 96.0 * illuminate
            np.clip(lifted, 0.0, 255.0, out=lifted)
            output[edge] = lifted
        if float(strengths.get("hue_shift", 0.0)):
            amount = float(strengths["hue_shift"])
            foliage = output[geometry.foliage_flat].copy()
            output[geometry.foliage_flat, 0] = np.rint(foliage[:, 0] * (1.0 - amount) + foliage[:, 2] * amount)
            output[geometry.foliage_flat, 2] = np.rint(foliage[:, 2] * (1.0 - amount) + foliage[:, 1] * amount)
        return output


class ComposerFinalPreview(InstalledFinalSceneRuntime):
    """Inert Composer adapter over the shared installed-final host runtime."""

    def render(self, canonical: CanonicalScene, elapsed: float, wall_time: datetime) -> RuntimeFrame:
        return super().render(ScenePresentationContext(canonical, elapsed, wall_time))


__all__ = [
    "ComposerFinalPreview", "InstalledFinalSceneRuntime", "NATIVE_AURORA_BUNDLE_DIGEST",
    "NATIVE_AURORA_COMPONENT_ID", "NATIVE_AURORA_HOST_ARTIFACT_DIGEST",
    "NATIVE_AURORA_PAYLOAD_DIGEST", "current_component_catalog",
    "current_component_descriptors", "native_aurora_descriptor",
]
