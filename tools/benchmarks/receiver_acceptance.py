#!/usr/bin/env python3
"""Run and evaluate the receiver-side hardware acceptance gates."""

from __future__ import annotations

import argparse
import json
import math
import time
from urllib import request


def _percentile(values, ratio):
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1)
    return ordered[index]


def evaluate_samples(samples, elapsed_seconds, min_displayed_fps=180.0):
    if len(samples) < 2 or elapsed_seconds <= 0:
        return {"passed": False, "failures": ["insufficient samples"]}

    first = samples[0]
    last = samples[-1]

    def delta(key):
        return int(last.get(key, 0) or 0) - int(first.get(key, 0) or 0)

    accepted = delta("receiver_frames_accepted")
    displayed = delta("receiver_frames_displayed")
    superseded = delta("receiver_frames_superseded")
    displayed_fps = displayed / elapsed_seconds
    outstanding = (
        int(last.get("receiver_frames_accepted", 0) or 0)
        - int(last.get("receiver_frames_displayed", 0) or 0)
        - int(last.get("receiver_frames_superseded", 0) or 0)
    )
    encode_p95 = _percentile(
        [int(sample.get("receiver_last_encode_us", 0) or 0) for sample in samples],
        0.95,
    )
    show_p95 = _percentile(
        [int(sample.get("receiver_last_show_us", 0) or 0) for sample in samples],
        0.95,
    )

    failures = []
    if any(int(sample.get("receiver_status_version", 0) or 0) != 2 for sample in samples):
        failures.append("receiver status v2 was not present in every sample")
    for key, label in (
        ("receiver_crc_errors", "CRC errors"),
        ("receiver_publish_drops", "mailbox publish drops"),
        ("receiver_spi_queue_errors", "SPI queue errors"),
        ("receiver_display_errors", "display errors"),
        ("receiver_status_misses", "missing receiver status responses"),
    ):
        if delta(key) != 0:
            failures.append(f"{label} increased by {delta(key)}")
    if show_p95 > 4800:
        failures.append(f"display DMA p95 {show_p95} us exceeds 4800 us")
    if encode_p95 > 1000:
        failures.append(f"frame encode p95 {encode_p95} us exceeds 1000 us")
    if displayed_fps < min_displayed_fps:
        failures.append(
            f"displayed rate {displayed_fps:.1f} FPS is below "
            f"{min_displayed_fps:g} FPS"
        )
    if accepted <= 0:
        failures.append("no frames were accepted")
    elif displayed + superseded < max(0, accepted - 3):
        failures.append(
            f"accepted accounting is incomplete: {accepted} accepted, "
            f"{displayed} displayed, {superseded} superseded"
        )
    if outstanding < 0 or outstanding > 3:
        failures.append(f"mailbox outstanding count {outstanding} is outside 0..3")

    return {
        "passed": not failures,
        "failures": failures,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "accepted_delta": accepted,
        "displayed_delta": displayed,
        "superseded_delta": superseded,
        "displayed_fps": round(displayed_fps, 2),
        "encode_p95_us": encode_p95,
        "show_p95_us": show_p95,
        "outstanding_frames": outstanding,
    }


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://ledgridwall.local:5000")
    parser.add_argument(
        "--device",
        type=int,
        action="append",
        dest="devices",
        help="logical receiver index; repeat to evaluate multiple receivers in one run",
    )
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--warmup", type=float, default=3.0)
    parser.add_argument("--animation", default="rainbow")
    parser.add_argument("--min-displayed-fps", type=float, default=180.0)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    if args.animation:
        _post_json(f"{base_url}/api/start/{args.animation}", {})
    time.sleep(args.warmup)

    devices_to_check = args.devices or [0]
    samples = {device: [] for device in devices_to_check}
    started = time.monotonic()
    while time.monotonic() - started < args.duration:
        metrics = _get_json(f"{base_url}/api/metrics")
        devices = metrics.get("driver", {}).get("devices", [])
        for device in devices_to_check:
            if device >= len(devices):
                raise SystemExit(
                    f"device index {device} is unavailable; metrics has {len(devices)} devices"
                )
            samples[device].append(devices[device])
        time.sleep(args.interval)

    elapsed = time.monotonic() - started
    device_results = {
        str(device): evaluate_samples(
            device_samples, elapsed, min_displayed_fps=args.min_displayed_fps
        )
        for device, device_samples in samples.items()
    }
    if len(device_results) == 1:
        result = next(iter(device_results.values()))
    else:
        result = {
            "passed": all(item["passed"] for item in device_results.values()),
            "devices": device_results,
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
