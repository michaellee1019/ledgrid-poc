#!/usr/bin/env python3
"""Capture fixed-webcam LED-wall references and run perspective correction."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import request


def post_json(base_url: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=10) as response:
        return json.load(response)


def wait_for_pattern(base_url: str, pattern_name: str, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        with request.urlopen(f"{base_url.rstrip('/')}/api/status", timeout=10) as response:
            last = json.load(response)
        stats = last.get("animation_stats", {})
        if last.get("current_animation") == "plant_calibration" and stats.get("current_pattern_name") == pattern_name:
            return
        time.sleep(0.15)
    raise RuntimeError(f"Controller did not enter {pattern_name!r}; last status: {last}")


def capture_frame(ffmpeg: str, camera_input: str, resolution: str, settle_frames: int, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "avfoundation",
            "-framerate",
            "30",
            "-video_size",
            resolution,
            "-pixel_format",
            "nv12",
            "-i",
            camera_input,
            "-vf",
            f"select=eq(n\\,{settle_frames})",
            "-frames:v",
            "1",
            "-update",
            "1",
            "-q:v",
            "2",
            "-y",
            str(output),
        ],
        check=True,
    )


def show_pattern(base_url: str, index: int, name: str, brightness: float) -> None:
    post_json(
        base_url,
        "/api/start/plant_calibration",
        {"manual_pattern_index": index, "brightness": brightness, "transition_seconds": 0.0},
    )
    wait_for_pattern(base_url, name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://ledgridwall.local:5000")
    parser.add_argument("--config", default="config/webcam_wall_calibration.json")
    parser.add_argument("--output-dir", default="calibration_photos")
    parser.add_argument("--prefix", default=datetime.now().strftime("webcam-%Y%m%d-%H%M%S"))
    parser.add_argument("--camera-input", default="0:none")
    parser.add_argument("--resolution", default="1920x1080")
    parser.add_argument("--settle-frames", type=int, default=60)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    paths = {
        name: output_dir / f"{args.prefix}-{name}.jpg"
        for name in ("off", "orientation", "white", "dimension")
    }

    post_json(args.base_url, "/api/stop", {})
    time.sleep(0.5)
    capture_frame(args.ffmpeg, args.camera_input, args.resolution, args.settle_frames, paths["off"])

    show_pattern(args.base_url, 0, "orientation_markers", 0.55)
    capture_frame(args.ffmpeg, args.camera_input, args.resolution, args.settle_frames, paths["orientation"])

    show_pattern(args.base_url, 4, "full_white", 0.05)
    capture_frame(args.ffmpeg, args.camera_input, args.resolution, args.settle_frames, paths["white"])

    show_pattern(args.base_url, 5, "dimension_probe", 0.05)
    capture_frame(args.ffmpeg, args.camera_input, args.resolution, args.settle_frames, paths["dimension"])

    processor = Path(__file__).with_name("process_webcam_wall.py")
    subprocess.run(
        [
            sys.executable,
            str(processor),
            "--config",
            args.config,
            "--off",
            str(paths["off"]),
            "--orientation",
            str(paths["orientation"]),
            "--white",
            str(paths["white"]),
            "--dimension-probe",
            str(paths["dimension"]),
            "--output-dir",
            str(output_dir),
            "--prefix",
            args.prefix,
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
