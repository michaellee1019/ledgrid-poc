"""Contract tests for the Composer-to-controller maintenance adapter."""

from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from ipc.control_channel import FileControlChannel
from ipc.scene_contract import canonical_json_sha256
from scripts.start_server import controller_maintenance_identity
from web.app import AnimationWebInterface


RELEASE = "a" * 64
AUTHORITY = "b" * 64
REQUEST_ID = "12345678-1234-4234-9234-123456789abc"


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = 33 * 138


class _Manager:
    controller = _Controller()
    preview_controller = controller

    def list_components(self):
        return []

    def list_animations(self):
        return []


def _status() -> dict:
    active_identity = {"scene": "synthetic"}
    return {
        "release_id": RELEASE,
        "controller_session_id": "synthetic-session",
        "controller_state_revision": 7,
        "active_identity": active_identity,
        "current_identity_digest": canonical_json_sha256(active_identity),
        "receiver_identity_authority_digest": AUTHORITY,
        "receiver_roster": [
            {
                "logical_device": item,
                "connected": True,
                "route": [item // 2, item % 2],
                "hardware_serial": f"aa:bb:cc:dd:ee:{item:02x}",
                "firmware_sha256": "f" * 64,
            }
            for item in range(5)
        ],
    }


def _body() -> dict:
    return {
        "diagnostic": "receiver_band",
        "target": {"receiver_id": 2},
        "intensity": 32,
        "duration_seconds": 1,
    }


class ComposerMaintenanceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.channel = FileControlChannel(str(root / "control.json"), str(root / "status.json"))
        self.channel.write_status(_status())
        self.interface = AnimationWebInterface(
            self.channel,
            _Manager(),
            release_id=RELEASE,
            activation_enabled=True,
            maintenance_enabled=True,
            project_root=root,
        )
        self.client = self.interface.app.test_client()

    def post(self, body: dict | None = None):
        return self.client.post(
            "/api/v1/composer/maintenance",
            headers={"Idempotency-Key": REQUEST_ID},
            json=_body() if body is None else body,
        )

    def test_named_request_uses_real_channel_and_exposes_queued_lifecycle(self) -> None:
        response = self.post()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["phase"], "queued")
        record = self.channel.read_maintenance_request(REQUEST_ID)
        self.assertEqual(record["authority_digest"], AUTHORITY)
        command = record["command"]
        self.assertEqual(command["diagnostic"], "receiver_band")
        self.assertEqual(command["expected_identity"]["controller_state_revision"], 7)
        self.assertNotIn("frame_data", command)
        self.assertNotIn("pixels", command)

        status = self.client.get(f"/api/v1/composer/maintenance/{REQUEST_ID}")
        payload = status.get_json()
        self.assertEqual(status.status_code, 200)
        self.assertEqual(payload["phase"], "queued")
        self.assertFalse(payload["terminal"])

    def test_exact_retry_is_idempotent_and_conflicting_retry_is_visible(self) -> None:
        self.assertEqual(self.post().status_code, 202)
        retry = self.post()
        self.assertEqual(retry.status_code, 202)
        self.assertTrue(retry.get_json()["exact_retry"])
        changed = deepcopy(_body())
        changed["intensity"] = 31
        conflict = self.post(changed)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["code"], "maintenance_conflict")

    def test_terminal_receipt_and_restore_are_not_reported_as_pending_success(self) -> None:
        self.assertEqual(self.post().status_code, 202)
        result = {
            "receipt": {"acknowledged_receivers": [{"logical_device": item} for item in range(5)]},
            "restore_receipt": {"acknowledged_receivers": [{"logical_device": item} for item in range(5)]},
        }
        self.channel.write_maintenance_status(
            REQUEST_ID, phase="running", authority_digest=AUTHORITY,
        )
        self.channel.write_maintenance_status(
            REQUEST_ID, phase="restored", authority_digest=AUTHORITY, result=result,
        )
        payload = self.client.get(f"/api/v1/composer/maintenance/{REQUEST_ID}").get_json()
        self.assertEqual(payload["phase"], "restored")
        self.assertTrue(payload["terminal"])
        self.assertEqual(payload["result"]["result"], result)

    def test_capability_default_off_gates_post_and_status(self) -> None:
        disabled = AnimationWebInterface(
            self.channel, _Manager(), release_id=RELEASE,
            activation_enabled=True, maintenance_enabled=False,
            project_root=Path(self.temporary.name),
        ).app.test_client()
        self.assertEqual(disabled.post(
            "/api/v1/composer/maintenance", headers={"Idempotency-Key": REQUEST_ID}, json=_body(),
        ).status_code, 503)
        self.assertEqual(disabled.get(
            f"/api/v1/composer/maintenance/{REQUEST_ID}",
        ).status_code, 503)

    def test_release_guard_rejects_changed_controller_before_enqueue(self) -> None:
        stale = _status()
        stale["release_id"] = "c" * 64
        self.channel.write_status(stale)
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.channel.read_maintenance_request(REQUEST_ID))

    def test_startup_publishes_only_the_pinned_complete_receiver_authority(self) -> None:
        identities = tuple(
            SimpleNamespace(
                logical_device=item,
                spi_route=(item // 2, item % 2),
                hardware_serial=f"aa:bb:cc:dd:ee:{item:02x}",
                firmware_sha256="f" * 64,
            )
            for item in range(5)
        )
        controller = SimpleNamespace(
            receiver_identity_authority_digest=AUTHORITY,
            receiver_identities=identities,
            _require_pinned_receipt_roster=lambda: identities,
        )
        identity = controller_maintenance_identity(controller)
        self.assertEqual(identity["receiver_identity_authority_digest"], AUTHORITY)
        self.assertEqual([item["logical_device"] for item in identity["receiver_roster"]], list(range(5)))
        controller.receiver_identities = controller.receiver_identities[:-1]
        self.assertIsNone(controller_maintenance_identity(controller))


if __name__ == "__main__":
    unittest.main()
