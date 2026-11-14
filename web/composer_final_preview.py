"""Inert, continuously-cadenced final preview for Composer Scene v2.

The runtime supplies calibrated geometry and final plant optics.  CanonicalSceneRuntime continues to own ordering, alpha
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
from animation.core.manager import PreviewLEDController
from animation.core.installation_profile_runtime import InstallationProfileRuntimeView
from animation.core.plant_awareness import (
    INSTALLATION_GEOMETRY_CONTACT_INPUT,
    InstallationGeometryContact,
    PlantMaskCache,
)
from animation.core.scene_runtime import (
    CanonicalSceneRuntime, CanonicalSceneRuntimeError, RuntimeFrame, ScenePresentationContext,
)
from animation.core.composer_catalog import current_component_catalog
from ipc.scene_contract import CanonicalScene, is_scene_pace_edit


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


def installed_plant_geometry(project_root: Path):
    """Read the same installed clearance used by Scene Widget placement."""
    return PlantMaskCache(_PlantGeometryOwner(33, 138, project_root)).get()


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
    ) -> None:
        self.controller = controller or PreviewLEDController(strips=33, leds_per_strip=138)
        if (getattr(self.controller, "strip_count", None), getattr(self.controller, "leds_per_strip", None)) != (33, 138):
            raise ValueError("installed Scene v2 presentation requires a 33x138 controller")
        self._geometry = PlantMaskCache(_PlantGeometryOwner(33, 138, project_root))
        self._installation_profile_view: InstallationProfileRuntimeView | None = None
        self._geometry_contact: InstallationGeometryContact | None = None
        self._geometry_contact_source: int | None = None
        self._runtime = CanonicalSceneRuntime(
            self.controller,
            catalog,
            plant_input_resolver=self._plant_inputs,
            plant_optics=self._plant_optics,
            widget_safe_geometry=self._widget_safe_geometry,
        )
        self._active_digest: str | None = None
        self._lock = RLock()

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


    def update_canonical_scene(self, canonical: CanonicalScene, elapsed: float) -> None:
        """Switch to a validated scene, retaining the runtime clock and unchanged slots."""
        with self._lock:
            self._runtime.tune(canonical, elapsed)
            self._active_digest = canonical.identity.digest

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

    def dispatch_animation_interaction(
        self, canonical: CanonicalScene, kind: str, x: float, y: float,
        strength: float = 1.0,
    ) -> bool:
        """Apply a gesture to this exact published final-preview basis only."""

        with self._lock:
            component = canonical.scene["animation"]
            descriptor = self._runtime.catalog.require(
                provider=component["provider"], component_id=component["component_id"], version=component["version"],
            )
            action = descriptor.input_capability.actions.get(kind)
            if action is None or not action.position:
                raise CanonicalSceneRuntimeError(f"interaction {kind!r} is not supported")
            if canonical.identity.digest != self._active_digest:
                self._runtime.activate(canonical)
                self._active_digest = canonical.identity.digest
            return self._runtime.dispatch_animation_interaction(kind, x, y, strength)

    def game_player_state(self) -> str | None:
        with self._lock:
            return self._runtime.game_player_state()

    def dispatch_game_input(self, action: str) -> bool:
        with self._lock:
            return self._runtime.dispatch_game_input(action)

    def input_capability(self):
        with self._lock:
            return self._runtime.input_capability()

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

    def render(
        self, canonical: CanonicalScene, elapsed: float, wall_time: datetime, *, continuous: bool = False,
    ) -> RuntimeFrame:
        with self._lock:
            previous = self._runtime.active_scene
            if continuous and is_scene_pace_edit(previous.scene if previous else None, canonical.scene):
                self.update_canonical_scene(canonical, elapsed)
            return super().render(ScenePresentationContext(canonical, elapsed, wall_time))


__all__ = [
    "ComposerFinalPreview", "InstalledFinalSceneRuntime", "NATIVE_AURORA_BUNDLE_DIGEST",
    "NATIVE_AURORA_COMPONENT_ID", "NATIVE_AURORA_HOST_ARTIFACT_DIGEST",
    "NATIVE_AURORA_PAYLOAD_DIGEST", "current_component_catalog",
]
