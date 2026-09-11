#!/usr/bin/env python3
"""Guarded playback qualification for the complete current Composer catalog.

This tool deliberately uses the public Check -> activate -> receipt path used by
the Composer.  It restores the exact starting scene document when the sweep
finishes, including when one catalog entry fails.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSER_JS = PROJECT_ROOT / "web" / "static" / "js" / "composer_slice.js"


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


def animation_components(bootstrap: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the exact current Animation packet published to the browser."""
    animations = sorted(
        (
            deepcopy(item)
            for item in bootstrap.get("components", [])
            if isinstance(item, Mapping)
            and item.get("provider") == "python"
            and item.get("role") == "animation"
        ),
        key=lambda item: str(item.get("plugin_id")),
    )
    if not animations:
        raise SweepError("Composer bootstrap has no current Animation components")
    seen: set[str] = set()
    for component in animations:
        component_id = component.get("plugin_id")
        capabilities = component.get("browser_capabilities") or {}
        managed = capabilities.get("managed_identity")
        if (
            not isinstance(component_id, str)
            or not component_id
            or component_id in seen
            or capabilities.get("activation_ready") is not True
            or not isinstance(managed, Mapping)
            or managed.get("provider") != "python"
            or managed.get("component_id") != component_id
            or not isinstance(component.get("defaults"), Mapping)
            or set(component.get("parameter_schema") or {}) != set(component["defaults"])
        ):
            raise SweepError(
                f"current Animation catalog entry is incomplete: {component_id!r}"
            )
        seen.add(component_id)
    return animations


