"""Causal protected-v8 proof for the first host-full frame, without hardware."""

from __future__ import annotations

import unittest

import numpy as np

from tests.unit.test_receiver_frame_receipts import _receipt_controller


class _DisplayDevice:
    def __init__(self, identity, *, display_after=1, corrupt=False, supersede=False):
        self.hardware_serial = identity.hardware_serial
        self.firmware_sha256 = identity.firmware_sha256
        self.receiver_identity_authority_digest = "a" * 64
        self.logical_device = identity.logical_device
        self.display_after = display_after
        self.corrupt = corrupt
        self.supersede = supersede
        self.accepted = 19
        self.displayed = 12
        self.accepted_sequence = 301
        self.displayed_sequence = 294
        self.errors = 0
        self.superseded = 0
        self.integrity_errors = 0
        self.polls_after_write = 0
        self.written = False

    def get_stats(self):
        return {"receiver_status_integrity_errors": self.integrity_errors}

    def query_causal_receiver_status(self, *, required_status_version):
        if required_status_version != 8:
            raise AssertionError("protected status-v8 is mandatory")
        if self.written:
            self.polls_after_write += 1
            if self.corrupt:
                self.integrity_errors += 1
            if self.supersede:
                self.superseded += 1
            if self.display_after is not None and self.polls_after_write >= self.display_after:
                self.displayed += 1
                self.displayed_sequence = self.accepted_sequence
                self.display_after = None
        return {
            "receiver_status_version": 8,
            "receiver_logical_device": self.logical_device,
            "receiver_frames_accepted": self.accepted,
            "receiver_frames_displayed": self.displayed,
            "receiver_last_accepted_sequence": self.accepted_sequence,
            "receiver_last_displayed_sequence": self.displayed_sequence,
            "receiver_frames_superseded": self.superseded,
            "receiver_display_errors": self.errors,
            "receiver_base_mode": 2,
        }

    def write(self):
        self.written = True
        self.accepted += 1
        self.accepted_sequence += 1

    def set_all_pixels(self, _frame, *, wall_frame_sequence=None):
        if getattr(self, "reject_write", False):
            raise RuntimeError("receiver write failed")
        if getattr(self, "reject_return", False):
            return False
        self.write()


def _wall(*, failure=None, display_after=1):
    controller = _receipt_controller()
    controller.devices = [
        _DisplayDevice(identity, display_after=(display_after if index == 3 else 1),
                       corrupt=failure == "integrity" and index == 3,
                       supersede=failure == "supersession" and index == 3)
        for index, identity in enumerate(controller.receiver_identities)
    ]

    def send(_frame):
        for index, device in enumerate(controller.devices):
            if failure == "write" and index == 3:
                return False
            device.write()
        return True

    controller.set_all_pixels = send
    return controller


class HostFullFirstDisplayTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((33 * 138, 3), dtype=np.uint8)

    def test_all_five_display_exact_new_accepted_sequence(self):
        wall = _wall(display_after=3)
        proof = wall.present_displayed_host_full_frame("scene-test", self.frame, timeout_seconds=.1)
        self.assertEqual([item["logical_device"] for item in proof["displayed_receivers"]], list(range(5)))
        self.assertEqual([item["receiver_displayed_sequence"] for item in proof["displayed_receivers"]], [302] * 5)
        self.assertGreaterEqual(wall.devices[3].polls_after_write, 3)

    def test_receiver_three_write_integrity_supersession_and_display_timeout_fail(self):
        for failure, expected in (("write", "takeover failed"),
                                  ("integrity", "integrity"),
                                  ("supersession", "supersession"),
                                  ("timeout", "timed out")):
            with self.subTest(failure=failure):
                wall = _wall(failure=failure,
                             display_after=None if failure == "timeout" else 1)
                with self.assertRaisesRegex(RuntimeError, expected):
                    wall.present_displayed_host_full_frame("scene-test", self.frame,
                                                           timeout_seconds=.015)

    def test_ordinary_set_all_returns_false_for_partial_receiver_write(self):
        wall = _wall()
        del wall.set_all_pixels
        wall._executor = None
        wall._devices_by_bus = {0: (0, 1), 1: (2, 3, 4)}
        wall._local_background_active = False
        wall._native_background_active = False
        wall._sparse_overlay_session_id = None
        wall._display_ownership_known = True
        wall.debug = False
        wall.devices[3].reject_write = True
        self.assertIs(wall.set_all_pixels(self.frame), False)
        self.assertFalse(wall.devices[3].written)
        wall.devices[3].reject_write = False
        wall.devices[3].reject_return = True
        self.assertIs(wall.set_all_pixels(self.frame), False)


if __name__ == "__main__":
    unittest.main()
