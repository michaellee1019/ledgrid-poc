"""Causal protected-v8 proof for the first host-full frame, without hardware."""

from __future__ import annotations

import unittest
from unittest import mock

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
        return {"receiver_status_integrity_errors": self.integrity_errors,
                "receiver_status_empty_responses": getattr(self, "empty_responses", 0)}

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
        controller._claim_logical_wall_frame_sequence()
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

    def test_receiver_three_dropped_first_frame_retries_without_weakening_exact_proof(self):
        wall = _wall()
        receiver = wall.devices[3]
        write = receiver.write
        attempts = []

        def drop_first_attempt_at_each_frame():
            attempts.append(receiver.accepted)
            if attempts.count(receiver.accepted) == 1:
                return
            write()

        receiver.write = drop_first_attempt_at_each_frame
        proof = wall.present_displayed_host_full_frame("scene-test", self.frame,
                                                       timeout_seconds=.5)
        self.assertEqual(len(proof["displayed_receivers"]), 5)
        self.assertEqual(receiver.accepted, 21)
        self.assertEqual(receiver.displayed, 21)
        self.assertEqual(wall._host_full_receiver3_frame_retries, 2)
        self.assertEqual([device.writes for device in wall.devices], [2] * 5)

    def test_receiver_three_retry_budget_and_double_acceptance_fail_closed(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                wall = _wall()
                receiver = wall.devices[3]
                write = receiver.write
                attempts = []

                def reject_or_double_accept():
                    attempts.append(True)
                    if duplicate and len(attempts) == 2:
                        write()
                        write()

                receiver.write = reject_or_double_accept
                with self.assertRaisesRegex(RuntimeError, "retry budget|safely accept"):
                    wall.present_displayed_host_full_frame("scene-test", self.frame,
                                                           timeout_seconds=.6)
                self.assertLessEqual(len(attempts), 4)
                self.assertEqual(wall.send_nonblack, [False])

    def test_other_receiver_drop_does_not_use_receiver_three_retry_policy(self):
        wall = _wall()
        wall.devices[0].write = lambda: None
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            wall.present_displayed_host_full_frame("scene-test", self.frame,
                                                   timeout_seconds=.12)
        self.assertEqual(getattr(wall, "_host_full_receiver3_frame_retries", 0), 0)
        self.assertEqual(wall.send_nonblack, [False])

    def test_empty_queued_responses_before_fresh_status_do_not_erase_display_proof(self):
        wall = _wall()
        receiver = wall.devices[3]
        original_query = receiver.query_causal_receiver_status

        def query_after_empty_response(**kwargs):
            receiver.integrity_errors += 1
            receiver.empty_responses = getattr(receiver, "empty_responses", 0) + 1
            return original_query(**kwargs)

        receiver.query_causal_receiver_status = query_after_empty_response
        proof = wall.present_displayed_host_full_frame("scene-test", self.frame,
                                                       timeout_seconds=.1)
        self.assertEqual(len(proof["displayed_receivers"]), 5)
        self.assertGreater(receiver.empty_responses, 1)
        self.assertEqual(receiver.integrity_errors, receiver.empty_responses)
        self.assertEqual(receiver.displayed_sequence, 303)
        self.assertEqual(wall.send_nonblack, [False, True])

    def test_empty_status_accounting_cannot_hide_nonempty_corruption(self):
        for errors, empty in ((1, 0), (1, 2), (1, -1), (1, True)):
            with self.subTest(errors=errors, empty=empty):
                wall = _wall(failure="integrity")
                receiver = wall.devices[3]
                receiver.integrity_errors = errors
                receiver.empty_responses = empty
                with self.assertRaisesRegex(RuntimeError, "protected-v8"):
                    wall.present_displayed_host_full_frame("scene-test", self.frame,
                                                           timeout_seconds=.1)

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

    def test_takeover_uses_post_command_status_instead_of_queued_old_mode(self):
        wall = _wall()
        del wall.set_all_pixels
        wall._executor = None
        wall._devices_by_bus = {0: (0, 1), 1: (2, 3, 4)}
        wall._local_background_active = False
        wall._native_background_active = False
        wall._sparse_overlay_session_id = None
        wall._display_ownership_known = False
        wall.debug = False
        for device in wall.devices:
            device.query_receiver_status = mock.Mock(return_value={
                "receiver_status_version": 8, "receiver_base_mode": 0,
            })
            device.query_causal_receiver_status = mock.Mock(
                wraps=device.query_causal_receiver_status
            )
        self.assertIs(wall.set_all_pixels(self.frame), True)
        for device in wall.devices:
            device.query_receiver_status.assert_not_called()
            device.query_causal_receiver_status.assert_called_once_with(
                required_status_version=8
            )
        # A fresh wrong mode or unavailable protected status still fails.
        for failure in ("wrong_mode", "unavailable"):
            with self.subTest(failure=failure):
                wall._display_ownership_known = False
                receiver = wall.devices[3]
                if failure == "wrong_mode":
                    receiver.query_causal_receiver_status = mock.Mock(return_value={
                        "receiver_status_version": 8, "receiver_base_mode": 0,
                    })
                else:
                    receiver.query_causal_receiver_status = mock.Mock(
                        side_effect=RuntimeError("protected status unavailable")
                    )
                self.assertIs(wall.set_all_pixels(self.frame), False)
                self.assertFalse(wall._display_ownership_known)
                self.assertEqual(wall._last_full_frame_failure["operation"],
                                 "set_all_takeover_verify")

    def test_receiver_three_stream_limit_never_skips_strict_frames_or_other_receivers(self):
        wall = _wall()
        del wall.set_all_pixels
        wall._executor = None
        wall._devices_by_bus = {0: (0, 1), 1: (2, 3, 4)}
        wall._local_background_active = False
        wall._native_background_active = False
        wall._sparse_overlay_session_id = None
        wall._display_ownership_known = True
        wall.debug = False
        for device in wall.devices:
            device.set_all_pixels = mock.Mock(wraps=device.set_all_pixels)
        with mock.patch("drivers.multi_device.time.perf_counter", return_value=0):
            self.assertIs(wall.stream_host_full_pixels(self.frame), True)
        with mock.patch("drivers.multi_device.time.perf_counter", return_value=.02):
            self.assertIs(wall.stream_host_full_pixels(self.frame * 2), True)
        self.assertEqual([d.set_all_pixels.call_count for d in wall.devices],
                         [2, 2, 2, 1, 2])
        # Mandatory proof/black frames bypass the stream cap and reach all five.
        with mock.patch("drivers.multi_device.time.perf_counter", return_value=.025):
            self.assertIs(wall.set_all_pixels(self.frame * 3), True)
        with mock.patch("drivers.multi_device.time.perf_counter", return_value=.04):
            self.assertIs(wall.stream_host_full_pixels(self.frame * 4), True)
        with mock.patch("drivers.multi_device.time.perf_counter", return_value=.1):
            self.assertIs(wall.stream_host_full_pixels(self.frame * 5), True)
        self.assertEqual([d.set_all_pixels.call_count for d in wall.devices],
                         [5, 5, 5, 3, 5])
        self.assertEqual(wall._host_full_receiver3_frames_skipped, 2)
        np.testing.assert_array_equal(wall.devices[3].set_all_pixels.call_args.args[0],
                                      np.full((1104, 3), 5, dtype=np.uint8))
        # Unknown ownership also bypasses the cap before entering HostFullScene.
        wall._display_ownership_known = False
        with mock.patch("drivers.multi_device.time.perf_counter", return_value=.11):
            self.assertIs(wall.stream_host_full_pixels(self.frame), True)
        self.assertEqual(wall.devices[3].set_all_pixels.call_count, 4)
        # A due send still propagates failure; throttling does not hide errors.
        wall.devices[3].reject_write = True
        with mock.patch("drivers.multi_device.time.perf_counter", return_value=.2):
            self.assertIs(wall.stream_host_full_pixels(self.frame), False)

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
