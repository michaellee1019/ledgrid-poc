#!/usr/bin/env python3
"""Explain the guarded replacement for the retired calibration capture flow."""

from __future__ import annotations

import argparse
from datetime import datetime


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
    parser.parse_args()

    parser.error(
        "automatic calibration-pattern switching is retired and this command "
        "made no wall or camera changes; activate each required calibration "
        "scene through Composer Check + guarded activation, then use the "
        "calibrate-led-plant-wall workflow to capture and bind the evidence"
    )


if __name__ == "__main__":
    main()
