"""Disk-backed, component-local preset choices for the Scene v2 Composer."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from animation.core.component_catalog import ComponentCatalog
from animation.core.plant_awareness import PlantModifierState


_MEMBERSHIP_PATH = Path(__file__).with_name("composer_preset_membership.v1.json")
_PRESET_SCHEMA_VERSION = 2
_LEGACY_GLOBAL_FIELDS = frozenset({
    "background", "brightness", "calibration", "geometry", "look", "output",
    "pace", "palette", "plant_aware", "plant_modifiers", "plants", "scene", "widgets",
})
_INSTALLATION_EFFECTS_FIELD = "installation_effects"
_FIREWORKS_PRESENTATION_FIELD = "presentation"
_LAVA_INSTALLATION_EFFECTS = frozenset((
    "refract", "bumper", "emitter", "habitat", "portal",
))
_FIREWORKS_PALETTES = frozenset(("neutral", "mist", "ember", "spectrum", "rainbow", "xmas", "fall", "easter", "july4"))
_LOOK_KEYS = frozenset({"palette_id", "pace", "presentation_brightness"})


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


def _finite_range(value: Any, minimum: float, maximum: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def _fireworks_presentation(
    component_id: str, raw: Mapping[str, Any], preset_id: str,
) -> dict[str, Any] | None:
    """Validate optional Look and background gain owned by one Fireworks card."""
    payload = raw.get(_FIREWORKS_PRESENTATION_FIELD)
    if payload is None:
        return None
    if component_id != "fireworks" or not isinstance(payload, Mapping):
        raise ValueError(f"Preset {preset_id} has invalid presentation")
    if not payload or set(payload) - {"look", "background_gain"}:
        raise ValueError(f"Preset {preset_id} has invalid presentation")
    presentation: dict[str, Any] = {}
    if "look" in payload:
        look = payload["look"]
        if not isinstance(look, Mapping) or set(look) != _LOOK_KEYS:
            raise ValueError(f"Preset {preset_id} has invalid presentation")
        palette_id = look["palette_id"]
        if not isinstance(palette_id, str) or palette_id not in _FIREWORKS_PALETTES:
            raise ValueError(f"Preset {preset_id} has invalid presentation")
        if not _finite_range(look["pace"], 0.0, 2.0) or not _finite_range(look["presentation_brightness"], 0.0, 2.0):
            raise ValueError(f"Preset {preset_id} has invalid presentation")
        presentation["look"] = {
            "palette_id": palette_id,
            "pace": float(look["pace"]),
            "presentation_brightness": float(look["presentation_brightness"]),
        }
    if "background_gain" in payload:
        if not _finite_range(payload["background_gain"], 0.0, 1.0):
            raise ValueError(f"Preset {preset_id} has invalid presentation")
        presentation["background_gain"] = float(payload["background_gain"])
    return presentation


class ComponentPresetCatalog:
    """Read a small authored preset family without giving it scene authority."""

    def __init__(self, root: Path, catalog: ComponentCatalog) -> None:
        self.root = Path(root)
        self._components = {descriptor.component_id: descriptor for descriptor in catalog.descriptors}
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
        descriptor = self._components.get(component_id)
        membership = self._membership.get(component_id)
        if descriptor is None or membership is None or preset_id not in membership["preset_ids"]:
            raise ValueError("Unknown authored preset")
        path = self.root / "animation" / "plugins" / descriptor.package_id / "presets" / f"{preset_id}.json"
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
            parameters = descriptor.normalize_parameters(raw["params"], authored_preset=True)
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
        presentation = _fireworks_presentation(component_id, raw, preset_id)
        if presentation is not None:
            choice["presentation"] = presentation
        return choice

    def choices(self, component_id: str) -> list[dict[str, Any]]:
        self.provider(component_id)
        return [self._choice(component_id, preset_id)
                for preset_id in self._membership.get(component_id, {}).get("preset_ids", ())]

    def operator_choices(self, component_id: str, presets_dir: Path) -> list[dict[str, Any]]:
        """Return authored choices plus retained, provider-qualified personal presets."""
        if component_id not in self._components:
            return []
        authored = {choice["preset_id"]: choice for choice in self.choices(component_id)}
        personal_dir = presets_dir / self.provider(component_id) / component_id
        for path in sorted(personal_dir.glob("*.json")):
            if path.stem == "before-deploy":
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if (not isinstance(record, dict) or record.get("preset_id") != path.stem
                        or record.get("animation") != component_id or record.get("provider") != "python"
                        or not isinstance(record.get("name"), str) or not isinstance(record.get("params"), dict)):
                    continue
                params = record["params"]
                normalized = self.normalize_parameters(component_id, params)
                if set(params) - set(normalized):
                    continue
            except (OSError, ValueError, TypeError):
                continue
            authored[path.stem] = {"preset_id": path.stem, "name": record["name"],
                                   "parameters": normalized}
        return list(authored.values())

    def choice(self, component_id: str, preset_id: str) -> dict[str, Any]:
        """Read one checked authored preset by its component-local identity."""
        return self._choice(component_id, preset_id)

    def contains(self, component_id: str, preset_id: str) -> bool:
        """Check membership without touching the authored preset file."""
        membership = self._membership.get(component_id)
        return bool(
            membership is not None
            and component_id in self._components
            and preset_id in membership["preset_ids"]
        )

    def provider(self, component_id: str) -> str:
        """Return the reviewed provider for a component-owned preset family."""
        descriptor = self._components.get(component_id)
        if descriptor is None:
            raise ValueError("Unknown Composer component")
        return descriptor.provider.value

    def normalize_parameters(
        self, component_id: str, parameters: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Validate one current component-local parameter payload."""
        descriptor = self._components.get(component_id)
        if descriptor is None:
            raise ValueError("Unknown Composer component")
        try:
            return descriptor.normalize_parameters(parameters)
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
        presentation = match.get("presentation")
        if presentation is not None:
            if "look" in presentation:
                look = result.get("look")
                if not isinstance(look, Mapping):
                    raise ValueError("Fireworks presentation presets require Scene v2 look")
                result["look"] = {**dict(look), **presentation["look"]}
            if "background_gain" in presentation:
                background = result.get("background")
                parameters = background.get("parameters") if isinstance(background, Mapping) else None
                if not isinstance(background, Mapping) or not isinstance(parameters, Mapping):
                    raise ValueError("Fireworks presentation presets require Scene v2 background")
                updated_background = dict(background)
                updated_parameters = dict(parameters)
                updated_parameters["gain"] = presentation["background_gain"]
                updated_background["parameters"] = updated_parameters
                result["background"] = updated_background
        return result
