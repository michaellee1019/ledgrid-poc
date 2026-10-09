#!/usr/bin/env python3
"""Build full-frame firmware and flash the five explicitly mapped USB receivers.

Uploads are sequential. A partial flash fails and leaves the service stopped;
rerunning full deployment flashes all five again. No installed-image ledger.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

ROUTES = [(0, 0), (0, 1), (1, 1), (1, 0), (1, 2)]
ENVIRONMENT = "esp32-s3-devkitc-1"


def serial_identity(value: str) -> str:
    identity = re.sub(r"[:\-]", "", value).lower()
    if re.fullmatch(r"[0-9a-f]{12}", identity) is None:
        raise ValueError(f"Invalid USB receiver serial {value!r}")
    return identity


def mapped_ports(mapping: dict, devices: list[dict]) -> list[str]:
    entries = mapping.get("identities")
    if not isinstance(entries, list) or len(entries) != 5:
        raise ValueError("Firmware flashing requires five explicit receiver mappings")
    selected = sorted(entries, key=lambda item: item["logical_device"])
    if [item.get("logical_device") for item in selected] != list(range(5)) or [item.get("spi_route") for item in selected] != [list(route) for route in ROUTES]:
        raise ValueError("Receiver mapping does not match the installed wall routes")
    serials = [serial_identity(item["hardware_serial"]) for item in selected]
    if len(set(serials)) != 5:
        raise ValueError("Duplicate mapped receiver serial")
    ports = {}
    for device in devices:
        port = device.get("port", device.get("path", ""))
        hwid = device.get("hwid", "")
        serial = re.search(r"(?:^|\s)SER=([^\s]+)", hwid)
        if not serial or not re.fullmatch(r"/dev/tty(?:ACM|USB)\d+", port):
            continue
        identity = serial_identity(serial[1])
        if identity not in serials:
            continue
        if identity in ports:
            raise ValueError(f"Receiver serial appears on multiple USB ports: {identity}")
        ports[identity] = port
    if any(serial not in ports for serial in serials):
        raise ValueError("A mapped receiver is absent from USB discovery; check connections")
    result = [ports[serial] for serial in serials]
    if len(set(result)) != 5:
        raise ValueError("Mapped USB receiver ports are not unique")
    return result


def flash(root: Path) -> None:
    pio = Path.home() / ".platformio-venv/bin/pio"
    if not pio.is_file():
        raise RuntimeError("PlatformIO is missing; run just setup")
    mapping_path = root / "data/run_state/receiver_identity_mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    devices = json.loads(subprocess.check_output([str(pio), "device", "list", "--json-output"]))
    ports = mapped_ports(mapping, devices)
    firmware = root / "app/firmware/esp32"
    subprocess.run([str(pio), "run", "-d", str(firmware), "-e", ENVIRONMENT], check=True)
    for logical_device, port in enumerate(ports):
        print(f"Flashing receiver {logical_device} on {port}", flush=True)
        subprocess.run([str(pio), "run", "-d", str(firmware), "-e", ENVIRONMENT, "-t", "upload", "--upload-port", port], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    flash(parser.parse_args().root)


if __name__ == "__main__":
    main()
