"""Inert, continuously-cadenced final preview for Composer Scene v2.

This is deliberately a presentation seam, not a second scene model.  It owns
only the host-side stand-in for the receiver-native background plus calibrated
final plant optics.  CanonicalSceneRuntime continues to own ordering, alpha
composition, resolved palette/pace, and the single output-brightness boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

import numpy as np

from animation.core.component_catalog import ComponentDescriptor
from animation.core.compositing import BaseFrame
from animation.core.manager import PreviewLEDController
from animation.core.plant_awareness import PlantMaskCache
from animation.core.scene_runtime import CanonicalSceneRuntime, RuntimeFrame
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
from animation.plugins.clock_overlay import ClockOverlayAnimation
from animation.plugins.conway_life import ConwayLifeAnimation
from ipc.scene_contract import CanonicalScene


NATIVE_AURORA_COMPONENT_ID = "native_aurora"
NATIVE_AURORA_BUNDLE_DIGEST = "d0b8c0f9c7d55a8f58b6156e20c59afe6e4c5a7e2821cb6b3a29d9af81c296bf"


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


_PALETTES: Mapping[str, tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]] = {
    "neutral": ((2, 10, 18), (24, 148, 132), (150, 255, 218)),
    "mist": ((3, 9, 20), (40, 102, 142), (170, 228, 245)),
    "spectrum": ((15, 3, 34), (84, 38, 194), (54, 238, 230)),
    "ember": ((18, 3, 2), (156, 42, 14), (255, 202, 92)),
}


class _NativeAuroraPreview:
    """Deterministic preview implementation of the chosen native background.

    It uses the native renderer's declared source cadence rather than browser
    animation-frame cadence.  This keeps preview frames stable between native
    redraw ticks while the other Scene v2 planes continue at their own rates.
    """

    def __init__(self, strips: int, leds: int) -> None:
        self._strips, self._leds = strips, leds
        self._x = np.linspace(0.0, 1.0, strips, dtype=np.float32)[:, None]
        self._y = np.linspace(0.0, 1.0, leds, dtype=np.float32)[None, :]
        self._field = np.empty((strips, leds), dtype=np.float32)
        self._rgb = np.empty((strips, leds, 3), dtype=np.float32)
        self._pixels = np.zeros((strips * leds, 3), dtype=np.uint8)
        self._key: tuple[Any, ...] | None = None

    def render(self, context: Any, _frame_count: int) -> BaseFrame:
        parameters = context.parameters
        source_fps = float(parameters["source_fps"])
        tick = int(math.floor(context.phase_time * source_fps + 1e-9))
        key = (context.palette["palette_id"], tick, float(parameters["gain"]), int(parameters["seed"]))
        if key == self._key:
            return BaseFrame(self._pixels, changed=False, dirty_ranges=())
        low, primary, accent = _PALETTES.get(str(context.palette["palette_id"]), _PALETTES["neutral"])
        phase = tick / source_fps
        seed = int(parameters["seed"])
        np.sin((self._x * (3.7 + (seed % 5) * .11)) + phase * .42 + self._y * 2.1, out=self._field)
        self._field *= .5
        self._field += .5
        self._field *= np.float32(parameters["gain"])
        np.clip(self._field, 0.0, 1.0, out=self._field)
        for channel in range(3):
            self._rgb[:, :, channel] = low[channel] + (primary[channel] - low[channel]) * self._field
            self._rgb[:, :, channel] += (accent[channel] - primary[channel]) * np.square(self._field) * .34
        np.clip(self._rgb, 0.0, 255.0, out=self._rgb)
        np.rint(self._rgb, out=self._rgb)
        self._pixels[:] = self._rgb.reshape((-1, 3))
        self._key = key
        return BaseFrame(self._pixels, changed=True)


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


class ComposerFinalPreview:
    """A preview-only Scene v2 runtime retained across animation frames.

    ``render`` is synchronous and lock-protected because Flask can serve two
    canvas refreshes at once.  It does not interact with Composer publication,
    desired/observed state, controller output, or recovery persistence.
    """

    def __init__(self, catalog: Any, project_root: Path) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)
        self._wall_time = datetime.now().astimezone()
        self._native = _NativeAuroraPreview(33, 138)
        self._geometry = PlantMaskCache(_PlantGeometryOwner(33, 138, project_root))
        self._runtime = CanonicalSceneRuntime(
            self.controller,
            catalog,
            background_renderer=self._native.render,
            animation_factory=self._animation_factory,
            widget_factory=self._widget_factory,
            plant_input_resolver=self._plant_inputs,
            plant_optics=self._plant_optics,
            widget_safe_geometry=self._widget_safe_geometry,
        )
        self._active_digest: str | None = None
        self._lock = RLock()

    def render(self, canonical: CanonicalScene, elapsed: float, wall_time: datetime) -> RuntimeFrame:
        with self._lock:
            self._wall_time = wall_time
            if canonical.identity.digest != self._active_digest:
                self._runtime.activate(canonical)
                self._active_digest = canonical.identity.digest
            return self._runtime.render(elapsed)

    def _animation_factory(self, descriptor: ComponentDescriptor, controller: Any, parameters: Mapping[str, Any]) -> Any:
        if descriptor.component_id == AuroraCurtainsAnimation.COMPONENT_ID:
            return AuroraCurtainsAnimation(controller, parameters)
        if descriptor.component_id == ConwayLifeAnimation.COMPONENT_ID:
            return ConwayLifeAnimation(controller, parameters)
        raise ValueError(f"Composer preview cannot render Animation {descriptor.component_id!r}")

    def _widget_factory(self, descriptor: ComponentDescriptor, controller: Any, parameters: Mapping[str, Any]) -> Any:
        if descriptor.component_id != ClockOverlayAnimation.COMPONENT_ID:
            raise ValueError(f"Composer preview cannot render Widget {descriptor.component_id!r}")
        clock = ClockOverlayAnimation(controller, parameters)
        # The component deliberately owns wall-clock cadence.  The source is
        # injected at this preview boundary so deterministic requests neither
        # read the host clock nor alter the shared clock implementation.
        clock._clock_now = lambda: self._wall_time  # type: ignore[method-assign]
        return clock

    def _plant_inputs(self, _plants: Mapping[str, Any], _descriptor: ComponentDescriptor) -> Mapping[str, float]:
        geometry = self._geometry.get()
        total = float(self.controller.total_leds)
        return {
            "foliage_density": geometry.foliage_count / total,
            "globe_proximity": geometry.globe_count / total,
            "occlusion": (geometry.foliage_count + geometry.globe_count) / total,
        }

    def _widget_safe_geometry(self, _plants: Mapping[str, Any], _strips: int, _leds: int) -> np.ndarray:
        """Bind Widget placement to the calibrated installation clearance map."""

        return self._geometry.get().safe_flat

    def _plant_optics(self, pixels: np.ndarray, plants: Mapping[str, Any]) -> np.ndarray:
        """Apply calibrated foliage/globe presentation once after composition."""

        geometry = self._geometry.get()
        output = pixels.copy()
        effects = plants["effects"]
        strengths = effects["strengths"]
        # The calibrated leaves and globes are visible installation optics even
        # when an effect is disabled; effect choices adjust that final treatment.
        foliage_factor = .70 - .25 * float(strengths.get("shadow", 0.0))
        globe_factor = .58 - .18 * float(strengths.get("shadow", 0.0))
        np.multiply(output[geometry.foliage_flat], foliage_factor, out=output[geometry.foliage_flat], casting="unsafe")
        np.multiply(output[geometry.globes_flat], globe_factor, out=output[geometry.globes_flat], casting="unsafe")
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


__all__ = [
    "ComposerFinalPreview", "NATIVE_AURORA_BUNDLE_DIGEST",
    "NATIVE_AURORA_COMPONENT_ID", "native_aurora_descriptor",
]
