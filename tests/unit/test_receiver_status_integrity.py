"""Checksummed MISO must precede all status authority and exact native ACKs."""
from __future__ import annotations

import binascii
import copy
import hashlib
import unittest
from unittest.mock import patch

from tests.unit.test_receiver_native_host_protocol import (
    _DelayedRefillNativeSpi, checksum_status, status_v6, status_v8,
)
from tests.unit.test_firmware_host_phase3a_protocol import controller
from drivers import spi_controller as protocol


def authority(item):
    return copy.deepcopy({key: value for key, value in vars(item).items()
                          if key != 'spi' and not key.endswith('_lock')
                          and key not in ('_receiver_status_integrity_errors',
                                          '_receiver_status_unprotected_rejections',
                                          '_receiver_status_integrity_last_failure')})


class StatusIntegrityTests(unittest.TestCase):
    def test_independent_ieee_vector_matches_portable_firmware_encoder(self):
        packet = bytearray(1252)
        packet[:5] = b'LGS8\x08'
        packet[7] = 255
        packet[12:16] = (123456).to_bytes(4, 'big')
        packet[64:68] = (1 << 21).to_bytes(4, 'big')
        packet[78:80] = (256).to_bytes(2, 'big')
        packet[313] = 0x35
        packet[314] = packet[321] = 1
        packet[316:320] = (191895).to_bytes(4, 'big')
        checksum_status(packet)
        self.assertEqual(packet[1248:].hex(), 'bc94929e')
        self.assertEqual(hashlib.sha256(packet).hexdigest(),
                         'ce560fc7c4d9ea7a3d82063352d023337378f89ce007173b89b9a79836457cd4')
        item = controller()
        self.assertTrue(item._update_receiver_status(packet))
        self.assertEqual(item.get_stats()['receiver_operation_sequence'], 191895)
        self.assertTrue(item.get_stats()['receiver_status_integrity_verified'])

    def test_every_snapshot_byte_is_protected_before_any_authority_mutates(self):
        item = controller()
        item._update_receiver_status(status_v8(sequence=191895, command=0x35))
        before = authority(item)
        for offset in range(protocol.RECEIVER_STATUS_BYTES_V8):
            with self.subTest(offset=offset):
                bad = status_v8(sequence=191896, command=0x32)
                bad[offset] ^= 1
                self.assertFalse(item._update_receiver_status(bad, full_status_expected=True))
                self.assertEqual(authority(item), before)
        stats = item.get_stats()
        self.assertEqual(stats['receiver_status_integrity_errors'] +
                         stats['receiver_status_unprotected_rejections'], 1252)

    def test_last_integrity_failure_is_detached_untrusted_and_never_authority(self):
        item = controller()
        good = status_v8(sequence=191895, command=0x35)
        item._update_receiver_status(good)
        before = authority(item)
        bad = bytearray(good)
        bad[316:320] = (383790).to_bytes(4, 'big')
        self.assertFalse(item._update_receiver_status(
            bad, full_status_expected=True, transfer_bytes=1260))
        self.assertEqual(authority(item), before)
        failure = item.get_stats()['receiver_status_integrity_last_failure']
        self.assertEqual(failure['reason'], 'crc32_mismatch')
        self.assertFalse(failure['all_zero'])
        self.assertEqual(failure['untrusted_operation_sequence'], 383790)
        self.assertEqual(failure['untrusted_command'], 0x35)
        self.assertNotEqual(failure['computed_crc32'], failure['untrusted_claimed_crc32'])
        self.assertEqual(item.get_stats()['receiver_operation_sequence'], 191895)
        failure['reason'] = 'caller mutation'
        self.assertEqual(item.get_stats()['receiver_status_integrity_last_failure']['reason'],
                         'crc32_mismatch')
        self.assertFalse(item._update_receiver_status(
            bytes(1260), full_status_expected=True, transfer_bytes=1260))
        self.assertEqual(authority(item), before)
        latest = item.get_stats()['receiver_status_integrity_last_failure']
        self.assertEqual(latest['reason'], 'invalid_header')
        self.assertTrue(latest['all_zero'])
        self.assertEqual(latest['received_bytes'], 1260)
        self.assertEqual(item.get_stats()['receiver_status_integrity_errors'], 2)

    def test_invalid_first_complete_packet_cannot_negotiate_transport_or_ownership(self):
        item = controller()
        before = authority(item)
        bad = status_v8(sequence=383790)
        bad[1248] ^= 1
        self.assertFalse(item._update_receiver_status(bad, full_status_expected=True))
        self.assertEqual(authority(item), before)
        self.assertEqual(item.get_stats()['receiver_status_integrity_errors'], 1)

    def test_pending_protection_rejects_header_downgrade_before_first_valid_v8(self):
        item = controller()
        item._receiver_status_integrity_required = True
        item._receiver_status_query_bytes = 1252
        before = authority(item)
        for magic in (b'LGS3\x03', b'LGS6\x06', b'LGS7\x07'):
            with self.subTest(magic=magic):
                bad = status_v8(sequence=383790)
                bad[:5] = magic
                self.assertFalse(item._update_receiver_status(bad, full_status_expected=True))
                self.assertEqual(authority(item), before)
        self.assertFalse(item.get_stats()['receiver_status_integrity_verified'])

    def test_legacy_discovery_and_sticky_reconnect_request_protection_without_authority(self):
        legacy = status_v6(sequence=383790)
        legacy[64:68] = (1 << 21).to_bytes(4, 'big')
        for hint in (legacy, status_v8(sequence=383790)[:320]):
            with self.subTest(header=hint[:5]):
                item = controller()
                self.assertFalse(item._update_receiver_status(hint))
                stats = item.get_stats()
                self.assertEqual(stats['receiver_status_version'], 0)
                self.assertEqual(stats['receiver_capabilities'], 0)
                self.assertIsNone(stats['receiver_operation_sequence'])
                self.assertTrue(stats['receiver_status_integrity_required'])
                self.assertFalse(stats['receiver_status_integrity_verified'])
                self.assertEqual(item._receiver_status_query_bytes, 1252)

    def test_real_two_slot_sticky_reconnect_drains_interleaved_legacy_without_false_error(self):
        class ReceiverQueue:
            def __init__(self):
                self.counter = 2
                self.sticky_v8 = True
                self.lengths = []
                self.queued = [self.snapshot(8, 1), self.snapshot(8, 2)]

            @staticmethod
            def snapshot(version, counter):
                packet = status_v8(sequence=191895)
                packet[12:16] = counter.to_bytes(4, 'big')
                if version == 3:
                    packet = packet[:320]
                    packet[:5] = b'LGS3\x03'
                else:
                    checksum_status(packet)
                return packet + bytes(4096 - len(packet))

            def xfer2(self, packet):
                wire = bytes(packet)
                if wire[0] == protocol.CMD_ALIGNED_ENVELOPE:
                    size = int.from_bytes(wire[2:4], 'big')
                    semantic = wire[4:4 + size]
                else:
                    semantic = wire[:-2]
                self.lengths.append(len(semantic))
                result = self.queued.pop(0)[:len(wire)]
                self.assert_query(semantic)
                self.counter += 1
                self.sticky_v8 = len(semantic) == 1252
                self.queued.append(self.snapshot(8 if self.sticky_v8 else 3, self.counter))
                return result

            @staticmethod
            def assert_query(semantic):
                assert semantic[0] == protocol.CMD_STATUS_QUERY
                assert not any(semantic[1:])

        spi = ReceiverQueue()
        item = controller(spi)
        item.query_receiver_status()  # Q320 reads old v8A prefix, queues legacy C.
        self.assertTrue(item.get_stats()['receiver_status_integrity_required'])
        self.assertFalse(item.get_stats()['receiver_status_integrity_verified'])
        item.query_receiver_status()  # Q1252 reads old complete v8B.
        self.assertTrue(item.get_stats()['receiver_status_integrity_verified'])
        self.assertFalse(item.get_stats()['receiver_status_integrity_established'])
        before = authority(item)
        item.query_receiver_status()  # Q1252 reads legitimate queued legacy C.
        self.assertEqual(item.get_stats()['receiver_status_integrity_errors'], 0)
        self.assertEqual(item.get_stats()['receiver_status_unprotected_rejections'], 1)
        self.assertEqual(item.get_stats()['receiver_operation_sequence'], 191895)
        self.assertEqual(item.get_stats()['receiver_status_version'], 8)
        # Transport byte counters may advance; authoritative status cannot.
        self.assertEqual(item._receiver_packets, before['_receiver_packets'])
        with patch.object(protocol.time, 'sleep'):
            result = item.query_causal_receiver_status(required_status_version=8)
        self.assertTrue(result['receiver_status_integrity_established'])
        self.assertEqual(result['receiver_status_integrity_errors'], 0)
        self.assertEqual(spi.lengths[0], 320)
        self.assertEqual(set(spi.lengths[1:]), {1252})
        spi.queued[0] = spi.snapshot(3, spi.counter + 1)
        item.query_receiver_status()
        self.assertEqual(item.get_stats()['receiver_status_integrity_errors'], 1)
        self.assertEqual(item._receiver_status_query_bytes, 1252)
        self.assertTrue(item.get_stats()['receiver_status_integrity_established'])

    def test_bootstrap_requires_three_distinct_advancing_checked_snapshots(self):
        item = controller()
        for packets in (50, 51, 51, 0, 1):
            packet = status_v8()
            packet[12:16] = packets.to_bytes(4, 'big')
            item._update_receiver_status(checksum_status(packet))
            self.assertFalse(item.get_stats()['receiver_status_integrity_established'])
        packet[12:16] = (2).to_bytes(4, 'big')
        item._update_receiver_status(checksum_status(packet))
        self.assertTrue(item.get_stats()['receiver_status_integrity_established'])

    def test_short_ordinary_transfers_are_unsampled_but_full_truncation_is_counted(self):
        item = controller()
        item._update_receiver_status(status_v8())
        before = authority(item)
        for size in (0, 1, 7, 320, 1248, 1251):
            with self.subTest(size=size):
                self.assertFalse(item._update_receiver_status(status_v8()[:size]))
                self.assertEqual(authority(item), before)
        self.assertEqual(item.get_stats()['receiver_status_integrity_errors'], 0)
        self.assertFalse(item._update_receiver_status(status_v8()[:1251], full_status_expected=True))
        self.assertEqual(item.get_stats()['receiver_status_integrity_errors'], 1)
        self.assertEqual(authority(item), before)

    def test_valid_v8_fec_accounting_survives_and_legacy_cannot_clear_it(self):
        item = controller()
        good = status_v8(sequence=191895)
        good[1216:1220] = (9).to_bytes(4, 'big')
        good[1220:1224] = (7).to_bytes(4, 'big')
        good[1232:1236] = (2).to_bytes(4, 'big')
        item._update_receiver_status(checksum_status(good))
        before = authority(item)
        self.assertEqual(item.get_stats()['receiver_fec_uncorrectable_packets'], 2)
        legacy = status_v6() + bytes(36)
        self.assertFalse(item._update_receiver_status(legacy, full_status_expected=True))
        self.assertEqual(authority(item), before)
        self.assertEqual(item._receiver_status_query_bytes, 1252)

    def test_v8_fec_negotiation_finalizes_terminal_baseline_without_query_downgrade(self):
        from tests.unit.test_spi_fec_envelope import (
            _controller, _status_v7_with_terminal_counts,
        )
        item = _controller(requested=True)
        for counter in (1, 2, 3):
            packet = _status_v7_with_terminal_counts(
                counter, uncorrectable=8, semantic_crc=0, framing=10) + bytearray(4)
            packet[:5] = b'LGS8\x08'
            packet[64:68] = (int.from_bytes(packet[64:68], 'big') |
                             protocol.CAPABILITY_STATUS_CRC32_V8).to_bytes(4, 'big')
            self.assertTrue(protocol.LEDController._update_receiver_status(
                item, checksum_status(packet)))
        self.assertTrue(item._fec_transport_enabled)
        self.assertTrue(item._receiver_fec_terminal_baseline_finalized)
        self.assertEqual(item._receiver_fec_terminal_baseline,
                         {'uncorrectable_packets': 8, 'semantic_crc_errors': 0,
                          'framing_errors': 10})
        self.assertEqual(item._receiver_status_query_bytes, 1252)

    def test_actual_shifted_baseline_is_rejected_before_single_exact_mutation(self):
        class ShiftedBaselineSpi(_DelayedRefillNativeSpi):
            reads = 0

            def xfer2(self, packet):
                result = bytearray(super().xfer2(packet))
                self.reads += 1
                if self.reads == 3:
                    result[316:320] = (191895 << 1).to_bytes(4, 'big')
                return result

        spi = ShiftedBaselineSpi()
        spi.sequence = 191895
        spi.ready = [spi.status(8), spi.status(8)]
        item = controller(spi)
        item._transport_envelope_enabled = True
        with patch.object(protocol.time, 'sleep', side_effect=spi.sleep), \
                patch.object(protocol.time, 'monotonic', side_effect=lambda: spi.now):
            result = item.native_stop()
        self.assertEqual(result['receiver_operation_sequence'], 191896)
        self.assertEqual(spi.attempts.count(protocol.CMD_NATIVE_STOP), 1)
        self.assertFalse(spi.dropped)
        self.assertEqual(result['receiver_status_integrity_errors'], 1)

    def test_corrupt_or_crc_valid_stale_ack_never_uses_cached_authority(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                class BadAckSpi(_DelayedRefillNativeSpi):
                    baseline_packets = 0

                    def xfer2(self, packet):
                        result = bytearray(super().xfer2(packet))
                        if len(result) >= 1252 and result[:5] == b'LGS8\x08':
                            if self.command == protocol.CMD_SET_ALL:
                                self.baseline_packets = int.from_bytes(result[12:16], 'big')
                            else:
                                if stale:
                                    result[12:16] = self.baseline_packets.to_bytes(4, 'big')
                                    checksum_status(result)
                                else:
                                    # Same actual command/sequence left shifts as wall trace.
                                    result[313] <<= 1
                                    sequence = int.from_bytes(result[316:320], 'big')
                                    result[316:320] = (sequence << 1).to_bytes(4, 'big')
                        return result

                spi = BadAckSpi()
                item = controller(spi)
                item._transport_envelope_enabled = True
                with patch.object(protocol.time, 'sleep', side_effect=spi.sleep), \
                        patch.object(protocol.time, 'monotonic', side_effect=lambda: spi.now):
                    with self.assertRaisesRegex(RuntimeError, 'next operation sequence'):
                        item.native_stop()
                self.assertEqual(spi.attempts.count(protocol.CMD_NATIVE_STOP), 1)
                self.assertFalse(spi.dropped)
                if not stale:
                    self.assertGreater(item.get_stats()['receiver_status_integrity_errors'], 0)

    def test_all_invalid_or_old_firmware_cannot_send_native_mutation(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                class InvalidSpi(_DelayedRefillNativeSpi):
                    def xfer2(self, packet):
                        result = bytearray(super().xfer2(packet))
                        if len(result) >= 1252:
                            if legacy:
                                result[:5] = b'LGS7\x07'
                            else:
                                result[1248] ^= 1
                        return result

                spi = InvalidSpi()
                item = controller(spi)
                item._transport_envelope_enabled = True
                with patch.object(protocol.time, 'sleep', side_effect=spi.sleep), \
                        patch.object(protocol.time, 'monotonic', side_effect=lambda: spi.now):
                    with self.assertRaisesRegex(RuntimeError, 'causal fresh status v8'):
                        item.native_stop()
                self.assertEqual(set(spi.attempts), {protocol.CMD_STATUS_QUERY})
                self.assertLess(spi.now, protocol.STORAGE_COMMAND_ACK_TIMEOUT_SECONDS + 0.01)
