"""Build a test-only native profile binding RED to the USB-identified bench board."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def build(output, leader_mac):
    source = ROOT / "vendor/scorebot"
    output.mkdir(parents=True, exist_ok=True)
    for directory in ("native", "lib/scorebot/src"):
        shutil.copytree(source / directory, output / directory, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("build*", "__pycache__"))
    (output / "src").mkdir(exist_ok=True)
    roles = (source / "src/BoardRole.cpp").read_text()
    original = "{BoardRole::Player_Red, 2934574912, 0x983daeea0f40ULL"
    replacement = "{BoardRole::Player_Red, 2934577084, 0x983daeea17bcULL"
    assert roles.count(original) == 1
    (output / "src/BoardRole.cpp").write_text(roles.replace(original, replacement))
    node = int(leader_mac.replace(":", ""), 16) & 0xFFFFFFFF
    environment = {**os.environ, "SCOREBOT_LEADER_NODE_ID": str(node),
                   "SCOREBOT_LEADER_BLUETOOTH_MAC": "0x" + leader_mac.replace(":", "") + "ULL"}
    subprocess.run(["make", "-C", str(output / "native"), "all"], env=environment, check=True)
    profile = {"leader_node": node, "leader_mac": leader_mac.upper(),
               "player_node": 2934577084, "player_base_mac": "98:3D:AE:EA:17:BC",
               "fixture_change": "RED identity/address table only; game policies unchanged",
               "pinned_source": (source / "REVISION").read_text().strip()}
    (output / "profile.json").write_text(json.dumps(profile, indent=2))
    print(json.dumps(profile))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--leader-mac", required=True)
    args = parser.parse_args()
    build(args.output.resolve(), args.leader_mac)
