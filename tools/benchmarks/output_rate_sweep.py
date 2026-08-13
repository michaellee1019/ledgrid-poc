#!/usr/bin/env python3
"""Step live output rates and correlate visual behavior with pipeline telemetry."""

from __future__ import annotations

import argparse
import json
import math
import time
from urllib import request

if __package__:
    from tools.benchmarks.live_display_state import (
        capture_scene,
        capture_target_fps,
        restore_scene,
        restore_target_fps,
    )
else:  # Direct script execution from the documented Just recipes.
    from live_display_state import (
        capture_scene,
        capture_target_fps,
        restore_scene,
        restore_target_fps,
    )


def _get_json(url):
    with request.urlopen(url, timeout=5) as response:
        return json.load(response)


def _post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=5) as response:
        return json.load(response)


def _delete_json(url):
    req = request.Request(url, method="DELETE")
    with request.urlopen(req, timeout=5) as response:
        return json.load(response)


def _wait_for_target(base_url, target, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        metrics = _get_json(f"{base_url}/api/metrics")
        if int(metrics.get("animation", {}).get("target_fps", 0) or 0) == target:
            return metrics
        time.sleep(0.1)
    raise RuntimeError(f"controller did not apply {target} FPS target")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://ledgridwall.local:5000")
    parser.add_argument("--rates", default="120,140,160,180,200")
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--animation", default="rainbow")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    rates = [int(value) for value in args.rates.split(',') if value.strip()]
    if not rates or any(not 1 <= rate <= 200 for rate in rates):
        parser.error("--rates must contain comma-separated integers from 1 to 200")
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be finite and greater than zero")

    scene_snapshot = capture_scene(base_url, _get_json)
    original_target_fps = capture_target_fps(base_url, _get_json)
    results = []
    run_failure = None
    cleanup_failures = []
    try:
        _post_json(f"{base_url}/api/start/{args.animation}", {})
        for rate in rates:
            _post_json(f"{base_url}/api/config/target-fps", {"target_fps": rate})
            first = _wait_for_target(base_url, rate)
            time.sleep(max(1.0, args.seconds))
            last = _get_json(f"{base_url}/api/metrics")
            first_devices = first.get("driver", {}).get("devices", [])
            last_devices = last.get("driver", {}).get("devices", [])
            receivers = []
            for index, (before, after) in enumerate(zip(first_devices, last_devices)):
                if int(after.get("receiver_status_version", 0) or 0) < 2:
                    continue
                elapsed = max(1.0, args.seconds)
                receivers.append({
                    "device": index,
                    "displayed_fps": round(
                        (int(after.get("receiver_frames_displayed", 0) or 0)
                         - int(before.get("receiver_frames_displayed", 0) or 0)) / elapsed,
                        2,
                    ),
                    "crc_error_delta": int(after.get("receiver_crc_errors", 0) or 0)
                    - int(before.get("receiver_crc_errors", 0) or 0),
                    "display_error_delta": int(after.get("receiver_display_errors", 0) or 0)
                    - int(before.get("receiver_display_errors", 0) or 0),
                })
            results.append({
                "target_fps": rate,
                "actual_fps": round(
                    float(last.get("animation", {}).get("actual_fps", 0) or 0), 2
                ),
                "receivers": receivers,
            })
    except Exception as exc:
        run_failure = str(exc)
    finally:
        try:
            restore_target_fps(
                base_url, original_target_fps, get_json=_get_json,
                post_json=_post_json,
            )
        except Exception as exc:
            cleanup_failures.append(f"target FPS: {exc}")
        try:
            restore_scene(
                base_url, scene_snapshot, get_json=_get_json,
                post_json=_post_json, delete_json=_delete_json,
            )
        except Exception as exc:
            cleanup_failures.append(f"scene: {exc}")

    output = {
        "passed": run_failure is None and not cleanup_failures,
        "animation": args.animation,
        "rates": results,
        "target_fps_restored": not any(
            item.startswith("target FPS:") for item in cleanup_failures
        ),
        "scene_restored": not any(item.startswith("scene:") for item in cleanup_failures),
    }
    if run_failure:
        output["failure"] = run_failure
    if cleanup_failures:
        output["cleanup_failures"] = cleanup_failures
    print(json.dumps(output, indent=2))
    raise SystemExit(0 if output["passed"] else 1)


if __name__ == "__main__":
    main()
