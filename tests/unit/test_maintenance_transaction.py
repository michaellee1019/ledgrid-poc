"""Synthetic durability and exclusive-presentation tests for maintenance."""

from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path

from animation.core.manager import AnimationManager
from ipc.control_channel import FileControlChannel
from scripts.start_server import process_maintenance_requests
from web.maintenance_api import (
    MAINTENANCE_SCHEMA,
    MAINTENANCE_SCHEMA_VERSION,
    MaintenanceRequest,
    identity_from_status,
)


AUTHORITY = "a" * 64
REQUEST_ID = "12345678-1234-4234-9234-123456789abc"


def _status():
    return {
        "controller_session_id": "session-a",
        "controller_state_revision": 7,
        "receiver_roster": [
            {"logical_device": number, "connected": True, "route": [number, 0],
             "hardware_serial": f"serial-{number}", "firmware_revision": "firmware"}
            for number in range(5)
        ],
    }


def _command():
    status = _status()
    return {
        "schema": MAINTENANCE_SCHEMA,
        "schema_version": MAINTENANCE_SCHEMA_VERSION,
        "request_id": REQUEST_ID,
        "diagnostic": "receiver_band",
        "target": {"receiver_id": 2},
        "intensity": 8,
        "duration_seconds": 0.01,
        "expected_identity": identity_from_status(status).to_dict(),
        "provenance": {"operator": "test", "source_revision": "b" * 40, "purpose": "test"},
    }


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    inline_show = True

    def __init__(
        self,
        *,
        restore_fails=False,
        acknowledgements=range(5),
        drained=None,
        wrong_restore_id=False,
    ):
        self.restore_fails = restore_fails
        self.acknowledgements = acknowledgements
        self.drained = drained
        self.wrong_restore_id = wrong_restore_id
        self.calls = []
        self.idle = []

    def present_trusted_full_frame(self, request_id, frame):
        if self.drained is not None:
            self.drained.assert_called_once()
        self.calls.append((request_id, frame))
        if request_id.endswith(":restore") and self.restore_fails:
            raise RuntimeError("restore disconnected")
        return {
            "request_id": (
                "wrong-restore-id"
                if request_id.endswith(":restore") and self.wrong_restore_id
                else request_id
            ),
            "authority_digest": AUTHORITY,
            "acknowledged_receivers": [
                {"logical_device": item} for item in self.acknowledgements
            ],
        }

    def set_all_pixels(self, frame):
        self.idle.append(frame)


def _manager(controller):
    manager = AnimationManager.__new__(AnimationManager)
    manager.controller = controller
    manager.frame_data_lock = threading.Lock()
    manager.current_frame_data = [(1, 2, 3)] * (33 * 138)
    manager._maintenance_lease_lock = threading.RLock()
    manager._maintenance_transaction_lock = threading.Lock()
    manager._maintenance_pause_condition = threading.Condition(threading.RLock())
    manager._maintenance_pause_requested = False
    manager._maintenance_pause_acknowledged = False
    manager.animation_thread = None
    manager._presentation_io_lock = threading.Lock()
    manager._scene_mode = True
    manager._active_scene_state = object()
    manager.output_brightness = 12
    manager.is_running = True
    return manager


class MaintenanceChannelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.channel = FileControlChannel(str(root / "control.json"), str(root / "status.json"))

    def test_immutable_idempotent_lifecycle_and_restart_nonreplay(self):
        command = _command()
        first = self.channel.enqueue_maintenance_request(command, authority_digest=AUTHORITY)
        self.assertEqual(self.channel.enqueue_maintenance_request(command, authority_digest=AUTHORITY), first)
        changed = dict(command)
        changed["intensity"] = 9
        with self.assertRaises(FileExistsError):
            self.channel.enqueue_maintenance_request(changed, authority_digest=AUTHORITY)
        self.channel.write_maintenance_status(REQUEST_ID, phase="running", authority_digest=AUTHORITY)

        manager = _manager(_Controller())
        process_maintenance_requests(
            self.channel, manager, authority_digest=AUTHORITY,
            controller_status=lambda: _status(),
        )
        result = self.channel.read_maintenance_result(REQUEST_ID)
        self.assertEqual(result["phase"], "failed")
        self.assertEqual(manager.controller.calls, [])

    def test_stale_authority_is_rejected_before_manager_mutation(self):
        self.channel.enqueue_maintenance_request(_command(), authority_digest=AUTHORITY)
        manager = _manager(_Controller())
        process_maintenance_requests(
            self.channel, manager, authority_digest="c" * 64,
            controller_status=lambda: _status(),
        )
        self.assertEqual(self.channel.read_maintenance_result(REQUEST_ID)["phase"], "rejected")
        self.assertEqual(manager.controller.calls, [])

    def test_queued_request_reaches_one_correlated_restored_result(self):
        self.channel.enqueue_maintenance_request(_command(), authority_digest=AUTHORITY)
        manager = _manager(_Controller())
        self.assertEqual(
            process_maintenance_requests(
                self.channel, manager, authority_digest=AUTHORITY,
                controller_status=lambda: _status(),
            ),
            1,
        )
        result = self.channel.read_maintenance_result(REQUEST_ID)
        self.assertEqual(result["phase"], "restored")
        self.assertEqual(result["authority_digest"], AUTHORITY)
        self.assertEqual(result["result"]["receipt"]["request_id"], REQUEST_ID)


