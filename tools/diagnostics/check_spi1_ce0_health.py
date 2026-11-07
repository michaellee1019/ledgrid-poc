#!/usr/bin/env python3
"""Verify SPI1 CE0 (logical receiver 0, columns 1-8) keeps up with SPI1 CE1."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from drivers.led_layout import WALL_DEVICE_MAP, WALL_RECEIVER_GLOBAL_STRIP_OFFSETS

TELEMETRY_URL = "http://127.0.0.1:5000/api/v1/composer/operations/telemetry"
LOGICAL_SPI1_CE0 = 0
LOGICAL_SPI1_PEER = 1
MIN_DISPLAY_RATIO = 0.85
MIN_DISPLAYED_DELTA = 40
MIN_BRIGHTNESS_LPC = 2  # SET_BRIGHTNESS


def _fetch(url: str) -> Mapping[str, Any]:
    with urllib.request.urlopen(url, timeout=15) as response:
        payload = json.load(response)
    if not isinstance(payload, Mapping):
        raise RuntimeError("telemetry response is not an object")
    return payload


def _receiver_stats(payload: Mapping[str, Any], logical_id: int) -> Mapping[str, Any]:
    devices = payload["diagnostics"]["driver_stats"]["devices"]
    if logical_id >= len(devices):
        raise RuntimeError(f"logical receiver {logical_id} missing from telemetry")
    return devices[logical_id]


def _sample(url: str) -> tuple[int, int, int, int]:
    payload = _fetch(url)
    ce0 = _receiver_stats(payload, LOGICAL_SPI1_CE0)
    peer = _receiver_stats(payload, LOGICAL_SPI1_PEER)
    return (
        int(ce0["receiver_frames_displayed"]),
        int(ce0["receiver_crc_errors"]),
        int(ce0["receiver_last_processed_command"]),
        int(peer["receiver_frames_displayed"]),
    )


def check_live(url: str, window_seconds: float) -> dict[str, Any]:
    start_disp, start_crc, start_lpc, start_peer = _sample(url)
    time.sleep(window_seconds)
    end_disp, end_crc, end_lpc, end_peer = _sample(url)
    peer_delta = max(1, end_peer - start_peer)
    disp_delta = end_disp - start_disp
    ratio = disp_delta / peer_delta
    report = {
        "mode": "live",
        "window_seconds": window_seconds,
        "ce0_displayed_delta": disp_delta,
        "peer_displayed_delta": end_peer - start_peer,
        "display_ratio": ratio,
        "ce0_crc_delta": end_crc - start_crc,
        "ce0_lpc_start": start_lpc,
        "ce0_lpc_end": end_lpc,
        "pass": (
            disp_delta >= MIN_DISPLAYED_DELTA
            and ratio >= MIN_DISPLAY_RATIO
            and end_lpc in (MIN_BRIGHTNESS_LPC, 6)  # 6 = SET_ALL during active playback
        ),
    }
    return report


def check_isolated(repo_root: str, frames: int) -> dict[str, Any]:
    route3 = WALL_DEVICE_MAP[LOGICAL_SPI1_CE0]
    route_peer = WALL_DEVICE_MAP[LOGICAL_SPI1_PEER]
    script = f"""
import numpy as np
from drivers.spi_controller import LEDController
frame = np.zeros((1104, 3), dtype=np.uint8)
frame[:, 0] = 255

def probe(bus, device, logical, offset, strip_count, fec):
    c = LEDController(
        bus=bus, device=device, speed=20_000_000, mode=0, strips=strip_count, leds_per_strip=138,
        fec_transport=fec, logical_device_id=logical, global_strip_offset=offset,
    )
    c.configure()
    c.set_stagger_phases(3)
    c.set_brightness(200)
    b = c.get_stats()["receiver_frames_displayed"]
    for _ in range({frames}):
        c.set_all_pixels(frame)
    c.query_fresh_receiver_status()
    s = c.get_stats()
    c.close()
    return s["receiver_frames_displayed"] - b, s["receiver_crc_errors"]

logical3 = probe({route3[0]}, {route3[1]}, 0, {WALL_RECEIVER_GLOBAL_STRIP_OFFSETS[LOGICAL_SPI1_CE0]}, 8, False)
peer = probe({route_peer[0]}, {route_peer[1]}, 1, {WALL_RECEIVER_GLOBAL_STRIP_OFFSETS[LOGICAL_SPI1_PEER]}, 8, False)
import json as _json
print(_json.dumps({{"logical3": logical3, "peer": peer, "route3": [{route3[0]}, {route3[1]}]}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(result.stdout.strip())
    ce0_disp, ce0_crc = parsed["logical3"]
    ce1_disp, ce1_crc = parsed["peer"]
    ratio = ce0_disp / max(1, ce1_disp)
    report = {
        "mode": "isolated",
        "frames": frames,
        "logical3_route": parsed.get("route3"),
        "logical3_displayed_delta": ce0_disp,
        "peer_displayed_delta": ce1_disp,
        "display_ratio": ratio,
        "logical3_crc_errors": ce0_crc,
        "peer_crc_errors": ce1_crc,
        "pass": ratio >= MIN_DISPLAY_RATIO and ce0_disp >= MIN_DISPLAYED_DELTA,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=TELEMETRY_URL)
    parser.add_argument("--window", type=float, default=20.0)
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="Stop-service direct SPI probe (run on the Pi with service stopped)",
    )
    parser.add_argument("--repo", default=".", help="Repo root for isolated mode")
    parser.add_argument("--frames", type=int, default=100)
    args = parser.parse_args(argv)
    if args.isolated:
        report = check_isolated(args.repo, args.frames)
    else:
        if args.window <= 0:
            parser.error("--window must be positive")
        report = check_live(args.url, args.window)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
