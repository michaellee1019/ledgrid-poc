"""Current local Composer controls for the two supported Scene-v1 overlays.

This intentionally sits above the generic Scene-v1 normalizer.  Scene v1 can
describe broader catalog combinations, while the local Composer editor offers
only the two product slots it can faithfully tune.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from animation.core.component_catalog import ComponentCatalog, ComponentRole


class ComponentEditorError(ValueError):
    """The local Composer editor request is outside its small product surface."""


_CHOICES = (
    {
        "slot_id": "conway_lower",
        "component_id": "conway_life",
        "label": "Conway Life",
        "controls": (
            {"id": "seed", "label": "Seed", "type": "number", "min": 0, "max": 999999, "step": 1},
            {"id": "rule", "label": "Rule", "type": "select", "options": ("B3/S23", "B36/S23")},
            {"id": "initial_density", "label": "Initial density", "type": "number", "min": 0, "max": 0.4, "step": 0.01},
            {"id": "generations_per_second", "label": "Generations / second", "type": "number", "min": 0.5, "max": 20, "step": 0.5},
        ),
    },
    {
        "slot_id": "clock_upper",
        "component_id": "clock_overlay",
        "label": "Clock Overlay",
        "controls": (
            {"id": "show_seconds", "label": "Show seconds", "type": "checkbox"},
            {"id": "color", "label": "Color", "type": "color"},
        ),
    },
)


def editor_catalog(catalog: ComponentCatalog) -> dict[str, list[dict[str, Any]]]:
    """Build the editor's closed catalog from qualified runtime descriptors."""
    choices: list[dict[str, Any]] = []
    for choice in _CHOICES:
        descriptor = catalog.require(
            provider="python", component_id=choice["component_id"], version=1,
        )
        if descriptor.role is not ComponentRole.OVERLAY:
            raise ComponentEditorError("Composer editor component is not an overlay")
        controls = [
            {**control, "options": list(control["options"])}
            if "options" in control else dict(control)
            for control in choice["controls"]
        ]
        parameters = dict(descriptor.defaults)
        control_ids = {control["id"] for control in controls}
        if not control_ids <= set(parameters):
            raise ComponentEditorError("Composer editor controls do not match the component catalog")
        choices.append({
            "slot_id": choice["slot_id"],
            "component_id": descriptor.component_id,
            "version": descriptor.version,
            "provider": descriptor.provider.value,
            "role": descriptor.role.value,
            "label": choice["label"],
            "parameters": deepcopy(parameters),
            "controls": controls,
        })
    return {"choices": choices}


def validate_editor_scene(request: Any, catalog: ComponentCatalog) -> None:
    """Reject duplicates or non-editor overlays before normalizing a draft.

    This is the documented compatibility seam: the generic Scene-v1 normalizer
    remains reusable, while this local product surface keeps stable slots and a
    one-of-each chooser across preview, autosave, look save, and Go Live.
    """
    if not isinstance(request, Mapping) or not isinstance(request.get("scene"), Mapping):
        return
    overlays = request["scene"].get("overlays")
    if overlays is None or not isinstance(overlays, list):
        return
    # Keep the normalizer's established "zero to two" error for an overfull
    # scene; that remains the shared Scene-v1 bound.
    if len(overlays) > 2:
        return
    choices = {choice["component_id"]: choice for choice in editor_catalog(catalog)["choices"]}
    used: set[str] = set()
    for overlay in overlays:
        if not isinstance(overlay, Mapping):
            continue
        component = overlay.get("component")
        if not isinstance(component, Mapping):
            continue
        component_id = component.get("component_id")
        if component_id not in choices:
            raise ComponentEditorError("Composer offers only Conway Life and Clock Overlay")
        if component_id in used:
            raise ComponentEditorError("Composer permits each overlay component only once")
        if overlay.get("slot_id") != choices[component_id]["slot_id"]:
            raise ComponentEditorError("Composer overlay slot does not match its stable component slot")
        used.add(component_id)
