"""Actual CoreBluetooth/GATT probe. Run only after verified firmware backups."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

import serial
from bleak import BleakClient, BleakScanner

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from drivers.cribbage_session import CribbageSession, Library  # noqa: E402
from drivers.cribbage_ble import IDENTITY, PROTOCOL, UPLINK, DOWNLINK, PAIRING  # noqa: E402


async def probe(args):
    transcript = []
    handle = serial.Serial(port=None, baudrate=115200, timeout=.1)
    handle.port, handle.dtr, handle.rts = args.port, False, False
    handle.open()
    session = CribbageSession(args.data / "state.json", library=Library(args.library))
    token = None
    try:
        await asyncio.sleep(1)
        handle.write(b"?\n")
        deadline = time.monotonic() + 5
        public = None
        while time.monotonic() < deadline:
            line = (await asyncio.to_thread(handle.readline)).decode(errors="replace").strip()
            if line:
                transcript.append({"serial": line})
            if line.startswith("READY "):
                _, ready_node, ready_address, ready_public = line.split()
                assert ready_address.upper() == args.address.upper() and ready_public == "1"
                public = int(ready_public)
                break
        if public is None:
            raise RuntimeError("No verified public radio identity from the USB-identified board")
        handle.write(b"P " + session.discovery().hex().encode() + b"\n")
        deadline = time.monotonic() + 5
        paired = False
        while time.monotonic() < deadline:
            line = (await asyncio.to_thread(handle.readline)).decode(errors="replace").strip()
            transcript.append({"serial": line})
            if line == "PAIRED":
                paired = True
                break
        assert paired
        devices = await BleakScanner.discover(timeout=6, return_adv=True, cb={"use_bdaddr": True})
        target = next((device for device, _adv in devices.values() if device.address.upper() == args.address.upper()), None)
        if target is None:
            raise RuntimeError("The USB-identified peripheral is not advertising")
        async with BleakClient(target, timeout=8) as client:
            services = [{"service": service.uuid, "characteristics": [c.uuid for c in service.characteristics]} for service in client.services]
            print(json.dumps({"gatt_inventory": services}), flush=True)
            node = bytes(await client.read_gatt_char(IDENTITY))
            protocol = bytes(await client.read_gatt_char(PROTOCOL))
            assert len(node) == 4 and len(protocol) == 2
            assert int.from_bytes(node, "little") == int(ready_node)
            # Address is supplied by CoreBluetooth's radio metadata, never game JSON.
            token = session.admit(int.from_bytes(node, "little"), int.from_bytes(protocol, "little"),
                                  public, int(target.address.replace(":", ""), 16),
                                  bytes(await client.read_gatt_char(PAIRING)))
            assert token is not None
            await client.start_notify(UPLINK, lambda _char, data: transcript.append({"uplink": bytes(data).decode()}))
            if session.latest().started:
                assert session.new_game()
            for player in (0, 1):
                assert session.request("set_lobby_mode", player, 0)
            assert session.request("start")
            assert session.request("local_action", 0, 17, 0)
            wire = session.wire()
            await client.write_gatt_char(DOWNLINK, wire, response=True)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                line = await asyncio.to_thread(handle.readline)
                if line:
                    decoded = line.decode(errors="replace").strip()
                    transcript.append({"serial": decoded})
                    print(decoded, flush=True)
            faults = [row["serial"] for row in transcript if row.get("serial", "").startswith("FAULT ")]
            accepted = any(row.get("serial", "") == "RX " + wire.decode() for row in transcript) and not faults
            result = {"transport": "actual Mac CoreBluetooth", "peripheral": target.address,
                      "identity_node": int.from_bytes(node, "little"), "protocol": int.from_bytes(protocol, "little"),
                      "wire_bytes": len(wire), "snapshot_accepted": accepted, "transcript": transcript}
            (args.data / "probe-result.json").write_text(json.dumps(result, indent=2))
            print(json.dumps({key: value for key, value in result.items() if key != "transcript"}), flush=True)
            if not accepted:
                raise RuntimeError("No exact full snapshot acknowledgement; inspect peer transport diagnostics")
    except Exception as error:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            line = await asyncio.to_thread(handle.readline)
            if line:
                decoded = line.decode(errors="replace").strip()
                transcript.append({"serial": decoded})
                print(decoded, flush=True)
        (args.data / "probe-failure.json").write_text(json.dumps({"error": repr(error), "transcript": transcript}, indent=2))
        raise
    finally:
        if token is not None:
            session.disconnect(token)
        session.close()
        handle.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    asyncio.run(probe(args))
