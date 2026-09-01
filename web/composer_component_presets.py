"""Disk-backed, component-local preset choices for the Scene v2 Composer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping


_MEMBERSHIP_PATH = Path(__file__).with_name("composer_preset_membership.v1.json")
_PRESET_SCHEMA_VERSION = 2
_LEGACY_GLOBAL_FIELDS = frozenset({
    "background", "brightness", "calibration", "geometry", "look", "output",
    "pace", "palette", "plant_aware", "plant_modifiers", "plants", "scene", "widgets",
})


def _exact_int(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


class ComponentPresetCatalog:
    """Read a small authored preset family without giving it scene authority."""

    def __init__(self, root: Path, normalizers: Mapping[str, Callable[[Mapping[str, Any]], dict[str, Any]]]) -> None:
        self.root = Path(root)
        self._normalizers = dict(normalizers)
        self._membership = self._read_membership()

    @staticmethod
    def _read_membership() -> dict[str, dict[str, Any]]:
        """Load the finite, reviewed catalog instead of discovering disk files."""
        try:
            payload = json.loads(_MEMBERSHIP_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Composer preset membership is unreadable") from exc
        components = payload.get("components") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or not _exact_int(payload.get("version"), 1)
            or not isinstance(components, dict)
        ):
            raise RuntimeError("Composer preset membership is malformed")
        result: dict[str, dict[str, Any]] = {}
        for component_id, entry in components.items():
            if (
                not isinstance(component_id, str)
                or not isinstance(entry, dict)
                or entry.get("provider") != "python"
                or not _exact_int(entry.get("component_version"), 1)
                or not isinstance(entry.get("preset_ids"), list)
                or not entry["preset_ids"]
                or any(not isinstance(item, str) or not item for item in entry["preset_ids"])
                or len(set(entry["preset_ids"])) != len(entry["preset_ids"])
            ):
                raise RuntimeError("Composer preset membership is malformed")
            result[component_id] = entry
        return result

    def _choice(self, component_id: str, preset_id: str) -> dict[str, Any]:
        normalizer = self._normalizers.get(component_id)
        membership = self._membership.get(component_id)
        if normalizer is None or membership is None or preset_id not in membership["preset_ids"]:
            raise ValueError("Unknown authored preset")
        path = self.root / "animation" / "plugins" / component_id / "presets" / f"{preset_id}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Preset {preset_id} is unreadable") from exc
        if (
            not isinstance(raw, dict)
            or not _exact_int(raw.get("version"), _PRESET_SCHEMA_VERSION)
            or raw.get("preset_id") != preset_id
            or raw.get("animation") != component_id
            or not isinstance(raw.get("name"), str)
            or not raw["name"].strip()
            or not isinstance(raw.get("description"), str)
            or not isinstance(raw.get("params"), dict)
        ):
            raise ValueError(f"Preset {preset_id} is malformed")
        # A component can legitimately use names such as ``palette`` or
        # ``pace`` for a component-local control.  Its normalizer remains the
        # authority for those parameters; only top-level scene-era metadata is
        # categorically forbidden here.
        if _LEGACY_GLOBAL_FIELDS & set(raw):
            raise ValueError(f"Preset {preset_id} contains legacy global fields")
        try:
            parameters = normalizer(raw["params"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Preset {preset_id} has non-local parameters") from exc
        return {
            "preset_id": preset_id,
            "name": raw["name"],
            "description": raw["description"],
            "parameters": parameters,
        }

    def choices(self, component_id: str) -> list[dict[str, Any]]:
        if component_id not in self._normalizers or component_id not in self._membership:
            raise ValueError("Component has no authored preset catalog")
        return [self._choice(component_id, preset_id) for preset_id in self._membership[component_id]["preset_ids"]]

    def apply(self, scene: Mapping[str, Any], preset_id: str) -> dict[str, Any]:
        """Return a complete candidate with only its selected component changed."""
        if not isinstance(scene, Mapping) or not isinstance(scene.get("animation"), Mapping):
            raise ValueError("A complete Scene v2 animation is required")
        component_id = scene["animation"].get("component_id")
        membership = self._membership.get(component_id) if isinstance(component_id, str) else None
        if not isinstance(component_id, str) or membership is None:
            raise ValueError("Scene animation component is missing")
        animation = scene["animation"]
        if (
            animation.get("provider") != membership["provider"]
            or not _exact_int(animation.get("version"), membership["component_version"])
            or animation.get("role") != "animation"
        ):
            raise ValueError("Scene animation identity does not match the authored preset")
        if not isinstance(preset_id, str):
            raise ValueError("Unknown authored preset")
        match = self._choice(component_id, preset_id)
        result = dict(scene)
        animation = dict(animation)
        # Presets replace the component's creative parameters atomically.  They
        # never reach background, widgets, plants, look, output, or calibration.
        animation["parameters"] = dict(match["parameters"])
        result["animation"] = animation
        return result
