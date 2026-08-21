#!/usr/bin/env python3
"""Build and benchmark a repository-native background on the host peer."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from animation.native.builder import build_plugin  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Host-proxy benchmark for a pinned receiver-native build"
    )
    parser.add_argument("--plugin", default="aurora_curtains_native")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPOSITORY_ROOT / "run_state/native_background_builds",
    )
    parser.add_argument("--max-p95-ms", type=float, default=4.0)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = build_plugin(
        REPOSITORY_ROOT, args.plugin, args.output_root, execute=True
    )
    assert result.bundle_path is not None
    assert result.payload_path is not None
    assert result.preview_path is not None
    assert result.default_timing is not None
    assert result.stress_timing is not None
    report = {
        "scope": "host preview proxy; not ESP32 hardware",
        "plugin_id": args.plugin,
        "bundle_digest": result.bundle_digest,
        "payload_digest": result.payload_digest,
        "bundle_bytes": result.bundle_path.stat().st_size,
        "payload_bytes": result.payload_path.stat().st_size,
        "preview_bytes": result.preview_path.stat().st_size,
        "default": {
            **asdict(result.default_timing),
            "missed_deadlines": result.default_missed_deadlines,
            "changed_frames": result.default_changed_frames,
            "total_frames": result.default_total_frames,
            "changed_frame_ratio": (
                result.default_changed_frames / result.default_total_frames
                if result.default_changed_frames is not None
                and result.default_total_frames
                else None
            ),
        },
        "stress": {
            **asdict(result.stress_timing),
            "missed_deadlines": result.stress_missed_deadlines,
            "changed_frames": result.stress_changed_frames,
            "total_frames": result.stress_total_frames,
            "changed_frame_ratio": (
                result.stress_changed_frames / result.stress_total_frames
                if result.stress_changed_frames is not None
                and result.stress_total_frames
                else None
            ),
        },
        "acceptance": {
            "max_p95_ms": args.max_p95_ms,
            "passed": (
                result.default_timing.p95_ms < args.max_p95_ms
                and result.stress_timing.p95_ms < args.max_p95_ms
                and result.default_missed_deadlines == 0
                and result.stress_missed_deadlines == 0
                and result.default_changed_frames == result.default_total_frames
                and result.stress_changed_frames == result.stress_total_frames
            ),
        },
    }
    if args.json:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if args.check and not report["acceptance"]["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
