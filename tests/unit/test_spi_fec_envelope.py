import hashlib
import sys
import threading
import types
import unittest
from unittest import mock

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

    def open(self, _bus, _device):
        return None

    def xfer2(self, packet):
        self.packets.append(bytes(packet))
        self.response_packets.append(bytes(packet))
        return bytes(len(packet))

    def writebytes2(self, packet):
        self.packets.append(bytes(packet))
        self.write_only_packets.append(bytes(packet))


def _controller(*, requested):
    item = protocol.LEDController.__new__(protocol.LEDController)
    item.spi = _RecordingSpi()
    item._transport_lock = threading.RLock()
    item._transport_envelope_enabled = True
    item._fec_transport_requested = requested
    item._fec_transport_enabled = requested
    item._receiver_status_integrity_established = True
    item._receiver_status_current_peer_rejected = False
    item._receiver_capabilities = (
        protocol.CAPABILITY_STATUS_CRC32_V8
        | protocol.CAPABILITY_ALIGNED_ENVELOPE_V1
        | (protocol.CAPABILITY_FEC_ENVELOPE_V7 if requested else 0)
    )
    item._fec_frames_sent = 0
    item._fec_codewords_sent = 0
    item._fec_parity_bytes_sent = 0
    item._fec_data_padding_bytes_sent = 0
    item._writebytes2_supported = None
    item._spidev_buffer_size = protocol.MAX_SPI_TRANSFER
    item._last_transfer_captured_response = False
    item._last_transfer_status_sampled = False
    item.logical_device_id = 3
    item.strip_count = 8
    item.leds_per_strip = 138
    item.total_leds = 8 * 138
    semantic_size = 1 + item.total_leds * 3
    item._frame_packet = bytearray(semantic_size + protocol.CRC_BYTES)
    item._aligned_frame_packet = bytearray(
        protocol._aligned_envelope_wire_size(semantic_size)
    )
    item._fec_frame_packet = bytearray(
        protocol._fec_envelope_wire_size(semantic_size)
    )
    item._bytes_sent = 0
    item._semantic_bytes_sent = 0
    item._transport_envelope_bytes_sent = 0
    item._transport_padding_bytes_sent = 0
    item._crc_bytes_sent = 0
    item._spi_transfers = 0
    item._errors = 0
    item._frames_sent = 0
    item._full_frame_transfers = 0
    item._full_frame_status_transfers = 0
    item._full_frame_status_samples = 0
    item._full_frame_status_sample_misses = 0
    item._full_frame_write_only_transfers = 0
    item._full_frame_frames_since_status_sample = 0
    item._full_frame_max_status_sample_gap = 0
    item._full_frame_semantic_bytes_sent = 0
    item._full_frame_wire_bytes_sent = 0
    item._full_frame_sequence = 0
    item._last_frame_duration = 0.0
    item._total_frame_duration = 0.0
    item._refresh_configuration = lambda: None
    item._update_receiver_status = lambda _response, **_kwargs: False
    return item


def _status_v7(receiver_packets, *, fec=True):
    response = bytearray(protocol.RECEIVER_STATUS_BYTES_V7)
    response[:5] = b"LGS7\x07"
    response[12:16] = receiver_packets.to_bytes(4, "big")
    capabilities = protocol.CAPABILITY_ALIGNED_ENVELOPE_V1
    if fec:
        capabilities |= (
            protocol.CAPABILITY_FEC_ENVELOPE_V2
            | protocol.CAPABILITY_FEC_ENVELOPE_V3
            | protocol.CAPABILITY_FEC_ENVELOPE_V4
            | protocol.CAPABILITY_FEC_ENVELOPE_V5
            | protocol.CAPABILITY_FEC_ENVELOPE_V6
            | protocol.CAPABILITY_FEC_ENVELOPE_V7
        )
    response[64:68] = capabilities.to_bytes(4, "big")
    response[314] = protocol.STAGGER_OFF
    return response


