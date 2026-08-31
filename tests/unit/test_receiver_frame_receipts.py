"""Synthetic contract coverage for authoritative full-frame receipts."""

from __future__ import annotations

import sys
import threading
import types
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from drivers.multi_device import MultiDeviceLEDController
from drivers.spi_controller import LEDController
from scripts.start_server import receiver_identity_authority_for_startup
from tools.deployment.receiver_identity_authority import (
    ReceiverIdentity,
    ReceiverIdentityAuthorityError,
)


ROUTES = ((0, 0), (0, 1), (1, 1), (1, 0), (1, 2))
AUTHORITY_DIGEST = "a" * 64


def _identities():
    return tuple(
        ReceiverIdentity(
            logical_device=index,
            spi_route=route,
            hardware_serial=f"02:00:00:00:00:{index:02x}",
            firmware_sha256=(f"{index + 1:x}" * 64),
        )
        for index, route in enumerate(ROUTES)
    )


class _ReceiptDevice:
    def __init__(self, identity, *, reject=False, drift=False, failure=None):
        self.hardware_serial = identity.hardware_serial
        self.firmware_sha256 = identity.firmware_sha256
        self.receiver_identity_authority_digest = AUTHORITY_DIGEST
        self.logical_device = identity.logical_device
        self.reject = reject
        self.drift = drift
        self.failure = failure
        self.calls = []
        self.lane_masks = []
        self.lane_mask = 0xFF
        self.status_responses = 0
        self.receiver_sequence = 0x1000 + self.logical_device

    def get_stats(self):
        return {"receiver_status_responses": self.status_responses}

    def query_fresh_receiver_status(self):
        self.status_responses += 1
        return {
            "receiver_status_responses": self.status_responses,
            "receiver_status_version": 3,
            "receiver_logical_device": self.logical_device,
            "receiver_lane_mask": self.lane_mask,
            "receiver_last_result": 1,
        }

    def set_lane_mask(self, lane_mask):
        self.lane_masks.append(lane_mask)
        self.lane_mask = lane_mask

    def present_complete_frame(self, frame, *, wall_frame_sequence, frame_digest):
        self.calls.append((tuple(frame), wall_frame_sequence, frame_digest))
        if self.failure is not None:
            raise self.failure
        if self.drift:
            self.hardware_serial = "02:00:00:00:ff:ff"
        prior_sequence = self.receiver_sequence
        self.receiver_sequence = (self.receiver_sequence + 1) & 0xFFFFFFFF
        sequence = prior_sequence if self.reject else self.receiver_sequence
        return {
            "logical_device": self.logical_device,
            "wall_frame_sequence": wall_frame_sequence,
            "receiver_accepted_sequence": sequence,
            "frame_digest": frame_digest,
            "status": {
                "receiver_logical_device": self.logical_device,
                "receiver_last_accepted_sequence": self.receiver_sequence,
            },
        }


def _receipt_controller(*, reject=None, drift=None, failure=None):
    controller = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
    controller._transport_lock = threading.RLock()
    controller.num_devices = 5
    controller.strip_count = 33
    controller.leds_per_strip = 138
    controller.total_leds = 33 * 138
    controller.receiver_strip_counts = (8, 8, 8, 8, 1)
    controller.receiver_global_strip_offsets = (0, 8, 16, 24, 32)
    controller.receiver_pixel_counts = tuple(width * 138 for width in controller.receiver_strip_counts)
    controller.receiver_pixel_offsets = tuple(offset * 138 for offset in controller.receiver_global_strip_offsets)
    controller.receiver_lane_masks = (0xFF,) * 5
    controller.reverse_host_strips_by_logical_receiver = (False,) * 5
    controller.device_map = list(ROUTES)
    controller._receiver_identities = _identities()
    controller._receiver_identity_authority_digest = AUTHORITY_DIGEST
    controller._logical_wall_frame_sequence = 0
    controller._logical_frames_sent = 0
    controller.devices = [
        _ReceiptDevice(
            identity,
            reject=index == reject,
            drift=index == drift,
            failure=failure if index == 3 else None,
        )
        for index, identity in enumerate(controller.receiver_identities)
    ]
    return controller


