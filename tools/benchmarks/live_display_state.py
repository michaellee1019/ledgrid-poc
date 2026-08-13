"""Capture and restore live scene state around mutating hardware acceptance.

Acceptance tools must not quietly become operator controls.  They may select a
test scene for the duration of a measurement, but they either restore the
previous scene (including its resolved parameters and overlays) or stop again
when the wall was initially idle.  Restoration is verified through the public
scene API so a queued command cannot be mistaken for a completed cleanup.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping


class DisplayStateError(RuntimeError):
    """The live display could not be captured or restored exactly."""


@dataclass(frozen=True)
class SceneSnapshot:
    active: bool
    scene: dict[str, Any] | None


def capture_scene(base_url: str, get_json: Callable[[str], Any]) -> SceneSnapshot:
    """Capture the canonical public scene before an acceptance mutation."""
    payload = get_json(f"{base_url}/api/v1/scene")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("active"), bool):
        raise DisplayStateError("live scene snapshot is unavailable or malformed")
    active = payload["active"]
    scene = payload.get("scene")
    if active and not isinstance(scene, Mapping):
        raise DisplayStateError("active live scene snapshot has no canonical scene")
    if not active:
        scene = None
    return SceneSnapshot(active=active, scene=deepcopy(dict(scene)) if scene else None)


def _matches(snapshot: SceneSnapshot, payload: Any) -> bool:
    if not isinstance(payload, Mapping) or payload.get("active") is not snapshot.active:
        return False
    if not snapshot.active:
        return payload.get("scene") is None
    return payload.get("scene") == snapshot.scene


def restore_scene(
    base_url: str,
    snapshot: SceneSnapshot,
    *,
    get_json: Callable[[str], Any],
    post_json: Callable[[str, Any], Any],
    delete_json: Callable[[str], Any],
    timeout: float = 5.0,
    poll_interval: float = 0.1,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Restore and verify a prior scene, raising if cleanup is incomplete."""
    scene_url = f"{base_url}/api/v1/scene"
    if snapshot.active:
        post_json(scene_url, snapshot.scene)
    else:
        delete_json(scene_url)

    deadline = clock() + timeout
    last_payload = None
    while True:
        last_payload = get_json(scene_url)
        if _matches(snapshot, last_payload):
            return
        if clock() >= deadline:
            break
        sleeper(poll_interval)
    raise DisplayStateError(
        "live scene restoration was not observed before timeout: "
        f"expected active={snapshot.active}, observed={last_payload!r}"
    )


def capture_target_fps(base_url: str, get_json: Callable[[str], Any]) -> int:
    """Capture the live manager cadence from the public metrics payload."""
    payload = get_json(f"{base_url}/api/metrics")
    animation = payload.get("animation") if isinstance(payload, Mapping) else None
    value = animation.get("target_fps") if isinstance(animation, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 200:
        raise DisplayStateError("live target FPS snapshot is unavailable or invalid")
    return value


def capture_plant_modifiers(
    base_url: str, get_json: Callable[[str], Any]
) -> dict[str, Any]:
    """Capture manager-global plant optics from the public status payload."""
    payload = get_json(f"{base_url}/api/status")
    value = payload.get("plant_modifiers") if isinstance(payload, Mapping) else None
    if not isinstance(value, Mapping):
        raise DisplayStateError(
            "live plant modifier snapshot is unavailable or malformed"
        )
    active = value.get("active")
    strengths = value.get("strengths")
    version = value.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or not isinstance(active, list)
        or not all(isinstance(item, str) and item for item in active)
        or not isinstance(strengths, Mapping)
    ):
        raise DisplayStateError(
            "live plant modifier snapshot is unavailable or malformed"
        )
    return deepcopy(dict(value))


def restore_target_fps(
    base_url: str,
    target_fps: int,
    *,
    get_json: Callable[[str], Any],
    post_json: Callable[[str, Any], Any],
    timeout: float = 5.0,
    poll_interval: float = 0.1,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Restore and verify the manager cadence after an output-rate sweep."""
    post_json(f"{base_url}/api/config/target-fps", {"target_fps": target_fps})
    deadline = clock() + timeout
    observed = None
    while True:
        payload = get_json(f"{base_url}/api/metrics")
        animation = payload.get("animation") if isinstance(payload, Mapping) else None
        observed = animation.get("target_fps") if isinstance(animation, Mapping) else None
        if observed == target_fps:
            return
        if clock() >= deadline:
            break
        sleeper(poll_interval)
    raise DisplayStateError(
        f"target FPS restoration was not observed: expected {target_fps}, "
        f"observed {observed!r}"
    )


def restore_plant_modifiers(
    base_url: str,
    plant_modifiers: Mapping[str, Any],
    *,
    get_json: Callable[[str], Any],
    post_json: Callable[[str, Any], Any],
    timeout: float = 5.0,
    poll_interval: float = 0.1,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Apply and verify manager-global plant optics through the public API."""
    expected = deepcopy(dict(plant_modifiers))
    post_json(
        f"{base_url}/api/config/plant-modifiers",
        {"plant_modifiers": expected},
    )
    deadline = clock() + timeout
    observed = None
    while True:
        payload = get_json(f"{base_url}/api/status")
        observed = (
            payload.get("plant_modifiers")
            if isinstance(payload, Mapping) else None
        )
        if observed == expected:
            return
        if clock() >= deadline:
            break
        sleeper(poll_interval)
    raise DisplayStateError(
        "plant modifier restoration was not observed: "
        f"expected {expected!r}, observed {observed!r}"
    )
