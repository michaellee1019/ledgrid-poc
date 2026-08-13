#!/usr/bin/env python3
"""Run and evaluate the receiver-side hardware acceptance gates."""

from __future__ import annotations

import argparse
import json
import math
import time
from urllib import request


CAPABILITY_STATIC_LOCAL_BACKGROUND = 1 << 0
CAPABILITY_PRESENTATION_CONTEXT_V1 = 1 << 1
CAPABILITY_STATUS_V3 = 1 << 2
CAPABILITY_EXPLICIT_BASE_OWNERSHIP = 1 << 3


def evaluate_phase3a_status(
    devices,
    *,
    refresh=None,
    expected_refresh_id=None,
    receiver_count=4,
    local_canary_device=None,
):
    """Evaluate fresh receiver-reported Phase 3A identity and capabilities."""

    failures = []
    if expected_refresh_id is not None:
        if not isinstance(refresh, dict):
            failures.append("fresh receiver-status proof is unavailable")
        else:
            if refresh.get("request_id") != expected_refresh_id:
                failures.append("receiver-status proof is stale")
            if not refresh.get("passed"):
                failures.append("fresh receiver-status query did not pass on every board")
            if not isinstance(refresh.get("completed_at"), (int, float)):
                failures.append("receiver-status proof has no completion timestamp")
    if not isinstance(devices, list) or len(devices) != receiver_count:
        actual = len(devices) if isinstance(devices, list) else 0
        failures.append(
            f"receiver telemetry has {actual} devices; expected {receiver_count}"
        )
        devices = devices if isinstance(devices, list) else []
    if local_canary_device is not None and not 0 <= local_canary_device < receiver_count:
        failures.append("local canary device is outside the receiver topology")

    for index, status in enumerate(devices[:receiver_count]):
        if not isinstance(status, dict):
            failures.append(f"receiver {index} status is unavailable")
            continue
        version = int(status.get("receiver_status_version", 0) or 0)
        capabilities = int(status.get("receiver_capabilities", 0) or 0)
        logical_id = status.get("receiver_logical_device")
        if version < 3:
            failures.append(f"receiver {index} reports status v{version}; v3 is required")
        required_status = CAPABILITY_STATUS_V3 | CAPABILITY_EXPLICIT_BASE_OWNERSHIP
        missing_status = required_status & ~capabilities
        if missing_status:
            failures.append(
                f"receiver {index} lacks Phase 3A status capabilities "
                f"0x{missing_status:08x}"
            )
        if logical_id != index:
            failures.append(
                f"receiver {index} reports logical identity {logical_id!r}"
            )
        if index == local_canary_device:
            required = (
                CAPABILITY_STATIC_LOCAL_BACKGROUND
                | CAPABILITY_PRESENTATION_CONTEXT_V1
            )
            missing = required & ~capabilities
            if missing:
                failures.append(
                    f"receiver {index} lacks local-canary capabilities 0x{missing:08x}"
                )
    return {"passed": not failures, "failures": failures}


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
    if any(int(sample.get("receiver_status_version", 0) or 0) < 2 for sample in samples):
        failures.append("receiver status v2+ was not present in every sample")
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
    parser.add_argument(
        "--phase3a-status-only",
        action="store_true",
        help="check fresh v3 ownership capability and receiver-reported identities",
    )
    parser.add_argument(
        "--local-canary-device",
        type=int,
        help="also require static-background/context capabilities on this receiver",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    if args.phase3a_status_only:
        accepted = _post_json(
            f"{base_url}/api/v1/receivers/status/refresh", {}
        )
        request_id = accepted.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise SystemExit("controller did not accept a receiver-status refresh")
        deadline = time.monotonic() + 10.0
        metrics = None
        refresh = None
        while time.monotonic() < deadline:
            metrics = _get_json(f"{base_url}/api/metrics")
            aggregate = metrics.get("driver", {}).get("aggregate", {})
            refresh = aggregate.get("receiver_status_refresh")
            if isinstance(refresh, dict) and refresh.get("request_id") == request_id:
                break
            time.sleep(0.1)
        devices = (metrics or {}).get("driver", {}).get("devices", [])
        result = evaluate_phase3a_status(
            devices,
            refresh=refresh,
            expected_refresh_id=request_id,
            local_canary_device=args.local_canary_device,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        raise SystemExit(0 if result["passed"] else 1)
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
