"""Production startup must not drain both DMA slots before receiver refill."""
from __future__ import annotations

import contextlib
import io
import threading
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
        self.status_version = 8
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
        checksum_status(value)
        return value + bytes(4096 - len(value))

    def complete(self):
        while self.pending and self.pending[0][0] <= self.clock.now:
            _, semantic = self.pending.pop(0)
            self.packets += 1
            command = semantic[0]
            if command == protocol.CMD_STATUS_QUERY:
                assert len(semantic) == protocol.RECEIVER_STATUS_BYTES_V8
                self.status_version = 8
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
        device._receiver_status_integrity_required = True
        device._transport_envelope_enabled = True
        devices.append(device)
    wall = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
    wall.devices = devices
    wall.receiver_strip_counts = widths
    wall.receiver_global_strip_offsets = offsets
    wall.receiver_lane_masks = masks
    return clock, wall


class _CurrentProtocolDevice:
    def __init__(self, *, current=True, sampled=True):
        self.current = current
        self._last_transfer_status_sampled = sampled
        self.query_calls = 0
        self.causal_calls = 0
        self.config_calls = 0
        self.lane_calls = 0
        self.logical_device_id = None

    def _status(self):
        return {
            "receiver_status_version": 8 if self.current else 7,
            "receiver_status_integrity_established": self.current,
            "receiver_capabilities": (
                protocol.CAPABILITY_STATUS_CRC32_V8
                | protocol.CAPABILITY_ALIGNED_ENVELOPE_V1
                if self.current else 0
            ),
            "receiver_last_result": 1,
            "receiver_logical_device": self.logical_device_id,
            "receiver_active_strips": 8,
            "receiver_global_strip_offset": 0,
            "receiver_lane_mask": 0xFF,
        }

    def query_receiver_status(self):
        self.query_calls += 1
        return self._status()

    def query_causal_receiver_status(self, *, required_status_version):
        self.causal_calls += 1
        assert required_status_version == 8
        self._last_transfer_status_sampled = True
        return self._status()

    def configure_acknowledged(self):
        self.config_calls += 1
        return {"receiver_last_result": 1}

    def set_lane_mask_acknowledged(self, _mask):
        self.lane_calls += 1
        return {"receiver_last_result": 1}


class ReceiverStartupStatusProtocolTests(unittest.TestCase):
    def test_later_obsolete_peer_rejects_roster_before_any_topology_mutation(self):
        wall = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        wall.devices = [
            _CurrentProtocolDevice(current=True),
            _CurrentProtocolDevice(current=False),
        ]
        wall.receiver_strip_counts = (8, 8)
        wall.receiver_global_strip_offsets = (0, 8)
        wall.receiver_lane_masks = (0xFF, 0xFF)

        with self.assertRaisesRegex(RuntimeError, "receiver 1.*not the supported"):
            wall._initialize_receiver_identity_observability()

        self.assertEqual(
            [(device.config_calls, device.lane_calls) for device in wall.devices],
            [(0, 0), (0, 0)],
        )


    def test_later_selected_fec_peer_rejects_roster_before_any_topology_mutation(self):
        clock, wall = startup_model()
        peer = wall.devices[3]
        peer._fec_transport_requested = True
        original_snapshot = peer.spi.snapshot

        def snapshot_without_current_fec():
            value = bytearray(original_snapshot())
            capabilities = int.from_bytes(value[64:68], "big")
            value[64:68] = (
                capabilities & ~protocol.CAPABILITY_FEC_ENVELOPE_V7
            ).to_bytes(4, "big")
            checksum_status(value)
            return bytes(value)

        peer.spi.snapshot = snapshot_without_current_fec
        peer.spi.ready = [peer.spi.snapshot(), peer.spi.snapshot()]
        with patch.object(protocol.time, "sleep", side_effect=clock.sleep), \
                patch.object(protocol.time, "monotonic", side_effect=lambda: clock.now):
            with self.assertRaisesRegex(RuntimeError, "receiver 3.*not the supported"):
                wall._initialize_receiver_identity_observability()

        self.assertEqual(
            [len(device.spi.applied) for device in wall.devices],
            [0, 0, 0, 0, 0],
        )

    def test_steady_status_collection_keeps_one_query_per_receiver(self):
        wall = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        qualified = _CurrentProtocolDevice()
        qualified.logical_device_id = 0
        wall.devices = [qualified]

        statuses = wall._receiver_statuses()

        self.assertEqual(len(statuses), 1)
        self.assertEqual((qualified.query_calls, qualified.causal_calls), (1, 0))

        unqualified = _CurrentProtocolDevice(sampled=False)
        unqualified.logical_device_id = 0
        wall.devices = [unqualified]
        wall._receiver_statuses()
        self.assertEqual((unqualified.query_calls, unqualified.causal_calls), (1, 1))


    def test_current_five_receiver_coldstart_and_reconnect_prove_topology_without_drops(self):
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


