"""A receiver reboot invalidates topology without acknowledging an old command."""
from __future__ import annotations

import unittest
from unittest import mock

import numpy as np

from tests.unit.test_firmware_host_phase3a_protocol import controller
from drivers import spi_controller as protocol
from tests.unit.test_host_full_first_display import _DisplayDevice, _wall
from tests.unit.test_receiver_native_host_protocol import (
    _DelayedRefillNativeSpi, checksum_status,
)


class _ResetDisplayDevice(_DisplayDevice):
    def __init__(self, identity, *, reset=False):
        super().__init__(identity)
        self.logical_device_id = identity.logical_device
        self.logical_device = 255 if reset else identity.logical_device
        self.strip_count = 1 if identity.logical_device == 4 else 8
        self.leds_per_strip = 138
        self.global_strip_offset = identity.logical_device * 8
        self.reverse_native_strip_order = identity.logical_device in (2, 3)
        self._presentation_commit_context_cache = {"old": "context"}
        self._last_acknowledged_sparse_command = {"old": "ack"}
        self.current_stagger_phases = 3
        self.applied_stagger_phases = 1 if reset else 3
        self.lane_mask = 255
        self.calls = []
        self.sequence = 0 if reset else 2381536
        self.quarantine = "4d" * 32
        self.override = {}
        self.fail_config = False
        self.wrong_config = False

    def query_causal_receiver_status(self, *, required_status_version=8):
        status = super().query_causal_receiver_status(
            required_status_version=required_status_version)
        return status | {
            "receiver_status_integrity_verified": True,
            "receiver_status_integrity_established": True,
            "receiver_capabilities": (protocol.CAPABILITY_STATUS_CRC32_V8
                                      | protocol.CAPABILITY_ALIGNED_ENVELOPE_V1),
            "receiver_operation_sequence": self.sequence,
            "receiver_last_result": 1,
            "receiver_active_strips": self.strip_count,
            "receiver_leds_per_strip": self.leds_per_strip,
            "receiver_global_strip_offset": self.global_strip_offset,
            "receiver_lane_mask": self.lane_mask,
            "receiver_stagger_phases": self.applied_stagger_phases,
            "receiver_native_quarantine_payload_digest": self.quarantine,
        } | self.override

    def _ack(self, operation, value=None):
        self.calls.append((operation, value))
        self.sequence += 1
        return self.query_causal_receiver_status()

    def configure_acknowledged(self):
        if self.fail_config:
            raise RuntimeError("receiver did not acknowledge next operation sequence")
        self.logical_device = 4 if self.wrong_config else self.logical_device_id
        return self._ack("configure")

    def set_lane_mask_acknowledged(self, value):
        self.lane_mask = value
        return self._ack("lane_mask", value)

    def set_brightness_acknowledged(self, value):
        return self._ack("brightness", value)

    def set_stagger_phases_acknowledged(self, value):
        self.applied_stagger_phases = value
        return self._ack("stagger", value)


def _reset_wall():
    wall = _wall()
    wall.devices = [_ResetDisplayDevice(identity, reset=index == 0)
                    for index, identity in enumerate(wall.receiver_identities)]
    wall.receiver_strip_counts = (8, 8, 8, 8, 1)
    wall.receiver_global_strip_offsets = (0, 8, 16, 24, 32)
    wall.receiver_lane_masks = (255,) * 5
    wall.reverse_native_strips_by_logical_receiver = (False, False, True, True, False)
    wall.current_brightness = 0
    wall._display_ownership_known = True
    wall._native_background_context = "old context"
    wall._sparse_overlay_session_id = "old session"
    wall._sparse_overlay_generation = 123
    wall._sparse_overlay_snapshot_digest = "old digest"
    return wall


