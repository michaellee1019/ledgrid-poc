#!/usr/bin/env python3
"""Capture an unmirrored still from a named macOS AVFoundation camera."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


DEVICE_RE = re.compile(r"\[(\d+)\]\s+(.+)$")


def list_video_devices(ffmpeg: str) -> list[tuple[int, str]]:
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-f",
            "avfoundation",
            "-list_devices",
            "true",
            "-i",
            "",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    devices: list[tuple[int, str]] = []
    in_video_section = False
    for line in result.stderr.splitlines():
        if "AVFoundation video devices:" in line:
            in_video_section = True
            continue
        if "AVFoundation audio devices:" in line:
            break
        if not in_video_section:
            continue
        match = DEVICE_RE.search(line)
        if match:
            devices.append((int(match.group(1)), match.group(2).strip()))
    return devices


def resolve_camera(devices: list[tuple[int, str]], requested: str) -> tuple[int, str]:
    exact = [device for device in devices if device[1] == requested]
    if len(exact) == 1:
        return exact[0]
    folded = requested.casefold()
    partial = [device for device in devices if folded in device[1].casefold()]
    if len(partial) == 1:
        return partial[0]
    available = ", ".join(f"{index}:{name}" for index, name in devices) or "none"
    if not partial:
        raise RuntimeError(
            f"Camera {requested!r} was not found; AVFoundation video devices: {available}"
        )
    raise RuntimeError(
        f"Camera name {requested!r} is ambiguous; AVFoundation video devices: {available}"
    )


def capture(
    ffmpeg: str,
    camera_index: int,
    resolution: str,
    framerate: int,
    settle_frames: int,
    output: Path,
) -> None:
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
            str(framerate),
            "-video_size",
            resolution,
            "-pixel_format",
            "nv12",
            "-i",
            f"{camera_index}:none",
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--camera-name", default="Anker PowerConf C200")
    parser.add_argument("--resolution", default="1920x1080")
    parser.add_argument("--framerate", type=int, default=30)
    parser.add_argument("--settle-frames", type=int, default=60)
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--list", action="store_true", help="List cameras and exit")
    args = parser.parse_args()

    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is required but was not found on PATH")
    devices = list_video_devices(ffmpeg)
    if args.list:
        print(json.dumps({"video_devices": devices}, indent=2))
        return
    if args.output is None:
        parser.error("--output is required unless --list is used")
    if args.framerate <= 0 or args.settle_frames < 0:
        parser.error("--framerate must be positive and --settle-frames nonnegative")

    camera_index, camera_name = resolve_camera(devices, args.camera_name)
    output = args.output.expanduser().resolve()
    capture(
        ffmpeg,
        camera_index,
        args.resolution,
        args.framerate,
        args.settle_frames,
        output,
    )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "camera_index": camera_index,
                "camera_name": camera_name,
                "mirrored": False,
                "output": str(output),
                "resolution": args.resolution,
                "sha256": digest,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