class _ServicedReceiverQueue(_ReceiverQueue):
    """Two DMA replies serviced independently of a 600 ms CONFIG executor.

    Queries refresh only an idle committed snapshot; every field remains frozen
    while the serial mutation executor is busy. There is no injected corruption.
    """
    def __init__(self, clock, *, sticky=False, hang=False, reject=False):
        self.committed = None
        self.hang = hang
        self.reject = reject
        self.result = 1
        self.queue_errors = 0
        self.fault_on_config = False
        self.events = []
        super().__init__(clock, sticky=sticky)
        self.committed = self._committed_snapshot()

    def _committed_snapshot(self):
        version = self.status_version
        self.status_version = 8
        value = super().snapshot()
        self.status_version = version
        value = bytearray(value)
        value[72] = self.result
        value[40:44] = self.queue_errors.to_bytes(4, "big")
        checksum_status(value)
        return bytes(value)

    def snapshot(self):
        if self.committed is None:
            return super().snapshot()
        return bytes(self.committed)

    def complete(self):
        while self.pending and self.pending[0][0] <= self.clock.now:
            _, semantic = self.pending.pop(0)
            self.sequence += 1
            self.command = semantic[0]
            self.result = 3 if self.reject and self.command == protocol.CMD_CONFIG else 1
            self.applied.append(semantic)
            if self.fault_on_config and self.command == protocol.CMD_CONFIG:
                self.queue_errors += 1
            self.events.append(('complete', self.command, self.clock.now))
            if self.command == protocol.CMD_CONFIG and self.result == 1:
                self.strips = semantic[1]
                if len(semantic) >= 6:
                    self.logical_id = semantic[5]
                if len(semantic) == 8:
                    self.offset = int.from_bytes(semantic[6:8], 'big')
            elif self.command == protocol.CMD_SET_LANE_MASK:
                self.lane_mask = semantic[1]
        if not self.pending:
            self.committed = self._committed_snapshot()

    def xfer2(self, packet):
        wire = bytes(packet)
        assert protocol._crc16_ccitt(wire[:-2]) == int.from_bytes(wire[-2:], 'big')
        if wire[0] == protocol.CMD_ALIGNED_ENVELOPE:
            size = int.from_bytes(wire[2:4], 'big')
            semantic = wire[4:4 + size]
        else:
            semantic = wire[:-2]
        self.complete()
        response = self.ready.pop(0)[:len(wire)]
        self.packets += 1
        command = semantic[0]
        if command == protocol.CMD_STATUS_QUERY:
            assert len(semantic) == protocol.RECEIVER_STATUS_BYTES_V8
            self.status_version = 8
        else:
            self.events.append(('receive', command, self.clock.now))
            available = self.pending[-1][0] if self.pending else self.clock.now
            latency = 0.6 if command == protocol.CMD_CONFIG else 0.0002
            if self.hang and command == protocol.CMD_CONFIG:
                latency = float('inf')
            self.pending.append((available + latency, bytes(semantic)))
        self.complete()
        self.ready.append(self.snapshot())
        self.clock.sleep(len(wire) * 8 / 20_000_000)
        return response

    def writebytes2(self, packet):
        self.xfer2(packet)


