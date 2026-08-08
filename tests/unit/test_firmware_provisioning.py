from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tools.deployment.check_receiver_readiness import (
    REQUIRED,
    validate_status,
    wait_for_status,
)
from tools.deployment.firmware_provisioning import (
    PINNED_PLATFORM,
    initialize,
    load_provisioning,
    render_platformio_config,
    render_sdkconfig,
)


def ports() -> list[str]:
    return [f"/dev/serial/by-id/usb-Espressif_receiver-{index}" for index in range(4)]


class FirmwareProvisioningTests(unittest.TestCase):
    def test_initialization_keeps_private_key_local_and_binds_four_stable_ports(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            created = initialize(state, ports())
            loaded = load_provisioning(
                state / "deploy.env", public_key=state / "public.pem"
            )

            self.assertEqual(loaded, created)
            self.assertEqual(loaded.ports, tuple(ports()))
            self.assertEqual(
                loaded.host_trusted_keys,
                f"{loaded.key_id}=run_state/firmware/public.pem",
            )
            self.assertEqual(os.stat(state / "signing_private.pem").st_mode & 0o777, 0o600)
            self.assertNotIn("PRIVATE", (state / "deploy.env").read_text())

    def test_duplicate_or_unstable_ports_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with self.assertRaisesRegex(ValueError, "four unique"):
                initialize(state, [ports()[0]] * 4)
            with self.assertRaisesRegex(ValueError, "four unique"):
                initialize(state, ["/dev/ttyACM0", *ports()[1:]])

    def test_generated_build_is_pinned_signed_and_device_specific(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            provisioning = initialize(state, ports())
            first = render_platformio_config(provisioning, 0, state / "build-0")
            third = render_platformio_config(provisioning, 2, state / "build-2")

            self.assertIn(f"platform = {PINNED_PLATFORM}", first)
            self.assertNotIn("/stable/", first)
            self.assertNotEqual(first, third)
            sdkconfig = render_sdkconfig(
                "CONFIG_SPIRAM=y\nCONFIG_LEDGRID_LOGICAL_DEVICE=0\n",
                provisioning,
                2,
            )
            self.assertIn(f'CONFIG_LEDGRID_TRUSTED_KEY_ID="{provisioning.key_id}"', sdkconfig)
            self.assertIn("# CONFIG_LEDGRID_ALLOW_UNSIGNED_DEVELOPMENT is not set", sdkconfig)
            self.assertIn("CONFIG_LEDGRID_LOGICAL_DEVICE=2", sdkconfig)
            self.assertNotIn("CONFIG_LEDGRID_LOGICAL_DEVICE=0", sdkconfig)


class ReceiverReadinessTests(unittest.TestCase):
    @staticmethod
    def payload() -> dict:
        return {
            "updated_at": 200.0,
            "driver_stats": {
                "devices": [
                    {
                        "receiver_status_version": 3,
                        "receiver_capabilities": REQUIRED | (index << 16),
                        "receiver_logical_device": index,
                        "receiver_cache_free_bytes": 1024,
                    }
                    for index in range(4)
                ]
            }
        }

    def test_all_four_signed_receivers_are_required(self):
        summary = validate_status(self.payload())
        self.assertEqual([item["logical_device"] for item in summary], list(range(4)))

        for mutation, message in (
            (lambda value: value["driver_stats"]["devices"].pop(), "exactly four"),
            (lambda value: value["driver_stats"]["devices"][2].update(
                receiver_logical_device=3), "identity"),
            (lambda value: value["driver_stats"]["devices"][1].update(
                receiver_capabilities=0), "lacks capabilities"),
        ):
            with self.subTest(message=message):
                value = json.loads(json.dumps(self.payload()))
                mutation(value)
                with self.assertRaisesRegex(ValueError, message):
                    validate_status(value)

    def test_both_missing_spi1_status_paths_are_reported_with_wiring_hint(self):
        value = self.payload()
        for index in (2, 3):
            value["driver_stats"]["devices"][index].update({
                "receiver_status_version": 0,
                "receiver_status_seen": False,
                "receiver_capabilities": 0,
                "receiver_logical_device": None,
            })

        with self.assertRaises(ValueError) as raised:
            validate_status(value)

        message = str(raised.exception)
        self.assertIn("receiver 2 has status version 0", message)
        self.assertIn("receiver 3 has status version 0", message)
        self.assertIn("SPI1", message)
        self.assertIn("Pi GPIO 19 / physical pin 35", message)

    def test_readiness_waits_through_empty_startup_status(self):
        empty = self.payload()
        for device in empty["driver_stats"]["devices"]:
            device.update({
                "receiver_status_version": 0,
                "receiver_status_seen": False,
                "receiver_capabilities": 0,
                "receiver_logical_device": None,
            })
        responses = iter((empty, empty, self.payload()))

        summary = wait_for_status(
            lambda: next(responses), wait_seconds=1.0, interval_seconds=0.0
        )

        self.assertEqual([item["logical_device"] for item in summary], list(range(4)))

    def test_readiness_timeout_keeps_complete_receiver_diagnosis(self):
        empty = self.payload()
        for device in empty["driver_stats"]["devices"]:
            device.update({
                "receiver_status_version": 0,
                "receiver_status_seen": False,
                "receiver_capabilities": 0,
                "receiver_logical_device": None,
            })

        with self.assertRaises(ValueError) as raised:
            wait_for_status(lambda: empty, wait_seconds=0.0)

        for index in range(4):
            self.assertIn(f"receiver {index} has status version 0", str(raised.exception))

    def test_readiness_rejects_preserved_status_from_before_restart(self):
        stale = self.payload()
        current = self.payload()
        stale["updated_at"] = 199.0
        current["updated_at"] = 201.0
        responses = iter((stale, current))

        summary = wait_for_status(
            lambda: next(responses),
            wait_seconds=1.0,
            interval_seconds=0.0,
            min_updated_at=200.0,
        )

        self.assertEqual([item["logical_device"] for item in summary], list(range(4)))

        with self.assertRaisesRegex(ValueError, "predates"):
            validate_status(stale, min_updated_at=200.0)


if __name__ == "__main__":
    unittest.main()