def _status_v3(receiver_packets, *, fec=False):
    response = bytearray(protocol.RECEIVER_STATUS_BYTES_V3)
    response[:5] = b"LGS3\x03"
    response[12:16] = receiver_packets.to_bytes(4, "big")
    capabilities = protocol.CAPABILITY_ALIGNED_ENVELOPE_V1
    if fec:
        capabilities |= (
            protocol.CAPABILITY_FEC_ENVELOPE_V2
            | protocol.CAPABILITY_FEC_ENVELOPE_V3
            | protocol.CAPABILITY_FEC_ENVELOPE_V4
            | protocol.CAPABILITY_FEC_ENVELOPE_V5
            | protocol.CAPABILITY_FEC_ENVELOPE_V6
            | protocol.CAPABILITY_FEC_ENVELOPE_V7
        )
    response[64:68] = capabilities.to_bytes(4, "big")
    response[314] = protocol.STAGGER_OFF
    return response


def _decode_clean_v7(packet):
    """Recover a canonical v7 semantic payload without exercising correction."""
    packet = bytes(packet)
    codewords = (
        len(packet) - protocol.FEC_WIRE_HEADER_BYTES
    ) // protocol.FEC_CODEWORD_BYTES
    data = bytearray(codewords * protocol.FEC_DATA_BYTES)
    matrix = protocol.FEC_ENVELOPE_HEADER_BYTES
    for block in range(codewords):
        for symbol in range(protocol.FEC_DATA_BYTES):
            wire_block = (block + symbol) % codewords
            data[block * protocol.FEC_DATA_BYTES + symbol] = packet[
                matrix + symbol * codewords + wire_block
            ]
    inner_size = int.from_bytes(data[2:4], "big")
    inner = data[protocol.FEC_ENVELOPE_HEADER_BYTES:
                 protocol.FEC_ENVELOPE_HEADER_BYTES + inner_size]
    semantic_size = int.from_bytes(inner[2:4], "big")
    return bytes(inner[protocol.ALIGNED_ENVELOPE_HEADER_BYTES:
                       protocol.ALIGNED_ENVELOPE_HEADER_BYTES + semantic_size])


