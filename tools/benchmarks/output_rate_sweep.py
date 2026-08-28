#!/usr/bin/env python3
"""Observe one pre-activated output rate without changing controller state."""

from __future__ import annotations

import argparse
import json
import math
import time
from urllib import request

if __package__:
    from tools.benchmarks.live_display_state import require_active_scene
else:  # Direct script execution from the documented Just recipe.
    from live_display_state import require_active_scene


def _get_json(url):
    with request.urlopen(url, timeout=5) as response:
        return json.load(response)


def _target_fps(metrics):
    return int(metrics.get("animation", {}).get("target_fps", 0) or 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://ledgridwall.local:5000")
    parser.add_argument("--rate", type=int, default=160)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--animation", default="rainbow")
    parser.add_argument("--expected-scene-digest", required=True)
    args = parser.parse_args()

    if not 1 <= args.rate <= 200:
        parser.error("--rate must be an integer from 1 to 200")
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be finite and greater than zero")

    base_url = args.base_url.rstrip("/")
    try:
        identity = require_active_scene(
            base_url, args.expected_scene_digest, _get_json,
            expected_plugin=args.animation, expected_provider="python",
        )
        first = _get_json(f"{base_url}/api/metrics")
        if _target_fps(first) != args.rate:
            raise RuntimeError(
                f"pre-activated target FPS is {_target_fps(first)}, expected {args.rate}; "
                "change it through the guarded operator surface before measuring"
            )
        time.sleep(args.seconds)
        last = _get_json(f"{base_url}/api/metrics")
        require_active_scene(
            base_url, args.expected_scene_digest, _get_json,
            expected_plugin=args.animation, expected_provider="python",
        )
        if _target_fps(last) != args.rate:
            raise RuntimeError("target FPS changed during the observation window")
        first_devices = first.get("driver", {}).get("devices", [])
        last_devices = last.get("driver", {}).get("devices", [])
        receivers = []
        for index, (before, after) in enumerate(zip(first_devices, last_devices)):
            if int(after.get("receiver_status_version", 0) or 0) < 2:
                continue
            receivers.append({
                "device": index,
                "displayed_fps": round(
                    (int(after.get("receiver_frames_displayed", 0) or 0)
                     - int(before.get("receiver_frames_displayed", 0) or 0))
                    / args.seconds,
                    2,
                ),
                "crc_error_delta": int(after.get("receiver_crc_errors", 0) or 0)
                - int(before.get("receiver_crc_errors", 0) or 0),
                "display_error_delta": int(after.get("receiver_display_errors", 0) or 0)
                - int(before.get("receiver_display_errors", 0) or 0),
            })
        output = {
            "passed": True,
            "observation_only": True,
            "active_identity": identity.__dict__,
            "target_fps": args.rate,
            "actual_fps": round(
                float(last.get("animation", {}).get("actual_fps", 0) or 0), 2
            ),
            "receivers": receivers,
        }
    except Exception as exc:
        output = {
            "passed": False,
            "observation_only": True,
            "failure": str(exc),
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    raise SystemExit(0 if output["passed"] else 1)


if __name__ == "__main__":
    main()
