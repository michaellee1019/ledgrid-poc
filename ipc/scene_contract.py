"""Closed Scene v1 identities for the Composer activation slice.

This module deliberately knows nothing about receiver names, output lanes, or
deployment topology.  It turns the narrow Composer request into the resolved
Scene-v1 bytes already defined by the presentation contract, and exposes only
that immutable identity to the control plane.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from animation.component_parameters import validate_component_parameters
from animation.core.component_catalog import (
    ComponentCatalog,
    ComponentProvider,
    ComponentRole,
)
from animation.core.presentation_contracts import resolve_scene


SCENE_V1_REVISION = 1
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SLOT_ID = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]*)*$")
_VIBES = {
    "neutral": ("neutral", 1.0, 1.0),
    "quiet": ("mist", 0.70, 0.82),
    "vivid": ("spectrum", 1.25, 1.15),
}


class SceneContractError(ValueError):
    """A Composer scene or its activation identity is not current Scene v1."""


@dataclass(frozen=True)
class SceneIdentity:
    """The complete, topology-neutral identity of one accepted Scene v1."""

    revision: int
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {"revision": self.revision, "digest": self.digest}


@dataclass(frozen=True)
class CanonicalScene:
    """Resolved canonical bytes and their immutable Scene-v1 identity."""

    scene: dict[str, Any]
    canonical_bytes: bytes
    identity: SceneIdentity


def canonical_json_bytes(value: Any) -> bytes:
    """Encode finite JSON deterministically, rejecting implicit coercion."""

    _validate_json(value)
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError) as exc:  # defensive boundary
        raise SceneContractError("value is not canonical JSON") from exc


def normalize_composer_scene(
    request: Mapping[str, Any], catalog: ComponentCatalog
) -> CanonicalScene:
    """Normalize the sole permitted Composer request into closed Scene v1.

    The envelope is intentionally small and closed: one Composer source and
    one source scene.  Legacy one-Python-background requests retain the
    presentation resolver's exact canonical bytes.  Full scenes add up to two
    ordered Python overlays and may use a catalog-qualified receiver-native
    background.
    """

    if not isinstance(request, Mapping):
        raise SceneContractError("Composer request must be an object")
    if set(request) != {"origin", "scene"}:
        raise SceneContractError("Composer request must contain only origin and scene")
    if request.get("origin") != "composer":
        raise SceneContractError("only Composer may submit a Scene v1 request")
    scene = request.get("scene")
    if not isinstance(scene, Mapping):
        raise SceneContractError("Composer scene must be an object")
    if not isinstance(catalog, ComponentCatalog):
        raise SceneContractError("Composer scene requires a component catalog")
    try:
        if _is_legacy_background_scene(scene):
            resolved = resolve_scene(scene, catalog, monotonic_elapsed=0.0)
            bytes_value = bytes(resolved.canonical_bytes)
        else:
            bytes_value = canonical_json_bytes(_normalize_full_scene(scene, catalog))
    except (TypeError, ValueError) as exc:
        raise SceneContractError(str(exc)) from exc
    # Reparse stable bytes so callers receive ordinary, non-mutable JSON data
    # rather than presentation-contract mapping proxies.
    normalized = json.loads(bytes_value.decode("ascii"))
    return CanonicalScene(
        scene=normalized,
        canonical_bytes=bytes_value,
        identity=SceneIdentity(
            revision=SCENE_V1_REVISION,
            digest=hashlib.sha256(bytes_value).hexdigest(),
        ),
    )


def _is_legacy_background_scene(scene: Mapping[str, Any]) -> bool:
    """Keep the first vertical slice's canonical basis strictly unchanged."""

    return "overlays" not in scene and "slot_id" not in scene.get("background", {})


