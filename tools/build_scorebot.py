#!/usr/bin/env python3
"""Build/check the pinned native core on the deployment target."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PIN = "078bb6b079bdfcbbabd34e312634c4235c789374"


def build(identity_path, configure=False):
    source = ROOT / "vendor/scorebot"
    if (source / "REVISION").read_text().strip() != PIN:
        raise ValueError("Unexpected Scorebot source revision")
    identity = json.loads(identity_path.read_text()) if identity_path.exists() else {"adapter": "hci0"}
    if not re.fullmatch(r"hci[0-9]+", identity["adapter"]):
        raise ValueError("Invalid Bluetooth adapter")
    subprocess.run(["make", "-C", str(source / "native"), "check"], check=True)
    if configure:
        sys.path.insert(0, str(ROOT))
        from drivers.cribbage_session import durable_save
        durable_save(identity_path, {"adapter": identity["adapter"]})
    print("Use matching protocol-v5 firmware; runtime pairing needs no compiled authority identity.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--configure", action="store_true")
    args = parser.parse_args()
    build(args.identity, args.configure)
