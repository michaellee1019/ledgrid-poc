"""Small, current-only parameter rules for Scene v1 components."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping


_PARAMETER_ID = re.compile(r"^[a-z][a-z0-9_]*$")

# These parameters remain owned by the scene/presentation pipeline and must
# never be captured inside a component preset or renderer-local payload.
SCENE_EXTERNAL_COMPONENT_PARAMETERS = frozenset((
    "plant_aware", "plant_modifiers", "vibe", "output",
))

# These names used to have ambiguous, global meaning.  Scene v1 deliberately
# has no compatibility bridge for them, including inside component parameters.
LEGACY_PARAMETER_ALIASES = frozenset({
    "speed", "rate", "brightness", "saturation", "value", "color_value",
    "color_saturation", "plant_aware", "plant_modifiers", "wall_clock",
    "full_scene", "global_palette", "global_vibe", "output",
})


def validate_component_parameters(
    parameters: Mapping[str, Any], *, intensity_parameter: str | None
) -> dict[str, Any]:
    """Return JSON-safe parameters after rejecting aliases before rendering.

    ``intensity_parameter`` is a component-owned artistic control.  It is not
    a global brightness setting and is intentionally passed unchanged to the
    renderer.
    """
    if not isinstance(parameters, Mapping):
        raise ValueError("component parameters must be an object")
    normalized: dict[str, Any] = {}
    for key, value in parameters.items():
        if not isinstance(key, str) or not _PARAMETER_ID.fullmatch(key):
            raise ValueError(f"invalid component parameter name {key!r}")
        if key in LEGACY_PARAMETER_ALIASES:
            raise ValueError(f"legacy parameter alias {key!r} is not supported")
        normalized[key] = _json_value(value)
    if intensity_parameter is not None:
        value = normalized.get(intensity_parameter)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
        ):
            raise ValueError(
                f"component intensity {intensity_parameter!r} must be 0..1"
            )
    return normalized


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("component parameters must contain finite JSON values")
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    raise ValueError("component parameters must contain JSON values")
