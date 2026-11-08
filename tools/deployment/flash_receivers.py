#!/usr/bin/env python3
"""Build full-frame firmware and flash mapped USB Serial/JTAG receivers.

Uploads are sequential. A partial flash fails and leaves the service stopped;
rerunning flashes every mapped receiver again. No installed-image ledger.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

try:
    from platformio_cli import select_platformio
except ModuleNotFoundError:  # Imported as tools.deployment.flash_receivers in tests.
    from tools.deployment.platformio_cli import select_platformio

ROUTES = [(1, 0), (1, 1), (0, 0), (0, 1), (0, 3)]
ENVIRONMENT = "esp32-s3-devkitc-1"
_SERIAL_PORT = re.compile(r"/dev/tty(?:ACM|USB)\d+")


def serial_identity(value: str) -> str:
    identity = re.sub(r"[:\-]", "", value).lower()
    if re.fullmatch(r"[0-9a-f]{12}", identity) is None:
        raise ValueError(f"Invalid USB receiver serial {value!r}")
    return identity


def mapped_ports(mapping: dict, devices: list[dict]) -> list[str]:
    entries = mapping.get("identities")
    if not isinstance(entries, list):
        raise ValueError("Firmware flashing requires explicit receiver mappings")
    selected = sorted(entries, key=lambda item: item["logical_device"])
    routes = [item.get("spi_route") for item in selected]
    if routes != [list(route) for route in ROUTES]:
        raise ValueError("Receiver mapping must match all five installed wall routes")
    expected = len(ROUTES)
    if [item.get("logical_device") for item in selected] != list(range(expected)):
        raise ValueError("Receiver mapping logical devices must be contiguous from 0")
    serials = [serial_identity(item["hardware_serial"]) for item in selected]
    if len(set(serials)) != expected:
        raise ValueError("Duplicate mapped receiver serial")
    ports = {}
    for device in devices:
        port = device.get("port", device.get("path", ""))
        hwid = device.get("hwid", "")
        serial = re.search(r"(?:^|\s)SER=([^\s]+)", hwid)
        if not serial or not _SERIAL_PORT.fullmatch(port):
            continue
        identity = re.sub(r"[:\-]", "", serial[1]).lower()
        if identity not in serials:
            continue
        if identity in ports:
            raise ValueError(f"Receiver serial appears on multiple USB ports: {identity}")
        ports[identity] = port
    if any(serial not in ports for serial in serials):
        raise ValueError("A mapped receiver is absent from USB discovery; check connections")
    result = [ports[serial] for serial in serials]
    if len(set(result)) != expected:
        raise ValueError("Mapped USB receiver ports are not unique")
    return result


def flash(root: Path) -> None:
    mapping_path = root / "data/run_state/receiver_identity_mapping.json"
    if not mapping_path.is_file():
        raise RuntimeError(f"Full deployment requires the five-receiver USB-to-SPI mapping at {mapping_path}")
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    try:
        pio = select_platformio()
    except SystemExit as exc:
        raise RuntimeError("PlatformIO is missing or unusable; run just setup") from exc
    devices = json.loads(subprocess.check_output([str(pio), "device", "list", "--json-output"]))
    ports = mapped_ports(mapping, devices)
    firmware = root / "app/firmware/esp32"
    subprocess.run([str(pio), "run", "-d", str(firmware), "-e", ENVIRONMENT], check=True)
    for logical_device, port in enumerate(ports):
        print(f"Flashing receiver {logical_device} on {port}", flush=True)
        subprocess.run([str(pio), "run", "-d", str(firmware), "-e", ENVIRONMENT, "-t", "upload", "--upload-port", port], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    root = parser.parse_args().root
    try:
        flash(root)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Receiver flash failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
