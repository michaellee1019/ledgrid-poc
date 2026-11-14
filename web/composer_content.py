"""Composer catalog and authored presets shared with the wall menu."""

from pathlib import Path
from typing import Any

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from animation.core.composer_catalog import current_component_catalog
from web.composer_component_presets import ComponentPresetCatalog


def composer_presets(project_root: Path) -> ComponentPresetCatalog:
    return ComponentPresetCatalog(project_root, current_component_catalog())


def component_entry(descriptor: ComponentDescriptor) -> dict[str, Any]:
    return {
        "key": f"{descriptor.provider.value}:{descriptor.component_id}:v{descriptor.version}",
        "component_id": descriptor.component_id,
        "provider": descriptor.provider.value,
        "version": descriptor.version,
        "role": descriptor.role.value,
        "name": descriptor.name,
        "description": descriptor.description,
        "parameters": descriptor.default_parameters(),
        "parameter_schema": descriptor.controls(),
        "preferred_parameters": list(descriptor.preferred_parameters),
        "input_capability": descriptor.input_capability.public(),
        "default_placement": dict(descriptor.default_placement),
        "available": True,
    }


def gallery_entries(catalog: ComponentCatalog, presets: ComponentPresetCatalog) -> list[dict[str, Any]]:
    """Project the current Scene v2 Animations in Composer display order."""
    entries = []
    for descriptor in catalog.descriptors:
        if descriptor.role.value != "animation":
            continue
        try:
            preset_count = len(presets.choices(descriptor.component_id))
        except ValueError:
            preset_count = 0
        entries.append({**component_entry(descriptor), "preset_count": preset_count})
    entries.sort(key=lambda item: (item["name"].casefold(), item["key"]))
    return entries
