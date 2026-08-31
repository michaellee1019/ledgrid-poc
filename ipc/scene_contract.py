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

from animation.core.component_catalog import ComponentCatalog
from animation.core.presentation_contracts import resolve_scene


SCENE_V1_REVISION = 1
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


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
    one source scene.  ``resolve_scene`` enforces the one Python-background,
    zero-overlay subset before this function can produce an identity.
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
        resolved = resolve_scene(scene, catalog, monotonic_elapsed=0.0)
    except (TypeError, ValueError) as exc:
        raise SceneContractError(str(exc)) from exc
    # Reparse stable bytes so callers receive ordinary, non-mutable JSON data
    # rather than presentation-contract mapping proxies.
    bytes_value = bytes(resolved.canonical_bytes)
    normalized = json.loads(bytes_value.decode("ascii"))
    return CanonicalScene(
        scene=normalized,
        canonical_bytes=bytes_value,
        identity=SceneIdentity(
            revision=SCENE_V1_REVISION,
            digest=hashlib.sha256(bytes_value).hexdigest(),
        ),
    )


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
        return identity

    def observed_identity(self) -> SceneIdentity | None:
        return self._observed


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
