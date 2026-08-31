"""Current-only Scene v1 resolution and the bounded presentation pipeline."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

import numpy as np

from animation.component_parameters import LEGACY_PARAMETER_ALIASES, validate_component_parameters
from animation.core.component_catalog import (
    ComponentCatalog, ComponentDescriptor, ComponentProvider, ComponentRole,
    PalettePolicy, TimingPolicy,
)


SCENE_V1_SCHEMA = "ledgrid.scene.v1"
PIPELINE_TRACE = (
    "validate_scene", "resolve_component_parameters", "resolve_vibe_values",
    "select_palette_policy", "compute_component_time", "render_component",
    "compose_layers", "apply_presentation_luminance", "apply_plant_optics",
    "apply_master_brightness",
)
NEUTRAL_PLANT_INPUTS = MappingProxyType({
    "foliage_density": 0.0, "globe_proximity": 0.0, "occlusion": 0.0,
})

_LEGACY_KEYS = LEGACY_PARAMETER_ALIASES | frozenset({
    "animation", "plugin_id", "preset", "look", "palette", "vibe_id",
    "global_settings", "migration", "migration_receipt", "overlays",
})
_VIBES = MappingProxyType({
    "neutral": ("neutral", 1.0, 1.0),
    "quiet": ("mist", 0.70, 0.82),
    "vivid": ("spectrum", 1.25, 1.15),
})


class SceneValidationError(ValueError):
    """A source scene is not valid current-only Scene v1 data."""


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


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Stable JSON bytes used for every Scene-v1 identity calculation."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def resolve_scene(
    scene: Mapping[str, Any], catalog: ComponentCatalog, *, monotonic_elapsed: float
) -> ResolvedScene:
    """Validate one opaque Python background and resolve all runtime context."""
    _reject_legacy_aliases(scene)
    if not isinstance(scene, Mapping):
        raise SceneValidationError("scene must be an object")
    allowed = {"schema", "background", "vibe", "custom", "master_brightness"}
    if set(scene) - allowed:
        raise SceneValidationError(f"unknown Scene v1 fields {sorted(set(scene) - allowed)!r}")
    if scene.get("schema") != SCENE_V1_SCHEMA:
        raise SceneValidationError("scene schema must be ledgrid.scene.v1")
    background = scene.get("background")
    if not isinstance(background, Mapping) or set(background) != {"component_id", "version", "provider", "role", "parameters"}:
        raise SceneValidationError("scene must contain exactly one qualified background")
    if background.get("provider") != ComponentProvider.PYTHON.value:
        raise SceneValidationError("packet A supports only Python backgrounds")
    if background.get("role") != ComponentRole.BACKGROUND.value:
        raise SceneValidationError("scene background role must be background")
    if type(background.get("version")) is not int:
        raise SceneValidationError("scene background version must be an integer")
    descriptor = catalog.require(
        provider=background["provider"], component_id=background["component_id"], version=background["version"]
    )
    if descriptor.provider is not ComponentProvider.PYTHON or descriptor.role is not ComponentRole.BACKGROUND:
        raise SceneValidationError("packet A requires a Python background descriptor")
    if descriptor.timing_policy is not TimingPolicy.SCALED_CONTEXT:
        raise SceneValidationError("wall_clock components are not part of packet A")
    authored_parameters = validate_component_parameters(
        background["parameters"], intensity_parameter=descriptor.intensity_parameter
    )
    parameters = validate_component_parameters(
        {**descriptor.defaults, **authored_parameters},
        intensity_parameter=descriptor.intensity_parameter,
    )
    palette_id, wall_pace, presentation_luminance, vibe_source = _resolve_vibe(scene)
    master_brightness = _factor(scene.get("master_brightness"), "master_brightness")
    phase_time = _finite_nonnegative(monotonic_elapsed, "monotonic_elapsed") * wall_pace
    palette = (
        _freeze_mapping({"palette_id": palette_id})
        if descriptor.palette_policy is PalettePolicy.SEMANTIC else None
    )
    plant_inputs = _freeze_mapping({
        key: NEUTRAL_PLANT_INPUTS.get(key, 0.0) for key in descriptor.optional_simulation_inputs
    })
    canonical = {
        "schema": SCENE_V1_SCHEMA,
        "background": {
            "component_id": descriptor.component_id,
            "version": descriptor.version,
            "provider": descriptor.provider.value,
            "role": descriptor.role.value,
            "parameters": parameters,
        },
        "vibe_source": vibe_source,
        "palette_id": palette_id,
        "wall_pace": wall_pace,
        "presentation_luminance": presentation_luminance,
        "master_brightness": master_brightness,
    }
    bytes_value = canonical_json_bytes(canonical)
    return ResolvedScene(
        canonical_scene=_freeze_mapping(canonical), canonical_bytes=bytes_value,
        digest=hashlib.sha256(bytes_value).hexdigest(), descriptor=descriptor,
        parameters=_freeze_mapping(parameters), palette=palette, phase_time=phase_time,
        plant_inputs=plant_inputs,
    )


def execute_scene(
    scene: Mapping[str, Any], catalog: ComponentCatalog, *, monotonic_elapsed: float,
    renderer: Callable[[ResolvedScene], np.ndarray],
) -> RenderResult:
    """Execute the full ten-stage packet-A path, with neutral plant optics."""
    resolved = resolve_scene(scene, catalog, monotonic_elapsed=monotonic_elapsed)
    rendered = np.asarray(renderer(resolved))
    if rendered.ndim < 1 or rendered.shape[-1] != 3:
        raise ValueError("renderer must return an RGB frame")
    if not np.issubdtype(rendered.dtype, np.number):
        raise ValueError("renderer must return numeric RGB pixels")
    # Packet A has one opaque background and no overlays, so composition is an
    # explicit identity. Plant optics is likewise a deterministic identity in
    # the headless profile; both remain observable trace stages.
    presentation = _scale_frame(rendered, resolved.canonical_scene["presentation_luminance"])
    final = _scale_frame(presentation, resolved.canonical_scene["master_brightness"])
    return RenderResult(frame=final, resolved=resolved)


def _resolve_vibe(scene: Mapping[str, Any]) -> tuple[str, float, float, str]:
    vibe = scene.get("vibe")
    custom = scene.get("custom")
    if (vibe is None) == (custom is None):
        raise SceneValidationError("scene requires exactly one of vibe or custom values")
    if vibe is not None:
        if not isinstance(vibe, str) or vibe not in _VIBES:
            raise SceneValidationError("scene vibe is not a current vibe")
        palette_id, wall_pace, presentation_luminance = _VIBES[vibe]
        return palette_id, wall_pace, presentation_luminance, vibe
    if not isinstance(custom, Mapping) or set(custom) != {"palette_id", "wall_pace", "presentation_luminance"}:
        raise SceneValidationError("custom values must set palette_id, wall_pace, and presentation_luminance")
    palette_id = custom["palette_id"]
    if not isinstance(palette_id, str) or not palette_id:
        raise SceneValidationError("custom palette_id must be a non-empty string")
    return (
        palette_id, _factor(custom["wall_pace"], "wall_pace"),
        _factor(custom["presentation_luminance"], "presentation_luminance"), "custom",
    )


def _reject_legacy_aliases(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _LEGACY_KEYS:
                raise SceneValidationError(f"legacy Scene v1 alias {key!r} is rejected")
            _reject_legacy_aliases(item)
    elif isinstance(value, list):
        for item in value:
            _reject_legacy_aliases(item)


def _factor(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise SceneValidationError(f"{name} must be a finite number")
    if not 0.0 <= float(value) <= 2.0:
        raise SceneValidationError(f"{name} must be from 0 to 2")
    return float(value)


def _finite_nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        raise SceneValidationError(f"{name} must be finite and non-negative")
    return float(value)


def _scale_frame(frame: np.ndarray, factor: float) -> np.ndarray:
    return np.clip(np.rint(np.asarray(frame, dtype=np.float64) * factor), 0, 255).astype(np.uint8)


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Prevent a resolved context from changing after its digest was computed."""
    return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value
