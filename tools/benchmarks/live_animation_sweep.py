#!/usr/bin/env python3
"""Start every registered animation and verify live wall pipeline integrity."""

from __future__ import annotations

import argparse
import json
import math
import time
from urllib import request

if __package__:
    from tools.benchmarks.live_display_state import capture_scene, restore_scene
    from tools.benchmarks.receiver_acceptance import (
        DEGRADED_SPI1_WRITE_ONLY_DEVICES,
        evaluate_write_only_samples,
    )
else:  # Direct script execution from the documented Just recipes.
    from live_display_state import capture_scene, restore_scene
    from receiver_acceptance import (
        DEGRADED_SPI1_WRITE_ONLY_DEVICES,
        evaluate_write_only_samples,
    )


ERROR_COUNTERS = (
    ("receiver_crc_errors", "CRC errors"),
    ("receiver_publish_drops", "publish drops"),
    ("receiver_spi_queue_errors", "SPI queue errors"),
    ("receiver_display_errors", "display errors"),
    ("receiver_status_misses", "status misses"),
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


def receiver_failures(first, last):
    failures = []
    if int(last.get("receiver_status_version", 0) or 0) < 2:
        return ["receiver status v2+ unavailable"]
    for key, label in ERROR_COUNTERS:
        delta = int(last.get(key, 0) or 0) - int(first.get(key, 0) or 0)
        if delta:
            failures.append(f"{label} increased by {delta}")
    return failures


def evaluate_receiver_topology(
    first_devices, last_devices, *, allow_degraded_spi1=False
):
    """Evaluate all four logical receivers without silently dropping telemetry gaps."""

    failures = []
    receiver_results = {}
    if len(first_devices) != 4 or len(last_devices) != 4:
        return {
            "failures": [
                "receiver topology must contain exactly four devices in both samples"
            ],
            "observable_receivers": [],
            "write_only_receivers": [],
            "receivers": receiver_results,
        }

    observable = []
    write_only = []
    frame_deltas = []
    for index, (first, last) in enumerate(zip(first_devices, last_devices)):
        frame_delta = (
            int(last.get("frames_sent", 0) or 0)
            - int(first.get("frames_sent", 0) or 0)
        )
        frame_deltas.append(frame_delta)
        first_version = int(first.get("receiver_status_version", 0) or 0)
        last_version = int(last.get("receiver_status_version", 0) or 0)
        if first_version >= 2 and last_version >= 2:
            observable.append(index)
            device_failures = receiver_failures(first, last)
            failures.extend(
                f"receiver {index}: {failure}" for failure in device_failures
            )
            receiver_results[str(index)] = {
                "telemetry": "readable",
                "passed": not device_failures,
                **({"failures": device_failures} if device_failures else {}),
            }
            continue

        if allow_degraded_spi1 and index in DEGRADED_SPI1_WRITE_ONLY_DEVICES:
            result = evaluate_write_only_samples(
                [first, last], 1.0, require_progress=False,
            )
            if result["known_write_only_state"]:
                write_only.append(index)
            if not result["passed"]:
                failures.extend(
                    f"receiver {index}: {failure}" for failure in result["failures"]
                )
            receiver_results[str(index)] = result
            continue

        failure = f"receiver {index}: receiver status v2+ unavailable"
        failures.append(failure)
        receiver_results[str(index)] = {
            "telemetry": "unavailable",
            "passed": False,
            "failures": [failure],
        }

    if allow_degraded_spi1 and set(write_only) != set(DEGRADED_SPI1_WRITE_ONLY_DEVICES):
        failures.append(
            "degraded SPI1 return-path policy requires the exact write-only "
            "logical-device pair 2 and 3"
        )
    if allow_degraded_spi1 and len(set(frame_deltas)) != 1:
        failures.append(
            "host frame deltas differ across logical receivers: "
            f"{frame_deltas!r}"
        )
    return {
        "failures": failures,
        "observable_receivers": observable,
        "write_only_receivers": write_only,
        "receivers": receiver_results,
    }


def _wait_until_running(base_url, animation, timeout=5.0):
    deadline = time.monotonic() + timeout
    status = {}
    while time.monotonic() < deadline:
        status = _get_json(f"{base_url}/api/status")
        if status.get("is_running") and status.get("current_animation") == animation:
            return status
        time.sleep(0.1)
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://ledgridwall.local:5000")
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--animation", action="append", dest="animations")
    parser.add_argument(
        "--allow-degraded-spi1-return-path",
        action="store_true",
        help=(
            "temporary installed-wall policy: require full telemetry on logical "
            "receivers 0 and 1 and exact write-only host evidence on 2 and 3"
        ),
    )
    args = parser.parse_args()

    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be finite and greater than zero")

    base_url = args.base_url.rstrip("/")
    snapshot = capture_scene(base_url, _get_json)
    results = []
    run_failure = None
    cleanup_failure = None
    try:
        registry = _get_json(f"{base_url}/api/animations")
        animations = args.animations or sorted(
            item["plugin_name"] for item in registry if item.get("plugin_name")
        )

        for animation in animations:
            _post_json(f"{base_url}/api/start/{animation}", {})
            status = _wait_until_running(base_url, animation)
            failures = []
            if not status.get("is_running") or status.get("current_animation") != animation:
                failures.append(
                    f"did not enter running state (current={status.get('current_animation')!r})"
                )

            first_metrics = _get_json(f"{base_url}/api/metrics")
            time.sleep(max(0.1, args.seconds))
            last_metrics = _get_json(f"{base_url}/api/metrics")
            first_driver = first_metrics.get("driver", {})
            last_driver = last_metrics.get("driver", {})
            host_errors = (
                int(last_driver.get("aggregate", {}).get("errors", 0) or 0)
                - int(first_driver.get("aggregate", {}).get("errors", 0) or 0)
            )
            if host_errors:
                failures.append(f"host SPI errors increased by {host_errors}")

            receiver_result = evaluate_receiver_topology(
                first_driver.get("devices", []),
                last_driver.get("devices", []),
                allow_degraded_spi1=args.allow_degraded_spi1_return_path,
            )
            failures.extend(receiver_result["failures"])

            performance = last_metrics.get("performance", {})
            results.append({
                "animation": animation,
                "passed": not failures,
                "failures": failures,
                "actual_fps": round(
                    float(last_metrics.get("animation", {}).get("actual_fps", 0) or 0), 2
                ),
                "generate_p95_ms": round(float(performance.get("p95_generate_ms", 0) or 0), 3),
                "host_spi_errors_delta": host_errors,
                "observable_receivers": receiver_result["observable_receivers"],
                "write_only_receivers": receiver_result["write_only_receivers"],
                "telemetry_complete": len(
                    receiver_result["observable_receivers"]
                ) == 4,
                "visual_verification_required": (
                    args.allow_degraded_spi1_return_path
                ),
                "receivers": receiver_result["receivers"],
            })
    except Exception as exc:
        run_failure = str(exc)
    finally:
        try:
            restore_scene(
                base_url, snapshot, get_json=_get_json, post_json=_post_json,
                delete_json=_delete_json,
            )
        except Exception as exc:
            cleanup_failure = str(exc)

    output = {
        "passed": (
            all(item["passed"] for item in results)
            and run_failure is None and cleanup_failure is None
        ),
        "animations": results,
        "scene_restored": cleanup_failure is None,
        "acceptance_policy": {
            "name": (
                "temporary_degraded_spi1_return_path"
                if args.allow_degraded_spi1_return_path
                else "strict_all_receiver_telemetry"
            ),
            "enabled": args.allow_degraded_spi1_return_path,
            "telemetry_complete": not args.allow_degraded_spi1_return_path,
            "visual_verification_required": args.allow_degraded_spi1_return_path,
            "miso_dependent_gates_deferred": args.allow_degraded_spi1_return_path,
        },
    }
    if args.allow_degraded_spi1_return_path:
        output["warnings"] = [
            "DEGRADED ACCEPTANCE: receivers 2 and 3 have no MISO telemetry; "
            "host outbound traffic does not prove receiver or physical display output"
        ]
    if run_failure:
        output["failure"] = run_failure
    if cleanup_failure:
        output["cleanup_failure"] = cleanup_failure
    print(json.dumps(output, indent=2, sort_keys=True))
    raise SystemExit(0 if output["passed"] else 1)


if __name__ == "__main__":
    main()
