#!/usr/bin/env python3
"""Run the full-speed localhost dashboard without LED hardware."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE
from animation.core.manager import AnimationManager, PreviewLEDController
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT
from web.app import AnimationWebInterface
from web.local_control import LocalControlChannel


def create_mac_dashboard(host: str, port: int, strips: int, leds_per_strip: int):
    controller = PreviewLEDController(strips, leds_per_strip)
    manager = AnimationManager(
        controller,
        animation_speed_scale=DEFAULT_ANIMATION_SPEED_SCALE,
        plant_aware=DEFAULT_PLANT_AWARE,
        auto_start=False,
    )
    # Supply fresh frames faster than a 60 Hz browser asks for them so Python
    # scheduling jitter does not turn into duplicated canvas frames.
    manager.target_fps = 120
    manager.start_animation(manager.DEFAULT_ANIMATION, {})
    return AnimationWebInterface(
        LocalControlChannel(manager), manager, host=host, port=port, local_mode=True
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--strips", type=int, default=DEFAULT_STRIP_COUNT)
    parser.add_argument("--leds-per-strip", type=int, default=DEFAULT_LEDS_PER_STRIP)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("the Mac dashboard only binds to localhost")
    dashboard = create_mac_dashboard(
        args.host, args.port, args.strips, args.leds_per_strip
    )
    # A 60 Hz canvas would otherwise print one Werkzeug access line per frame.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    print(f"🍎 Full-speed Mac dashboard: http://{args.host}:{args.port}")
    try:
        dashboard.run(debug=False)
    finally:
        dashboard.preview_manager.stop_animation()


if __name__ == "__main__":
    main()
