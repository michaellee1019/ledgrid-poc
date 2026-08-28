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
    item._transport_envelope_enabled = False
    item._fec_transport_requested = requested
    item._fec_transport_enabled = False
    item._transport_envelope_candidate = None
    item._transport_envelope_candidate_streak = 0
    item._transport_envelope_last_receiver_packets = None
    item._transport_envelope_fresh_observations = 0
    item._transport_envelope_stale_observations = 0
    item._transport_envelope_counter_resets = 0
    item._transport_envelope_invalid_resets = 0
    item._transport_envelope_transitions = 0
    item._fec_transport_candidate = None
    item._fec_transport_candidate_streak = 0
    item._fec_transport_last_receiver_packets = None
    item._fec_transport_fresh_observations = 0
    item._fec_transport_stale_observations = 0
    item._fec_transport_counter_resets = 0
    item._fec_transport_invalid_resets = 0
    item._fec_transport_transitions = 0
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
    item._update_receiver_status = lambda _response: False
    return item


def _status_v7(receiver_packets, *, fec=True):
    response = bytearray(protocol.RECEIVER_STATUS_BYTES_V7)
    response[:5] = b"LGS7\x07"
    response[12:16] = receiver_packets.to_bytes(4, "big")
    capabilities = protocol.CAPABILITY_ALIGNED_ENVELOPE_V1
    if fec:
        capabilities |= protocol.CAPABILITY_FEC_ENVELOPE_V2
    response[64:68] = capabilities.to_bytes(4, "big")
    response[314] = protocol.STAGGER_OFF
    return response


def _status_v3(receiver_packets):
    response = bytearray(protocol.RECEIVER_STATUS_BYTES_V3)
    response[:5] = b"LGS3\x03"
    response[12:16] = receiver_packets.to_bytes(4, "big")
    response[64:68] = protocol.CAPABILITY_ALIGNED_ENVELOPE_V1.to_bytes(4, "big")
    response[314] = protocol.STAGGER_OFF
    return response


