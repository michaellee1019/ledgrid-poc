#!/usr/bin/env python3
"""Retired multi-scene mutation sweep; retained for safe operator guidance."""

from __future__ import annotations

import argparse
import math

if __package__:
    from tools.benchmarks.receiver_acceptance import (
        DEGRADED_SPI1_WRITE_ONLY_DEVICES,
        INSTALLED_RECEIVER_COUNT,
        evaluate_write_only_samples,
    )
else:  # Direct script execution from the documented Just recipes.
    from receiver_acceptance import (
        DEGRADED_SPI1_WRITE_ONLY_DEVICES,
        INSTALLED_RECEIVER_COUNT,
        evaluate_write_only_samples,
    )


ERROR_COUNTERS = (
    ("receiver_crc_errors", "CRC errors"),
    ("receiver_publish_drops", "publish drops"),
    ("receiver_spi_queue_errors", "SPI queue errors"),
    ("receiver_display_errors", "display errors"),
    ("receiver_status_misses", "status misses"),
)


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
    """Evaluate the exact installed receiver roster without dropping telemetry gaps."""

    failures = []
    receiver_results = {}
    if (len(first_devices) != INSTALLED_RECEIVER_COUNT
            or len(last_devices) != INSTALLED_RECEIVER_COUNT):
        return {
            "failures": [
                "receiver topology must contain exactly "
                f"{INSTALLED_RECEIVER_COUNT} devices in both samples"
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
    if (
        allow_degraded_spi1
        and frame_deltas
        and max(frame_deltas) - min(frame_deltas) > 1
    ):
        failures.append(
            "host frame deltas differ by more than one in-flight frame across "
            "logical receivers: "
            f"{frame_deltas!r}"
        )
    return {
        "failures": failures,
        "observable_receivers": observable,
        "write_only_receivers": write_only,
        "receivers": receiver_results,
    }


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
            "receivers 0, 1, and 4 and exact write-only host evidence on 2 and 3"
        ),
    )
    args = parser.parse_args()

    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be finite and greater than zero")

    parser.error(
        "multi-scene live sweep is retired and made no wall changes; activate "
        "one exact scene through Composer Check + guarded activation, then run "
        "the observation-only receiver acceptance for its receipt-bound digest"
    )


if __name__ == "__main__":
    main()
