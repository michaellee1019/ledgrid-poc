import binascii
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest

import numpy as np


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from drivers import spi_controller as protocol


class _RecordingSpi:
    max_speed_hz = 20_000_000
    mode = 0

    def __init__(self):
        self.packets = []
        self.response_packets = []
        self.write_only_packets = []
        self.miso_responses = []

    def xfer2(self, packet):
        packet = bytes(packet)
        self.packets.append(packet)
        self.response_packets.append(packet)
        if self.miso_responses:
            return self.miso_responses.pop(0)
        return bytes(len(packet))

    def writebytes2(self, packet):
        packet = bytes(packet)
        self.packets.append(packet)
        self.write_only_packets.append(packet)


def _controller(*, envelope):
    item = protocol.LEDController.__new__(protocol.LEDController)
    item.spi = _RecordingSpi()
    item._transport_lock = threading.RLock()
    item._transport_envelope_enabled = envelope
    item.logical_device_id = 0
    item._spidev_buffer_size = protocol.MAX_SPI_TRANSFER
    item._writebytes2_supported = None
    item._last_transfer_captured_response = False
    item._last_transfer_status_sampled = False
    item._bytes_sent = 0
    item._semantic_bytes_sent = 0
    item._transport_envelope_bytes_sent = 0
    item._transport_padding_bytes_sent = 0
    item._crc_bytes_sent = 0
    item._spi_transfers = 0
    item._errors = 0
    item._frame_packet = bytearray(1 + 8 * 138 * 3 + protocol.CRC_BYTES)
    item._aligned_frame_packet = bytearray(
        protocol._aligned_envelope_wire_size(1 + 8 * 138 * 3)
    )
    item._update_receiver_status = lambda _response: True
    item.strip_count = 8
    item.leds_per_strip = 138
    item.total_leds = 8 * 138
    item._full_frame_transfers = 0
    item._full_frame_status_transfers = 0
    item._full_frame_status_samples = 0
    item._full_frame_status_sample_misses = 0
    item._full_frame_write_only_transfers = 0
    item._full_frame_frames_since_status_sample = 0
    item._full_frame_max_status_sample_gap = 0
    item._full_frame_sequence = 0
    item._frames_sent = 0
    item._last_frame_duration = 0.0
    item._total_frame_duration = 0.0
    return item


def _status_v3(receiver_packets, *, aligned):
    response = bytearray(protocol.RECEIVER_STATUS_BYTES_V3)
    response[:5] = b"LGS3\x03"
    response[12:16] = int(receiver_packets).to_bytes(4, "big")
    capabilities = (
        protocol.CAPABILITY_ALIGNED_ENVELOPE_V1 if aligned else 0
    )
    response[64:68] = capabilities.to_bytes(4, "big")
    response[314] = protocol.STAGGER_OFF
    return response


def _status_v6(receiver_packets, *, aligned):
    response = bytearray(protocol.RECEIVER_STATUS_BYTES_V6)
    response[:protocol.RECEIVER_STATUS_BYTES_V3] = _status_v3(
        receiver_packets, aligned=aligned
    )
    response[:5] = b"LGS6\x06"
    return response


def _status_v2(receiver_packets):
    response = bytearray(protocol.RECEIVER_STATUS_BYTES_V2)
    response[:5] = b"LGS2\x02"
    response[12:16] = int(receiver_packets).to_bytes(4, "big")
    response[64] = protocol.STAGGER_OFF
    return response


