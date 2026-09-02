#!/usr/bin/env python3
"""Guarded Go Live qualification for every Composer Animation.

This tool deliberately uses the public Check -> activate -> receipt path used by
the Composer.  It restores the exact starting scene document when the sweep
finishes, including when one catalog entry fails.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid


COMPOSER_ANIMATIONS = (
    "aurora_curtains",
    "canopy_cup",
    "ascii_drop",
    "emoji",
    "christmas_tree",
    "night_train_windows",
    "conway_life",
    "tetris",
    "firefly_synchrony",
    "fireworks",
    "flame_burst",
    "fluid_tank",
    "cyclic_reef",
    "lava_lamp",
    "snake",
    "maze_chase",
    "pinball",
    "pixel_quest",
    "gradient",
    "rainbow",
    "solid",
    "sparkle",
    "wave",
    "circadian_window",
    "cloud_canyon",
    "desert_wind",
    "moonlit_fog_banks",
    "rain_on_glass",
    "tidal_bioluminescence",
    "waterfall_veil",
    "cellular_tapestry",
    "flow_field_silk",
    "frostwork",
    "living_stained_glass",
    "quasicrystal_bloom",
    "living_ecosystem",
    "physarum_network",
    "reaction_diffusion_garden",
    "wind_in_the_reeds",
)

TERMINAL_PHASES = frozenset({"active", "failed", "timed_out", "rolled_back"})
HARD_COUNTERS = (
    "errors",
    "receiver_publish_drops",
    "receiver_spi_queue_errors",
    "receiver_display_errors",
    "receiver_status_misses",
)
KNOWN_RECEIVER_COUNTERS = (
    "receiver_crc_errors",
    "receiver_fec_uncorrectable_packets",
    "receiver_fec_semantic_crc_errors",
    "receiver_fec_framing_errors",
)


class SweepError(RuntimeError):
    pass


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    body = None
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=15) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SweepError(f"{method} {path} returned {exc.code}: {detail}") from exc
    except (OSError, URLError, ValueError) as exc:
        raise SweepError(f"{method} {path} failed: {exc}") from exc
    if not isinstance(decoded, dict):
        raise SweepError(f"{method} {path} did not return an object")
    return decoded


def _managed_component(bootstrap: Mapping[str, Any], component_id: str) -> dict:
    matches = [
        item for item in bootstrap.get("components", [])
        if item.get("provider") == "python"
        and item.get("plugin_id") == component_id
        and item.get("role") in {"background", "overlay"}
    ]
    if len(matches) != 1:
        raise SweepError(f"{component_id} has {len(matches)} managed catalog entries")
    component = matches[0]
    capabilities = component.get("browser_capabilities") or {}
    managed = capabilities.get("managed_identity")
    if capabilities.get("activation_ready") is not True or not isinstance(managed, dict):
        raise SweepError(
            f"{component_id} is not activation-ready: {capabilities.get('reason')}"
        )
    return component


def _binding(
    bootstrap: Mapping[str, Any],
    component_id: str,
    parameters: Mapping[str, Any] | None = None,
) -> dict:
    component = _managed_component(bootstrap, component_id)
    managed = component["browser_capabilities"]["managed_identity"]
    return {
        "provider": managed["provider"],
        "component_id": managed["component_id"],
        "component_digest": managed["component_digest"],
        "runtime_digest": managed["runtime_digest"],
        "parameter_schema_version": managed["parameter_schema_version"],
        "parameters": deepcopy(
            component.get("defaults") if parameters is None else dict(parameters)
        ),
    }


def _browser_scene(
    bootstrap: Mapping[str, Any],
    *,
    component_id: str,
    parameters: Mapping[str, Any] | None,
    revision: int,
    profile_digest: str,
) -> dict:
    background = _binding(bootstrap, component_id, parameters)
    return {
        "schema": "ledgrid.browser-scene",
        "schema_version": 1,
        "revision": revision,
        "background": background,
        "layers": [],
        "installation_profile": {"digest": profile_digest},
        "fallback": deepcopy(background),
    }


def _global_settings(observation: Mapping[str, Any]) -> dict:
    active = observation.get("active_identity") or {}
    global_identity = active.get("global_settings_identity") or {}
    vibe = observation.get("vibe") or {}
    vibe_state = vibe.get("state") if isinstance(vibe.get("state"), dict) else vibe
    return {
        "schema": "ledgrid.global-settings-state",
        "schema_version": 1,
        "revision": int(global_identity["revision"]),
        "vibe": {
            "vibe_id": vibe_state["vibe_id"],
            "profile_version": vibe_state["profile_version"],
            "resolved_profile_digest": vibe_state["resolved_profile_digest"],
        },
        "plant_modifiers": deepcopy(observation.get("plant_modifiers") or {
            "version": 1, "active": [], "strengths": {},
        }),
        "output": {
            "power": True,
            "brightness": int(observation["brightness"]),
            "animation_speed_scale": float(observation["animation_speed_scale"]),
            "target_fps": int(observation["target_fps"]),
        },
    }


def _activate(
    base_url: str,
    scene: Mapping[str, Any],
    settings: Mapping[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    checked = _request_json(
        base_url,
        "/api/v1/scene/checks",
        method="POST",
        payload={"scene": scene, "global_settings": settings},
    )
    controller = (checked.get("basis") or {}).get("controller") or {}
    if not checked.get("check_token") or not controller.get("session_id"):
        raise SweepError("Check returned an incomplete activation authorization")
    accepted = _request_json(
        base_url,
        "/api/v1/scene",
        method="PUT",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        payload={
            "check_token": checked["check_token"],
            "expected_controller_session_id": controller["session_id"],
            "expected_controller_state_revision": controller["state_revision"],
            "scene": scene,
            "global_settings": settings,
        },
    )
    activation_id = accepted.get("activation_id")
    if not activation_id:
        raise SweepError("activation response has no activation_id")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        receipt = _request_json(
            base_url, f"/api/v1/scene/activations/{activation_id}"
        )
        phase = receipt.get("phase")
        if phase in TERMINAL_PHASES:
            if phase != "active":
                raise SweepError(
                    f"activation {activation_id} ended in {phase}: {receipt.get('error')}"
                )
            if receipt.get("requested_identity") != receipt.get("observed_identity"):
                raise SweepError("active receipt did not observe the requested identity")
            return receipt
        time.sleep(0.2)
    raise SweepError(f"activation {activation_id} did not settle within {timeout:.1f}s")


def _device_counters(telemetry: Mapping[str, Any]) -> dict[int, dict[str, int]]:
    devices = (
        telemetry.get("diagnostics", {})
        .get("driver_stats", {})
        .get("devices", [])
    )
    return {
        int(device["receiver_logical_device"]): {
            name: int(device.get(name, 0))
            for name in HARD_COUNTERS + KNOWN_RECEIVER_COUNTERS
        }
        for device in devices
    }


def _counter_deltas(
    before: Mapping[int, Mapping[str, int]],
    after: Mapping[int, Mapping[str, int]],
) -> tuple[dict[str, int], list[str]]:
    deltas: dict[str, int] = {}
    failures: list[str] = []
    for receiver, final in sorted(after.items()):
        initial = before.get(receiver, {})
        for name, value in final.items():
            delta = max(0, value - int(initial.get(name, value)))
            if delta:
                deltas[f"receiver_{receiver}.{name}"] = delta
                # CRC/FEC counters are transport diagnostics, not evidence that
                # the scene failed to activate or display.  Preserve every
                # delta for the follow-up rate sweep; only presentation-path
                # failures stop catalog qualification.
                if name in HARD_COUNTERS:
                    failures.append(f"receiver {receiver} {name} increased by {delta}")
    return deltas, failures


def _wait_for_component(
    base_url: str,
    component_id: str,
    *,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        telemetry = _request_json(
            base_url, "/api/v1/composer/operations/telemetry"
        )
        if telemetry.get("controller", {}).get("current_animation") == component_id:
            return telemetry
        time.sleep(0.2)
    raise SweepError(f"telemetry did not observe restored {component_id!r}")


def _starting_scene(
    bootstrap: Mapping[str, Any],
    host_scene: Mapping[str, Any],
    profile_digest: str,
) -> dict:
    background = host_scene.get("background") or {}
    if background.get("provider") != "python" or not background.get("plugin_id"):
        raise SweepError("starting scene is not a restorable Python Composer scene")
    scene = _browser_scene(
        bootstrap,
        component_id=background["plugin_id"],
        parameters=background.get("parameter_overrides") or {},
        revision=int(host_scene["revision"]),
        profile_digest=profile_digest,
    )
    layers = []
    for overlay in host_scene.get("overlays", []):
        component = overlay.get("component") or {}
        if not overlay.get("enabled", True) or component.get("provider") != "python":
            continue
        layers.append({
            "role": overlay.get("slot_id", "overlay"),
            "component": _binding(
                bootstrap,
                component["plugin_id"],
                component.get("parameter_overrides") or {},
            ),
            "enabled": True,
            "opacity": int(overlay.get("opacity", 255)),
            "blend_mode": overlay.get("blend_mode", "source_over"),
        })
    scene["layers"] = layers
    return scene


def run(base_url: str, *, hold: float, timeout: float) -> dict[str, Any]:
    bootstrap = _request_json(base_url, "/api/v1/composer/bootstrap")
    available = {
        item.get("plugin_id") for item in bootstrap.get("components", [])
        if item.get("provider") == "python"
        and item.get("role") == "background"
        and (item.get("browser_capabilities") or {}).get("activation_ready") is True
    }
    missing = sorted(set(COMPOSER_ANIMATIONS) - available)
    if missing:
        raise SweepError(f"Composer animations are not activation-ready: {missing}")
    observation = _request_json(base_url, "/api/v1/composer/settings/observed")
    scene_payload = _request_json(base_url, "/api/v1/scene")
    if not observation.get("is_running") or not isinstance(scene_payload.get("scene"), dict):
        raise SweepError("the wall must begin with a restorable active Composer scene")
    profile_digest = observation["installation_profile_digest"]
    settings = _global_settings(observation)
    original_scene = _starting_scene(
        bootstrap, scene_payload["scene"], profile_digest
    )
    baseline = _device_counters(
        _request_json(base_url, "/api/v1/composer/operations/telemetry")
    )
    results: list[dict[str, Any]] = []
    failure: str | None = None
    try:
        for index, component_id in enumerate(COMPOSER_ANIMATIONS, 1):
            candidate = _browser_scene(
                bootstrap,
                component_id=component_id,
                parameters=None,
                revision=int(original_scene["revision"]) + index,
                profile_digest=profile_digest,
            )
            started = time.monotonic()
            receipt = _activate(base_url, candidate, settings, timeout=timeout)
            time.sleep(hold)
            telemetry = _request_json(
                base_url, "/api/v1/composer/operations/telemetry"
            )
            controller = telemetry.get("controller") or {}
            performance = (telemetry.get("diagnostics") or {}).get("performance") or {}
            if controller.get("current_animation") != component_id:
                raise SweepError(
                    f"telemetry reports {controller.get('current_animation')!r} "
                    f"while {component_id!r} should be active"
                )
            deltas, counter_failures = _counter_deltas(
                baseline, _device_counters(telemetry)
            )
            if counter_failures:
                raise SweepError("; ".join(counter_failures))
            result = {
                "component_id": component_id,
                "activation_id": receipt["activation_id"],
                "phase": receipt["phase"],
                "actual_fps": round(float(controller.get("actual_fps", 0.0)), 2),
                "pipeline_fps": round(float(controller.get("pipeline_fps", 0.0)), 2),
                "p95_generate_ms": round(float(performance.get("p95_generate_ms", 0.0)), 3),
                "p95_frame_ms": round(float(performance.get("p95_frame_ms", 0.0)), 3),
                "deadline_miss_ratio": round(float(performance.get("deadline_miss_ratio", 0.0)), 4),
                "known_receiver_deltas": deltas,
                "elapsed_seconds": round(time.monotonic() - started, 2),
            }
            results.append(result)
            print(json.dumps(result, sort_keys=True), flush=True)
    except Exception as exc:  # restore is mandatory for every catalog failure
        failure = f"{type(exc).__name__}: {exc}"
    restore_error = None
    final_telemetry = None
    try:
        restored = _activate(base_url, original_scene, settings, timeout=timeout)
        if restored.get("requested_identity") != restored.get("observed_identity"):
            raise SweepError("restored receipt identity mismatch")
        final_telemetry = _wait_for_component(
            base_url,
            original_scene["background"]["component_id"],
            timeout=timeout,
        )
    except Exception as exc:
        restore_error = f"{type(exc).__name__}: {exc}"
    if final_telemetry is None:
        final_telemetry = _request_json(
            base_url, "/api/v1/composer/operations/telemetry"
        )
    final_deltas, final_counter_failures = _counter_deltas(
        baseline, _device_counters(final_telemetry)
    )
    summary = {
        "schema": "ledgrid.catalog-live-sweep",
        "schema_version": 1,
        "catalog_count": len(COMPOSER_ANIMATIONS),
        "passed_count": len(results),
        "passed": (
            failure is None
            and restore_error is None
            and not final_counter_failures
            and len(results) == len(COMPOSER_ANIMATIONS)
        ),
        "failure": failure,
        "restore_error": restore_error,
        "restored_component": final_telemetry.get("controller", {}).get(
            "current_animation"
        ),
        "receiver_counter_deltas": final_deltas,
        "receiver_counter_failures": final_counter_failures,
        "results": results,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--hold", type=float, default=2.25)
    parser.add_argument("--activation-timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary = run(
        args.base_url,
        hold=max(0.25, args.hold),
        timeout=max(5.0, args.activation_timeout),
    )
    if args.output is not None:
        args.output.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print("CATALOG_LIVE_SWEEP_SUMMARY=" + json.dumps({
        key: summary[key] for key in (
            "catalog_count", "passed_count", "passed", "failure",
            "restore_error", "restored_component", "receiver_counter_deltas",
            "receiver_counter_failures",
        )
    }, sort_keys=True))
    raise SystemExit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    try:
        main()
    except SweepError as exc:
        print(f"catalog live sweep failed before activation: {exc}", file=sys.stderr)
        raise SystemExit(1)
