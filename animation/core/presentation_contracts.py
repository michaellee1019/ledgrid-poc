"""Scene v2 presentation context.

This module resolves immutable rendering inputs only. Composition and output
transport deliberately remain outside the schema boundary so brightness and
plant optics can each be applied exactly once by their owning stages.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping

import numpy as np

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor, PalettePolicy
from ipc.scene_contract import SCENE_V2_SCHEMA as _SCENE_V2_SCHEMA, normalize_composer_scene


SCENE_V2_SCHEMA = _SCENE_V2_SCHEMA
PIPELINE_TRACE = (
    "validate_scene", "resolve_component_parameters", "resolve_look", "resolve_plant_effect_intent",
    "select_palette_policy", "compute_component_time",
)
NEUTRAL_PLANT_INPUTS = MappingProxyType({
    "foliage_density": 0.0, "globe_proximity": 0.0, "occlusion": 0.0,
})


class SceneValidationError(ValueError):
    """A source scene is not valid current-only Scene v2 data."""


@dataclass(frozen=True)
class ResolvedScene:
    canonical_scene: Mapping[str, Any]
    canonical_bytes: bytes
    digest: str
    descriptor: ComponentDescriptor
    parameters: Mapping[str, Any]
    palette: Mapping[str, Any] | None
    phase_time: float
    plant_inputs: Mapping[str, float]
    trace: tuple[str, ...] = PIPELINE_TRACE


@dataclass(frozen=True)
class RenderResult:
    frame: np.ndarray
    resolved: ResolvedScene
    trace: tuple[str, ...] = PIPELINE_TRACE


def resolve_scene(scene: Mapping[str, Any], catalog: ComponentCatalog, *, monotonic_elapsed: float) -> ResolvedScene:
    """Resolve the selected Python Animation's v2 presentation context."""

    if (
        isinstance(monotonic_elapsed, bool)
        or not isinstance(monotonic_elapsed, (int, float))
        or not math.isfinite(float(monotonic_elapsed))
        or monotonic_elapsed < 0
    ):
        raise SceneValidationError("monotonic_elapsed must be finite and non-negative")
    try:
        canonical = normalize_composer_scene({"origin": "composer", "scene": scene}, catalog)
        descriptor = catalog.require(
            provider=canonical.scene["animation"]["provider"],
            component_id=canonical.scene["animation"]["component_id"],
            version=canonical.scene["animation"]["version"],
        )
    except ValueError as exc:
        raise SceneValidationError(str(exc)) from exc
    look = canonical.scene["look"]
    palette = (
        _freeze_mapping({"palette_id": look["palette_id"]})
        if descriptor.palette_policy is PalettePolicy.SEMANTIC else None
    )
    plant_inputs = _freeze_mapping({
        key: NEUTRAL_PLANT_INPUTS.get(key, 0.0)
        for key in descriptor.optional_simulation_inputs + descriptor.required_simulation_inputs
    })
    return ResolvedScene(
        canonical_scene=_freeze_mapping(canonical.scene),
        canonical_bytes=canonical.canonical_bytes,
        digest=canonical.identity.digest,
        descriptor=descriptor,
        parameters=_freeze_mapping(canonical.scene["animation"]["parameters"]),
        palette=palette,
        phase_time=float(monotonic_elapsed) * float(look["pace"]),
        plant_inputs=plant_inputs,
    )


def execute_scene(
    scene: Mapping[str, Any], catalog: ComponentCatalog, *, monotonic_elapsed: float,
    renderer: Callable[[ResolvedScene], np.ndarray],
) -> RenderResult:
    """Render a v2 Animation plane; compositor ownership remains external."""

    resolved = resolve_scene(scene, catalog, monotonic_elapsed=monotonic_elapsed)
    frame = np.asarray(renderer(resolved))
    if frame.ndim < 1 or frame.shape[-1] not in {3, 4} or not np.issubdtype(frame.dtype, np.number):
        raise ValueError("renderer must return numeric RGB or RGBA pixels")
    return RenderResult(frame=frame, resolved=resolved)


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value