def _normalize_full_scene(scene: Mapping[str, Any], catalog: ComponentCatalog) -> dict[str, Any]:
    """Resolve the bounded hybrid Scene-v1 shape without topology details."""

    allowed = {"schema", "background", "overlays", "vibe", "custom", "master_brightness"}
    if set(scene) - allowed:
        raise SceneContractError("unknown Scene v1 fields")
    if scene.get("schema") != "ledgrid.scene.v1":
        raise SceneContractError("scene schema must be ledgrid.scene.v1")
    raw_overlays = scene.get("overlays")
    if not isinstance(raw_overlays, list) or len(raw_overlays) > 2:
        raise SceneContractError("scene overlays must contain zero to two entries")

    palette_id, wall_pace, presentation_luminance, vibe_source = _resolve_vibe(scene)
    background = _normalize_component(
        scene.get("background"), catalog=catalog, expected_role=ComponentRole.BACKGROUND,
        allowed_providers={ComponentProvider.PYTHON, ComponentProvider.RECEIVER_NATIVE},
        name="scene background", default_slot_id="background",
    )
    overlays = []
    slot_ids = {background["slot_id"]}
    for index, raw_overlay in enumerate(raw_overlays):
        if not isinstance(raw_overlay, Mapping) or set(raw_overlay) != {
            "slot_id", "component", "enabled", "opacity", "placement", "stale_policy",
        }:
            raise SceneContractError(f"scene overlays[{index}] is malformed")
        component = _normalize_component(
            raw_overlay["component"], catalog=catalog, expected_role=ComponentRole.OVERLAY,
            allowed_providers={ComponentProvider.PYTHON}, name=f"scene overlays[{index}] component",
            default_slot_id=None,
        )
        slot_id = _slot_id(raw_overlay.get("slot_id"), f"scene overlays[{index}] slot_id")
        if slot_id in slot_ids:
            raise SceneContractError("scene component slot_ids must be unique")
        slot_ids.add(slot_id)
        enabled = raw_overlay["enabled"]
        if not isinstance(enabled, bool):
            raise SceneContractError("scene overlay enabled must be boolean")
        opacity = raw_overlay["opacity"]
        if isinstance(opacity, bool) or not isinstance(opacity, int) or not 0 <= opacity <= 255:
            raise SceneContractError("scene overlay opacity must be an integer from 0 to 255")
        overlays.append({
            "slot_id": slot_id,
            "component": component,
            "enabled": enabled,
            "opacity": opacity,
            "placement": _placement(raw_overlay["placement"]),
            "stale_policy": _stale_policy(raw_overlay["stale_policy"]),
        })
    return {
        "schema": "ledgrid.scene.v1",
        "background": background,
        "overlays": overlays,
        "vibe_source": vibe_source,
        "palette_id": palette_id,
        "wall_pace": wall_pace,
        "presentation_luminance": presentation_luminance,
        "master_brightness": _factor(scene.get("master_brightness"), "master_brightness"),
    }


def _normalize_component(
    value: Any, *, catalog: ComponentCatalog, expected_role: ComponentRole,
    allowed_providers: set[ComponentProvider], name: str, default_slot_id: str | None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SceneContractError(f"{name} must be an object")
    allowed = {"component_id", "version", "provider", "role", "parameters", "bundle_digest"}
    if default_slot_id is not None:
        allowed.add("slot_id")
    if set(value) - allowed:
        raise SceneContractError(f"{name} has unknown fields")
    required = {"component_id", "version", "provider", "role", "parameters"}
    if not required <= set(value):
        raise SceneContractError(f"{name} is incomplete")
    provider = value["provider"]
    if provider not in {item.value for item in allowed_providers}:
        raise SceneContractError(f"{name} provider is not supported")
    if value["role"] != expected_role.value:
        raise SceneContractError(f"{name} role is not supported")
    if not isinstance(value["component_id"], str) or not value["component_id"]:
        raise SceneContractError(f"{name} component_id must be a non-empty string")
    if type(value["version"]) is not int:
        raise SceneContractError(f"{name} version must be an integer")
    slot_id = (
        _slot_id(value.get("slot_id", default_slot_id), f"{name} slot_id")
        if default_slot_id is not None else None
    )
    descriptor = catalog.require(
        provider=provider, component_id=value["component_id"], version=value["version"]
    )
    if descriptor.provider.value != provider or descriptor.role is not expected_role:
        raise SceneContractError(f"{name} is not a qualified catalog component")
    defaults = dict(descriptor.defaults)
    catalog_bundle = defaults.pop("bundle_digest", None)
    if provider == ComponentProvider.RECEIVER_NATIVE.value:
        supplied_bundle = value.get("bundle_digest")
        if not isinstance(catalog_bundle, str) or _DIGEST.fullmatch(catalog_bundle) is None:
            raise SceneContractError(f"{name} catalog bundle identity is missing")
        if not isinstance(supplied_bundle, str) or _DIGEST.fullmatch(supplied_bundle) is None:
            raise SceneContractError(f"{name} bundle_digest must be a lowercase SHA-256 digest")
        if supplied_bundle != catalog_bundle:
            raise SceneContractError(f"{name} bundle_digest does not match the catalog")
    elif "bundle_digest" in value:
        raise SceneContractError(f"{name} Python components must not declare bundle_digest")
    authored = validate_component_parameters(
        value["parameters"], intensity_parameter=descriptor.intensity_parameter
    )
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
    if slot_id is not None:
        result["slot_id"] = slot_id
    if provider == ComponentProvider.RECEIVER_NATIVE.value:
        result["bundle_digest"] = catalog_bundle
    return result


def _slot_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SLOT_ID.fullmatch(value) is None:
        raise SceneContractError(f"{name} must be a stable slot identifier")
    return value


def _placement(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "strip_translation", "led_translation", "clip_policy",
    }:
        raise SceneContractError("scene overlay placement is malformed")
    result: dict[str, Any] = {"clip_policy": value["clip_policy"]}
    if result["clip_policy"] != "clip_to_wall":
        raise SceneContractError("scene overlay clip_policy must be clip_to_wall")
    for name in ("strip_translation", "led_translation"):
        item = value[name]
        if isinstance(item, bool) or not isinstance(item, int) or not -(2**31) <= item < 2**31:
            raise SceneContractError(f"scene overlay {name} must be a signed 32-bit integer")
        result[name] = item
    return result


def _stale_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) not in ({"policy"}, {"policy", "lease_ms"}):
        raise SceneContractError("scene overlay stale_policy is malformed")
    policy = value.get("policy")
    lease_ms = value.get("lease_ms")
    if policy == "hold" and "lease_ms" not in value:
        return {"policy": "hold"}
    if policy == "clear_after_lease" and (
        type(lease_ms) is int and 1 <= lease_ms < 2**32
    ):
        return {"policy": policy, "lease_ms": lease_ms}
    raise SceneContractError("scene overlay stale policy is invalid")


