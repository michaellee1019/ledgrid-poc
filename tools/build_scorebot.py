#!/usr/bin/env python3
"""Build the pinned Scorebot core using this Pi's verified public adapter."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PIN = "af383b408234fb17d4078d93dd23e28c10fcf20f"


def public_adapter(adapter):
    if not re.fullmatch(r"hci[0-9]+", adapter):
        raise ValueError("Invalid Bluetooth adapter")
    def property_value(name):
        output = subprocess.check_output([
            "busctl", "--system", "get-property", "org.bluez",
            f"/org/bluez/{adapter}", "org.bluez.Adapter1", name,
        ], text=True, timeout=5)
        return shlex.split(output)[1]
    if property_value("AddressType") != "public":
        raise ValueError("Scorebot requires a stable public Pi address")
    address = property_value("Address").upper()
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address):
        raise ValueError("Invalid public adapter address")
    return address


def build(identity_path, configure=False):
    source = ROOT / "vendor/scorebot"
    if (source / "REVISION").read_text().strip() != PIN:
        raise ValueError("Unexpected Scorebot source revision")
    if identity_path.exists():
        identity = json.loads(identity_path.read_text())
        if public_adapter(identity["adapter"]) != identity["mac"]:
            raise ValueError("Pi adapter differs from the saved Scorebot identity")
    elif configure:
        address = public_adapter("hci0")
        identity = {"adapter": "hci0", "mac": address,
                    "node": int(address.replace(":", ""), 16) & 0xFFFFFFFF}
    else:
        raise ValueError("Configure the actual Pi identity first (--configure)")
    node = identity["node"]
    if type(node) is not int or not 0 < node <= 0xFFFFFFFF:
        raise ValueError("Scorebot node must be a nonzero uint32")
    environment = {**os.environ, "SCOREBOT_LEADER_NODE_ID": str(node),
                   "SCOREBOT_LEADER_BLUETOOTH_MAC": "0x" + identity["mac"].replace(":", "") + "ULL"}
    subprocess.run(["make", "-C", str(source / "native"), "check"], env=environment, check=True)
    sys.path.insert(0, str(ROOT))
    from drivers.cribbage_session import durable_save
    durable_save(identity_path, identity)
    print("Player firmware must use the same identity:")
    print(shlex.join(["env", *(key + "=" + environment[key] for key in
          ("SCOREBOT_LEADER_NODE_ID", "SCOREBOT_LEADER_BLUETOOTH_MAC")),
          "pio", "run", "-c", "platformio.pi.ini", "-e", "pi_players"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--configure", action="store_true")
    args = parser.parse_args()
    build(args.identity, args.configure)
