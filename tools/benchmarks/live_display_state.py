"""Read-only binding helpers for live acceptance tools.

Acceptance tools are observers, not alternate operator controls. A caller must
name the exact canonical scene digest that Composer already activated. These
helpers reject idle, malformed, changed, or differently parameterized scenes
before a measurement begins and never attempt cleanup or restoration.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Callable, Mapping

from tools.operations_telemetry import status_from_telemetry


DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class DisplayStateError(RuntimeError):
    """The exact pre-activated live display identity was not observed."""


@dataclass(frozen=True)
class ActiveDisplayIdentity:
    scene_digest: str
    scene_revision: int
    provider: str
    plugin_id: str


def canonical_scene_digest(scene: Mapping[str, Any]) -> str:
    """Return the stable identity of one complete canonical scene document."""

    try:
        encoded = json.dumps(
            dict(scene), sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DisplayStateError("active scene is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def require_active_scene(
    base_url: str,
    expected_scene_digest: str,
    get_json: Callable[[str], Any],
    *,
    expected_plugin: str | None = None,
    expected_provider: str | None = None,
) -> ActiveDisplayIdentity:
    """Observe and bind the exact Composer-activated scene without mutating it."""

    if DIGEST_PATTERN.fullmatch(expected_scene_digest or "") is None:
        raise DisplayStateError(
            "--expected-scene-digest must be the 64-character digest from the "
            "guarded Composer activation receipt"
        )
    try:
        payload = status_from_telemetry(
            get_json(f"{base_url}/api/v1/composer/operations/telemetry")
        )
    except ValueError as exc:
        raise DisplayStateError("live operations telemetry is unavailable or malformed") from exc
    scene = payload.get("scene_state")
    if not isinstance(scene, Mapping):
        raise DisplayStateError(
            "no canonical active scene is observable; activate it through "
            "Composer Check + guarded activation first"
        )
    revision = scene.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise DisplayStateError("active scene revision is unavailable or invalid")
    background = scene.get("background")
    if not isinstance(background, Mapping):
        raise DisplayStateError("active scene background identity is unavailable")
    provider = background.get("provider", "python")
    plugin_id = background.get("plugin_id")
    if not isinstance(provider, str) or not isinstance(plugin_id, str):
        raise DisplayStateError("active scene background identity is malformed")
    observed_digest = canonical_scene_digest(scene)
    if observed_digest != expected_scene_digest:
        raise DisplayStateError(
            "active scene identity does not match the guarded activation receipt: "
            f"expected {expected_scene_digest}, observed {observed_digest}"
        )
    if expected_plugin is not None and plugin_id != expected_plugin:
        raise DisplayStateError(
            f"active background is {plugin_id!r}, expected {expected_plugin!r}"
        )
    if expected_provider is not None and provider != expected_provider:
        raise DisplayStateError(
            f"active provider is {provider!r}, expected {expected_provider!r}"
        )
    return ActiveDisplayIdentity(
        scene_digest=observed_digest,
        scene_revision=revision,
        provider=provider,
        plugin_id=plugin_id,
    )
