"""Per-device ESP32 discovery and firmware-evidence acceptance tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.deployment.receiver_firmware_inventory import (
    FirmwareInventoryRecord,
    ReceiverUSBDevice,
    inventory_path,
    parse_platformio_receiver_devices,
    plan_receiver_flashes,
    read_firmware_inventory,
    write_firmware_inventory,
)


INSTALLATION_DIGEST = "a" * 64
FIRMWARE_SHA256 = "b" * 64
ENVIRONMENT = "esp32-s3-devkitc-1-local-canary"


def platformio_payload(
    serials: tuple[str, ...] = (
        "02:10:20:30:40:01",
        "02:10:20:30:40:02",
        "02:10:20:30:40:03",
        "02:10:20:30:40:04",
    ),
) -> list[dict[str, str]]:
    payload = [{"port": "/dev/ttyS0", "description": "n/a", "hwid": "n/a"}]
    # Deliberately reverse tty enumeration relative to physical USB location.
    for index, serial in enumerate(reversed(serials)):
        location = 4 - index
        payload.append(
            {
                "port": f"/dev/ttyACM{index}",
                "description": "USB JTAG/serial debug unit",
                "hwid": (
                    f"USB VID:PID=303A:1001 SER={serial} "
                    f"LOCATION=1-1.{location}:1.0"
                ),
            }
        )
    return payload


def devices() -> tuple[ReceiverUSBDevice, ...]:
    return parse_platformio_receiver_devices(
        platformio_payload(), receiver_count=4
    )


def records_for(
    observed: tuple[ReceiverUSBDevice, ...],
    *,
    installation_digest: str = INSTALLATION_DIGEST,
) -> dict[str, FirmwareInventoryRecord]:
    return {
        device.hardware_serial: FirmwareInventoryRecord(
            hardware_serial=device.hardware_serial,
            installation_digest=installation_digest,
            firmware_environment=ENVIRONMENT,
            firmware_sha256=FIRMWARE_SHA256,
        )
        for device in observed
    }


class ReceiverHardwareDiscoveryTests(unittest.TestCase):
    def test_parses_factory_serial_and_sorts_by_physical_usb_location(self) -> None:
        parsed = parse_platformio_receiver_devices(
            json.dumps(platformio_payload()), receiver_count=4
        )

        self.assertEqual(
            [item.physical_location for item in parsed],
            ["1-1.1:1.0", "1-1.2:1.0", "1-1.3:1.0", "1-1.4:1.0"],
        )
        self.assertEqual(
            [item.hardware_serial for item in parsed],
            [
                "02:10:20:30:40:01",
                "02:10:20:30:40:02",
                "02:10:20:30:40:03",
                "02:10:20:30:40:04",
            ],
        )
        self.assertEqual(parsed[0].port, "/dev/ttyACM3")

    def test_fails_closed_on_missing_or_duplicate_hardware_identity(self) -> None:
        cases = {}
        missing_serial = platformio_payload()
        missing_serial[1]["hwid"] = "USB VID:PID=303A:1001 LOCATION=1-1.4:1.0"
        cases["no unique USB serial"] = missing_serial

        missing_location = platformio_payload()
        missing_location[1]["hwid"] = (
            "USB VID:PID=303A:1001 SER=02:10:20:30:40:04"
        )
        cases["no safe physical USB location"] = missing_location

        duplicate_serial = platformio_payload()
        duplicate_serial[1]["hwid"] = duplicate_serial[2]["hwid"].replace(
            "LOCATION=1-1.3:1.0", "LOCATION=1-1.4:1.0"
        )
        cases["duplicate receiver hardware serials"] = duplicate_serial

        duplicate_location = platformio_payload()
        duplicate_location[1]["hwid"] = duplicate_location[1]["hwid"].replace(
            "LOCATION=1-1.4:1.0", "LOCATION=1-1.3:1.0"
        )
        cases["duplicate receiver physical USB locations"] = duplicate_location

        for expected, payload in cases.items():
            with self.subTest(expected=expected), self.assertRaisesRegex(
                RuntimeError, expected
            ):
                parse_platformio_receiver_devices(payload, receiver_count=4)

    def test_requires_exact_receiver_count_and_mac_form_serial(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "expected exactly 4"):
            parse_platformio_receiver_devices(
                platformio_payload()[:-1], receiver_count=4
            )
        invalid = platformio_payload()
        invalid[1]["hwid"] = invalid[1]["hwid"].replace(
            "02:10:20:30:40:04", "generic-usb-serial"
        )
        with self.assertRaisesRegex(RuntimeError, "factory MAC"):
            parse_platformio_receiver_devices(invalid, receiver_count=4)


class ReceiverFlashPlanningTests(unittest.TestCase):
    def plan(self, observed, installed, **changes):
        values = {
            "installation_digest": INSTALLATION_DIGEST,
            "firmware_environment": ENVIRONMENT,
            "firmware_sha256": FIRMWARE_SHA256,
        }
        values.update(changes)
        return plan_receiver_flashes(observed, installed, **values)

    def test_missing_inventory_safely_migrates_by_flashing_every_device(self) -> None:
        observed = devices()
        targets = self.plan(observed, {})

        self.assertEqual([item.device for item in targets], list(observed))
        self.assertEqual({item.reason for item in targets}, {"unrecorded_hardware"})

    def test_new_hardware_serial_selects_only_replaced_device(self) -> None:
        before = devices()
        installed = records_for(before)
        replacement = ReceiverUSBDevice(
            port=before[2].port,
            hardware_serial="10:20:30:40:50:60",
            physical_location=before[2].physical_location,
        )
        after = (*before[:2], replacement, *before[3:])

        targets = self.plan(after, installed)

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].device, replacement)
        self.assertEqual(targets[0].reason, "unrecorded_hardware")

    def test_changed_image_marker_or_force_selects_every_device(self) -> None:
        observed = devices()
        installed = records_for(observed)
        cases = (
            ({"installation_digest": "c" * 64}, "installation_digest_changed"),
            ({"aggregate_marker_matches": False}, "aggregate_marker_mismatch"),
            ({"force": True}, "forced"),
        )
        for changes, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                targets = self.plan(observed, installed, **changes)
                self.assertEqual(len(targets), 4)
                self.assertEqual(
                    {item.reason for item in targets}, {expected_reason}
                )

    def test_matching_per_device_evidence_is_a_true_flash_noop(self) -> None:
        observed = devices()
        self.assertEqual(self.plan(observed, records_for(observed)), ())


class ReceiverFirmwareInventoryPersistenceTests(unittest.TestCase):
    def test_atomic_round_trip_records_only_the_current_hardware_roster(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            observed = devices()
            path = write_firmware_inventory(
                root,
                observed,
                installation_digest=INSTALLATION_DIGEST,
                firmware_environment=ENVIRONMENT,
                firmware_sha256=FIRMWARE_SHA256,
            )

            self.assertEqual(path, inventory_path(root))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            restored = read_firmware_inventory(root)
            self.assertEqual(set(restored), {item.hardware_serial for item in observed})
            self.assertEqual(
                {item.installation_digest for item in restored.values()},
                {INSTALLATION_DIGEST},
            )
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_missing_inventory_is_no_evidence_but_unsafe_state_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            self.assertEqual(read_firmware_inventory(root), {})

            path = inventory_path(root)
            path.parent.mkdir(parents=True)
            sentinel = root / "sentinel"
            sentinel.write_text("do not trust\n", encoding="utf-8")
            path.symlink_to(sentinel)
            with self.assertRaisesRegex(RuntimeError, "non-symlink regular file"):
                read_firmware_inventory(root)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not trust\n")

            path.unlink()
            path.write_text("not-json\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unreadable"):
                read_firmware_inventory(root)


if __name__ == "__main__":
    unittest.main()
