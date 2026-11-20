"""Live Pi authority acceptance; simulated HID packets and optional real RF peer.

Never opens a Game. The installed controller is the only authority. Use the
reviewed lab.py restoration owner when running with a temporary USB fixture.
"""
import argparse
import json
from pathlib import Path
import queue
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from web.starter_looks import get_starter  # noqa: E402
from drivers.cribbage_session import State  # noqa: E402

HANDS = ((8, 10), (12, 6), (4, 8), (10, 12), (6, 4), (16, 14),
         (8, 10), (12, 6), (14, 12), (10, 14), (12, 10), (9, 8))


class Wall:
    def __init__(self, args):
        self.args = args
        self.ssh = ["ssh", "-i", str(args.key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=10", args.host, "--"]
        remote = [args.root + "/venv/bin/python", "-u", args.root + "/validation/cribbage-078bb6b/pi_input_client.py",
                  "--socket", args.root + "/validation/cribbage-078bb6b/shim/nimbus.sock",
                  "--data", args.root + "/data"]
        self.client = subprocess.Popen([*self.ssh, shlex.join(remote)], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, text=True, bufsize=1)
        self.events = json.loads(args.evidence.read_text())["events"] if args.resume_round else []
        self.selection = 0
        self.buttons = 0
        self.radio = None

    def rpc(self, buttons=(), **expected):
        self.client.stdin.write(json.dumps({"buttons": list(buttons), "expect": expected}) + "\n")
        self.client.stdin.flush()
        result = json.loads(self.client.stdout.readline())
        assert result["ok"], result
        self.buttons += len(buttons)
        return result

    def state(self):
        return self.rpc()["state"]

    def wait(self, **expected):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            state = self.state()
            if all(state.get(key) == value for key, value in expected.items()):
                return state
            time.sleep(.1)
        raise RuntimeError(f"Expected {expected}; authoritative state {state}")

    def record(self, label):
        result = self.rpc()
        assert not result["cribbage"]["error"], result
        self.events.append({"event": label, "time": time.monotonic(), **result})
        self.save()
        print(label, result["state"]["scores"], flush=True)

    def save(self):
        self.args.evidence.write_text(json.dumps({"input": "simulated raw HID through production reader",
                                                 "buttons": self.buttons, "events": self.events,
                                                 "radio": self.radio.transcript if self.radio else []}, indent=2))

    def api(self, path, payload):
        request = urllib.request.Request(self.args.url + path, json.dumps(payload).encode(),
                                         {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def scene(self, cribbage=True):
        scene = get_starter("aurora")["scene"]
        if cribbage:
            scene["animation"] = {"component_id": "cribbage_leaderboard", "provider": "python",
                                  "role": "animation", "version": 1,
                                  "parameters": {"glow": .7, "serial_port": ""}}
        result = self.api("/api/v1/scene", {"scene": scene})
        command = result["command_id"]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with urllib.request.urlopen(self.args.url + "/api/v1/composer/operations/telemetry", timeout=5) as response:
                status = json.load(response)
            # The durable controller status is also read independently by rpc.
            if self.rpc()["scene"] == scene["animation"]["component_id"]:
                time.sleep(.6)
                return
            time.sleep(.1)
        raise RuntimeError(f"Scene command {command} did not reach controller: {status}")

    def enter(self):
        self.scene(False)
        self.scene(True)
        self.rpc(["a"])
        self.selection = 0

    def select(self, player):
        before = self.state()["turn"]
        choices = list(range(4)) if not self.state()["started"] else [0, 1]
        while self.selection != player:
            self.rpc(["down"])
            self.selection = choices[(choices.index(self.selection) + 1) % len(choices)]
        assert self.state()["turn"] == before

    def score(self, player, delta, passes=False):
        self.select(player)
        before = self.state()
        scores = before["scores"].copy()
        scores[player] += delta
        buttons = ["right" if delta > 0 else "left"] * abs(delta) + ["a" if passes else "x"]
        self.rpc(buttons, scores=scores)
        self.record(f"Nimbus {player} {delta:+d} {'OK' if passes else 'ADD'}")

    def new_game(self):
        before = self.state()
        self.rpc(["y", "down", "a", "b"])
        assert self.state() == before, "Canceled New Game changed authority"
        self.rpc(["y", "down", "a", "a"], started=0, scores=[0] * 4, game_id=before["game_id"] + 1)
        after = self.state()
        assert (after["term"], after["version"]) > (before["term"], before["version"])
        assert after["last_operation"] == before["last_operation"]
        self.record("confirmed New Game preserved operation marks and advanced epoch")

    def configure(self, physical=False):
        for player in range(2):
            self.select(player)
            desired = 1 if physical and player == 0 else 0
            for _ in range(3):
                state = self.state()
                bit = 1 << player
                mode = 0 if state["local_control_mask"] & bit else 1 if state["lobby_enabled_mask"] & bit else 2
                if mode == desired:
                    break
                self.rpc(["right"])
            assert mode == desired
        self.rpc(["a"], started=0)
        self.record("review roster without starting")
        self.rpc(["a"], started=1, roster_mask=3, turn=0)
        self.selection = 1

    def restart(self):
        before = self.state()
        subprocess.run([*self.ssh, "sudo systemctl stop " + shlex.quote(self.args.unit)],
                       check=True, timeout=30)
        stopped = self.state()
        # systemd SIGTERM may kill Python before its transport finally block.
        assert stopped["connected_mask"] in (0, before["connected_mask"])
        assert stopped["version"] == before["version"] + int(stopped["connected_mask"] != before["connected_mask"])
        subprocess.run([*self.ssh, "sudo systemctl start " + shlex.quote(self.args.unit)],
                       check=True, timeout=30)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            after = self.state()
            if after["term"] > before["term"]:
                break
            time.sleep(.1)
        for field in ("scores", "game_id", "last_operation", "roster_mask", "turn"):
            assert after[field] == before[field], (field, before, after)
        assert stopped["version"] <= after["version"] <= stopped["version"] + int(self.radio is not None)
        assert after["term"] == before["term"] + 1
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.args.url + "/api/composer/status", timeout=2):
                    break
            except urllib.error.URLError:
                time.sleep(.2)
        self.enter()
        self.record("durable authority restart advanced term without replay")

    def close(self):
        self.client.stdin.close()
        self.client.wait(timeout=10)


def local(wall):
    wall.enter()
    if wall.args.resume_round:
        expected = [sum(hand[player] for hand in HANDS[:wall.args.resume_round]) for player in range(2)] + [0, 0]
        assert wall.state()["scores"] == expected
        prior = wall.events[-1]["state"]
        assert wall.state()["term"] == prior["term"] + 1
        wall.record("resumed durable game after authority restart")
    else:
        wall.new_game()
        wall.configure()
    assert wall.rpc()["cribbage"]["ble_enabled"] is False
    for round_index, hands in enumerate(HANDS):
        if round_index < wall.args.resume_round:
            continue
        for player, hand in enumerate(hands):
            assert wall.state()["turn"] == player
            wall.score(player, 2)
            assert wall.state()["turn"] == player
            wall.score(player, hand - 2, True)
            if wall.state()["scores"][0] == 121:
                break
        if round_index == 4:
            wall.restart()
    assert wall.state()["scores"] == [121, 106, 0, 0]
    wall.record("complete zero-board LOCAL match 121–106")
    wall.new_game()


class Board:
    def __init__(self, wall):
        import serial
        self.wall = wall
        self.serial = serial.Serial(port=None, baudrate=115200, timeout=.1)
        self.serial.port = wall.args.port
        self.serial.dtr = self.serial.rts = False
        self.serial.open()
        self.lines = queue.Queue()
        self.transcript = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.reader, daemon=True)
        self.thread.start()
        time.sleep(1)  # Native USB opening can reset the MCU; let its RX queue start.
        self.operation = wall.state()["last_operation"][0]

    def reader(self):
        partial = bytearray()
        while not self.stop.is_set():
            partial.extend(self.serial.readline())
            if len(partial) > 4096:
                self.lines.put((time.monotonic(), "FAULT diagnostic-size"))
                return
            if partial.endswith(b"\n"):
                line = partial.decode(errors="replace").strip()
                partial.clear()
                if line:
                    stamp = time.monotonic()
                    self.transcript.append({"time": stamp, "serial": line})
                    self.lines.put((stamp, line))

    def send(self, command):
        self.transcript.append({"time": time.monotonic(), "serial": "TX " + command})
        self.serial.write(command.encode() + b"\n")

    def wait(self, predicate, timeout=20, since=0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                stamp, line = self.lines.get(timeout=.2)
            except queue.Empty:
                continue
            assert not line.startswith("FAULT"), line
            if stamp >= since and predicate(line):
                return line
        raise RuntimeError("Board observation timed out; tail=" + repr(self.transcript[-6:]))

    def pair(self, controller=False):
        stamp = time.monotonic()
        self.send("")  # Discard a partial console line from the USB reset.
        self.send("?")
        ready = self.wait(lambda line: line.startswith("READY "), since=stamp)
        _, node, address, public = ready.split()
        assert address.upper() == self.wall.args.address.upper() and public == "1", ready
        self.node = int(node)
        self.send(("C " if controller else "P ") + self.wall.args.packet)
        self.wait(lambda line: line == "PAIRED")
        peer = self.wait(lambda line: line.startswith("PEER "))
        assert peer.lower() == "peer 2c:cf:67:c2:c4:0a 1", peer
        self.wall.wait(connected_mask=0 if controller else 1, sleeping_mask=0)
        self.wire()
        kind = "controller" if controller else "player"
        self.wall.record(f"real public Pi initiator admitted actual public {kind} GATT identity")

    def wire(self, since=0):
        def matches(line):
            if not line.startswith("RX "):
                return False
            result = self.wall.rpc()
            state = State.from_dict(result["state"])
            state._controllers, state._control_ops = result["controllers"]
            expected = state.to_wire(5, result["pairing"]["session_id"])
            return json.loads(line[3:]) == expected
        return self.wait(matches, since=since)

    def raw(self, packet, **expected):
        stamp = time.monotonic()
        self.send("N " + packet)
        self.wait(lambda line: line == "NOTIFY_OK", since=stamp)
        self.wall.wait(**expected)
        self.wire(stamp)

    def score(self, delta, passes=False, duplicate=False):
        before = self.wall.state()
        self.operation = max(self.operation, before["last_operation"][0]) + 1
        self.last = json.dumps({"type": "player", "protocol": 5, "fromNodeId": self.node,
                                "game": before["game_id"], "score": delta, "turnPassed": passes,
                                "operationId": self.operation}, separators=(",", ":"))
        scores = before["scores"].copy()
        scores[0] += delta
        marks = before["last_operation"].copy()
        marks[0] = self.operation
        self.raw(self.last, scores=scores, last_operation=marks)
        self.wall.record(f"real RF RED {delta:+d} {'OK' if passes else 'ADD'}")
        if duplicate:
            before = self.wall.state()
            self.raw(self.last)
            assert self.wall.state() == before
            self.wall.record("duplicate RF request acknowledged without score replay")

    def drop(self, sleeping):
        before = self.wall.state()
        self.send("S" if sleeping else "D")
        self.wall.wait(connected_mask=0, sleeping_mask=1 if sleeping else 0)
        after = self.wall.state()
        assert after["roster_mask"] == before["roster_mask"] and after["turn"] == before["turn"]
        self.wall.record("validated sleep followed by physical disconnect" if sleeping else "physical disconnect")
        return before

    def rejoin(self, slept):
        if slept:
            time.sleep(3.5)
            self.pair()
        else:
            self.wall.wait(connected_mask=1, sleeping_mask=0)
            self.wire()
        self.wall.record("physical reconnect retained roster and cleared sleep")

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)
        self.serial.close()


def mixed(wall):
    wall.enter()
    wall.new_game()
    board = wall.radio = Board(wall)
    try:
        board.pair()
        wall.configure(True)
        malformed = {"type": "player", "protocol": 5, "fromNodeId": board.node,
                     "game": wall.state()["game_id"], "score": True, "turnPassed": False, "operationId": 1}
        before = wall.state()
        board.raw(json.dumps(malformed))
        malformed.update(score=1, fromNodeId=board.node + 1)
        board.raw(json.dumps(malformed))
        board.raw('{"type":"player","protocol":5,"protocol":5}')
        assert wall.state() == before
        wall.record("malformed bool, spoofed node and duplicate JSON fields rejected over RF")
        for round_index, hands in enumerate(HANDS):
            assert wall.state()["turn"] == 0
            if round_index in (2, 3):
                slept = round_index == 2
                board.drop(slept)
                wall.enter()
                wall.score(0, 2)
                wall.score(0, hands[0] - 2, True)
                board.rejoin(slept)
            else:
                if round_index == 4:
                    wall.select(1)
                    wall.rpc(["right"] * 3 + ["menu"])
                    wall.scene(False)
                board.score(2, duplicate=True)
                board.score(hands[0] - 2, True)
                if round_index == 4:
                    wall.enter()
                    before = wall.state()["scores"]
                    wall.rpc(["x"], scores=before)
                    wall.record("physical play through Menu and other scene; Nimbus draft discarded")
            assert wall.state()["turn"] == 1
            if wall.state()["scores"][0] == 121:
                break
            wall.score(1, 2)
            board.wire()
            wall.score(1, hands[1] - 2, True)
            board.wire()
            if round_index == 5:
                wall.restart()
                wall.wait(connected_mask=1)
                board.wire()
        assert wall.state()["scores"] == [121, 106, 0, 0]
        wall.record("complete real RF mixed match 121–106")
        finish_cases(wall, board)
    finally:
        wall.save()
        board.close()


def finish_cases(wall, board):
    # Real idle snapshots must continue long beyond the 15-second expiry.
    start = time.monotonic()
    time.sleep(18)
    stamps = [item["time"] for item in board.transcript
              if item["time"] >= start and item["serial"].startswith("RX ")]
    metrics = {"count": len(stamps), "gaps": [b - a for a, b in zip(stamps, stamps[1:])]}
    wall.record("18-second real RF heartbeat observations")
    wall.events[-1]["heartbeat"] = metrics
    wall.save()
    print("HEARTBEAT", metrics, flush=True)
    assert len(stamps) >= 10 and max(metrics["gaps"]) < 2.5
    current = wall.state()
    old = json.dumps({"type": "player", "protocol": 5, "fromNodeId": board.node,
                      "game": current["game_id"], "score": 1, "turnPassed": False,
                      "operationId": board.operation + 1})
    wall.new_game()
    before = wall.state()
    board.raw(old)
    assert wall.state() == before
    wall.record("delayed old-game RF request rejected after New Game epoch")
    wall.configure(True)
    board.score(0x7FFFFFFF)
    before = wall.state()
    board.operation += 1
    overflow = json.loads(board.last)
    overflow.update(score=1, operationId=board.operation)
    marks = before["last_operation"].copy()
    marks[0] = board.operation
    board.raw(json.dumps(overflow), scores=before["scores"], last_operation=marks,
              version=before["version"] + 1)
    wall.record("physical int32 overflow consumed durably and acknowledged")
    board.drop(False)
    before = wall.state()
    wall.select(0)
    wall.rpc(["right", "x"])
    assert wall.state() == before
    wall.rpc(["b", "x"])
    assert wall.state() == before
    wall.record("local overflow preserved authority and draft until B cleared it")
    wall.new_game()


def finish(wall):
    assert wall.state()["scores"] == [121, 106, 0, 0]
    wall.enter()
    board = wall.radio = Board(wall)
    try:
        board.pair()
        finish_cases(wall, board)
    finally:
        wall.save()
        board.close()


def controller(wall):
    wall.enter()
    wall.new_game()
    board = wall.radio = Board(wall)
    try:
        board.pair(controller=True)
        marks = wall.rpc()["controllers"]
        slot = marks[0].index(board.node) if board.node in marks[0] else marks[0].index(0)
        operation = marks[1][slot]

        def control(command, player=0, value=0, passes=False, **expected):
            nonlocal operation
            operation += 1
            before = wall.state()
            packet = json.dumps({"type": "control", "protocol": 5, "from": board.node,
                                 "op": operation, "game": before["game_id"], "term": before["term"],
                                 "command": command, "player": player, "value": value, "pass": passes})
            board.raw(packet, version=before["version"] + 1, **expected)
            saved = wall.rpc()["controllers"]
            assert saved[0][slot] == board.node and saved[1][slot] == operation
            wall.record(f"real controller RF command {command} op {operation}")
            return packet

        control(2, player=0, value=0, local_control_mask=3)
        control(2, player=1, value=0, local_control_mask=3)
        control(1, started=1, roster_mask=3, turn=0)
        duplicate = control(0, value=7, scores=[7, 0, 0, 0], turn=0)
        before = wall.rpc()
        board.raw(duplicate)
        assert wall.state() == before["state"] and wall.rpc()["controllers"] == before["controllers"]
        wall.record("real controller duplicate acknowledged without replay")
        control(0, passes=True, scores=[7, 0, 0, 0], turn=1)
        control(0, player=1, value=-2, scores=[7, -2, 0, 0], turn=1)
        spoof = json.dumps({"type": "player", "protocol": 5, "fromNodeId": board.node,
                            "game": wall.state()["game_id"], "score": 999, "turnPassed": True, "operationId": 99})
        before = wall.state()
        board.raw(spoof)
        assert wall.state() == before and before["connected_mask"] == 0
        wall.record("controller has no player slot or player-message privilege")
        wall.score(1, 3)
        board.wire()
        wall.record("Nimbus score replicated back to real controller")
        receipt = wall.rpc()["controllers"]
        wall.restart()
        board.wire()
        assert wall.rpc()["controllers"] == receipt
        wall.record("controller receipt survived durable authority restart")
        before = wall.state()
        stale = json.loads(duplicate)
        stale["op"] = operation + 1
        board.raw(json.dumps(stale))
        assert wall.state() == before
        wall.record("controller old-term request rejected without replay")
        control(0, value=1, scores=[8, 1, 0, 0])
        wall.new_game()
    finally:
        wall.save()
        board.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("local", "mixed", "finish", "controller"), default="local")
    parser.add_argument("--resume-round", type=int, default=0)
    parser.add_argument("--host", default="ledgridwall5@ledgridwall5.local")
    parser.add_argument("--url", default="http://ledgridwall5.local:5000")
    parser.add_argument("--key", type=Path, default=ROOT / ".gpt-key")
    parser.add_argument("--root", default="/home/ledgridwall5/ledgrid-pod")
    parser.add_argument("--unit", default="ledgrid-cribbage-acceptance.service")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--port", default="/dev/cu.usbmodem5B5E0766461")
    parser.add_argument("--address", default="1C:DB:D4:47:14:3D")
    parser.add_argument("--packet", default="ffff53500540cfb6fe40cfb6fe404f72311002010300")
    args = parser.parse_args()
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    wall = Wall(args)
    try:
        {"local": local, "mixed": mixed, "finish": finish, "controller": controller}[args.phase](wall)
    finally:
        wall.close()