class SpiFecEnvelopeTests(unittest.TestCase):
    def test_exact_fixed_codeword_layout_and_golden_digest(self):
        packet = protocol._encode_fec_envelope(bytes((protocol.CMD_SHOW,)))
        self.assertEqual(len(packet), 260)
        self.assertEqual(packet[:8], b"\x0b\x02\x00\x08\x0b\x01\x00\x01")
        self.assertEqual(packet[12:128], bytes(116))
        self.assertEqual(packet[128:130], bytes.fromhex("007c"))
        self.assertEqual(packet[130:258], bytes(128))
        self.assertEqual(packet[258:260], bytes(2))
        self.assertEqual(
            hashlib.sha256(packet).hexdigest(),
            "25ea439adb682e424ad60e15d0f4f398e15b4f405d8fdedf716d4a93f152349d",
        )

    def test_full_and_tail_frames_have_exact_bounded_wire_overhead(self):
        for semantic_size, codewords, wire_size, data_padding in (
            (3313, 26, 3380, 4),
            (415, 4, 520, 84),
            (protocol.MAX_FEC_SEMANTIC_BYTES, 30, 3900, 0),
        ):
            with self.subTest(semantic_size=semantic_size):
                packet = protocol._encode_fec_envelope(bytes(semantic_size))
                self.assertEqual(len(packet), wire_size)
                self.assertEqual(len(packet) % 4, 0)
                self.assertLessEqual(len(packet), protocol.MAX_SPI_TRANSFER)
                self.assertEqual(
                    codewords * protocol.FEC_DATA_BYTES
                    - protocol.FEC_ENVELOPE_HEADER_BYTES
                    - protocol._aligned_envelope_wire_size(semantic_size),
                    data_padding,
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

    def test_opt_in_requires_three_fresh_capability_observations(self):
        item = _controller(requested=True)
        for counter in (1, 2):
            protocol.LEDController._update_receiver_status(
                item, _status_v7(counter)
            )
            self.assertFalse(item._fec_transport_enabled)
        protocol.LEDController._update_receiver_status(item, _status_v7(3))
        self.assertTrue(item._transport_envelope_enabled)
        self.assertTrue(item._fec_transport_enabled)
        self.assertEqual(
            item._receiver_status_query_bytes,
            protocol.RECEIVER_STATUS_BYTES_V7,
        )

    def test_unrequested_receiver_never_enables_v2(self):
        item = _controller(requested=False)
        for counter in range(1, 8):
            protocol.LEDController._update_receiver_status(
                item, _status_v7(counter)
            )
        self.assertTrue(item._transport_envelope_enabled)
        self.assertFalse(item._fec_transport_enabled)

    def test_stale_and_capability_absence_cannot_enable(self):
        item = _controller(requested=True)
        protocol.LEDController._update_receiver_status(item, _status_v7(1))
        protocol.LEDController._update_receiver_status(item, _status_v7(1))
        protocol.LEDController._update_receiver_status(item, _status_v7(2))
        self.assertFalse(item._fec_transport_enabled)

    def test_negotiated_fec_downgrades_only_after_fresh_consensus_and_resets(self):
        item = _controller(requested=True)
        for counter in (1, 2, 3):
            protocol.LEDController._update_receiver_status(item, _status_v7(counter))
        self.assertTrue(item._fec_transport_enabled)
        for counter in (4, 5):
            protocol.LEDController._update_receiver_status(
                item, _status_v7(counter, fec=False)
            )
            self.assertTrue(item._fec_transport_enabled)
        protocol.LEDController._update_receiver_status(
            item, _status_v7(6, fec=False)
        )
        self.assertFalse(item._fec_transport_enabled)
        protocol.LEDController._update_receiver_status(item, _status_v7(1))
        self.assertFalse(item._fec_transport_enabled)
        self.assertGreaterEqual(item._fec_transport_counter_resets, 1)
        for counter in (3, 4, 5):
            protocol.LEDController._update_receiver_status(
                item, _status_v7(counter, fec=False)
            )
        self.assertFalse(item._fec_transport_enabled)

    def test_selected_full_frame_uses_v2_once_and_accounts_exactly(self):
        item = _controller(requested=True)
        item._transport_envelope_enabled = True
        item._fec_transport_enabled = True
        colors = np.zeros((8 * 138, 3), dtype=np.uint8)
        item.set_all_pixels(colors, wall_frame_sequence=1)
        packet = item.spi.packets[-1]
        self.assertEqual(packet[:2], b"\x0b\x02")
        self.assertEqual(len(packet), 3380)
        self.assertEqual(item._fec_frames_sent, 1)
        self.assertEqual(item._fec_codewords_sent, 26)
        self.assertEqual(item._fec_parity_bytes_sent, 52)
        self.assertEqual(item._fec_data_padding_bytes_sent, 4)
        self.assertEqual(item._full_frame_wire_bytes_sent, 3380)

    def test_scheduled_fec_sample_uses_query_then_write_only_frame(self):
        item = _controller(requested=True)
        item._transport_envelope_enabled = True
        item._fec_transport_enabled = True
        item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V7
        item._update_receiver_status = lambda _response: True
        colors = np.zeros((8 * 138, 3), dtype=np.uint8)

        item.set_all_pixels(colors, wall_frame_sequence=76)

        self.assertEqual(len(item.spi.response_packets), 1)
        self.assertEqual(
            len(item.spi.response_packets[0]),
            protocol._aligned_envelope_wire_size(
                protocol.RECEIVER_STATUS_BYTES_V7
            ),
        )
        self.assertEqual(len(item.spi.write_only_packets), 1)
        self.assertEqual(len(item.spi.write_only_packets[0]), 3380)
        self.assertEqual(item.spi.write_only_packets[0][:2], b"\x0b\x02")
        self.assertEqual(item._spi_transfers, 2)
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

        item.spi.writebytes2 = fail
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
        self.assertEqual(item._bytes_sent, 3380)
        self.assertEqual(item._full_frame_transfers, 0)
        self.assertEqual(item._errors, 1)

    def test_non_fec_legacy_geometry_above_fec_max_constructs_and_configures(self):
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

    def test_status_v7_parses_exact_accounting_and_timing(self):
        item = _controller(requested=True)
        response = _status_v7(9)
        values = (11, 7, 3, 4, 1, 2, 1)
        for offset, value in zip(range(1216, 1244, 4), values):
            response[offset:offset + 4] = value.to_bytes(4, "big")
        response[1244:1246] = (83).to_bytes(2, "big")
        response[1246:1248] = (109).to_bytes(2, "big")
        self.assertTrue(
            protocol.LEDController._update_receiver_status(item, response)
        )
        self.assertEqual(item._receiver_fec_packets_received, 11)
        self.assertEqual(item._receiver_fec_packets_accepted, 7)
        self.assertEqual(item._receiver_fec_corrected_packets, 3)
        self.assertEqual(item._receiver_fec_corrected_codewords, 4)
        self.assertEqual(item._receiver_fec_uncorrectable_packets, 1)
        self.assertEqual(item._receiver_fec_semantic_crc_errors, 2)
        self.assertEqual(item._receiver_fec_framing_errors, 1)
        self.assertEqual(11, 7 + 1 + 2 + 1)
        self.assertEqual(item._receiver_fec_last_decode_us, 83)
        self.assertEqual(item._receiver_fec_max_decode_us, 109)

    def test_latest_status_version_can_return_to_v3_after_v7_without_losing_proof(self):
        item = _controller(requested=True)

        self.assertTrue(
            protocol.LEDController._update_receiver_status(item, _status_v7(9))
        )
        self.assertEqual(item._receiver_status_version, 7)
        self.assertEqual(item._receiver_status_max_version_seen, 7)

        self.assertTrue(
            protocol.LEDController._update_receiver_status(item, _status_v3(10))
        )
        self.assertEqual(item._receiver_status_version, 3)
        self.assertEqual(item._receiver_status_max_version_seen, 7)

    def test_status_max_version_seen_is_actual_and_starts_at_observed_v3(self):
        item = _controller(requested=True)

        self.assertTrue(
            protocol.LEDController._update_receiver_status(item, _status_v3(1))
        )

        self.assertEqual(item._receiver_status_version, 3)
        self.assertEqual(item._receiver_status_max_version_seen, 3)

        mismatched = _status_v7(2)
        mismatched[4] = 3
        self.assertFalse(
            protocol.LEDController._update_receiver_status(item, mismatched)
        )
        self.assertEqual(item._receiver_status_version, 3)
        self.assertEqual(item._receiver_status_max_version_seen, 3)


if __name__ == "__main__":
    unittest.main()
