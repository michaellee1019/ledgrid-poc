#!/usr/bin/env python3
"""Retain read-only controller evidence, including failed or unavailable endpoints.

This is a diagnostic capture, not an activation or physical acceptance result.
Each completed sample is flushed to a new JSONL file before the next request.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.parse import quote
from urllib.request import urlopen


ENDPOINTS = (
    "/api/v1/scene",
    "/api/v1/composer/settings/observed",
    "/api/v1/composer/operations/telemetry",
)


def get_json(url: str) -> object:
    with urlopen(url, timeout=10) as response:
        return json.load(response)


def capture(base_url: str, output: Path, *, samples: int = 2,
            interval: float = 1.0, activation_id: str | None = None,
            fetch=get_json, sleep=time.sleep) -> int:
    """Return endpoint failure count; never overwrite prior evidence."""
    if samples < 1 or not 0 <= interval <= 60:
        raise ValueError("samples must be positive and interval within 0..60 seconds")
    endpoints = list(ENDPOINTS)
    if activation_id:
        endpoints.append("/api/v1/scene/activations/" + quote(activation_id, safe=""))
    output.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    with output.open("x", encoding="utf-8") as handle:
        for sample in range(samples):
            for endpoint in endpoints:
                record = {
                    "schema": "ledgrid.native-activation-diagnostic",
                    "schema_version": 1,
                    "evidence_kind": "controller_status_only_not_acceptance",
                    "base_url": base_url.rstrip("/"),
                    "sample": sample,
                    "endpoint": endpoint,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
                started = time.monotonic()
                try:
                    record["payload"] = fetch(base_url.rstrip("/") + endpoint)
                except Exception as exc:
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    failures += 1
                record["elapsed_seconds"] = time.monotonic() - started
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            if sample + 1 < samples:
                sleep(interval)
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://ledgridwall.local:5000")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--activation-id")
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    if not 1 <= args.samples <= 600 or not 0 <= args.interval <= 60:
        parser.error("samples must be 1..600 and interval must be 0..60 seconds")
    failures = capture(args.base_url, args.output, samples=args.samples,
                       interval=args.interval, activation_id=args.activation_id)
    print(json.dumps({"output": str(args.output), "endpoint_failures": failures,
                      "acceptance_claimed": False}))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