class MaintenanceLeaseTests(unittest.TestCase):
    def test_transaction_restores_exact_frame_and_blocks_interleaved_present(self):
        controller = _Controller()
        manager = _manager(controller)
        request = MaintenanceRequest.from_mapping(_command())
        entered = threading.Event()
        release = threading.Event()

        def hold(_seconds):
            entered.set()
            self.assertTrue(release.wait(1))

        worker = threading.Thread(
            target=lambda: manager.run_maintenance_transaction(request, authority_digest=AUTHORITY, sleep=hold)
        )
        worker.start()
        self.assertTrue(entered.wait(1))
        normal = threading.Thread(target=lambda: manager._present_frame([(9, 9, 9)], None, False, True))
        normal.start()
        self.assertTrue(normal.is_alive())
        release.set()
        worker.join(1)
        normal.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(controller.calls[1][1], [(1, 2, 3)] * (33 * 138))

    def test_restore_failure_enters_visible_safe_idle(self):
        controller = _Controller(restore_fails=True)
        manager = _manager(controller)
        with self.assertRaisesRegex(RuntimeError, "safe idle"):
            manager.run_maintenance_transaction(
                MaintenanceRequest.from_mapping(_command()), authority_digest=AUTHORITY,
                sleep=lambda _seconds: None,
            )
        self.assertEqual(len(controller.idle), 1)

    def test_partial_acknowledgement_enters_visible_safe_idle(self):
        controller = _Controller(acknowledgements=range(4))
        manager = _manager(controller)
        with self.assertRaisesRegex(RuntimeError, "safe idle"):
            manager.run_maintenance_transaction(
                MaintenanceRequest.from_mapping(_command()), authority_digest=AUTHORITY,
                sleep=lambda _seconds: None,
            )
        self.assertEqual(len(controller.idle), 1)

    def test_render_boundary_drains_before_capture_and_parks_generation(self):
        presentation_started = threading.Event()
        presentation_release = threading.Event()
        presentation_drained = threading.Event()
        hold_entered = threading.Event()
        hold_release = threading.Event()
        render_boundary_ready = threading.Event()
        permit_boundary = threading.Event()
        render_entered = threading.Event()

        def pending_presentation():
            presentation_started.set()
            self.assertTrue(presentation_release.wait(1))
            presentation_drained.set()
            return (0.0, 0.0)

        future = Future()

        def complete_presentation():
            try:
                future.set_result(pending_presentation())
            except BaseException as exc:  # pragma: no cover - test plumbing
                future.set_exception(exc)

        controller = _Controller()
        manager = _manager(controller)

        def render_loop_boundary():
            manager.animation_thread = threading.current_thread()
            render_boundary_ready.set()
            self.assertTrue(permit_boundary.wait(1))
            manager._maintenance_pause_at_render_boundary(future)
            render_entered.set()

        presenter = threading.Thread(target=complete_presentation)
        renderer = threading.Thread(target=render_loop_boundary)
        presenter.start()
        renderer.start()
        self.assertTrue(presentation_started.wait(1))
        self.assertTrue(render_boundary_ready.wait(1))

        def hold(_seconds):
            hold_entered.set()
            self.assertTrue(hold_release.wait(1))

        transaction = threading.Thread(
            target=lambda: manager.run_maintenance_transaction(
                MaintenanceRequest.from_mapping(_command()),
                authority_digest=AUTHORITY,
                sleep=hold,
            )
        )
        transaction.start()
        permit_boundary.set()
        self.assertFalse(hold_entered.wait(0.05))
        presentation_release.set()
        self.assertTrue(hold_entered.wait(1))
        self.assertTrue(presentation_drained.is_set())
        self.assertFalse(render_entered.is_set())
        hold_release.set()
        transaction.join(1)
        renderer.join(1)
        presenter.join(1)
        self.assertFalse(transaction.is_alive())
        self.assertTrue(render_entered.is_set())

    def test_restore_receipt_must_match_restore_request_and_full_roster(self):
        controller = _Controller(wrong_restore_id=True)
        manager = _manager(controller)
        with self.assertRaisesRegex(RuntimeError, "safe idle"):
            manager.run_maintenance_transaction(
                MaintenanceRequest.from_mapping(_command()), authority_digest=AUTHORITY,
                sleep=lambda _seconds: None,
            )
        self.assertEqual(len(controller.idle), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
