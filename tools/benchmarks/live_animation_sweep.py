#!/usr/bin/env python3
"""Start every registered animation and verify live wall pipeline integrity."""

from __future__ import annotations

import argparse
import json
import time
from urllib import request


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


def receiver_failures(first, last):
    failures = []
    if int(last.get("receiver_status_version", 0) or 0) != 2:
        return ["receiver status v2 unavailable"]
    for key, label in ERROR_COUNTERS:
        delta = int(last.get(key, 0) or 0) - int(first.get(key, 0) or 0)
        if delta:
            failures.append(f"{label} increased by {delta}")
    return failures


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
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    registry = _get_json(f"{base_url}/api/animations")
    animations = args.animations or sorted(
        item["plugin_name"] for item in registry if item.get("plugin_name")
    )
    results = []

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

        observable = 0
        for index, (first, last) in enumerate(zip(
            first_driver.get("devices", []), last_driver.get("devices", [])
        )):
            if int(last.get("receiver_status_version", 0) or 0) != 2:
                continue
            observable += 1
            failures.extend(
                f"receiver {index}: {failure}"
                for failure in receiver_failures(first, last)
            )
        if observable == 0:
            failures.append("no receiver status v2 telemetry was observable")

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
            "observable_receivers": observable,
        })

    output = {"passed": all(item["passed"] for item in results), "animations": results}
    print(json.dumps(output, indent=2, sort_keys=True))
    raise SystemExit(0 if output["passed"] else 1)


if __name__ == "__main__":
    main()