def catalog_cases(
    bootstrap: Mapping[str, Any],
    *,
    base_scene: Mapping[str, Any],
    presets: Mapping[str, Sequence[Mapping[str, Any]]],
    starters: Sequence[Mapping[str, Any]],
    looks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build current defaults, authored presets, starters, and reopened Looks."""
    cases: list[dict[str, Any]] = []
    for component in animation_components(bootstrap):
        component_id = component["plugin_id"]
        default_scene = deepcopy(base_scene)
        default_scene["animation"] = {
            "component_id": component_id,
            "version": 1,
            "provider": "python",
            "role": "animation",
            "parameters": deepcopy(component["defaults"]),
        }
        cases.append({"case_id": f"default:{component_id}", "kind": "default",
                      "component_id": component_id, "scene": default_scene})
        component_presets = presets.get(component_id)
        if component_presets is None:
            raise SweepError(f"preset endpoint missing for {component_id}")
        bootstrap_ids = {
            item.get("preset_id") for item in component.get("presets", [])
            if isinstance(item, Mapping) and item.get("ownership") == "built_in"
        }
        endpoint_ids = {
            item.get("preset_id") for item in component_presets
            if isinstance(item, Mapping)
        }
        if bootstrap_ids != endpoint_ids or None in endpoint_ids:
            raise SweepError(f"preset membership disagrees for {component_id}")
        for preset in component_presets:
            preset_id = preset["preset_id"]
            parameters = preset.get("parameters")
            if not isinstance(parameters, Mapping):
                raise SweepError(f"preset {component_id}/{preset_id} has no parameters")
            if set(parameters) != set(component["parameter_schema"]):
                raise SweepError(
                    f"preset {component_id}/{preset_id} does not expose the current controls"
                )
            scene = deepcopy(default_scene)
            scene["animation"]["parameters"] = deepcopy(parameters)
            if preset.get("installation_effects") is not None:
                scene["plants"]["effects"] = deepcopy(preset["installation_effects"])
            cases.append({"case_id": f"preset:{component_id}:{preset_id}",
                          "kind": "preset", "component_id": component_id,
                          "preset_id": preset_id, "scene": scene})
    for kind, values in (("starter", starters), ("look", looks)):
        for value in values:
            if not isinstance(value.get("id"), str) or not isinstance(value.get("scene"), Mapping):
                raise SweepError(f"{kind} endpoint returned an incomplete Scene")
            cases.append({"case_id": f"{kind}:{value['id']}", "kind": kind,
                          "component_id": value["scene"]["animation"]["component_id"],
                          "scene": deepcopy(value["scene"])})
    if len({case["case_id"] for case in cases}) != len(cases):
        raise SweepError("qualification cases contain duplicate identities")
    return cases


def representative_look_ids(looks: Sequence[Mapping[str, Any]]) -> list[str]:
    ids = [item.get("id") for item in looks if isinstance(item.get("id"), str)]
    if len(ids) <= 3:
        return ids
    return [ids[0], ids[len(ids) // 2], ids[-1]]


def browser_scene_requests(
    bootstrap: Mapping[str, Any], observation: Mapping[str, Any],
    scenes: Sequence[Mapping[str, Any]], *, composer_js: Path = COMPOSER_JS,
) -> list[dict[str, Any]]:
    """Run the production browserSceneForWall helper in Node for each Scene."""
    runner = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(payload.composer_js, 'utf8');
const start = source.indexOf('function managedWallComponent');
const end = source.indexOf('  function globalSettingsForWall');
if (start < 0 || end <= start) throw new Error('browserSceneForWall source boundary missing');
const context = {structuredClone, JSON, Number, Boolean, Math, Object, Set,
  state: {wall: {bootstrap: payload.bootstrap, observation: payload.observation}},
  scenes: payload.scenes};
vm.runInNewContext(source.slice(start, end) +
  '; result = scenes.map((scene) => browserSceneForWall(scene));', context);
process.stdout.write(JSON.stringify(context.result));
"""
    completed = subprocess.run(
        ["node", "-e", runner],
        input=json.dumps({"composer_js": str(composer_js), "bootstrap": bootstrap,
                          "observation": observation, "scenes": list(scenes)},
                         separators=(",", ":")),
        check=False, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise SweepError("production browser request construction failed: " +
                         (completed.stderr.strip() or "Node exited without diagnostics"))
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SweepError("production browser request construction returned invalid JSON") from exc
    if not isinstance(result, list) or len(result) != len(scenes):
        raise SweepError("production browser request construction returned the wrong case count")
    return result


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


def _settings_at_current_revision(
    base_url: str, captured: Mapping[str, Any]
) -> dict[str, Any]:
    """Keep captured values while binding Check to the latest controller revision."""
    observation = _request_json(
        base_url, "/api/v1/composer/settings/observed"
    )
    identity = (observation.get("active_identity") or {}).get(
        "global_settings_identity"
    ) or {}
    updated = deepcopy(captured)
    updated["revision"] = int(identity["revision"])
    return updated


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


def run(
    base_url: str, *, hold: float, timeout: float,
    physical_wall_authorized: bool = False,
) -> dict[str, Any]:
    if not physical_wall_authorized:
        raise SweepError("physical-wall authorization is required before activation")
    bootstrap = _request_json(base_url, "/api/v1/composer/bootstrap")
    animations = animation_components(bootstrap)
    observation = _request_json(base_url, "/api/v1/composer/settings/observed")
    scene_payload = _request_json(base_url, "/api/v1/scene")
    if not observation.get("is_running") or not isinstance(scene_payload.get("scene"), dict):
        raise SweepError("the wall must begin with a restorable active Composer scene")
    settings = _global_settings(observation)
    original_scene = deepcopy(scene_payload["scene"])
    if original_scene.get("schema") != "ledgrid.scene.v2":
        raise SweepError(
            "the wall must begin with a complete Scene v2 before catalog activation"
        )
    presets = {}
    for component in animations:
        component_id = component["plugin_id"]
        payload = _request_json(
            base_url,
            "/api/composer/components/" + quote(component_id, safe="") + "/presets",
        )
        if not isinstance(payload.get("presets"), list):
            raise SweepError(f"preset endpoint is incomplete for {component_id}")
        presets[component_id] = payload["presets"]
    starter_summaries = _request_json(base_url, "/api/composer/starters").get(
        "starters", []
    )
    if not isinstance(starter_summaries, list):
        raise SweepError("starter endpoint is incomplete")
    starters = [
        _request_json(
            base_url, "/api/composer/starters/" + quote(item["id"], safe="")
        )["starter"]
        for item in starter_summaries
    ]
    look_summaries = _request_json(base_url, "/api/composer/looks").get("looks", [])
    if not isinstance(look_summaries, list):
        raise SweepError("saved Look endpoint is incomplete")
    looks = [
        _request_json(
            base_url, "/api/composer/looks/" + quote(look_id, safe="")
        )["look"]
        for look_id in representative_look_ids(look_summaries)
    ]
    cases = catalog_cases(
        bootstrap,
        base_scene=starters[0]["scene"] if starters else original_scene,
        presets=presets,
        starters=starters,
        looks=looks,
    )
    browser_scenes = browser_scene_requests(
        bootstrap, observation, [case["scene"] for case in cases] + [original_scene]
    )
    original_browser_scene = browser_scenes[-1]
    baseline = _device_counters(
        _request_json(base_url, "/api/v1/composer/operations/telemetry")
    )
    results: list[dict[str, Any]] = []
    failure: str | None = None
    rejection: dict[str, Any] | None = None
    restore_error = None
    final_telemetry = None
    try:
        invalid = deepcopy(browser_scenes[0])
        invalid["components"][1]["component_digest"] = "0" * 64
        try:
            _request_json(
                base_url, "/api/v1/scene/checks", method="POST",
                payload={"scene": invalid, "global_settings": settings},
            )
        except SweepError as exc:
            detail = str(exc)
            if (
                "returned 400:" not in detail
                or "managed identity is stale" not in detail
            ):
                raise SweepError(
                    "stale managed Animation identity did not receive its exact rejection: "
                    + detail
                ) from exc
            rejection = {"passed": True, "detail": detail}
        else:
            raise SweepError("a stale managed Animation identity was accepted")

        schemas = {
            component["plugin_id"]: component["parameter_schema"]
            for component in animations
        }
        for case, candidate in zip(cases, browser_scenes[:-1], strict=True):
            started = time.monotonic()
            current_settings = _settings_at_current_revision(base_url, settings)
            receipt = _activate(
                base_url, candidate, current_settings, timeout=timeout
            )
            time.sleep(hold)
            telemetry = _request_json(
                base_url, "/api/v1/composer/operations/telemetry"
            )
            controller = telemetry.get("controller") or {}
            performance = (telemetry.get("diagnostics") or {}).get("performance") or {}
            if controller.get("current_animation") != case["component_id"]:
                raise SweepError(
                    f"telemetry reports {controller.get('current_animation')!r} "
                    f"while {case['component_id']!r} should be active"
                )
            deltas, counter_failures = _counter_deltas(
                baseline, _device_counters(telemetry)
            )
            if counter_failures:
                raise SweepError("; ".join(counter_failures))
            result = {
                "case_id": case["case_id"],
                "kind": case["kind"],
                "component_id": case["component_id"],
                "activation_id": receipt["activation_id"],
                "phase": receipt["phase"],
                "requested_identity": receipt["requested_identity"],
                "observed_identity": receipt["observed_identity"],
                "control_count": len(schemas[case["component_id"]]),
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
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            restore_settings = _settings_at_current_revision(base_url, settings)
            restored = _activate(
                base_url, original_browser_scene, restore_settings, timeout=timeout
            )
            if restored.get("requested_identity") != restored.get("observed_identity"):
                raise SweepError("restored receipt identity mismatch")
            final_telemetry = _wait_for_component(
                base_url,
                original_scene["animation"]["component_id"],
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
        "schema_version": 2,
        "browser_request_builder": "composer_slice.js:browserSceneForWall",
        "preview_qualification": "required_local_production_preview_matrix",
        "animation_count": len(animations),
        "preset_count": sum(len(items) for items in presets.values()),
        "starter_count": len(starters),
        "look_count": len(looks),
        "catalog_count": len(cases),
        "passed_count": len(results),
        "truthful_rejection": rejection,
        "passed": (
            failure is None
            and restore_error is None
            and not final_counter_failures
            and rejection is not None
            and len(results) == len(cases)
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


def run_local() -> dict[str, Any]:
    """Run the reusable current-catalog matrix with only receiver I/O mocked."""
    test_id = (
        "tests.unit.test_canonical_scene_activation.CanonicalSceneActivationTests."
        "test_full_current_catalog_matrix_uses_browser_requests_preview_and_exact_receipts"
    )
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", test_id],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    bootstrap = json.loads(
        (PROJECT_ROOT / "web/static/generated/composer/bootstrap.v1.json").read_text(
            encoding="utf-8"
        )
    )
    animations = animation_components(bootstrap)
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from web.starter_looks import list_starters

    counts = {
        "defaults": len(animations),
        "presets": sum(len(component.get("presets", [])) for component in animations),
        "starters": len(list_starters()),
        "reopened_looks": 3,
    }
    return {
        "schema": "ledgrid.catalog-local-qualification",
        "schema_version": 1,
        "test_id": test_id,
        "passed": completed.returncode == 0,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "case_counts": counts,
        "case_count": sum(counts.values()),
        "physical_wall_mutations": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        help="physical-wall server; omit to run the complete local qualification",
    )
    parser.add_argument("--hold", type=float, default=2.25)
    parser.add_argument("--activation-timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--physical-wall-authorized", action="store_true",
        help="confirm separate authorization to change physical wall output",
    )
    args = parser.parse_args()
    if args.base_url is None:
        summary = run_local()
        if args.output is not None:
            args.output.write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print("CATALOG_LOCAL_QUALIFICATION_SUMMARY=" + json.dumps({
            "passed": summary["passed"],
            "case_count": summary["case_count"],
            "case_counts": summary["case_counts"],
            "physical_wall_mutations": summary["physical_wall_mutations"],
            "test_id": summary["test_id"],
        }, sort_keys=True))
        if not summary["passed"]:
            print(summary["stdout"], file=sys.stderr)
            print(summary["stderr"], file=sys.stderr)
        raise SystemExit(0 if summary["passed"] else 1)
    if not args.physical_wall_authorized:
        parser.error(
            "this command changes physical wall output; pass "
            "--physical-wall-authorized only with explicit authorization"
        )
    summary = run(
        args.base_url,
        hold=max(0.25, args.hold),
        timeout=max(5.0, args.activation_timeout),
        physical_wall_authorized=True,
    )
    if args.output is not None:
        args.output.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print("CATALOG_LIVE_SWEEP_SUMMARY=" + json.dumps({
        key: summary[key] for key in (
            "animation_count", "preset_count", "starter_count", "look_count",
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
