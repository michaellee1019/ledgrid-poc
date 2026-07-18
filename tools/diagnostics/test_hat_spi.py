#!/usr/bin/env python3
"""Quick check that both HAT ESP32 modules respond over SPI0."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from drivers.spi_controller import CMD_PING, LEDController

ACTIVE_SPI_ON = re.compile(r"^\s*dtparam=spi=on\s*$", re.MULTILINE)


def check_boot_config() -> bool:
    paths = ["/boot/firmware/config.txt", "/boot/config.txt"]
    for path in paths:
        if not os.path.exists(path):
            continue
        print(f"--- {path} ---")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        for line in text.splitlines():
            if "spi" in line.lower():
                print(line.rstrip())
        if os.environ.get("LEDGRID_HAT", "1").lower() in ("1", "true", "yes"):
            if ACTIVE_SPI_ON.search(text):
                print("OK: SPI0 enabled (dtparam=spi=on)")
                return True
            print("ERROR: missing dtparam=spi=on")
            return False
        return True
    print("WARNING: no boot config file found")
    return False


def check_spidev_nodes() -> bool:
    print("--- /dev/spidev* ---")
    required = [Path("/dev/spidev0.0"), Path("/dev/spidev0.1")]
    ok = True
    for node in required:
        if node.exists():
            print(node)
        else:
            print(f"ERROR: missing {node}")
            ok = False
    for node in sorted(Path("/dev").glob("spidev*")):
        if node not in required:
            print(node)
    return ok


def ping_device(label: str, bus: int, device: int) -> bool:
    print(f"--- {label}: /dev/spidev{bus}.{device} ---")
    controller = None
    try:
        controller = LEDController(
            bus=bus,
            device=device,
            strips=8,
            leds_per_strip=140,
            debug=True,
        )
        controller._xfer([CMD_PING])
        print(f"{label}: PING OK")
        return True
    except Exception as exc:
        print(f"ERROR: {label} failed: {exc}")
        return False
    finally:
        if controller is not None:
            controller.close()


def stop_controller_service() -> None:
    print("--- ledgrid service ---")
    result = subprocess.run(
        ["systemctl", "is-active", "ledgrid.service"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout.strip() != "active":
        print("ledgrid.service is not running")
        return
    print("Stopping ledgrid.service for exclusive SPI access...")
    subprocess.run(["sudo", "systemctl", "stop", "ledgrid.service"], check=False)


def main() -> int:
    os.environ.setdefault("LEDGRID_HAT", "1")

    print("LED Grid Wall HAT SPI diagnostic")
    print(f"LEDGRID_HAT={os.environ.get('LEDGRID_HAT')}")
    print()

    stop_controller_service()
    print()

    boot_ok = check_boot_config()
    print()
    nodes_ok = check_spidev_nodes()
    print()
    esp1_ok = ping_device("ESP1", bus=0, device=0)
    print()
    esp2_ok = ping_device("ESP2", bus=0, device=1)

    return 0 if boot_ok and nodes_ok and esp1_ok and esp2_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