class ReceiverResetRecoveryTests(unittest.TestCase):
    def test_reset_board_recovers_before_strict_all_five_display_proof(self):
        wall = _reset_wall()
        result = wall.present_displayed_host_full_frame(
            "new-scene", np.ones((4554, 3), dtype=np.uint8))
        self.assertEqual(len(result["displayed_receivers"]), 5)
        device = wall.devices[0]
        self.assertEqual(device.calls,
                         [("configure", None), ("brightness", 0),
                          ("lane_mask", 255), ("stagger", 3)])
        self.assertEqual(device.sequence, 4)
        self.assertTrue(all(not device.calls for device in wall.devices[1:]))
        self.assertIsNone(wall._native_background_context)
        self.assertIsNone(wall._sparse_overlay_session_id)
        self.assertEqual(wall._sparse_overlay_generation, 0)
        self.assertIsNone(device._last_acknowledged_sparse_command)
        self.assertEqual(device._presentation_commit_context_cache, {})
        self.assertEqual(device.quarantine, "4d" * 32)
        self.assertEqual(wall.send_nonblack, [False, True])

    def test_unprotected_reset_or_wrong_peer_cannot_trigger_configuration(self):
        for override in ({"receiver_status_version": 7},
                         {"receiver_status_integrity_verified": False},
                         {"receiver_status_integrity_established": False},
                         {"receiver_capabilities": 0}):
            with self.subTest(override=override):
                wall = _reset_wall()
                wall.devices[0].override = override
                with self.assertRaisesRegex(RuntimeError, "recovery authority"):
                    wall._prepare_receiver_topology()
                self.assertTrue(all(not device.calls for device in wall.devices))
        wall = _reset_wall()
        wall.devices[2].logical_device = 1
        with self.assertRaisesRegex(RuntimeError, "recovery authority"):
            wall._prepare_receiver_topology()
        self.assertTrue(all(not device.calls for device in wall.devices))

    def test_changed_pinned_roster_cannot_configure_reset_board(self):
        wall = _reset_wall()
        wall.devices[0].hardware_serial = "changed"
        with self.assertRaisesRegex(RuntimeError, "identity drifted"):
            wall._prepare_receiver_topology()
        self.assertFalse(wall.devices[0].calls)

    def test_missing_config_ack_or_wrong_final_identity_cannot_display(self):
        for fault in ("fail_config", "wrong_config"):
            with self.subTest(fault=fault):
                wall = _reset_wall()
                setattr(wall.devices[0], fault, True)
                with self.assertRaises(RuntimeError):
                    wall.present_displayed_host_full_frame(
                        "new-scene", np.ones((4554, 3), dtype=np.uint8))
                self.assertEqual(wall.send_count, 0)
                self.assertFalse(wall._display_ownership_known)

    def test_healthy_topology_never_reconfigures_or_discards_authority(self):
        wall = _reset_wall()
        wall.devices[0].logical_device = 0
        wall._prepare_receiver_topology()
        self.assertTrue(all(not device.calls for device in wall.devices))
        self.assertTrue(wall._display_ownership_known)
        self.assertEqual(wall._native_background_context, "old context")

    def test_reset_interrupts_native_ack_then_config_uses_current_sequence(self):
        class ResetSpi(_DelayedRefillNativeSpi):
            reset = False

            def status(self, version):
                packet = super().status(version)
                packet[312] = 255 if self.reset else 0
                packet[72] = 1
                return checksum_status(packet)

            def sleep(self, seconds):
                super().sleep(seconds)
                if self.command == protocol.CMD_NATIVE_ACTIVATE and not self.reset:
                    self.reset = True
                    self.command = 0
                    self.sequence = 0
                    self.received = 0
                    self.ready = [self.status(8), self.status(8)]
                    self.pending.clear()

        spi = ResetSpi()
        spi.sequence = 2381536
        spi.ready = [spi.status(8), spi.status(8)]
        device = controller(spi)
        device._transport_envelope_enabled = True
        with mock.patch.object(protocol.time, "sleep", side_effect=spi.sleep), \
                mock.patch.object(protocol.time, "monotonic", side_effect=lambda: spi.now):
            with self.assertRaisesRegex(RuntimeError, "expected 2381537"):
                device._command_status(bytes((protocol.CMD_NATIVE_ACTIVATE,)))
            self.assertEqual(device.get_stats()["receiver_operation_sequence"], 0)
            self.assertEqual(device.get_stats()["receiver_logical_device"], 255)
            status = device.configure_acknowledged()
            self.assertEqual(status["receiver_operation_sequence"], 1)
            self.assertEqual(status["receiver_last_processed_command"], protocol.CMD_CONFIG)
            for command, operation in (
                (protocol.CMD_SET_BRIGHTNESS, lambda: device.set_brightness_acknowledged(0)),
                (protocol.CMD_SET_STAGGER, lambda: device.set_stagger_phases_acknowledged(3)),
            ):
                status = operation()
                self.assertEqual(status["receiver_last_processed_command"], command)
        self.assertEqual(spi.attempts.count(protocol.CMD_NATIVE_ACTIVATE), 1)
        self.assertEqual(spi.attempts.count(protocol.CMD_CONFIG), 1)
        self.assertEqual(device.current_brightness, 0)


if __name__ == "__main__":
    unittest.main()
