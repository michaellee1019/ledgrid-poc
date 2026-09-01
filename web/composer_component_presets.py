"""Disk-backed, component-local preset choices for the Scene v2 Composer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping


class ComponentPresetCatalog:
    """Read a small authored preset family without giving it scene authority."""

    def __init__(self, root: Path, normalizers: Mapping[str, Callable[[Mapping[str, Any]], dict[str, Any]]]) -> None:
        self.root = Path(root)
        self._normalizers = dict(normalizers)

    def choices(self, component_id: str) -> list[dict[str, Any]]:
        normalizer = self._normalizers.get(component_id)
        if normalizer is None:
            raise ValueError("Component has no authored preset catalog")
        directory = self.root / "animation" / "plugins" / component_id / "presets"
        choices: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Preset {path.name} is unreadable") from exc
            if not isinstance(raw, dict) or raw.get("preset_id") != path.stem or raw.get("animation") != component_id:
                raise ValueError(f"Preset {path.name} is not a {component_id} component choice")
            if not isinstance(raw.get("name"), str) or not raw["name"] or not isinstance(raw.get("params"), dict):
                raise ValueError(f"Preset {path.name} is malformed")
            choices.append({"preset_id": path.stem, "name": raw["name"], "description": raw.get("description", ""), "parameters": normalizer(raw["params"])})
        return choices

    def apply(self, scene: Mapping[str, Any], preset_id: str) -> dict[str, Any]:
        """Return a complete candidate with only its selected component changed."""
        if not isinstance(scene, Mapping) or not isinstance(scene.get("animation"), Mapping):
            raise ValueError("A complete Scene v2 animation is required")
        component_id = scene["animation"].get("component_id")
        if not isinstance(component_id, str):
            raise ValueError("Scene animation component is missing")
        match = next((choice for choice in self.choices(component_id) if choice["preset_id"] == preset_id), None)
        if match is None:
            raise ValueError("Unknown authored preset")
        result = dict(scene)
        animation = dict(scene["animation"])
        # Presets replace the component's creative parameters atomically.  They
        # never reach background, widgets, plants, look, output, or calibration.
        animation["parameters"] = dict(match["parameters"])
        result["animation"] = animation
        return result
