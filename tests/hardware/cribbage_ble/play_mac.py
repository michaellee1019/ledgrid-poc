"""Real Mac BLE plus production Nimbus/renderer and native authority playthrough.

The USB command injects a board gesture; the resulting GATT notification must
arrive over RF. Every reply must match the entire snapshot echoed by the board.
This is a radio fixture, not acceptance of production player/display firmware.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

import serial
from bleak import BleakClient, BleakScanner

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from drivers.cribbage_ble import DOWNLINK, IDENTITY, PROTOCOL, UPLINK, PAIRING, HEARTBEAT  # noqa: E402
from drivers.cribbage_session import Library, Result  # noqa: E402
from scripts.start_server import handle_command  # noqa: E402
from tests.unit.test_cribbage_play_sessions import HANDS, PlaySession  # noqa: E402
from web.starter_looks import get_starter  # noqa: E402


class Radio:
    def __init__(self, args, play):
        self.args, self.play = args, play
        self.serial = None
        self.client = None
        self.token = None
        self.operation = 0
        self.transcript = []
        self.snapshots = []
        self.requests = []
        self.partial = bytearray()
        self.notifications = asyncio.Queue(maxsize=8)

    async def usb_identity(self):
        self.serial = serial.Serial(port=None, baudrate=115200, timeout=.1)
        self.serial.port, self.serial.dtr, self.serial.rts = self.args.port, False, False
        self.serial.open()
        await asyncio.sleep(1)
        self.serial.write(b"?\n")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            line = await self.line()
            if line.startswith("READY "):
                _, node, address, public = line.split()
                assert address.upper() == self.args.address.upper() and public == "1"
                self.node, self.public = int(node), int(public)
                self.serial.write(b"P " + self.play.session.discovery().hex().encode() + b"\n")
                while time.monotonic() < deadline:
                    if await self.line() == "PAIRED":
                        return
        raise RuntimeError("USB board did not verify its actual public radio identity")

    async def line(self):
        raw = await asyncio.to_thread(self.serial.readline)
        self.partial.extend(raw)
        if len(self.partial) > 4096:
            raise RuntimeError("Oversized USB diagnostic line")
        if not self.partial.endswith(b"\n"):
            return ""
        line = self.partial.decode(errors="replace").strip()
        self.partial.clear()
        if line:
            self.transcript.append({"time": time.monotonic(), "serial": line})
            if line.startswith("FAULT "):
                raise RuntimeError(line)
        return line

    async def connect(self):
        devices = await BleakScanner.discover(timeout=4, return_adv=True, cb={"use_bdaddr": True})
        target = next(device for device, _adv in devices.values()
                      if device.address.upper() == self.args.address.upper())
        self.client = BleakClient(target, timeout=8)
        await self.client.connect()
        node = bytes(await self.client.read_gatt_char(IDENTITY))
        protocol = bytes(await self.client.read_gatt_char(PROTOCOL))
        assert len(node) == 4 and int.from_bytes(node, "little") == self.node
        assert len(protocol) == 2 and int.from_bytes(protocol, "little") == 5
        pairing = bytes(await self.client.read_gatt_char(PAIRING))
        self.token = self.play.session.admit(self.node, 5, self.public,
                                            int(target.address.replace(":", ""), 16), pairing)
        assert self.token is not None
        await self.client.start_notify(UPLINK, lambda _char, raw: self.notifications.put_nowait(bytes(raw)))
        high_water = json.loads(self.play.session.wire())["operations"][0]
        self.operation = self.play.library.reconcile_operation(self.operation, high_water)
        await self.publish()
        print("ADMITTED actual public GATT peer", target.address, flush=True)

    async def publish(self):
        wire = self.play.session.wire()
        assert len(wire) <= 512
        await self.client.write_gatt_char(DOWNLINK, wire, response=True)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if await self.line() == "RX " + wire.decode():
                self.snapshots.append({"time": time.monotonic(), "wire": json.loads(wire), "bytes": len(wire)})
                return
        raise RuntimeError("No exact full snapshot received over BLE")

    async def raw(self, payload, expected):
        assert self.notifications.empty() and len(payload) <= 512
        self.serial.write(b"N " + payload + b"\n")
        observed = await asyncio.wait_for(self.notifications.get(), 4)
        assert observed == payload, "RF notification was changed or truncated"
        result = self.play.session.receive(self.token, observed)
        assert result in expected, (result, expected)
        self.requests.append({"payload": payload.decode(), "result": result.name})
        await self.publish()  # Includes duplicate and rejected requests.
        return result

    async def score(self, delta, passes=False, duplicate=False):
        self.operation += 1
        self.last_packet = json.dumps({"type": "player", "protocol": 5, "fromNodeId": self.node,
                                       "game": self.play.state()["game_id"], "score": delta,
                                       "turnPassed": passes, "operationId": self.operation},
                                      separators=(",", ":")).encode()
        await self.raw(self.last_packet, (Result.OK, Result.ACCEPTED_WITHOUT_TURN_CHANGE))
        self.play.expected[0] += delta
        self.play.submissions += 1
        self.play.record(f"physical RF board: {delta:+d} {'OK' if passes else 'ADD'}")
        if duplicate:
            before = self.play.state()
            await self.raw(self.last_packet, (Result.DUPLICATE,))
            assert self.play.state() == before

    async def drop(self, sleeping=False):
        before = self.play.state()
        if sleeping:
            self.serial.write(b"S\n")
            observed = await asyncio.wait_for(self.notifications.get(), 3)
            assert json.loads(observed) == {"type": "status", "protocol": 5,
                                             "fromNodeId": self.node, "state": "sleep"}
            assert self.play.session.receive(self.token, observed) == Result.OK
            assert self.play.state()["sleeping_mask"] == 1
        else:
            self.serial.write(b"D\n")
        deadline = time.monotonic() + 8
        while self.client.is_connected and time.monotonic() < deadline:
            await asyncio.sleep(.05)
        assert not self.client.is_connected, "The physical board did not disconnect"
        self.play.session.disconnect(self.token)
        self.token = None
        await self.client.disconnect()
        self.client = None
        after = self.play.state()
        assert after["roster_mask"] == before["roster_mask"] and after["turn"] == before["turn"]
        if sleeping:
            self.serial.close()
            await asyncio.sleep(4)
            await self.usb_identity()
        self.play.record("physical sleep/disconnect" if sleeping else "physical unexpected disconnect")

    async def close(self):
        if self.client is not None:
            await self.client.disconnect()
        if self.token is not None:
            self.play.session.disconnect(self.token)
        if self.serial is not None:
            self.serial.close()


async def exercise(args):
    if (args.data / "cribbage.json").exists():
        raise ValueError("Choose a new test data directory; existing scores are never discarded")
    args.data.mkdir(parents=True, exist_ok=True)
    play = PlaySession(args.data, Library(args.library), "actual-rf-two-player")
    radio = Radio(args, play)
    try:
        await radio.usb_identity()
        await radio.connect()
        play.configure(physical=(0,))
        await radio.publish()
        malformed = {"type": "player", "protocol": 5, "fromNodeId": radio.node,
                     "game": play.state()["game_id"], "score": True, "turnPassed": False, "operationId": 1}
        before = play.state()
        await radio.raw(json.dumps(malformed).encode(), (Result.INVALID_ARGUMENT,))
        malformed.update(score=1, fromNodeId=radio.node + 1)
        await radio.raw(json.dumps(malformed).encode(), (Result.INVALID_ARGUMENT,))
        await radio.raw(b'{"type":"player","protocol":5,"protocol":5}', (Result.INVALID_ARGUMENT,))
        assert play.state() == before
        for round_index, hands in enumerate(HANDS):
            assert play.state()["turn"] == 0
            if round_index in (2, 3):
                await radio.drop(sleeping=round_index == 2)
                play.score(0, 2)
                play.score(0, hands[0] - 2, passes=True)
                await radio.connect()
                assert not play.state()["sleeping_mask"]
            else:
                if round_index == 4:
                    play.select(1)
                    play.edit(3)
                    assert handle_command(play.manager, "start_scene", {"scene": get_starter("aurora")["scene"]})
                await radio.score(2, duplicate=True)
                await radio.score(hands[0] - 2, passes=True)
                if round_index == 4:
                    assert handle_command(play.manager, "start_scene", {"scene": play.scene})
                    play.press("a", "x")
                    assert not any(play.game()._drafts)
                    play.record("physical RF scoring continued through Composer Scene handoff", capture=True)
            assert play.state()["turn"] == 1
            if play.expected[0] == 121:
                break
            play.score(1, 2)
            await radio.publish()
            play.score(1, hands[1] - 2, passes=True)
            await radio.publish()
            play.record(f"actual RF round {round_index + 1} complete", capture=True)
            print("ROUND", round_index + 1, play.expected, flush=True)
            if round_index == 5:
                pending = radio.last_packet
                await radio.close()
                radio.token, radio.client = None, None
                play.restart()
                await radio.usb_identity()
                await radio.connect()
                await radio.raw(pending, (Result.DUPLICATE,))
            if round_index == 6:
                times = []
                for _ in range(12):
                    await asyncio.sleep(HEARTBEAT)
                    await radio.publish()
                    times.append(radio.snapshots[-1]["time"])
                gaps = [right - left for left, right in zip(times, times[1:])]
                assert max(gaps) < 2.2, gaps
                play.record("18-second idle period maintained physical BLE heartbeats")
        assert play.expected == [121, 106, 0, 0]
        completed = list(play.expected)
        old = play.state()
        play.press("y", "down", "a", "b")
        assert play.state() == old
        play.press("y", "down", "a", "a")
        play.expected = [0] * 4
        fresh = play.state()
        assert fresh["game_id"] == old["game_id"] + 1 and fresh["version"] > old["version"]
        await radio.publish()
        delayed = json.loads(radio.last_packet)
        delayed["operationId"] += 100
        await radio.raw(json.dumps(delayed).encode(), (Result.WRONG_GAME,))
        assert play.state() == fresh
        play.configure(physical=(0,))
        assert play.session.request("local_action", 0, 0x7FFFFFFF, 0)
        play.expected[0] = 0x7FFFFFFF
        await radio.publish()
        before = play.state()
        assert not play.session.request("local_action", 0, 1, 0)
        assert play.state() == before
        radio.operation += 1
        overflow = json.dumps({**delayed, "game": fresh["game_id"], "score": 1,
                               "turnPassed": False, "operationId": radio.operation}).encode()
        await radio.raw(overflow, (Result.SCORE_OUT_OF_RANGE,))
        after = play.state()
        assert after["scores"] == before["scores"] and after["version"] == before["version"] + 1
        assert after["last_operation"][0] == radio.operation
        await radio.raw(overflow, (Result.DUPLICATE,))
        assert play.state() == after
        play.record("consumed RF overflow persisted and acknowledged; local overflow unchanged", capture=True)
        os.environ["CRIBBAGE_PLAY_EVIDENCE"] = str(args.data / "evidence")
        play.save_evidence()
        evidence_path = args.data / "evidence" / (play.name + ".json")
        evidence = json.loads(evidence_path.read_text())
        evidence.update(transport="actual Mac CoreBluetooth/GATT", completed_match_scores=completed,
                        physical_board=args.address, player_firmware="temporary radio fixture",
                        gatt_requests=radio.requests, exact_snapshots=radio.snapshots,
                        serial_transcript=radio.transcript)
        evidence_path.write_text(json.dumps(evidence, indent=2))
        print(json.dumps({"completed_match": completed, "rf_requests": len(radio.requests),
                          "exact_snapshots": len(radio.snapshots), "nimbus_buttons": play.buttons,
                          "restarts": play.restarts, "result": "PASS"}), flush=True)
    except BaseException as error:
        failure = {"error": repr(error), "serial_transcript": radio.transcript,
                   "partial_usb_line": radio.partial.decode(errors="replace"),
                   "gatt_requests": radio.requests, "exact_snapshots": radio.snapshots,
                   "events": play.events}
        (args.data / "failure.json").write_text(json.dumps(failure, indent=2))
        print(json.dumps({"error": repr(error), "last_serial": radio.transcript[-5:]}), flush=True)
        raise
    finally:
        try:
            await radio.close()
        finally:
            play.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--data", type=Path, required=True)
    asyncio.run(exercise(parser.parse_args()))
