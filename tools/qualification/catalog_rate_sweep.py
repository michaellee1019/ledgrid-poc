#!/usr/bin/env python3
"""Select the highest stable controller rate with guarded Lava activations."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from catalog_live_sweep import (
    _activate,
    _browser_scene,
    _counter_deltas,
    _device_counters,
    _global_settings,
    _request_json,
)


def run(base_url: str, rates: list[int], *, hold: float, timeout: float) -> dict:
    bootstrap = _request_json(base_url, "/api/v1/composer/bootstrap")
    observation = _request_json(base_url, "/api/v1/composer/settings/observed")
    current = _request_json(base_url, "/api/v1/scene")["scene"]
    profile_digest = observation["installation_profile_digest"]
    base_settings = _global_settings(observation)
    base_global_revision = int(base_settings["revision"])
    base_scene_revision = int(current["revision"])
    results = []
    for index, rate in enumerate(rates, 1):
        settings = deepcopy(base_settings)
        settings["revision"] = base_global_revision + index
        settings["output"]["target_fps"] = rate
        scene = _browser_scene(
            bootstrap,
            component_id="lava_lamp",
            parameters=None,
            revision=base_scene_revision + index,
            profile_digest=profile_digest,
        )
        receipt = _activate(base_url, scene, settings, timeout=timeout)
        before = _request_json(
            base_url, "/api/v1/composer/operations/telemetry"
        )
        before_devices = (
            before.get("diagnostics", {}).get("driver_stats", {}).get("devices", [])
        )
        started = time.monotonic()
        time.sleep(hold)
        elapsed = time.monotonic() - started
        after = _request_json(
            base_url, "/api/v1/composer/operations/telemetry"
        )
        counter_deltas, hard_failures = _counter_deltas(
            _device_counters(before), _device_counters(after)
        )
        after_devices = (
            after.get("diagnostics", {}).get("driver_stats", {}).get("devices", [])
        )
        displayed_rates = []
        for prior, final in zip(before_devices, after_devices):
            displayed_rates.append(round(
                (
                    int(final.get("receiver_frames_displayed", 0))
                    - int(prior.get("receiver_frames_displayed", 0))
                ) / elapsed,
                2,
            ))
        controller = after.get("controller") or {}
        performance = after.get("diagnostics", {}).get("performance", {})
        result = {
            "target_fps": rate,
            "activation_id": receipt["activation_id"],
            "actual_fps": round(float(controller.get("actual_fps", 0.0)), 2),
            "pipeline_fps": round(float(controller.get("pipeline_fps", 0.0)), 2),
            "receiver_displayed_fps": displayed_rates,
            "p95_generate_ms": round(float(performance.get("p95_generate_ms", 0.0)), 3),
            "p95_frame_ms": round(float(performance.get("p95_frame_ms", 0.0)), 3),
            "deadline_miss_ratio": round(float(performance.get("deadline_miss_ratio", 0.0)), 4),
            "counter_deltas": counter_deltas,
            "hard_failures": hard_failures,
            "passed": not hard_failures,
        }
        results.append(result)
        print(json.dumps(result, sort_keys=True), flush=True)

    qualified = [item for item in results if item["passed"]]
    if not qualified:
        raise RuntimeError("no tested controller rate preserved the display path")
    selected = max(qualified, key=lambda item: item["target_fps"])
    if selected["target_fps"] != rates[-1]:
        settings = deepcopy(base_settings)
        settings["revision"] = base_global_revision + len(rates) + 1
        settings["output"]["target_fps"] = selected["target_fps"]
        scene = _browser_scene(
            bootstrap,
            component_id="lava_lamp",
            parameters=None,
            revision=base_scene_revision + len(rates) + 1,
            profile_digest=profile_digest,
        )
        _activate(base_url, scene, settings, timeout=timeout)
    final = _request_json(base_url, "/api/v1/composer/settings/observed")
    return {
        "schema": "ledgrid.catalog-rate-sweep",
        "schema_version": 1,
        "selected_target_fps": selected["target_fps"],
        "observed_target_fps": final["target_fps"],
        "active_component": (
            _request_json(base_url, "/api/v1/scene")["scene"]["background"][
                "plugin_id"
            ]
        ),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--rates", default="160,171,180,200")
    parser.add_argument("--hold", type=float, default=8.0)
    parser.add_argument("--activation-timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rates = [int(value) for value in args.rates.split(",")]
    if not rates or any(rate < 1 or rate > 200 for rate in rates):
        parser.error("--rates must contain integers from 1 through 200")
    summary = run(
        args.base_url,
        rates,
        hold=max(2.0, args.hold),
        timeout=max(5.0, args.activation_timeout),
    )
    if args.output:
        args.output.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print("CATALOG_RATE_SWEEP_SUMMARY=" + json.dumps({
        key: summary[key] for key in (
            "selected_target_fps", "observed_target_fps", "active_component"
        )
    }, sort_keys=True))


if __name__ == "__main__":
    main()
