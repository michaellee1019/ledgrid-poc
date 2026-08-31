"""Closed identities for the current-only, live-first Scene v2 contract."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from animation.component_parameters import validate_component_parameters
from animation.core.component_catalog import (
    AlphaBehavior,
    ComponentCatalog,
    ComponentProvider,
    ComponentRole,
)
from animation.core.plant_awareness import PlantModifierState


SCENE_V2_SCHEMA = "ledgrid.scene.v2"
SCENE_V2_REVISION = 2
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]*)*$")
_LEGACY_KEYS = frozenset({
    "plugin_id", "preset", "vibe", "custom", "overlays",
    "master_brightness", "presentation_luminance", "output_power",
    "output_enabled", "power", "calibration", "calibration_geometry",
    "plant_geometry", "slot_id", "stale_policy", "opacity",
})


class SceneContractError(ValueError):
    """A Composer scene is not valid current-only Scene v2 data."""


@dataclass(frozen=True)
class SceneIdentity:
    revision: int
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {"revision": self.revision, "digest": self.digest}


@dataclass(frozen=True)
class CanonicalScene:
    scene: dict[str, Any]
    canonical_bytes: bytes
    identity: SceneIdentity


def canonical_json_bytes(value: Any) -> bytes:
    """Encode finite JSON deterministically, rejecting implicit coercion."""

    _validate_json(value)
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, OverflowError) as exc:
        raise SceneContractError("value is not canonical JSON") from exc


def normalize_composer_scene(request: Mapping[str, Any], catalog: ComponentCatalog) -> CanonicalScene:
    """Validate and canonicalize one complete v2 scene without mutating input."""

    if not isinstance(request, Mapping) or set(request) != {"origin", "scene"}:
        raise SceneContractError("Composer request must contain only origin and scene")
    if request.get("origin") != "composer":
        raise SceneContractError("only Composer may submit a Scene v2 request")
    if not isinstance(request["scene"], Mapping):
        raise SceneContractError("Composer scene must be an object")
    if not isinstance(catalog, ComponentCatalog):
        raise SceneContractError("Composer scene requires a component catalog")
    try:
        canonical_scene = _normalize_scene(request["scene"], catalog)
        bytes_value = canonical_json_bytes(canonical_scene)
    except (TypeError, ValueError) as exc:
        raise SceneContractError(str(exc)) from exc
    return CanonicalScene(
        scene=json.loads(bytes_value.decode("ascii")),
        canonical_bytes=bytes_value,
        identity=SceneIdentity(SCENE_V2_REVISION, hashlib.sha256(bytes_value).hexdigest()),
    )


def _normalize_scene(scene: Mapping[str, Any], catalog: ComponentCatalog) -> dict[str, Any]:
    _reject_legacy_aliases(scene)
    required = {"schema", "background", "animation", "widgets", "plants", "look"}
    if set(scene) != required:
        raise SceneContractError("Scene v2 must contain exactly background, animation, widgets, plants, and look")
    if scene.get("schema") != SCENE_V2_SCHEMA:
        raise SceneContractError("scene schema must be ledgrid.scene.v2")
    background = _normalize_component(
        scene["background"], catalog, ComponentRole.BACKGROUND, "background"
    )
    animation = _normalize_component(
        scene["animation"], catalog, ComponentRole.ANIMATION, "animation"
    )
    widgets = _normalize_widgets(scene["widgets"], catalog)
    return {
        "schema": SCENE_V2_SCHEMA,
        "background": background,
        "animation": animation,
        "widgets": widgets,
        "plants": _normalize_plants(scene["plants"]),
        "look": _normalize_look(scene["look"]),
    }


def _normalize_component(value: Any, catalog: ComponentCatalog, expected_role: ComponentRole, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SceneContractError(f"{name} must be an object")
    required = {"component_id", "version", "provider", "role", "parameters"}
    allowed = required | {"bundle_digest"}
    if set(value) - allowed or not required <= set(value):
        raise SceneContractError(f"{name} component fields are malformed")
    if value.get("role") != expected_role.value:
        raise SceneContractError(f"{name} role must be {expected_role.value}")
    if not isinstance(value["component_id"], str) or not value["component_id"]:
        raise SceneContractError(f"{name} component_id must be a non-empty string")
    if type(value["version"]) is not int:
        raise SceneContractError(f"{name} version must be an integer")
    try:
        descriptor = catalog.require(
            provider=value["provider"], component_id=value["component_id"], version=value["version"]
        )
        descriptor.validate_scene_v2()
    except (TypeError, ValueError) as exc:
        raise SceneContractError(f"{name} is not a qualified Scene v2 component") from exc
    if descriptor.role is not expected_role or descriptor.provider.value != value["provider"]:
        raise SceneContractError(f"{name} is not a qualified Scene v2 component")
    if expected_role is ComponentRole.BACKGROUND:
        if descriptor.provider is not ComponentProvider.RECEIVER_NATIVE or descriptor.alpha_behavior is not AlphaBehavior.NONE:
            raise SceneContractError("Background must be receiver_native with no alpha plane")
    elif expected_role is ComponentRole.ANIMATION:
        if descriptor.provider is not ComponentProvider.PYTHON or descriptor.alpha_behavior not in {
            AlphaBehavior.PREMULTIPLIED_RGBA, AlphaBehavior.OPAQUE,
        }:
            raise SceneContractError("Animation must be Python premultiplied_rgba or opaque")
    elif descriptor.provider is not ComponentProvider.PYTHON or descriptor.alpha_behavior is not AlphaBehavior.PREMULTIPLIED_RGBA:
        raise SceneContractError("Widget must be Python premultiplied_rgba")
    defaults = dict(descriptor.defaults)
    catalog_digest = defaults.pop("bundle_digest", None)
    if descriptor.provider is ComponentProvider.RECEIVER_NATIVE:
        supplied = value.get("bundle_digest")
        if not isinstance(catalog_digest, str) or _DIGEST.fullmatch(catalog_digest) is None:
            raise SceneContractError(f"{name} catalog bundle identity is missing")
        if not isinstance(supplied, str) or supplied != catalog_digest:
            raise SceneContractError(f"{name} bundle_digest does not match the catalog")
    elif "bundle_digest" in value:
        raise SceneContractError(f"{name} Python components must not declare bundle_digest")
    authored = validate_component_parameters(value["parameters"], intensity_parameter=descriptor.intensity_parameter)
    parameters = validate_component_parameters(
        {**defaults, **authored}, intensity_parameter=descriptor.intensity_parameter
    )
    result = {
        "component_id": descriptor.component_id,
        "version": descriptor.version,
        "provider": descriptor.provider.value,
        "role": descriptor.role.value,
        "parameters": parameters,
    }
    if descriptor.provider is ComponentProvider.RECEIVER_NATIVE:
        result["bundle_digest"] = catalog_digest
    return result


def _normalize_widgets(value: Any, catalog: ComponentCatalog) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise SceneContractError("widgets must be an ordered list")
    widget_ids: set[str] = set()
    widgets: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {"id", "component", "visible", "placement"}:
            raise SceneContractError(f"widgets[{index}] is malformed")
        widget_id = item["id"]
        if not isinstance(widget_id, str) or _STABLE_ID.fullmatch(widget_id) is None or widget_id in widget_ids:
            raise SceneContractError("widget IDs must be unique stable identifiers")
        if not isinstance(item["visible"], bool):
            raise SceneContractError("widget visibility must be boolean")
        widget_ids.add(widget_id)
        widgets.append({
            "id": widget_id,
            "component": _normalize_component(item["component"], catalog, ComponentRole.WIDGET, f"widgets[{index}]"),
            "visible": item["visible"],
            "placement": _normalize_placement(item["placement"]),
        })
    return widgets


def _normalize_placement(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not isinstance(value.get("mode"), str):
        raise SceneContractError("widget placement is malformed")
    if value["mode"] == "auto" and set(value) == {"mode"}:
        return {"mode": "auto"}
    if value["mode"] == "manual" and set(value) == {"mode", "strip_translation", "led_translation"}:
        result = {"mode": "manual"}
        for key in ("strip_translation", "led_translation"):
            number = value[key]
            if isinstance(number, bool) or not isinstance(number, int) or not -(2 ** 31) <= number < 2 ** 31:
                raise SceneContractError(f"widget placement {key} must be a signed 32-bit integer")
            result[key] = number
        return result
    raise SceneContractError("widget placement must be auto or manual")


def _normalize_plants(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"effects"} or not isinstance(value["effects"], Mapping):
        raise SceneContractError("plants must contain only effect intent")
    effects = value["effects"]
    if set(effects) - {"version", "active", "strengths"}:
        raise SceneContractError("plants effects may contain only version, active, and strengths")
    try:
        return {"effects": PlantModifierState.from_payload(effects).to_dict()}
    except ValueError as exc:
        raise SceneContractError(f"plants effects are invalid: {exc}") from exc


def _normalize_look(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"palette_id", "pace", "presentation_brightness"}:
        raise SceneContractError("look must contain only palette_id, pace, and presentation_brightness")
    if not isinstance(value["palette_id"], str) or not value["palette_id"]:
        raise SceneContractError("look palette_id must be a non-empty string")
    return {
        "palette_id": value["palette_id"],
        "pace": _factor(value["pace"], "look pace"),
        "presentation_brightness": _factor(value["presentation_brightness"], "look presentation_brightness"),
    }


def _factor(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 2.0:
        raise SceneContractError(f"{name} must be a finite number from 0 to 2")
    return float(value)


def _reject_legacy_aliases(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _LEGACY_KEYS:
                raise SceneContractError(f"legacy or forbidden Scene field {key!r} is rejected")
            _reject_legacy_aliases(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_legacy_aliases(item)


def normalize_scene_identity(value: Mapping[str, Any]) -> SceneIdentity:
    if not isinstance(value, Mapping) or set(value) != {"revision", "digest"}:
        raise SceneContractError("scene basis must contain exactly revision and digest")
    if type(value.get("revision")) is not int or value["revision"] != SCENE_V2_REVISION:
        raise SceneContractError("scene basis revision is not current Scene v2")
    if not isinstance(value.get("digest"), str) or _DIGEST.fullmatch(value["digest"]) is None:
        raise SceneContractError("scene basis digest must be a lowercase SHA-256 digest")
    return SceneIdentity(revision=value["revision"], digest=value["digest"])


def build_scene_activation_command(canonical: CanonicalScene) -> dict[str, Any]:
    return {
        "action": "activate_scene",
        "basis": canonical.identity.to_dict(),
        "scene": json.loads(canonical.canonical_bytes.decode("ascii")),
    }


class LocalSceneAdapter:
    """Topology-free v2 control double with atomic observation updates."""

    def __init__(self, catalog: ComponentCatalog) -> None:
        if not isinstance(catalog, ComponentCatalog):
            raise TypeError("LocalSceneAdapter requires a ComponentCatalog")
        self._catalog = catalog
        self._observed: SceneIdentity | None = None
        self._safe_idle: SceneIdentity | None = None

    def validate_control(self, command: Mapping[str, Any]) -> tuple[SceneIdentity, bytes]:
        if not isinstance(command, Mapping) or set(command) != {"action", "basis", "scene"} or command.get("action") != "activate_scene":
            raise SceneContractError("activation control command is malformed")
        identity = normalize_scene_identity(command["basis"])
        scene = command["scene"]
        if not isinstance(scene, Mapping):
            raise SceneContractError("activation control scene is malformed")
        # The control payload is untrusted even when it self-digests. Re-run
        # the complete catalog-bound normalization before observation changes.
        canonical = normalize_composer_scene({"origin": "composer", "scene": scene}, self._catalog)
        if canonical.identity != identity:
            raise SceneContractError("activation control scene does not match its basis")
        return identity, canonical.canonical_bytes

    def accept_control(self, command: Mapping[str, Any]) -> SceneIdentity:
        identity, _ = self.validate_control(command)
        self._observed = identity
        self._safe_idle = None
        return identity

    def accept_stop(self, basis: Mapping[str, Any]) -> SceneIdentity:
        identity = normalize_scene_identity(basis)
        if self._observed != identity:
            raise SceneContractError("stop basis does not match the observed scene")
        self._safe_idle = identity
        return identity

    def observed_identity(self) -> SceneIdentity | None:
        return self._observed

    def safe_idle_identity(self) -> SceneIdentity | None:
        return self._safe_idle


def _validate_json(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise SceneContractError("canonical JSON numbers must be finite")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SceneContractError("canonical JSON object keys must be strings")
            _validate_json(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json(item)
        return
    raise SceneContractError(f"canonical JSON does not support {type(value).__name__}")
