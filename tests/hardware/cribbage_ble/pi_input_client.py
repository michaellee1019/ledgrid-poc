"""Line-oriented test client; reads durable state, never opens another Game."""
import argparse
import json
from pathlib import Path
import socket
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--socket", type=Path, required=True)
parser.add_argument("--data", type=Path, required=True)
args = parser.parse_args()
sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
buttons = {name: index for index, name in enumerate(("up", "right", "down", "left", "a", "b", "y", "x"))}


def read():
    game = json.loads((args.data / "run_state/cribbage.json").read_text())
    status = json.loads((args.data / "run_state/status.json").read_text())
    return {"state": game["state"], "pairing": game["pairing"], "controllers": game["controllers"],
            "cribbage": status.get("cribbage"), "nimbus": status.get("nimbus"),
            "brightness": status.get("brightness"), "scene": status.get("current_animation")}


for line in sys.stdin:
    try:
        request = json.loads(line)
        for name in request.get("buttons", []):
            report = bytearray(17)
            if name == "menu":
                report[12] = 1
            else:
                report[buttons[name]] = 255
            sender.sendto(report, str(args.socket))
            time.sleep(.07)
            sender.sendto(bytes(17), str(args.socket))
            time.sleep(.07)
        deadline = time.monotonic() + 6
        while True:
            result = read()
            if all(result["state"].get(key) == value for key, value in request.get("expect", {}).items()):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Expected state not committed: " + repr(result))
            time.sleep(.05)
        print(json.dumps({"ok": True, **result}), flush=True)
    except Exception as error:
        print(json.dumps({"ok": False, "error": repr(error)}), flush=True)
