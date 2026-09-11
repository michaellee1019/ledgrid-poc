"""Disk-backed, component-local preset choices for the Scene v2 Composer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

from animation.core.plant_awareness import PlantModifierState


_MEMBERSHIP_PATH = Path(__file__).with_name("composer_preset_membership.v1.json")
_PRESET_SCHEMA_VERSION = 2
_LEGACY_GLOBAL_FIELDS = frozenset({
    "background", "brightness", "calibration", "geometry", "look", "output",
    "pace", "palette", "plant_aware", "plant_modifiers", "plants", "scene", "widgets",
})
_INSTALLATION_EFFECTS_FIELD = "installation_effects"
_LAVA_INSTALLATION_EFFECTS = frozenset((
    "refract", "bumper", "emitter", "habitat", "portal",
))


def _exact_int(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


def _installation_effects(
    component_id: str, raw: Mapping[str, Any], preset_id: str,
) -> dict[str, Any] | None:
    """Validate the narrow Scene-owned intent declaration for Lava cards."""
    payload = raw.get(_INSTALLATION_EFFECTS_FIELD)
    if payload is None:
        return None
    if component_id != "lava_lamp" or not isinstance(payload, Mapping):
        raise ValueError(f"Preset {preset_id} has invalid installation effects")
    try:
        state = PlantModifierState.from_payload(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Preset {preset_id} has invalid installation effects") from exc
    if (
        len(state.active) != 1
        or state.active[0] not in _LAVA_INSTALLATION_EFFECTS
        or state.strength(state.active[0]) <= 0.0
    ):
        raise ValueError(f"Preset {preset_id} has invalid installation effects")
    return state.to_dict()


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
        choice = {
            "preset_id": preset_id,
            "name": raw["name"],
            "description": raw["description"],
            "parameters": parameters,
        }
        effects = _installation_effects(component_id, raw, preset_id)
        if effects is not None:
            choice["installation_effects"] = effects
        return choice

    def choices(self, component_id: str) -> list[dict[str, Any]]:
        if component_id not in self._normalizers or component_id not in self._membership:
            raise ValueError("Component has no authored preset catalog")
        return [self._choice(component_id, preset_id) for preset_id in self._membership[component_id]["preset_ids"]]

    def choice(self, component_id: str, preset_id: str) -> dict[str, Any]:
        """Read one checked authored preset by its component-local identity."""
        return self._choice(component_id, preset_id)

    def contains(self, component_id: str, preset_id: str) -> bool:
        """Check membership without touching the authored preset file."""
        membership = self._membership.get(component_id)
        return bool(
            membership is not None
            and component_id in self._normalizers
            and preset_id in membership["preset_ids"]
        )

    def provider(self, component_id: str) -> str:
        """Return the reviewed provider for a component-owned preset family."""
        membership = self._membership.get(component_id)
        if membership is None or component_id not in self._normalizers:
            raise ValueError("Component has no authored preset catalog")
        return membership["provider"]

    def normalize_parameters(
        self, component_id: str, parameters: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Validate one current component-local parameter payload."""
        normalizer = self._normalizers.get(component_id)
        if normalizer is None or component_id not in self._membership:
            raise ValueError("Component has no authored preset catalog")
        try:
            return normalizer(parameters)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Preset has non-local parameters") from exc

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
        # Presets replace the component's creative parameters atomically.  The
        # five reviewed Lava installation cards are the narrow exception: they
        # select one bounded, Scene-owned effects intent, never geometry.
        animation["parameters"] = dict(match["parameters"])
        result["animation"] = animation
        effects = match.get("installation_effects")
        if effects is not None:
            plants = result.get("plants")
            if not isinstance(plants, Mapping):
                raise ValueError("Lava installation presets require Scene v2 plants")
            updated_plants = dict(plants)
            updated_plants["effects"] = dict(effects)
            result["plants"] = updated_plants
        return result