class SpiFecEnvelopeTests(unittest.TestCase):
    def test_exact_fixed_codeword_layout_and_golden_digest(self):
        packet = protocol._encode_fec_envelope(bytes((protocol.CMD_SHOW,)))
        self.assertEqual(len(packet), 248)
        self.assertEqual(packet[:4], b"\x0b\x07\x00\x08")
        self.assertEqual(packet[-4:], packet[:4])
        self.assertEqual(
            hashlib.sha256(packet).hexdigest(),
            "5ee009702d5af614fbd1d4cd58576cd12f22db059e5438e6a8dddece77933a8e",
        )

    def test_full_and_tail_frames_have_exact_bounded_wire_overhead(self):
        for semantic_size, codewords, wire_size, data_padding in (
            (3313, 68, 4088, 26),
            (415, 12, 728, 122),
            (protocol.MAX_FEC_SEMANTIC_BYTES, 68, 4088, 2),
        ):
            with self.subTest(semantic_size=semantic_size):
                packet = protocol._encode_fec_envelope(bytes(semantic_size))
                self.assertEqual(len(packet), wire_size)
                self.assertEqual(len(packet) % 4, 0)
                self.assertLessEqual(len(packet), protocol.MAX_SPI_TRANSFER)
                self.assertEqual(
                    codewords * protocol.FEC_DATA_BYTES
                    - protocol.FEC_OUTER_PARITY_BYTES
                    - protocol.FEC_ENVELOPE_HEADER_BYTES
                    - protocol._aligned_envelope_wire_size(semantic_size),
                    data_padding,
                )

    def test_v7_inner_rs_outer_parity_and_diagonal_distribution_are_exact(self):
        packet = protocol._encode_fec_envelope(bytes(range(1, 65)))
        codewords = (
            len(packet) - protocol.FEC_WIRE_HEADER_BYTES
        ) // protocol.FEC_CODEWORD_BYTES
        matrix = protocol.FEC_ENVELOPE_HEADER_BYTES
        for block in range(codewords):
            syndromes = [0] * protocol.FEC_PARITY_BYTES
            for symbol in range(protocol.FEC_CODEWORD_BYTES):
                wire_block = (block + symbol) % codewords
                value = packet[matrix + symbol * codewords + wire_block]
                evaluation = symbol + 1
                for power in range(protocol.FEC_PARITY_BYTES):
                    syndromes[power] ^= protocol._fec_gf_multiply(
                        value, protocol._fec_gf_power(evaluation, power)
                    )
            self.assertEqual(syndromes, [0] * protocol.FEC_PARITY_BYTES)
        outer_block = codewords - 1
        for symbol in range(protocol.FEC_DATA_BYTES):
            outer_value = packet[
                matrix
                + symbol * codewords
                + (outer_block + symbol) % codewords
            ]
            expected = 0
            for block in range(outer_block):
                expected ^= packet[
                    matrix
                    + symbol * codewords
                    + (block + symbol) % codewords
                ]
            self.assertEqual(outer_value, expected)
        fixed_wire_block = 3
        affected_logical_blocks = [
            (fixed_wire_block - symbol) % codewords
            for symbol in range(protocol.FEC_CODEWORD_BYTES)
        ]
        self.assertEqual(set(affected_logical_blocks), set(range(codewords)))
        self.assertLessEqual(
            max(affected_logical_blocks.count(block) for block in range(codewords)),
            (protocol.FEC_CODEWORD_BYTES + codewords - 1) // codewords,
        )
        installed = protocol._encode_fec_envelope(bytes(3313))
        installed_codewords = (
            len(installed) - protocol.FEC_WIRE_HEADER_BYTES
        ) // protocol.FEC_CODEWORD_BYTES
        self.assertEqual(installed_codewords, 68)
        installed_fixed_position = [
            (fixed_wire_block - symbol) % installed_codewords
            for symbol in range(protocol.FEC_CODEWORD_BYTES)
        ]
        self.assertEqual(len(set(installed_fixed_position)), 60)
        self.assertTrue(
            all(
                installed_fixed_position.count(block) <= 1
                for block in range(installed_codewords)
            )
        )

    def test_bad_types_bounds_and_output_size_fail_closed(self):
        for value, error in (
            (True, TypeError),
            (0, ValueError),
            (protocol.MAX_FEC_SEMANTIC_BYTES + 1, ValueError),
        ):
            with self.subTest(value=value):
                with self.assertRaises(error):
                    protocol._fec_envelope_wire_size(value)
        with self.assertRaisesRegex(ValueError, "exact FEC wire size"):
            protocol._encode_fec_envelope(b"x", bytearray(132))






    def test_selected_full_frame_uses_v7_once_and_accounts_exactly(self):
        item = _controller(requested=True)
        item._transport_envelope_enabled = True
        item._fec_transport_enabled = True
        status_update = mock.Mock(return_value=True)
        item._update_receiver_status = status_update
        colors = np.zeros((8 * 138, 3), dtype=np.uint8)
        item.set_all_pixels(colors, wall_frame_sequence=1)
        packet = item.spi.packets[-1]
        self.assertEqual(packet[:2], b"\x0b\x07")
        self.assertEqual(len(packet), 4088)
        self.assertEqual(item._fec_frames_sent, 1)
        self.assertEqual(item._fec_codewords_sent, 68)
        self.assertEqual(item._fec_parity_bytes_sent, 730)
        self.assertEqual(item._fec_data_padding_bytes_sent, 26)
        self.assertEqual(item._full_frame_wire_bytes_sent, 4088)
        status_update.assert_not_called()
        self.assertFalse(item._last_transfer_captured_response)
        self.assertFalse(item._last_transfer_status_sampled)



    def test_scheduled_fec_sample_drains_queue_before_full_duplex_fec_frame(self):
        item = _controller(requested=True)
        item._transport_envelope_enabled = True
        item._fec_transport_enabled = True
        item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V8
        item._update_receiver_status = lambda _response, **_kwargs: True
        colors = np.zeros((8 * 138, 3), dtype=np.uint8)

        with mock.patch.object(protocol.time, "sleep") as sleep:
            item.set_all_pixels(colors, wall_frame_sequence=76)

        self.assertEqual(len(item.spi.response_packets), 4)
        for packet in item.spi.response_packets[:3]:
            self.assertEqual(
                len(packet),
                protocol._aligned_envelope_wire_size(
                    protocol.RECEIVER_STATUS_BYTES_V8
                ),
            )
        self.assertEqual(len(item.spi.write_only_packets), 0)
        self.assertEqual(len(item.spi.response_packets[3]), 4088)
        self.assertEqual(item.spi.response_packets[3][:2], b"\x0b\x07")
        self.assertEqual(item._spi_transfers, 4)
        self.assertEqual(
            sleep.call_args_list,
            [
                mock.call(protocol.FRESH_STATUS_DRAIN_INTERVAL_SECONDS),
                mock.call(protocol.FRESH_STATUS_DRAIN_INTERVAL_SECONDS),
            ],
        )
        self.assertEqual(item._fec_frames_sent, 1)
        self.assertEqual(item._full_frame_transfers, 1)
        self.assertEqual(item._full_frame_status_transfers, 1)
        self.assertEqual(item._full_frame_status_samples, 1)
        self.assertEqual(item._full_frame_status_sample_misses, 0)
        self.assertEqual(item._full_frame_write_only_transfers, 0)
        self.assertEqual(item._full_frame_frames_since_status_sample, 0)
        self.assertEqual(item._full_frame_max_status_sample_gap, 0)


    def test_failed_transfer_does_not_claim_fec_or_full_frame_sent(self):
        item = _controller(requested=True)
        item._transport_envelope_enabled = True
        item._fec_transport_enabled = True

        def fail(_packet):
            raise OSError("injected transfer failure")

        item.spi.xfer2 = fail
        colors = np.zeros((8 * 138, 3), dtype=np.uint8)
        with self.assertRaisesRegex(OSError, "injected transfer failure"):
            item.set_all_pixels(colors, wall_frame_sequence=1)
        self.assertEqual(item._fec_frames_sent, 0)
        self.assertEqual(item._fec_codewords_sent, 0)
        self.assertEqual(item._fec_parity_bytes_sent, 0)
        self.assertEqual(item._fec_data_padding_bytes_sent, 0)
        # The long-standing transport counters describe the single attempted
        # ioctl (which is never retried); FEC/full-frame ``sent`` counters only
        # advance after successful I/O.
        self.assertEqual(item._spi_transfers, 1)
        self.assertEqual(item._bytes_sent, 4088)
        self.assertEqual(item._full_frame_transfers, 0)
        self.assertEqual(item._errors, 1)

    def test_non_fec_current_geometry_above_fec_max_constructs_and_configures(self):
        fake = _RecordingSpi()
        with mock.patch.object(protocol.spidev, "SpiDev", return_value=fake):
            item = protocol.LEDController(strips=10, leds_per_strip=130)
        self.assertGreater(1 + item.total_leds * 3, protocol.MAX_FEC_SEMANTIC_BYTES)
        self.assertIsNone(item._fec_frame_packet)
        item._refresh_configuration = lambda **_kwargs: None
        item.configure()
        self.assertIsNone(item._fec_frame_packet)
        self.assertEqual(len(item._aligned_frame_packet), 3908)
        previous_frame = item._frame_packet
        previous_aligned = item._aligned_frame_packet
        item._fec_transport_requested = True
        with self.assertRaisesRegex(ValueError, "FEC semantic limit"):
            item.configure()
        self.assertIs(item._frame_packet, previous_frame)
        self.assertIs(item._aligned_frame_packet, previous_aligned)









if __name__ == "__main__":
    unittest.main()