class ReceiverServicedConfigTests(unittest.TestCase):
    @contextlib.contextmanager
    def model(self, *, sticky=False, hang=False, reject=False):
        clock, wall = startup_model(sticky=sticky)
        clock.receivers.clear()
        for device in wall.devices:
            device.spi = _ServicedReceiverQueue(clock, sticky=sticky, hang=hang, reject=reject)
        with patch.object(protocol.time, 'sleep', side_effect=clock.sleep), \
                patch.object(protocol.time, 'monotonic', side_effect=lambda: clock.now), \
                patch.object(protocol.time, 'time', side_effect=lambda: clock.now):
            yield clock, wall

    def test_cold_and_reconnect_config_keep_all_five_clean_with_real_slow_validation(self):
        for sticky in (False, True):
            with self.subTest(sticky=sticky), self.model(sticky=sticky) as (clock, wall):
                wall._initialize_receiver_identity_observability()
                self.assertGreater(clock.now, 3.0)  # Five genuine 600 ms validations.
                for index, device in enumerate(wall.devices):
                    status = device.get_stats()
                    self.assertTrue(status['receiver_status_integrity_established'])
                    self.assertEqual(status['receiver_status_integrity_errors'], 0)
                    self.assertEqual(status['receiver_operation_sequence'], 2)
                    self.assertEqual(status['receiver_logical_device'], index)
                    self.assertEqual(status['receiver_active_strips'], wall.receiver_strip_counts[index])
                    self.assertEqual(status['receiver_global_strip_offset'], wall.receiver_global_strip_offsets[index])
                    self.assertEqual(status['receiver_lane_mask'], wall.receiver_lane_masks[index])
                    self.assertEqual([p[0] for p in device.spi.applied],
                                     [protocol.CMD_CONFIG, protocol.CMD_SET_LANE_MASK])

    def test_periodic_config_and_brightness_block_frames_until_exact_ack(self):
        with self.model() as (clock, wall):
            wall._initialize_receiver_identity_observability()
            device = wall.devices[0]
            original_ack = device._command_status

            def observe_ack(payload, **kwargs):
                result = original_ack(payload, **kwargs)
                device.spi.events.append(('ack', int(payload[0]), clock.now))
                return result

            device._command_status = observe_ack
            pixels = [(0, 0, 0)] * device.total_leds
            start_sequence = device.spi.sequence
            # Exercise actual frame producer calls before and across the 30s boundary.
            for _ in range(3):
                device.set_all_pixels(pixels)
                clock.sleep(1 / 150)
            clock.sleep(31)
            device.set_all_pixels(pixels)
            device.set_brightness(0)
            clock.sleep(0.01)
            mutations = [event for event in device.spi.events if event[0] != 'complete']
            for position, event in enumerate(mutations):
                if event[0] == 'ack' and event[1] == protocol.CMD_CONFIG:
                    self.assertEqual(mutations[position - 1][0:2], ('receive', protocol.CMD_CONFIG))
                    self.assertGreaterEqual(event[2] - mutations[position - 1][2], 0.6)
                    self.assertIn(mutations[position + 1][1],
                                  (protocol.CMD_SET_ALL, protocol.CMD_SET_BRIGHTNESS))
            self.assertEqual(sum(e[:2] == ('ack', protocol.CMD_CONFIG) for e in mutations), 2)
            self.assertEqual(device.spi.sequence - start_sequence, 7)  # 4 frames, 2 CONFIG, brightness.
            self.assertEqual(device.get_stats()['receiver_status_integrity_errors'], 0)

    def test_hung_or_rejected_config_does_not_mark_sent_or_send_following_mutation(self):
        for hang in (False, True):
            with self.subTest(hang=hang), self.model(hang=hang, reject=not hang) as (clock, wall):
                device = wall.devices[0]
                device.query_causal_receiver_status(required_status_version=8)
                prior_config = device._last_sent_config
                prior_refresh = device._last_config_refresh
                with self.assertRaises(RuntimeError):
                    device.set_all_pixels([(0, 0, 0)] * device.total_leds)
                self.assertEqual(device._last_sent_config, prior_config)
                self.assertEqual(device._last_config_refresh, prior_refresh)
                self.assertEqual([e[1] for e in device.spi.events if e[0] == 'receive'],
                                 [protocol.CMD_CONFIG])
                self.assertEqual(device.get_stats()['receiver_status_integrity_errors'], 0)
                if hang:
                    self.assertGreaterEqual(clock.now, protocol.STORAGE_COMMAND_ACK_TIMEOUT_SECONDS)
                    self.assertLess(clock.now, protocol.STORAGE_COMMAND_ACK_TIMEOUT_SECONDS + 0.1)
                    self.assertEqual(device.spi.sequence, 0)


    def test_queue_fault_rejects_even_an_exact_success_shaped_ack_and_prior_fault(self):
        for prior in (False, True):
            with self.subTest(prior=prior), self.model() as (clock, wall):
                device = wall.devices[0]
                if prior:
                    device.spi.queue_errors = 1
                else:
                    device.spi.fault_on_config = True
                device.query_causal_receiver_status(required_status_version=8)
                with self.assertRaisesRegex(RuntimeError, "SPI queue fault"):
                    device._refresh_configuration(force=True)
                self.assertEqual(len([e for e in device.spi.events if e[0] == 'receive']),
                                 0 if prior else 1)
                self.assertEqual(device.get_stats()['receiver_spi_queue_errors'], 1)
                self.assertEqual(device.spi.result, 1)  # Cannot disguise fault as success.

    def test_concurrent_frame_producer_cannot_cross_config_ack_lock(self):
        device = controller()
        device._receiver_status_integrity_required = True
        device.logical_device_id = 0
        device.global_strip_offset = 0
        entered, release, frame_started, frame_done = (threading.Event() for _ in range(4))
        transfers, failures = [], []

        def acknowledge(payload, **kwargs):
            entered.set()
            if not release.wait(2):
                raise AssertionError('test did not release CONFIG')
            transfers.append('config_ack')
            return {'receiver_last_result': 1}

        def transfer(buffer, size, **kwargs):
            transfers.append(int(buffer[0]))

        def brightness():
            try:
                device.set_brightness(0)
            except Exception as error:
                failures.append(error)

        def frame():
            try:
                frame_started.set()
                device.set_all_pixels([(0, 0, 0)] * device.total_leds)
            except Exception as error:
                failures.append(error)
            finally:
                frame_done.set()

        device._command_status = acknowledge
        device._xfer_packet = transfer
        first = threading.Thread(target=brightness)
        second = threading.Thread(target=frame)
        try:
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            self.assertTrue(frame_started.wait(2))
            self.assertFalse(frame_done.wait(0.02))
            self.assertEqual(transfers, [])
        finally:
            release.set()
            first.join(2)
            if second.ident is not None:
                second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(transfers[0], 'config_ack')
        self.assertIn(protocol.CMD_SET_ALL, transfers)


    def test_removed_unacknowledged_refresh_admitted_frame_before_validation_completed(self):
        with self.model() as (clock, wall):
            wall._initialize_receiver_identity_observability()
            device = wall.devices[0]
            clock.sleep(31)
            # Negative control reproduces the previous automatic CONFIG branch.
            device._receiver_status_integrity_required = False
            device._refresh_configuration()
            device._receiver_status_integrity_required = True
            device.set_all_pixels([(0, 0, 0)] * device.total_leds)
            self.assertEqual([entry[1][0] for entry in device.spi.pending],
                             [protocol.CMD_CONFIG, protocol.CMD_SET_ALL])
            self.assertEqual(device.spi.sequence, 2)  # Neither mutation completed.
