"""Production startup must not drain both DMA slots before receiver refill."""
from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

from tests.unit.test_firmware_host_phase3a_protocol import controller
from tests.unit.test_receiver_native_host_protocol import checksum_status, status_v8
from drivers import spi_controller as protocol
from drivers.multi_device import MultiDeviceLEDController


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.receivers = []

    def sleep(self, seconds):
        self.now += seconds
        for receiver in self.receivers:
            receiver.complete()


class _ReceiverQueue:
    """Real framing, two full padded snapshots and delayed sequential dispatch."""
    def __init__(self, clock, *, sticky=False):
        self.clock = clock
        clock.receivers.append(self)
        self.status_version = 8 if sticky else 3
        self.packets = 0
        self.sequence = 0
        self.command = 0
        self.strips = 8
        self.logical_id = 0
        self.offset = 0
        self.lane_mask = 0xFF
        self.pending = []
        self.applied = []
        self.dropped = []
        self.ready = [self.snapshot(), self.snapshot()]

    def snapshot(self):
        value = status_v8(command=self.command, sequence=self.sequence)
        value[6] = self.strips
        value[7] = self.lane_mask
        value[8:10] = (138).to_bytes(2, 'big')
        value[12:16] = self.packets.to_bytes(4, 'big')
        value[64:68] = (int.from_bytes(value[64:68], 'big') |
                        protocol.CAPABILITY_ALIGNED_ENVELOPE_V1).to_bytes(4, 'big')
        value[72] = 1
        value[80:84] = self.offset.to_bytes(4, 'big')
        value[312] = self.logical_id
        value[314] = 1
        if self.status_version == 3:
            value = value[:320]
            value[:5] = b'LGS3\x03'
        else:
            checksum_status(value)
        return value + bytes(4096 - len(value))

    def complete(self):
        while self.pending and self.pending[0][0] <= self.clock.now:
            _, semantic = self.pending.pop(0)
            self.packets += 1
            command = semantic[0]
            if command == protocol.CMD_STATUS_QUERY:
                self.status_version = 8 if len(semantic) == 1252 else 3
            else:
                self.sequence += 1
                self.command = command
                self.applied.append(semantic)
                if command == protocol.CMD_CONFIG:
                    self.strips = semantic[1]
                    if len(semantic) >= 6:
                        self.logical_id = semantic[5]
                    if len(semantic) == 8:
                        self.offset = int.from_bytes(semantic[6:8], 'big')
                elif command == protocol.CMD_SET_LANE_MASK:
                    self.lane_mask = semantic[1]
            self.ready.append(self.snapshot())

    def xfer2(self, packet):
        wire = bytes(packet)
        if wire[0] == protocol.CMD_ALIGNED_ENVELOPE:
            size = int.from_bytes(wire[2:4], 'big')
            semantic = wire[4:4 + size]
        else:
            semantic = wire[:-2]
        assert protocol._crc16_ccitt(wire[:-2]) == int.from_bytes(wire[-2:], 'big')
        duration = len(wire) * 8 / 20_000_000
        if not self.ready:
            self.dropped.append(semantic[0])
            self.clock.sleep(duration)
            return bytes(len(wire))
        response = self.ready.pop(0)[:len(wire)]
        available = self.pending[-1][0] if self.pending else self.clock.now
        self.pending.append((max(available, self.clock.now + duration) + 0.003, semantic))
        self.clock.sleep(duration)
        return response


def startup_model(*, sticky=False):
    clock = _Clock()
    widths = (8, 8, 8, 8, 1)
    offsets = (0, 8, 16, 24, 32)
    masks = (255, 255, 255, 255, 255)
    devices = []
    for index, width in enumerate(widths):
        device = controller(_ReceiverQueue(clock, sticky=sticky))
        device.strip_count = width
        device.total_leds = width * 138
        device.logical_device_id = index
        device.global_strip_offset = offsets[index]
        devices.append(device)
    wall = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
    wall.devices = devices
    wall.receiver_strip_counts = widths
    wall.receiver_global_strip_offsets = offsets
    wall.receiver_lane_masks = masks
    return clock, wall


class ReceiverStartupStatusProtocolTests(unittest.TestCase):
    def test_old_unpaced_discovery_burst_permanently_poisoned_clean_health(self):
        clock, wall = startup_model()
        device = wall.devices[0]
        with patch.object(protocol.time, 'sleep', side_effect=clock.sleep), \
                patch.object(protocol.time, 'monotonic', side_effect=lambda: clock.now):
            # Exact removed startup burst; no corruption is injected.
            for _ in range(5):
                device.query_receiver_status()
            self.assertEqual(device.get_stats()['receiver_status_integrity_errors'], 3)
            self.assertEqual(device.spi.dropped, [protocol.CMD_STATUS_QUERY] * 3)
            clock.sleep(0.03)
            status = device.query_causal_receiver_status(required_status_version=8)
        self.assertTrue(status['receiver_status_integrity_established'])
        self.assertEqual(status['receiver_status_integrity_errors'], 3)
        failure = status['receiver_status_integrity_last_failure']
        self.assertEqual(failure['reason'], 'invalid_header')
        self.assertTrue(failure['all_zero'])
        self.assertEqual(failure['transfer_bytes'], 1260)

    def test_cold_and_sticky_five_receiver_startup_proves_topology_without_drops(self):
        for sticky in (False, True):
            with self.subTest(sticky=sticky):
                clock, wall = startup_model(sticky=sticky)
                with patch.object(protocol.time, 'sleep', side_effect=clock.sleep), \
                        patch.object(protocol.time, 'monotonic', side_effect=lambda: clock.now), \
                        contextlib.redirect_stderr(io.StringIO()) as stderr:
                    wall._initialize_receiver_identity_observability()
                self.assertEqual(stderr.getvalue(), '')
                self.assertLess(clock.now, 1.0)
                for index, device in enumerate(wall.devices):
                    status = device.get_stats()
                    self.assertEqual(status['receiver_status_integrity_errors'], 0)
                    self.assertIsNone(status['receiver_status_integrity_last_failure'])
                    self.assertTrue(status['receiver_status_integrity_established'])
                    self.assertTrue(status['transport_envelope_enabled'])
                    self.assertEqual(status['receiver_logical_device'], index)
                    self.assertEqual(status['receiver_active_strips'], wall.receiver_strip_counts[index])
                    self.assertEqual(status['receiver_global_strip_offset'], wall.receiver_global_strip_offsets[index])
                    self.assertEqual(status['receiver_lane_mask'], wall.receiver_lane_masks[index])
                    self.assertFalse(device.spi.dropped)
                    self.assertEqual([p[0] for p in device.spi.applied],
                                     [protocol.CMD_CONFIG, protocol.CMD_SET_LANE_MASK])
                    self.assertEqual(device.spi.applied[0][5], index)
                    self.assertEqual(status['receiver_operation_sequence'], 2)
