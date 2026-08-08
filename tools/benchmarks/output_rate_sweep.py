#!/usr/bin/env python3
"""Step live output rates and correlate visual behavior with pipeline telemetry."""

from __future__ import annotations

import argparse
import json
import time
from urllib import request


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
    parser.add_argument("--restore", type=int, default=160)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    rates = [int(value) for value in args.rates.split(',') if value.strip()]
    _post_json(f"{base_url}/api/start/{args.animation}", {})
    results = []
    try:
        for rate in rates:
            _post_json(f"{base_url}/api/config/target-fps", {"target_fps": rate})
            first = _wait_for_target(base_url, rate)
            time.sleep(max(1.0, args.seconds))
            last = _get_json(f"{base_url}/api/metrics")
            first_devices = first.get("driver", {}).get("devices", [])
            last_devices = last.get("driver", {}).get("devices", [])
            receivers = []
            for index, (before, after) in enumerate(zip(first_devices, last_devices)):
                if int(after.get("receiver_status_version", 0) or 0) != 2:
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
    finally:
        _post_json(
            f"{base_url}/api/config/target-fps", {"target_fps": args.restore}
        )

    print(json.dumps({"animation": args.animation, "rates": results}, indent=2))


if __name__ == "__main__":
    main()
