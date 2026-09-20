"""Causal protected-v8 proof for the first host-full frame, without hardware."""

from __future__ import annotations

import unittest

import numpy as np

from tests.unit.test_receiver_frame_receipts import _receipt_controller


class _DisplayDevice:
    def __init__(self, identity, *, display_after=1, corrupt=False, supersede=False,
                 predecessor_after=0, split_predecessor=False, split_new=False,
                 wrong_new=False, extra_new=False, baseline_corrupt=False,
                 baseline_supersede=False, baseline_display_error=False,
                 cancel_history=False, native_gap_on_black=1, wrong_black=False,
                 extra_black=False):
        self.hardware_serial = identity.hardware_serial
        self.firmware_sha256 = identity.firmware_sha256
        self.receiver_identity_authority_digest = "a" * 64
        self.logical_device = identity.logical_device
        self.display_delay = display_after
        self.display_after = None
        self.corrupt = corrupt
        self.supersede = supersede
        self.split_predecessor = split_predecessor
        self.split_new = split_new
        self.wrong_new = wrong_new
        self.extra_new = extra_new
        self.baseline_corrupt = baseline_corrupt
        self.baseline_supersede = baseline_supersede
        self.baseline_display_error = baseline_display_error
        self.native_gap_on_black = native_gap_on_black
        self.wrong_black = wrong_black
        self.extra_black = extra_black
        self.accepted = 19
        self.displayed = 19
        self.accepted_sequence = 301
        self.displayed_sequence = 301
        self.predecessor_pending = predecessor_after != 0
        self.predecessor_after = predecessor_after
        self.predecessor_polls = 0
        self.pending_sequence_update = None
        self.sequence_updates_applied = 0
        self.pending_write_sequence = None
        if self.predecessor_pending or cancel_history:
            self.accepted += 1
            self.accepted_sequence += 1
        self.predecessor_sequence = self.accepted_sequence
        self.errors = 0
        self.superseded = 0
        self.integrity_errors = 0
        self.polls_after_write = 0
        self.status_queries = 0
        self.written = False
        self.writes = 0

    def get_stats(self):
        return {"receiver_status_integrity_errors": self.integrity_errors}

    def query_causal_receiver_status(self, *, required_status_version):
        if required_status_version != 8:
            raise AssertionError("protected status-v8 is mandatory")
        self.status_queries += 1
        if self.baseline_corrupt and not self.written:
            self.integrity_errors += 1
        if self.pending_sequence_update is not None:
            self.displayed_sequence = self.pending_sequence_update
            self.pending_sequence_update = None
            self.sequence_updates_applied += 1
        if self.predecessor_pending:
            self.predecessor_polls += 1
            if self.predecessor_polls == 2:
                if self.baseline_supersede:
                    self.superseded += 1
                    self.predecessor_pending = False
                if self.baseline_display_error:
                    self.errors += 1
            if (self.predecessor_pending and self.predecessor_after is not None
                    and self.predecessor_polls >= self.predecessor_after):
                self.displayed += 1
                self.predecessor_pending = False
                if self.split_predecessor:
                    self.pending_sequence_update = self.predecessor_sequence
                else:
                    self.displayed_sequence = self.predecessor_sequence
        elif self.pending_write_sequence is not None:
            self.polls_after_write += 1
            if self.corrupt:
                self.integrity_errors += 1
            if self.supersede and self.writes >= 2:
                self.superseded += 1
            if self.display_after is not None and self.polls_after_write >= self.display_after:
                self.displayed += (5 if self.extra_black and self.writes == 1 else
                                   2 if self.extra_new and self.writes >= 2 else 1)
                sequence = self.pending_write_sequence
                if self.wrong_new and self.writes >= 2:
                    sequence += 1
                if self.wrong_black and self.writes == 1:
                    sequence += 1
                if self.split_new:
                    self.pending_sequence_update = sequence
                else:
                    self.displayed_sequence = sequence
                self.display_after = None
                self.pending_write_sequence = None
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
        self.writes += 1
        self.accepted += 1
        self.accepted_sequence += self.native_gap_on_black if self.writes == 1 else 1
        self.pending_write_sequence = self.accepted_sequence
        self.display_after = self.display_delay
        self.polls_after_write = 0

    def set_all_pixels(self, _frame, *, wall_frame_sequence=None):
        if getattr(self, "reject_write", False):
            raise RuntimeError("receiver write failed")
        if getattr(self, "reject_return", False):
            return False
        self.write()


def _wall(*, failure=None, display_after=1, predecessor_after=0,
          split_predecessor=False, split_new=False, wrong_new=False,
          extra_new=False, baseline_corrupt=False, baseline_supersede=False,
          baseline_display_error=False, cancel_history=False, native_gap_on_black=1,
          wrong_black=False, extra_black=False):
    controller = _receipt_controller()
    controller.devices = [
        _DisplayDevice(identity, display_after=(display_after if index == 3 else 1),
                       corrupt=failure == "integrity" and index == 3,
                       supersede=failure == "supersession" and index == 3,
                       predecessor_after=predecessor_after if index == 0 else 0,
                       split_predecessor=split_predecessor and index == 0,
                       split_new=split_new and index == 0,
                       wrong_new=wrong_new and index == 0,
                       extra_new=extra_new and index == 0,
                       baseline_corrupt=baseline_corrupt and index == 0,
                       baseline_supersede=baseline_supersede and index == 0,
                       baseline_display_error=baseline_display_error and index == 0,
                       cancel_history=cancel_history and index == 0,
                       native_gap_on_black=native_gap_on_black if index == 0 else 1,
                       wrong_black=wrong_black and index == 0,
                       extra_black=extra_black and index == 0)
        for index, identity in enumerate(controller.receiver_identities)
    ]
    controller.send_count = 0
    controller.pre_send_state = None
    controller.send_states = []
    controller.send_nonblack = []

    def send(_frame):
        controller.send_count += 1
        controller.send_nonblack.append(bool(np.any(_frame)))
        controller.pre_send_state = [
            (device.accepted, device.displayed, device.accepted_sequence,
             device.displayed_sequence) for device in controller.devices
        ]
        controller.send_states.append(controller.pre_send_state)
        for index, device in enumerate(controller.devices):
            if index == 3 and (failure == "write" or
                               (failure == "scene_write" and controller.send_count == 2)):
                return False
            if index == 3 and failure == "scene_timeout" and controller.send_count == 2:
                device.display_delay = None
            device.write()
        return True

    controller.set_all_pixels = send
    return controller


class HostFullFirstDisplayTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.ones((33 * 138, 3), dtype=np.uint8)

    def test_all_five_display_exact_new_accepted_sequence(self):
        wall = _wall(display_after=3)
        proof = wall.present_displayed_host_full_frame("scene-test", self.frame, timeout_seconds=.1)
        self.assertEqual([item["logical_device"] for item in proof["displayed_receivers"]], list(range(5)))
        self.assertEqual([item["receiver_displayed_sequence"] for item in proof["displayed_receivers"]], [303] * 5)
        self.assertEqual(wall.send_count, 2)
        self.assertEqual(wall.send_nonblack, [False, True])
        self.assertGreaterEqual(sum(device.status_queries for device in wall.devices), 25)
        self.assertGreaterEqual(wall.devices[3].polls_after_write, 3)

    def test_receiver_three_write_integrity_supersession_and_display_timeout_fail(self):
        for failure, expected in (("write", "black barrier failed"),
                                  ("scene_write", "takeover failed"),
                                  ("integrity", "protected-v8"),
                                  ("supersession", "supersession"),
                                  ("timeout", "timed out"),
                                  ("scene_timeout", "timed out")):
            with self.subTest(failure=failure):
                wall = _wall(failure=failure,
                             display_after=None if failure == "timeout" else 1)
                with self.assertRaisesRegex(RuntimeError, expected):
                    wall.present_displayed_host_full_frame("scene-test", self.frame,
                                                           timeout_seconds=.015)

    def test_pending_predecessor_black_drains_before_first_send(self):
        wall = _wall(predecessor_after=2)
        proof = wall.present_displayed_host_full_frame("scene-test", self.frame, timeout_seconds=1.0)
        self.assertEqual(wall.send_count, 2)
        self.assertEqual(wall.pre_send_state[0], (21, 21, 303, 303))
        self.assertEqual(proof["displayed_receivers"][0]["receiver_displayed_sequence"], 304)

    def test_split_count_sequence_snapshots_are_polled_to_exact_proof(self):
        wall = _wall(predecessor_after=2, split_predecessor=True, split_new=True)
        proof = wall.present_displayed_host_full_frame("scene-test", self.frame, timeout_seconds=1.0)
        self.assertEqual(wall.pre_send_state[0], (21, 21, 303, 303))
        self.assertEqual(proof["displayed_receivers"][0]["receiver_displayed_sequence"], 304)
        self.assertGreaterEqual(wall.devices[0].sequence_updates_applied, 2)

    def test_canceled_read_history_is_not_mistaken_for_pending_frame(self):
        wall = _wall(cancel_history=True)
        proof = wall.present_displayed_host_full_frame("scene-test", self.frame, timeout_seconds=.5)
        self.assertEqual(wall.pre_send_state[0], (21, 20, 303, 303))
        self.assertEqual(wall.devices[0].displayed, 21)
        self.assertEqual(proof["displayed_receivers"][0]["receiver_displayed_sequence"], 304)

    def test_black_barrier_allows_native_sequence_gap_and_old_supersession(self):
        wall = _wall(predecessor_after=None, baseline_supersede=True,
                     native_gap_on_black=7)
        proof = wall.present_displayed_host_full_frame("scene-test", self.frame, timeout_seconds=.5)
        self.assertEqual(wall.send_count, 2)
        self.assertEqual(wall.devices[0].superseded, 1)
        self.assertEqual(wall.pre_send_state[0], (21, 20, 309, 309))
        self.assertEqual(proof["displayed_receivers"][0]["receiver_displayed_sequence"], 310)

    def test_unproven_black_barrier_never_sends_first_scene_frame(self):
        for kwargs, timeout, expected in (
                ({"predecessor_after": None}, .015, "black barrier display proof timed out"),
                ({"wrong_black": True}, .015, "black barrier display proof timed out"),
                ({"extra_black": True}, .015, "impossible black barrier display advance"),
                ({"baseline_corrupt": True}, .015, "protected-v8"),
                ({"predecessor_after": None, "baseline_display_error": True}, .5, "safely accept black barrier")):
            with self.subTest(kwargs=kwargs, timeout=timeout):
                wall = _wall(**kwargs)
                with self.assertRaisesRegex(RuntimeError, expected):
                    wall.present_displayed_host_full_frame("scene-test", self.frame,
                                                           timeout_seconds=timeout)
                self.assertLessEqual(wall.send_count, 1)

    def test_stuck_predecessor_cannot_become_false_active(self):
        wall = _wall(predecessor_after=None)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            wall.present_displayed_host_full_frame("scene-test", self.frame,
                                                   timeout_seconds=.3)
        self.assertEqual(wall.send_count, 1)

    def test_wrong_final_sequence_or_extra_display_count_never_proves_frame(self):
        for kwargs, expected in (({"wrong_new": True}, "timed out"),
                                 ({"extra_new": True}, "different frame")):
            with self.subTest(kwargs=kwargs):
                wall = _wall(**kwargs)
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