class ReceiverFrameReceiptTests(unittest.TestCase):
    def test_trusted_single_receiver_lane_mask_is_fresh_and_does_not_persist_topology(self):
        controller = _receipt_controller()

        prior = controller.capture_trusted_receiver_lane_mask(4)
        applied = controller.set_trusted_receiver_lane_mask(4, 0x08)
        restored = controller.set_trusted_receiver_lane_mask(4, prior["lane_mask"])

        self.assertEqual(prior["lane_mask"], 0xFF)
        self.assertEqual(applied["lane_mask"], 0x08)
        self.assertEqual(restored["lane_mask"], 0xFF)
        self.assertEqual(controller.devices[4].lane_masks, [0x08, 0xFF])
        self.assertTrue(all(not device.lane_masks for device in controller.devices[:4]))
        self.assertEqual(controller.receiver_lane_masks, (0xFF,) * 5)

    def test_receipt_is_exactly_authority_backed_after_all_five_acknowledgements(self):
        controller = _receipt_controller()
        frame = np.zeros((33, 138, 3), dtype=np.uint8)

        receipt = controller.present_trusted_full_frame("request-1", frame)

        self.assertEqual(receipt["authority_digest"], AUTHORITY_DIGEST)
        self.assertEqual(receipt["wall_frame_sequence"], 0)
        self.assertEqual(
            [entry["receiver_accepted_sequence"] for entry in receipt["acknowledged_receivers"]],
            [0x1001, 0x1002, 0x1003, 0x1004, 0x1005],
        )
        self.assertEqual(
            [entry["logical_device"] for entry in receipt["acknowledged_receivers"]],
            [0, 1, 2, 3, 4],
        )
        self.assertTrue(all(device.calls for device in controller.devices))

    def test_rejected_partial_timeout_disconnect_or_identity_drift_never_returns_a_receipt(self):
        frame = [(0, 0, 0)] * (33 * 138)
        for controller, expected in (
            (_receipt_controller(reject=2), "rejected or stale"),
            (_receipt_controller(drift=4), "identity drifted"),
            (_receipt_controller(failure=OSError("partial write")), "did not acknowledge"),
            (_receipt_controller(failure=TimeoutError("timeout")), "did not acknowledge"),
            (_receipt_controller(failure=ConnectionError("disconnect")), "did not acknowledge"),
        ):
            with self.subTest(expected=expected), self.assertRaisesRegex(RuntimeError, expected):
                controller.present_trusted_full_frame("request-1", frame)

    def test_single_receiver_boundary_rejects_a_stale_status(self):
        controller = LEDController.__new__(LEDController)
        controller.logical_device_id = 3
        controller.get_stats = mock.Mock(return_value={
            "receiver_status_responses": 7,
            "receiver_frames_accepted": 12,
            "receiver_last_accepted_sequence": 0x1234,
        })
        controller.set_all_pixels = mock.Mock()
        controller.query_fresh_receiver_status = mock.Mock(return_value={
            "receiver_status_responses": 7,
            "receiver_frames_accepted": 12,
            "receiver_status_version": 3,
            "receiver_logical_device": 3,
            "receiver_last_accepted_sequence": 0x1235,
        })
        with self.assertRaisesRegex(RuntimeError, "stale"):
            controller.present_complete_frame(
                [(0, 0, 0)], wall_frame_sequence=9, frame_digest="b" * 64
            )

    def test_single_receiver_boundary_rejects_sequence_zero_without_acceptance_advance(self):
        controller = LEDController.__new__(LEDController)
        controller.logical_device_id = 3
        controller.get_stats = mock.Mock(return_value={
            "receiver_status_responses": 7,
            "receiver_frames_accepted": 0,
            "receiver_last_accepted_sequence": 0,
        })
        controller.set_all_pixels = mock.Mock()
        controller.query_fresh_receiver_status = mock.Mock(return_value={
            "receiver_status_responses": 8,
            "receiver_frames_accepted": 0,
            "receiver_last_accepted_sequence": 0,
            "receiver_status_version": 3,
            "receiver_logical_device": 3,
        })
        with self.assertRaisesRegex(RuntimeError, "accepted-frame counter"):
            controller.present_complete_frame(
                [(0, 0, 0)], wall_frame_sequence=0, frame_digest="b" * 64
            )

    def test_single_receiver_boundary_accepts_independent_nonzero_receiver_sequence(self):
        controller = LEDController.__new__(LEDController)
        controller.logical_device_id = 3
        controller.get_stats = mock.Mock(return_value={
            "receiver_status_responses": 7,
            "receiver_frames_accepted": 12,
            "receiver_last_accepted_sequence": 0x1000,
        })
        controller.set_all_pixels = mock.Mock()
        controller.query_fresh_receiver_status = mock.Mock(return_value={
            "receiver_status_responses": 8,
            "receiver_frames_accepted": 13,
            "receiver_status_version": 3,
            "receiver_logical_device": 3,
            "receiver_last_accepted_sequence": 0x1001,
        })
        receipt = controller.present_complete_frame(
            [(0, 0, 0)], wall_frame_sequence=9, frame_digest="b" * 64
        )
        self.assertEqual(receipt["wall_frame_sequence"], 9)
        self.assertEqual(receipt["receiver_accepted_sequence"], 0x1001)

    def test_single_receiver_boundary_rejects_unchanged_or_invalid_receiver_sequence(self):
        cases = (
            (0x1000, "did not advance"),
            (-1, "invalid receiver-assigned"),
        )
        for after_sequence, expected in cases:
            with self.subTest(after_sequence=after_sequence):
                controller = LEDController.__new__(LEDController)
                controller.logical_device_id = 3
                controller.get_stats = mock.Mock(return_value={
                    "receiver_status_responses": 7,
                    "receiver_frames_accepted": 12,
                    "receiver_last_accepted_sequence": 0x1000,
                })
                controller.set_all_pixels = mock.Mock()
                controller.query_fresh_receiver_status = mock.Mock(return_value={
                    "receiver_status_responses": 8,
                    "receiver_frames_accepted": 13,
                    "receiver_status_version": 3,
                    "receiver_logical_device": 3,
                    "receiver_last_accepted_sequence": after_sequence,
                })
                with self.assertRaisesRegex(RuntimeError, expected):
                    controller.present_complete_frame(
                        [(0, 0, 0)], wall_frame_sequence=9, frame_digest="b" * 64
                    )

    def test_single_receiver_boundary_accepts_uint32_receiver_sequence_wrap(self):
        controller = LEDController.__new__(LEDController)
        controller.logical_device_id = 3
        controller.get_stats = mock.Mock(return_value={
            "receiver_status_responses": 7,
            "receiver_frames_accepted": 0xFFFFFFFF,
            "receiver_last_accepted_sequence": 0xFFFFFFFF,
        })
        controller.set_all_pixels = mock.Mock()
        controller.query_fresh_receiver_status = mock.Mock(return_value={
            "receiver_status_responses": 8,
            "receiver_frames_accepted": 0,
            "receiver_status_version": 3,
            "receiver_logical_device": 3,
            "receiver_last_accepted_sequence": 0,
        })
        receipt = controller.present_complete_frame(
            [(0, 0, 0)], wall_frame_sequence=9, frame_digest="b" * 64
        )
        self.assertEqual(receipt["receiver_accepted_sequence"], 0)

    def test_startup_refuses_authority_route_drift_before_controller_construction(self):
        authority = SimpleNamespace(identities=_identities(), authority_digest=AUTHORITY_DIGEST)
        with mock.patch("scripts.start_server.load_receiver_identity_authority", return_value=authority):
            self.assertEqual(
                receiver_identity_authority_for_startup(
                    SimpleNamespace(), logical_to_transport_routes=ROUTES
                ),
                authority,
            )
            with self.assertRaisesRegex(RuntimeError, "routes do not match"):
                receiver_identity_authority_for_startup(
                    SimpleNamespace(), logical_to_transport_routes=ROUTES[::-1]
                )
        with mock.patch(
            "scripts.start_server.load_receiver_identity_authority",
            side_effect=ReceiverIdentityAuthorityError("stale inventory"),
        ), self.assertRaisesRegex(RuntimeError, "unavailable"):
            receiver_identity_authority_for_startup(
                SimpleNamespace(), logical_to_transport_routes=ROUTES
            )

    def test_controller_rejects_mutable_or_duplicate_identity_bindings(self):
        mutable = tuple(
            SimpleNamespace(
                logical_device=identity.logical_device,
                spi_route=identity.spi_route,
                hardware_serial=identity.hardware_serial,
                firmware_sha256=identity.firmware_sha256,
            )
            for identity in _identities()
        )
        with self.assertRaisesRegex(ValueError, "incomplete or unordered"):
            MultiDeviceLEDController._validate_receiver_identity_authority(
                mutable,
                AUTHORITY_DIGEST,
                num_devices=5,
                device_map=ROUTES,
            )
        duplicate = list(_identities())
        duplicate[4] = duplicate[3]
        with self.assertRaisesRegex(ValueError, "incomplete or unordered"):
            MultiDeviceLEDController._validate_receiver_identity_authority(
                tuple(duplicate),
                AUTHORITY_DIGEST,
                num_devices=5,
                device_map=ROUTES,
            )


if __name__ == "__main__":
    unittest.main()
