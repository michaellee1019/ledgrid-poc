"""Build the pinned native core for the Mac RF bench; identities are runtime data."""
import argparse
import json
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
    subprocess.run(["make", "-C", str(output / "native"), "all"], check=True)
    profile = {"runtime_pairing": True, "radio_filter_only": leader_mac,
               "pinned_source": (source / "REVISION").read_text().strip()}
    (output / "profile.json").write_text(json.dumps(profile, indent=2))
    print(json.dumps(profile))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--leader-mac", help="Optional actual Mac radio filter metadata; never a native identity")
    args = parser.parse_args()
    build(args.output.resolve(), args.leader_mac)
