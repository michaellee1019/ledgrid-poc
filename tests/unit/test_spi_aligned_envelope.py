import binascii
import sys
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

    def xfer2(self, packet):
        self.packets.append(bytes(packet))
        return bytes(len(packet))


def _controller(*, envelope):
    item = protocol.LEDController.__new__(protocol.LEDController)
    item.spi = _RecordingSpi()
    item._transport_lock = threading.RLock()
    item._transport_envelope_enabled = envelope
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
    item._update_receiver_status = lambda _response: None
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

    def test_controller_rejects_oversized_semantic_payload_before_io(self):
        item = _controller(envelope=True)
        payload = bytearray(protocol.MAX_ALIGNED_SEMANTIC_BYTES + 1 + 2)
        with self.assertRaisesRegex(ValueError, "1..4090"):
            item._xfer_packet(payload, protocol.MAX_ALIGNED_SEMANTIC_BYTES + 1)
        self.assertEqual(item.spi.packets, [])


if __name__ == "__main__":
    unittest.main()
