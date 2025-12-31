#!/usr/bin/env python3
"""
Emit a JSON snapshot describing the current animation configuration and status.
Used by local/remote diagnostics tooling.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animation.core.manager import AnimationManager
from animation.core.plugin_loader import AnimationPluginLoader
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


def build_snapshot(animations_dir: str, status_path: str) -> dict:
    loader = AnimationPluginLoader(
        animations_dir,
        allowed_plugins=AnimationManager.ALLOWED_PLUGINS,
    )
    loader.load_all_plugins()
    payload = {
        "default_strip_count": DEFAULT_STRIP_COUNT,
        "default_leds_per_strip": DEFAULT_LEDS_PER_STRIP,
        "allowed_plugins": sorted(AnimationManager.ALLOWED_PLUGINS),
        "loaded_plugins": [],
    }
    for name in sorted(loader.list_plugins()):
        info = loader.get_plugin_info(name) or {"plugin_name": name, "error": "no info"}
        payload["loaded_plugins"].append(info)

    status_file = Path(status_path)
    if status_file.exists():
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
        except Exception as exc:
            payload["status_error"] = f"Failed to parse {status_path}: {exc}"
        else:
            payload["status_keys"] = list(status.keys())
            payload["runtime_stats"] = status.get("runtime_stats")
    else:
        payload["status_error"] = f"{status_path} missing"

    return payload


def main():
    parser = argparse.ArgumentParser(description="Dump animation configuration snapshot")
    default_plugins_dir = str(REPO_ROOT / "animation" / "plugins")
    parser.add_argument("--animations-dir", default=default_plugins_dir, help="Animation directory to scan")
    parser.add_argument("--status", default="run_state/status.json", help="Status JSON file to read")
    args = parser.parse_args()

    snapshot = build_snapshot(args.animations_dir, args.status)
    print(json.dumps(snapshot, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