def _resolve_vibe(scene: Mapping[str, Any]) -> tuple[str, float, float, str]:
    vibe = scene.get("vibe")
    custom = scene.get("custom")
    if (vibe is None) == (custom is None):
        raise SceneContractError("scene requires exactly one of vibe or custom values")
    if vibe is not None:
        if not isinstance(vibe, str) or vibe not in _VIBES:
            raise SceneContractError("scene vibe is not a current vibe")
        palette_id, wall_pace, presentation_luminance = _VIBES[vibe]
        return palette_id, wall_pace, presentation_luminance, vibe
    if not isinstance(custom, Mapping) or set(custom) != {
        "palette_id", "wall_pace", "presentation_luminance",
    }:
        raise SceneContractError("custom values are malformed")
    palette_id = custom["palette_id"]
    if not isinstance(palette_id, str) or not palette_id:
        raise SceneContractError("custom palette_id must be a non-empty string")
    return (
        palette_id,
        _factor(custom["wall_pace"], "wall_pace"),
        _factor(custom["presentation_luminance"], "presentation_luminance"),
        "custom",
    )


def _factor(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise SceneContractError(f"{name} must be a finite number")
    if not 0.0 <= float(value) <= 2.0:
        raise SceneContractError(f"{name} must be from 0 to 2")
    return float(value)


def normalize_scene_identity(value: Mapping[str, Any]) -> SceneIdentity:
    """Validate the exact revision/digest basis accepted by activation."""

    if not isinstance(value, Mapping) or set(value) != {"revision", "digest"}:
        raise SceneContractError("scene basis must contain exactly revision and digest")
    revision = value.get("revision")
    digest = value.get("digest")
    if type(revision) is not int or revision != SCENE_V1_REVISION:
        raise SceneContractError("scene basis revision is not current Scene v1")
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise SceneContractError("scene basis digest must be a lowercase SHA-256 digest")
    return SceneIdentity(revision=revision, digest=digest)


def build_scene_activation_command(canonical: CanonicalScene) -> dict[str, Any]:
    """Build the exact-basis control message; no topology enters this payload."""

    return {
        "action": "activate_scene",
        "basis": canonical.identity.to_dict(),
        "scene": json.loads(canonical.canonical_bytes.decode("ascii")),
    }


class LocalSceneAdapter:
    """Small local receiver-facing adapter with identity-only observation.

    It is intentionally a local contract double: the adapter never accepts,
    derives, or reports physical receiver information.  A later target layer
    may transport this accepted identity to real receivers without widening the
    Scene-v1 control contract.
    """

    def __init__(self) -> None:
        self._observed: SceneIdentity | None = None
        self._safe_idle: SceneIdentity | None = None

    def validate_control(self, command: Mapping[str, Any]) -> tuple[SceneIdentity, bytes]:
        if not isinstance(command, Mapping) or set(command) != {"action", "basis", "scene"}:
            raise SceneContractError("activation control command is malformed")
        if command.get("action") != "activate_scene":
            raise SceneContractError("activation control action is invalid")
        identity = normalize_scene_identity(command["basis"])
        bytes_value = canonical_json_bytes(command["scene"])
        if hashlib.sha256(bytes_value).hexdigest() != identity.digest:
            raise SceneContractError("activation control scene does not match its basis")
        return identity, bytes_value

    def accept_control(self, command: Mapping[str, Any]) -> SceneIdentity:
        """Record an already-validated identity and expose it as observed."""

        identity, _ = self.validate_control(command)
        self._observed = identity
        self._safe_idle = None
        return identity

    def accept_stop(self, basis: Mapping[str, Any]) -> SceneIdentity:
        """Acknowledge safe idle only for the exact currently observed basis."""
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