class SpiAlignedEnvelopeTests(unittest.TestCase):
    def assert_crc(self, packet):
        self.assertEqual(
            int.from_bytes(packet[-2:], "big"),
            binascii.crc_hqx(packet[:-2], 0xFFFF),
        )

    def test_exact_short_packet_is_versioned_padded_crc_covered_and_aligned(self):
        packet = protocol._encode_aligned_envelope(bytes((protocol.CMD_SHOW,)))
        self.assertEqual(len(packet), 8)
        self.assertEqual(
            packet[:5],
            bytes((
                protocol.CMD_ALIGNED_ENVELOPE,
                protocol.ALIGNED_ENVELOPE_VERSION,
                0,
                1,
                protocol.CMD_SHOW,
            )),
        )
        self.assertEqual(packet[5], 0)
        self.assertEqual(len(packet) % protocol.SPI_DMA_ALIGNMENT_BYTES, 0)
        self.assert_crc(packet)

    def test_full_receiver_frame_has_exact_semantic_bytes_and_one_zero_pad(self):
        semantic = bytes((protocol.CMD_SET_ALL,)) + bytes(8 * 138 * 3)
        packet = protocol._encode_aligned_envelope(semantic)
        self.assertEqual(len(packet), 3320)
        self.assertEqual(int.from_bytes(packet[2:4], "big"), len(semantic))
        self.assertEqual(packet[4:4 + len(semantic)], semantic)
        self.assertEqual(packet[-3], 0)
        self.assert_crc(packet)

    def test_status_queries_preserve_full_v3_through_v6_semantic_snapshots(self):
        cases = (
            (protocol.RECEIVER_STATUS_BYTES_V3, 328),
            (protocol.RECEIVER_STATUS_BYTES_V4, 424),
            (protocol.RECEIVER_STATUS_BYTES_V5, 776),
            (protocol.RECEIVER_STATUS_BYTES_V6, 1224),
        )
        for semantic_size, expected_wire_size in cases:
            with self.subTest(semantic_size=semantic_size):
                semantic = bytes((protocol.CMD_STATUS_QUERY,)) + bytes(
                    semantic_size - 1
                )
                packet = protocol._encode_aligned_envelope(semantic)
                self.assertEqual(len(packet), expected_wire_size)
                self.assertEqual(int.from_bytes(packet[2:4], "big"), semantic_size)
                self.assertEqual(packet[4:4 + semantic_size], semantic)
                self.assert_crc(packet)

    def test_exact_maximum_fits_4096_and_one_byte_more_fails_closed(self):
        semantic = bytes((protocol.CMD_NATIVE_CHUNK,)) + bytes(
            protocol.MAX_ALIGNED_SEMANTIC_BYTES - 1
        )
        packet = protocol._encode_aligned_envelope(semantic)
        self.assertEqual(len(packet), protocol.MAX_SPI_TRANSFER)
        self.assert_crc(packet)
        with self.assertRaisesRegex(ValueError, "1..4090"):
            protocol._encode_aligned_envelope(semantic + b"x")

    def test_spidev_buffer_capacity_requires_positive_decimal_sysfs_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bufsiz"
            for value, expected in (
                ("4096\n", 4096),
                ("0\n", None),
                ("-1\n", None),
                ("4096.0\n", None),
                ("unavailable\n", None),
            ):
                with self.subTest(value=value):
                    path.write_text(value, encoding="ascii")
                    self.assertEqual(
                        protocol._read_spidev_buffer_size(path), expected
                    )
            self.assertIsNone(
                protocol._read_spidev_buffer_size(path.with_name("missing"))
            )

    def test_controller_keeps_legacy_wire_until_three_fresh_observations(self):
        item = _controller(envelope=False)
        item._xfer(bytes((protocol.CMD_PING,)))
        self.assertEqual(len(item.spi.packets[-1]), 3)
        self.assertEqual(item.spi.packets[-1][0], protocol.CMD_PING)
        self.assert_crc(item.spi.packets[-1])

        item._xfer(bytes((protocol.CMD_STATUS_QUERY,)) + bytes(319))
        self.assertEqual(len(item.spi.packets[-1]), 322)
        self.assertEqual(item.spi.packets[-1][0], protocol.CMD_STATUS_QUERY)
        self.assert_crc(item.spi.packets[-1])

        for receiver_packets in (1, 2):
            item._update_receiver_status_v3(
                _status_v3(receiver_packets, aligned=True)
            )
            self.assertFalse(item._transport_envelope_enabled)
        item._update_receiver_status_v3(_status_v3(3, aligned=True))
        self.assertTrue(item._transport_envelope_enabled)

        item._xfer(bytes((protocol.CMD_SET_ALL,)) + bytes(8 * 138 * 3))
        self.assertEqual(len(item.spi.packets[-1]), 3320)
        self.assertEqual(item.spi.packets[-1][0], protocol.CMD_ALIGNED_ENVELOPE)
        self.assert_crc(item.spi.packets[-1])

    def test_one_snapshot_and_repeated_stale_counter_never_enable(self):
        item = _controller(envelope=False)
        item._update_receiver_status_v3(_status_v3(7, aligned=True))
        self.assertFalse(item._transport_envelope_enabled)
        self.assertEqual(item._transport_envelope_candidate_streak, 1)
        for _ in range(3):
            item._update_receiver_status_v3(_status_v3(7, aligned=True))
        self.assertFalse(item._transport_envelope_enabled)
        self.assertEqual(item._transport_envelope_candidate_streak, 0)
        self.assertEqual(item._transport_envelope_stale_observations, 3)

    def test_one_fresh_absence_does_not_downgrade_but_three_do(self):
        item = _controller(envelope=False)
        for counter in (1, 2, 3):
            item._update_receiver_status_v3(_status_v3(counter, aligned=True))
        self.assertTrue(item._transport_envelope_enabled)

        item._update_receiver_status_v3(_status_v3(4, aligned=False))
        self.assertTrue(item._transport_envelope_enabled)
        self.assertEqual(item._transport_envelope_candidate_streak, 1)
        item._update_receiver_status_v3(_status_v3(5, aligned=True))
        self.assertTrue(item._transport_envelope_enabled)
        self.assertEqual(item._transport_envelope_candidate_streak, 0)

        for counter in (6, 7, 8):
            item._update_receiver_status_v3(_status_v3(counter, aligned=False))
        self.assertFalse(item._transport_envelope_enabled)
        self.assertEqual(item._transport_envelope_transitions, 2)

    def test_reboot_counter_reset_requires_three_fresh_legacy_observations(self):
        item = _controller(envelope=False)
        item._receiver_status_responses = 0
        item._legacy_snapshot_warned = False
        item.debug = False
        for counter in (100, 101, 102):
            item._update_receiver_status_v3(_status_v3(counter, aligned=True))
        self.assertTrue(item._transport_envelope_enabled)

        for index, counter in enumerate((1, 2), start=1):
            protocol.LEDController._update_receiver_status(
                item, _status_v2(counter)
            )
            self.assertTrue(item._transport_envelope_enabled)
            self.assertEqual(item._transport_envelope_candidate_streak, index)
        protocol.LEDController._update_receiver_status(item, _status_v2(3))
        self.assertFalse(item._transport_envelope_enabled)
        self.assertEqual(item._transport_envelope_counter_resets, 1)

    def test_invalid_miso_resets_pending_streak_without_flipping_active_state(self):
        item = _controller(envelope=False)
        for counter in (1, 2):
            item._update_receiver_status_v3(_status_v3(counter, aligned=True))
        self.assertEqual(item._transport_envelope_candidate_streak, 2)
        invalid = bytearray(protocol.RECEIVER_STATUS_BYTES_V3)
        protocol.LEDController._update_receiver_status(item, invalid)
        self.assertFalse(item._transport_envelope_enabled)
        self.assertEqual(item._transport_envelope_candidate_streak, 0)
        self.assertEqual(item._transport_envelope_invalid_resets, 1)

        for counter in (3, 4, 5):
            item._update_receiver_status_v3(_status_v3(counter, aligned=True))
        self.assertTrue(item._transport_envelope_enabled)
        item._update_receiver_status_v3(_status_v3(6, aligned=False))
        protocol.LEDController._update_receiver_status(item, bytes(10))
        self.assertTrue(item._transport_envelope_enabled)
        self.assertEqual(item._transport_envelope_candidate_streak, 0)
        self.assertEqual(item._transport_envelope_invalid_resets, 1)

        item._update_receiver_status_v3(_status_v3(7, aligned=False))
        truncated = _status_v3(8, aligned=False)[:100]
        protocol.LEDController._update_receiver_status(item, truncated)
        self.assertTrue(item._transport_envelope_enabled)
        self.assertEqual(item._transport_envelope_candidate_streak, 0)
        self.assertEqual(item._transport_envelope_invalid_resets, 1)

    def test_controller_accounts_semantic_envelope_padding_crc_and_wire_bytes(self):
        item = _controller(envelope=True)
        item._xfer(bytes((protocol.CMD_SHOW,)))
        packet = item.spi.packets[-1]
        self.assertEqual(len(packet), 8)
        self.assertEqual(item._semantic_bytes_sent, 1)
        self.assertEqual(item._transport_envelope_bytes_sent, 4)
        self.assertEqual(item._transport_padding_bytes_sent, 1)
        self.assertEqual(item._crc_bytes_sent, 2)
        self.assertEqual(item._bytes_sent, 8)
        self.assertEqual(len(item.spi.response_packets), 1)
        self.assertEqual(item.spi.write_only_packets, [])

    def test_successful_set_all_has_dedicated_exact_broad_and_tail_accounting(self):
        for strips, semantic_bytes, wire_bytes in (
            (8, 3313, 3320),
            (1, 415, 424),
        ):
            with self.subTest(strips=strips):
                item = _controller(envelope=True)
                item.strip_count = strips
                item.leds_per_strip = 138
                item.total_leds = strips * 138
                item._frame_packet = bytearray(semantic_bytes + protocol.CRC_BYTES)
                item._aligned_frame_packet = bytearray(wire_bytes)
                item._refresh_configuration = lambda: None
                item._frames_sent = 0
                item._last_frame_duration = 0.0
                item._total_frame_duration = 0.0
                item.set_all_pixels(np.zeros((item.total_leds, 3), dtype=np.uint8))
                self.assertEqual(item._full_frame_transfers, 1)
                self.assertEqual(item._full_frame_semantic_bytes_sent, semantic_bytes)
                self.assertEqual(item._full_frame_wire_bytes_sent, wire_bytes)

    def test_frame_hot_path_reuses_exact_aligned_buffer(self):
        item = _controller(envelope=True)
        item._frame_packet[0] = protocol.CMD_SET_ALL
        item._xfer_packet(item._frame_packet, 1 + 8 * 138 * 3)
        self.assertEqual(len(item.spi.packets[-1]), 3320)
        self.assertEqual(len(item._aligned_frame_packet), 3320)
        self.assert_crc(item.spi.packets[-1])

    def test_aligned_full_frames_stagger_one_status_sample_across_five_receivers(self):
        due_by_frame = {}
        for logical_id in range(5):
            item = _controller(envelope=True)
            item.logical_device_id = logical_id
            due = [
                frame
                for frame in range(protocol.FULL_FRAME_STATUS_SAMPLE_INTERVAL)
                if item._full_frame_status_response_required(frame)
            ]
            self.assertEqual(len(due), 1)
            due_by_frame.setdefault(due[0], []).append(logical_id)

        self.assertEqual(len(due_by_frame), 5)
        self.assertTrue(all(len(receivers) == 1 for receivers in due_by_frame.values()))

    def test_single_receiver_default_sequence_remains_monotonic_and_validated(self):
        item = _controller(envelope=True)
        self.assertEqual(item._claim_full_frame_sequence(None), 0)
        self.assertEqual(item._claim_full_frame_sequence(None), 1)
        self.assertEqual(item._claim_full_frame_sequence(7), 7)
        self.assertEqual(item._claim_full_frame_sequence(None), 8)
        for invalid in (-1, 1.5, True, "9"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "non-negative integer"):
                    item._claim_full_frame_sequence(invalid)

    def test_aligned_full_frame_uses_writebytes2_between_status_samples(self):
        item = _controller(envelope=True)
        item.logical_device_id = 0
        item._refresh_configuration = lambda: None
        frame = np.zeros((8 * 138, 3), dtype=np.uint8)

        item.set_all_pixels(frame)
        item.set_all_pixels(frame)

        self.assertEqual(len(item.spi.response_packets), 1)
        self.assertEqual(len(item.spi.write_only_packets), 1)
        self.assertEqual(item._full_frame_transfers, 2)
        self.assertEqual(item._full_frame_status_transfers, 1)
        self.assertEqual(item._full_frame_status_samples, 1)
        self.assertEqual(item._full_frame_status_sample_misses, 0)
        self.assertEqual(item._full_frame_write_only_transfers, 1)
        self.assertEqual(
            item._full_frame_status_transfers
            + item._full_frame_write_only_transfers,
            item._full_frame_transfers,
        )

    def test_tail_scheduled_sample_uses_status_query_then_write_only_frame(self):
        item = _controller(envelope=True)
        item.logical_device_id = 4
        item.strip_count = 1
        item.total_leds = 138
        item._frame_packet = bytearray(1 + 138 * 3 + protocol.CRC_BYTES)
        item._aligned_frame_packet = bytearray(424)
        item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V6
        item._refresh_configuration = lambda: None
        item._update_receiver_status = types.MethodType(
            protocol.LEDController._update_receiver_status, item
        )
        item.spi.miso_responses = [
            bytes(protocol.RECEIVER_STATUS_BYTES_V6),
            bytes(protocol.RECEIVER_STATUS_BYTES_V6),
            _status_v6(20, aligned=True),
        ]

        item.set_all_pixels(
            np.zeros((138, 3), dtype=np.uint8),
            wall_frame_sequence=102,
        )
        item.set_all_pixels(
            np.zeros((138, 3), dtype=np.uint8),
            wall_frame_sequence=103,
        )

        self.assertEqual(len(item.spi.response_packets), 3)
        for packet in item.spi.response_packets:
            self.assertEqual(len(packet), 1224)
            self.assertEqual(packet[0], protocol.CMD_ALIGNED_ENVELOPE)
        self.assertEqual(len(item.spi.write_only_packets), 2)
        self.assertTrue(all(
            len(packet) == 424 for packet in item.spi.write_only_packets
        ))
        self.assertEqual(item._spi_transfers, 5)
        self.assertEqual(item._full_frame_transfers, 2)
        self.assertEqual(item._full_frame_status_transfers, 1)
        self.assertEqual(item._full_frame_status_samples, 1)
        self.assertEqual(item._full_frame_status_sample_misses, 0)
        self.assertEqual(item._full_frame_write_only_transfers, 1)
        self.assertEqual(item._full_frame_frames_since_status_sample, 1)
        self.assertEqual(item._full_frame_max_status_sample_gap, 1)

    def test_tail_truncated_fallback_is_a_miss_never_a_sample(self):
        item = _controller(envelope=True)
        item.logical_device_id = 4
        item.strip_count = 1
        item.total_leds = 138
        item._frame_packet = bytearray(1 + 138 * 3 + protocol.CRC_BYTES)
        item._aligned_frame_packet = bytearray(424)
        item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V6
        item._refresh_configuration = lambda: None
        item._update_receiver_status = types.MethodType(
            protocol.LEDController._update_receiver_status, item
        )
        item.spi.miso_responses = [
            _status_v6(20, aligned=True)[:424]
        ]

        item.set_all_pixels(
            np.zeros((138, 3), dtype=np.uint8),
            wall_frame_sequence=102,
        )

        self.assertEqual(item._full_frame_transfers, 1)
        self.assertEqual(item._full_frame_status_transfers, 1)
        self.assertEqual(item._full_frame_status_samples, 0)
        self.assertEqual(item._full_frame_status_sample_misses, 1)
        self.assertEqual(item._full_frame_write_only_transfers, 0)
        self.assertEqual(item._full_frame_frames_since_status_sample, 1)
        self.assertEqual(item._full_frame_max_status_sample_gap, 1)
        self.assertEqual(len(item.spi.write_only_packets), 1)

    def test_split_capacity_falls_back_for_broad_but_keeps_tail_fast_path(self):
        broad = _controller(envelope=True)
        broad._spidev_buffer_size = 3319
        broad._refresh_configuration = lambda: None
        broad.set_all_pixels(
            np.zeros((8 * 138, 3), dtype=np.uint8), wall_frame_sequence=1
        )

        tail = _controller(envelope=True)
        tail.strip_count = 1
        tail.total_leds = 138
        tail._frame_packet = bytearray(1 + 138 * 3 + protocol.CRC_BYTES)
        tail._aligned_frame_packet = bytearray(424)
        tail._spidev_buffer_size = 3319
        tail._refresh_configuration = lambda: None
        tail.set_all_pixels(
            np.zeros((138, 3), dtype=np.uint8), wall_frame_sequence=1
        )

        self.assertEqual(len(broad.spi.response_packets), 1)
        self.assertEqual(broad.spi.write_only_packets, [])
        self.assertFalse(broad._full_frame_write_only_supported())
        self.assertEqual(broad._full_frame_status_transfers, 1)
        self.assertEqual(len(tail.spi.response_packets), 0)
        self.assertEqual(len(tail.spi.write_only_packets), 1)
        self.assertTrue(tail._full_frame_write_only_supported())
        self.assertEqual(tail._full_frame_write_only_transfers, 1)

    def test_unavailable_or_invalid_capacity_uses_one_full_duplex_transfer(self):
        for capacity in (None, 0, -1, "4096"):
            with self.subTest(capacity=capacity):
                item = _controller(envelope=True)
                item._spidev_buffer_size = capacity
                item._refresh_configuration = lambda: None
                item.set_all_pixels(
                    np.zeros((8 * 138, 3), dtype=np.uint8),
                    wall_frame_sequence=1,
                )
                self.assertEqual(len(item.spi.packets), 1)
                self.assertEqual(len(item.spi.response_packets), 1)
                self.assertEqual(item.spi.write_only_packets, [])
                self.assertEqual(item._full_frame_status_transfers, 1)
                self.assertEqual(item._full_frame_write_only_transfers, 0)

    def test_fresh_status_samples_are_subset_and_stale_or_invalid_are_misses(self):
        item = _controller(envelope=True)
        item._refresh_configuration = lambda: None
        item._update_receiver_status = types.MethodType(
            protocol.LEDController._update_receiver_status, item
        )
        frame = np.zeros((8 * 138, 3), dtype=np.uint8)
        fresh = bytes(_status_v3(20, aligned=True))
        item.spi.miso_responses = [
            bytes(3320),
            fresh[:100],
            fresh + bytes(3320 - len(fresh)),
            fresh + bytes(3320 - len(fresh)),
        ]

        item.set_all_pixels(frame, wall_frame_sequence=0)
        item.set_all_pixels(frame, wall_frame_sequence=1)
        item.set_all_pixels(frame, wall_frame_sequence=128)
        item.set_all_pixels(frame, wall_frame_sequence=256)
        item.set_all_pixels(frame, wall_frame_sequence=384)

        self.assertEqual(item._full_frame_transfers, 5)
        self.assertEqual(item._full_frame_status_transfers, 4)
        self.assertEqual(item._full_frame_status_samples, 1)
        self.assertEqual(item._full_frame_status_sample_misses, 3)
        self.assertEqual(item._full_frame_write_only_transfers, 1)
        self.assertEqual(item._full_frame_frames_since_status_sample, 1)
        self.assertEqual(item._full_frame_max_status_sample_gap, 3)
        self.assertEqual(
            item._full_frame_status_transfers
            + item._full_frame_write_only_transfers,
            item._full_frame_transfers,
        )

    def test_missing_writebytes2_falls_back_to_one_full_duplex_transfer(self):
        item = _controller(envelope=True)
        item.spi.writebytes2 = None
        item._refresh_configuration = lambda: None
        item._full_frame_transfers = 1
        item._full_frame_status_transfers = 1
        item._full_frame_status_samples = 1
        item.set_all_pixels(
            np.zeros((8 * 138, 3), dtype=np.uint8), wall_frame_sequence=1
        )

        self.assertEqual(len(item.spi.packets), 1)
        self.assertEqual(len(item.spi.response_packets), 1)
        self.assertEqual(item._spi_transfers, 1)
        self.assertEqual(item._frames_sent, 1)
        self.assertEqual(item._full_frame_transfers, 2)
        self.assertEqual(item._full_frame_status_transfers, 2)
        self.assertEqual(item._full_frame_status_samples, 2)
        self.assertEqual(item._full_frame_write_only_transfers, 0)
        self.assertFalse(item._writebytes2_supported)
        self.assertTrue(item._last_transfer_captured_response)

    def test_unsupported_writebytes2_falls_back_without_duplicate_or_error(self):
        item = _controller(envelope=True)
        attempts = []

        def unsupported(_packet):
            attempts.append(True)
            raise NotImplementedError("buffer writes unavailable")

        item.spi.writebytes2 = unsupported
        item._refresh_configuration = lambda: None
        item._full_frame_transfers = 1
        item._full_frame_status_transfers = 1
        item._full_frame_status_samples = 1
        item.set_all_pixels(
            np.zeros((8 * 138, 3), dtype=np.uint8), wall_frame_sequence=1
        )

        self.assertEqual(attempts, [True])
        self.assertEqual(len(item.spi.packets), 1)
        self.assertEqual(len(item.spi.response_packets), 1)
        self.assertEqual(item._spi_transfers, 1)
        self.assertEqual(item._frames_sent, 1)
        self.assertEqual(item._full_frame_transfers, 2)
        self.assertEqual(item._full_frame_status_transfers, 2)
        self.assertEqual(item._full_frame_status_samples, 2)
        self.assertEqual(item._full_frame_write_only_transfers, 0)
        self.assertEqual(item._errors, 0)
        self.assertFalse(item._writebytes2_supported)

    def test_ambiguous_writebytes2_io_error_is_not_retried(self):
        item = _controller(envelope=True)
        attempts = []

        def failed_ioctl(_packet):
            attempts.append(True)
            raise OSError("transfer failed")

        item.spi.writebytes2 = failed_ioctl
        with self.assertRaisesRegex(OSError, "transfer failed"):
            item._xfer_packet(
                item._frame_packet,
                1 + 8 * 138 * 3,
                response_required=False,
            )

        self.assertEqual(attempts, [True])
        self.assertEqual(item.spi.response_packets, [])
        self.assertEqual(item._spi_transfers, 1)
        self.assertEqual(item._errors, 1)

    def test_controller_rejects_oversized_semantic_payload_before_io(self):
        item = _controller(envelope=True)
        payload = bytearray(protocol.MAX_ALIGNED_SEMANTIC_BYTES + 1 + 2)
        with self.assertRaisesRegex(ValueError, "1..4090"):
            item._xfer_packet(payload, protocol.MAX_ALIGNED_SEMANTIC_BYTES + 1)
        self.assertEqual(item.spi.packets, [])


if __name__ == "__main__":
    unittest.main()
