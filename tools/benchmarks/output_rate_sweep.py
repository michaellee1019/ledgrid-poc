#!/usr/bin/env python3
"""Measure host cadence and protected receiver counters without changing playback."""
import argparse
import json
import math
import statistics
import time
from urllib.request import urlopen


DEFAULT_URL = "http://ledgridwall5.local:5000"
TELEMETRY_PATH = "/api/v1/composer/operations/telemetry"
WALL_RECEIVERS = range(5)
ERROR_COUNTERS = (
    "errors",
    "receiver_crc_errors",
    "receiver_status_integrity_errors",
    "receiver_spi_queue_errors",
    "receiver_publish_drops",
    "receiver_display_errors",
)
DISPLAY_COUNTER = "receiver_frames_displayed"
SUPERSEDED_COUNTER = "receiver_frames_superseded"


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def indexed_receivers(observation):
    devices = observation["diagnostics"]["driver_stats"].get("devices", [])
    by_index = {device["device_index"]: device for device in devices}
    if len(by_index) != len(devices):
        raise ValueError("Duplicate receiver index in telemetry")
    return by_index


def receiver_interval(index, first, last, start, end):
    result = {
        "receiver": index,
        "spi_route": last.get("spi_route"),
        "spi_speed_hz": last.get("spi_speed_hz"),
        "connected": last.get("receiver_connected") is True,
        "display_counter_fps": None,
        "error_deltas": {},
    }
    for device, controller_time in ((first, start), (last, end)):
        seen = device.get("receiver_status_observed_at")
        if (
            device.get("receiver_connected") is not True
            or device.get("receiver_status_integrity_verified") is not True
            or not finite_number(seen)
            or not 0 <= controller_time - seen < 10
        ):
            return dict(result, evidence="missing or stale protected status")
    if not first.get("spi_route") or first["spi_route"] != last.get("spi_route"):
        return dict(result, evidence="SPI route changed or missing")
    if first.get("spi_speed_hz") != last.get("spi_speed_hz"):
        return dict(result, evidence="SPI speed changed during interval")
    elapsed = last["receiver_status_observed_at"] - first["receiver_status_observed_at"]
    if elapsed <= 0:
        return dict(result, evidence="protected status did not advance")
    deltas = {}
    for key in (*ERROR_COUNTERS, DISPLAY_COUNTER, SUPERSEDED_COUNTER):
        before, after = first.get(key), last.get(key)
        if type(before) is not int or type(after) is not int or not 0 <= before <= after:
            return dict(result, evidence=f"counter missing or reset: {key}")
        deltas[key] = after - before
    result.update(
        counter_interval_seconds=elapsed,
        frames_displayed=deltas[DISPLAY_COUNTER],
        frames_superseded=deltas[SUPERSEDED_COUNTER],
        error_deltas={key: deltas[key] for key in ERROR_COUNTERS},
    )
    if deltas["receiver_status_integrity_errors"]:
        return dict(result, evidence="protected status rejected during interval")
    return dict(result, evidence="verified", display_counter_fps=deltas[DISPLAY_COUNTER] / elapsed)


def summarize(observations, min_fps=None):
    first, last = observations[0], observations[-1]
    controllers = [item["controller"] for item in observations]
    initial, final = controllers[0], controllers[-1]
    start, end = initial.get("updated_at"), final.get("updated_at")
    sample_times = {item["updated_at"] for item in controllers if finite_number(item.get("updated_at"))}
    stable = (
        finite_number(start)
        and finite_number(end)
        and end > start
        and bool(initial.get("controller_session_id"))
        and all(
            item.get(key) == initial.get(key)
            for item in controllers
            for key in ("controller_session_id", "controller_state_revision", "current_identity_digest",
                        "current_animation", "is_running", "target_fps", "brightness")
        )
    )
    rates = {
        item["updated_at"]: item["actual_fps"]
        for item in controllers
        if finite_number(item.get("updated_at")) and finite_number(item.get("actual_fps"))
    }
    before, after = indexed_receivers(first), indexed_receivers(last)
    receivers = [
        receiver_interval(index, before.get(index, {}), after.get(index, {}), start, end)
        if stable else {
            "receiver": index,
            "display_counter_fps": None,
            "evidence": "controller changed or did not publish a new observation",
        }
        for index in WALL_RECEIVERS
    ]
    complete = (
        stable
        and set(before) == set(after) == set(WALL_RECEIVERS)
        and all(item["display_counter_fps"] is not None for item in receivers)
    )
    passed = (
        complete
        and initial.get("is_running") is True
        and all(not any(item["error_deltas"].values()) for item in receivers)
        and all(item["display_counter_fps"] >= min_fps for item in receivers)
    ) if min_fps is not None else None
    return {
        "observation": "controller cadence and receiver counters; visual smoothness requires observation",
        "controller": {
            "animation": final.get("current_animation"),
            "is_running": final.get("is_running"),
            "brightness": final.get("brightness"),
            "target_fps": final.get("target_fps"),
            "published_samples": len(sample_times),
            "mean_tick_fps": statistics.mean(rates.values()) if stable and rates else None,
        },
        "receiver_rates": receivers,
        "complete": complete,
        "min_display_counter_fps": min_fps,
        "passed": passed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_URL)
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--min-fps", type=float, help="Require this display-counter rate and zero new errors on all five receivers")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 3600:
        parser.error("--seconds must be finite and between 0 and 3600")
    if args.min_fps is not None and (not math.isfinite(args.min_fps) or args.min_fps <= 0):
        parser.error("--min-fps must be finite and positive")

    url = args.base_url.rstrip("/") + TELEMETRY_PATH
    observations = []
    deadline = time.monotonic() + args.seconds
    try:
        while True:
            with urlopen(url, timeout=5) as response:
                observations.append(json.load(response))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1, remaining))
        result = summarize(observations, args.min_fps)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Cannot measure wall telemetry at {url}: {exc}\n")
    print(json.dumps(result))
    return int(result["passed"] is False)


if __name__ == "__main__":
    raise SystemExit(main())
