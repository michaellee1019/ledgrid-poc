#!/usr/bin/env python3
"""Fail unless all four receivers are ready for signed native animations."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable


CAPABILITY_NATIVE = 1 << 0
CAPABILITY_FRAME_TRACK = 1 << 1
CAPABILITY_SIGNED_PACKAGES = 1 << 2
CAPABILITY_ASSET_UPLOAD = 1 << 3
CAPABILITY_TYPED_PARAMETERS = 1 << 6
CAPABILITY_LOGICAL_DEVICE_IDENTITY = 1 << 18
REQUIRED = (
    CAPABILITY_NATIVE | CAPABILITY_FRAME_TRACK | CAPABILITY_SIGNED_PACKAGES |
    CAPABILITY_ASSET_UPLOAD | CAPABILITY_TYPED_PARAMETERS |
    CAPABILITY_LOGICAL_DEVICE_IDENTITY
)


def validate_status(
    payload: Any, *, min_updated_at: float | None = None
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("status response is not an object")
    if min_updated_at is not None:
        updated_at = payload.get("updated_at", payload.get("written_at"))
        if (
            not isinstance(updated_at, (int, float))
            or isinstance(updated_at, bool)
            or float(updated_at) < min_updated_at
        ):
            raise ValueError(
                "status response predates the current controller restart"
            )
    driver = payload.get("driver_stats")
    devices = driver.get("devices") if isinstance(driver, dict) else None
    if not isinstance(devices, list) or len(devices) != 4:
        raise ValueError("status must contain exactly four receiver devices")
    summary = []
    errors: list[str] = []
    missing_status: list[int] = []
    for index, device in enumerate(devices):
        if not isinstance(device, dict):
            errors.append(f"receiver {index} status is not an object")
            continue
        version = int(device.get("receiver_status_version", 0) or 0)
        capabilities = int(device.get("receiver_capabilities", 0) or 0)
        logical_device = device.get("receiver_logical_device")
        missing = REQUIRED & ~capabilities
        if version != 3:
            errors.append(
                f"receiver {index} has status version {version}, expected 3"
            )
            if version == 0 and not device.get("receiver_status_seen", False):
                missing_status.append(index)
            continue
        if logical_device != index:
            errors.append(
                f"receiver {index} reports logical identity {logical_device!r}"
            )
            continue
        if missing:
            errors.append(
                f"receiver {index} lacks capabilities 0x{missing:x} "
                f"(reported 0x{capabilities:x})"
            )
            continue
        summary.append({
            "logical_device": index,
            "capabilities": capabilities,
            "cache_free_bytes": int(device.get("receiver_cache_free_bytes", 0) or 0),
        })
    if missing_status == [2, 3]:
        errors.append(
            "receivers 2 and 3 share SPI1: verify both ESP32 GPIO 13 MISO "
            "connections reach Pi GPIO 19 / physical pin 35"
        )
    if errors:
        raise ValueError("; ".join(errors))
    return summary


def wait_for_status(
    fetch_status: Callable[[], Any],
    *,
    wait_seconds: float = 0.0,
    interval_seconds: float = 0.5,
    min_updated_at: float | None = None,
) -> list[dict[str, Any]]:
    """Retry transient startup snapshots, preserving the final diagnosis."""
    if wait_seconds < 0 or interval_seconds < 0:
        raise ValueError("readiness wait and interval must be non-negative")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return validate_status(
                fetch_status(), min_updated_at=min_updated_at
            )
        except (OSError, ValueError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(interval_seconds, max(0.0, deadline - time.monotonic())))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("status", nargs="?", type=Path)
    parser.add_argument("--url")
    parser.add_argument("--wait-seconds", type=float, default=0.0)
    parser.add_argument("--interval-seconds", type=float, default=0.5)
    parser.add_argument("--min-updated-at", type=float)
    args = parser.parse_args(argv)
    try:
        if args.url and args.status:
            raise ValueError("choose either a status file or --url")
        if args.wait_seconds and not args.url:
            raise ValueError("--wait-seconds requires --url")
        if args.url:
            def fetch_status() -> Any:
                with urllib.request.urlopen(args.url, timeout=5.0) as response:
                    return json.load(response)
        elif args.status:
            def fetch_status() -> Any:
                return json.loads(args.status.read_text(encoding="utf-8"))
        else:
            def fetch_status() -> Any:
                return json.load(sys.stdin)
        summary = wait_for_status(
            fetch_status,
            wait_seconds=args.wait_seconds,
            interval_seconds=args.interval_seconds,
            min_updated_at=args.min_updated_at,
        )
        print(json.dumps({"receivers": summary}, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
