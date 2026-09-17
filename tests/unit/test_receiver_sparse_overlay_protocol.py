"""Host-side acceptance for the frozen sparse-overlay SPI contract."""

from __future__ import annotations

import binascii
import json
from pathlib import Path
import struct
import sys
import types
import unittest
from unittest import mock

import numpy as np


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from drivers import spi_controller as protocol


SESSION = bytes(range(16))
DIGEST = bytes(range(32))


class FakeSparseSpi:
    """Two-deep response queue with v3-to-v4 status negotiation."""

    def __init__(
        self,
        *,
        sparse_capable=True,
        acknowledge=True,
        acknowledge_after_status_queries=0,
        overlay_result=1,
        crc_on_command=False,
        corrupt_status_after_command=False,
        reset_on_command=False,
        advance_packets_after_command=False,
        fec_capable=False,
        drop_sparse_attempts=0,
        hide_sparse_ack_attempts=0,
        drift_sparse_authority=False,
        drift_sparse_generation=False,
        protected_current=True,
    ):
        self.max_speed_hz = 20_000_000
        self.mode = 0
        self.bits_per_word = 8
        self.sparse_capable = sparse_capable
        self.acknowledge = acknowledge
        self.acknowledge_after_status_queries = acknowledge_after_status_queries
        self.overlay_result = overlay_result
        self.configured_overlay_result = overlay_result
        self.crc_on_command = crc_on_command
        self.corrupt_status_after_command = corrupt_status_after_command
        self.reset_on_command = reset_on_command
        self.advance_packets_after_command = advance_packets_after_command
        self.fec_capable = fec_capable
        self.drop_sparse_attempts = drop_sparse_attempts
        self.hide_sparse_ack_attempts = hide_sparse_ack_attempts
        self.drift_sparse_authority = drift_sparse_authority
        self.drift_sparse_generation = drift_sparse_generation
        self.protected_current = protected_current
        self.sparse_attempts = 0
        self.effective_sparse_mutations = 0
        self.last_sparse_payload = None
        self.hide_sparse_status = False
        self.hidden_status_queries = 0
        self.overlay_session = bytes(16)
        self.overlay_staged_generation = 1
        self.mutation_seen = False
        self.packet_counter = 100
        self.crc_errors = 0
        self.packets = []
        self.last_command = 0
        self.operation_sequence = 0
        self.pending_ack = None
        self.queued = [(0, 0), (0, 0)]
        initial_status_bytes = (
            protocol.RECEIVER_STATUS_BYTES_V8
            if protected_current else protocol.RECEIVER_STATUS_BYTES_V3
        )
        self.queued_status_bytes = [initial_status_bytes, initial_status_bytes]

    def open(self, _bus, _device):
        pass

    def close(self):
        pass

    def _status(self, length, state):
        if length < protocol.RECEIVER_STATUS_BYTES_V3:
            return bytes(length)
        status_bytes = (
            protocol.RECEIVER_STATUS_BYTES_V8
            if self.protected_current
            and length >= protocol.RECEIVER_STATUS_BYTES_V8
            else protocol.RECEIVER_STATUS_BYTES_V4
            if self.sparse_capable and length >= protocol.RECEIVER_STATUS_BYTES_V4
            else protocol.RECEIVER_STATUS_BYTES_V3
        )
        response = bytearray(status_bytes)
        response[:5] = (
            b"LGS8\x08"
            if status_bytes == protocol.RECEIVER_STATUS_BYTES_V8
            else b"LGS4\x04"
            if status_bytes == protocol.RECEIVER_STATUS_BYTES_V4
            else b"LGS3\x03"
        )
        capabilities = (
            protocol.CAPABILITY_STATUS_V3
            | protocol.CAPABILITY_EXPLICIT_BASE_OWNERSHIP
            | protocol.CAPABILITY_ALIGNED_ENVELOPE_V1
        )
        if self.sparse_capable:
            capabilities |= (
                protocol.CAPABILITY_SPARSE_OVERLAY_V1
                | protocol.CAPABILITY_SPARSE_OVERLAY_BATCH_V1
            )
        if self.protected_current:
            capabilities |= protocol.CAPABILITY_STATUS_CRC32_V8
        if self.fec_capable:
            capabilities |= (
                protocol.CAPABILITY_FEC_ENVELOPE_V7
            )
        response[64:68] = capabilities.to_bytes(4, "big")
        response[12:16] = self.packet_counter.to_bytes(4, "big")
        response[16:20] = self.crc_errors.to_bytes(4, "big")
        if self.protected_current:
            response[68] = 1
            response[73] = 2
            response[96:104] = (1).to_bytes(8, "big")
            response[144:176] = bytes((0x11,)) * 32
            response[280:296] = bytes((0x22,)) * 16
        response[313] = state[0]
        response[316:320] = state[1].to_bytes(4, "big")
        if status_bytes >= protocol.RECEIVER_STATUS_BYTES_V4:
            if state[1]:
                response[320] = self.overlay_result
            response[336:344] = self.overlay_staged_generation.to_bytes(8, "big")
            response[344:352] = (1).to_bytes(8, "big")
            response[352:360] = (1).to_bytes(8, "big")
            response[360:368] = (1).to_bytes(8, "big")
            response[384:400] = self.overlay_session
        if status_bytes == protocol.RECEIVER_STATUS_BYTES_V8:
            response[protocol.RECEIVER_STATUS_BYTES_V7:] = binascii.crc32(
                response[:protocol.RECEIVER_STATUS_BYTES_V7]
            ).to_bytes(4, "big")
        return bytes(response[:length])

    def xfer2(self, packet):
        wire = bytes(packet)
        command = wire[0]
        semantic_length = len(wire) - protocol.CRC_BYTES
        semantic = wire[:semantic_length]
        if command == protocol.CMD_ALIGNED_ENVELOPE:
            if wire[1] == protocol.FEC_ENVELOPE_VERSION:
                semantic = _decode_clean_v7(wire)
                semantic_length = len(semantic)
                command = semantic[0]
            else:
                semantic_length = int.from_bytes(wire[2:4], "big")
                semantic = wire[
                    protocol.ALIGNED_ENVELOPE_HEADER_BYTES:
                    protocol.ALIGNED_ENVELOPE_HEADER_BYTES + semantic_length
                ]
                command = semantic[0]
        status_query = command == protocol.CMD_STATUS_QUERY
        self.packets.append(wire)
        prior = self.queued.pop(0)
        prior_status_bytes = self.queued_status_bytes.pop(0)
        response = self._status(prior_status_bytes, prior)
        if len(response) < len(wire):
            response += bytes(len(wire) - len(response))
        else:
            response = response[:len(wire)]
        if status_query and self.pending_ack is not None:
            command, sequence, remaining = self.pending_ack
            remaining -= 1
            if remaining <= 0:
                self.last_command = command
                self.operation_sequence = sequence
                self.pending_ack = None
            else:
                self.pending_ack = (command, sequence, remaining)
        elif not status_query:
            self.mutation_seen = True
            sparse = command in (
                protocol.CMD_OVERLAY_PATCH,
                protocol.CMD_OVERLAY_PATCH_BATCH,
            )
            dropped = False
            if sparse:
                self.sparse_attempts += 1
                dropped = self.sparse_attempts <= self.drop_sparse_attempts
                self.hide_sparse_status = (
                    self.sparse_attempts <= self.hide_sparse_ack_attempts
                )
                if self.drift_sparse_authority and self.sparse_attempts == 1:
                    self.overlay_session = bytes((0xA5,)) * 16
                if self.drift_sparse_generation and self.sparse_attempts == 1:
                    self.overlay_staged_generation = 2
            if self.reset_on_command:
                self.packet_counter = 0
                self.last_command = 0
                self.operation_sequence = 0
                self.pending_ack = None
            elif self.crc_on_command:
                self.crc_errors += 1
            elif self.acknowledge and not dropped:
                duplicate = bool(
                    sparse
                    and self.protected_current
                    and self.last_sparse_payload == bytes(semantic)
                    and self.last_command == command
                )
                if duplicate:
                    if self.configured_overlay_result in (1, 2):
                        self.overlay_result = 2
                    next_sequence = self.operation_sequence
                else:
                    if self.configured_overlay_result in (1, 2):
                        self.overlay_result = 1
                    next_sequence = self.operation_sequence + 1
                    if sparse:
                        self.effective_sparse_mutations += 1
                        self.last_sparse_payload = bytes(semantic)
                if self.acknowledge_after_status_queries:
                    self.pending_ack = (
                        command,
                        next_sequence,
                        self.acknowledge_after_status_queries,
                    )
                else:
                    self.last_command = command
                    self.operation_sequence = next_sequence
        if status_query and (
            self.protected_current
            or (self.mutation_seen and self.advance_packets_after_command)
        ):
            self.packet_counter += 1
        self.queued.append((self.last_command, self.operation_sequence))
        requested_v4 = (
            status_query
            and self.sparse_capable
            and semantic_length >= protocol.RECEIVER_STATUS_BYTES_V4
        )
        self.queued_status_bytes.append(
            protocol.RECEIVER_STATUS_BYTES_V8
            if self.protected_current
            else protocol.RECEIVER_STATUS_BYTES_V4
            if requested_v4 else protocol.RECEIVER_STATUS_BYTES_V3
        )
        hide_response = bool(
            status_query and self.mutation_seen and self.hide_sparse_status
        )
        if hide_response:
            self.hidden_status_queries += 1
            if self.hidden_status_queries >= protocol.COMMAND_ACK_MAX_STATUS_QUERIES:
                self.hide_sparse_status = False
        if (
            status_query
            and self.mutation_seen
            and (self.corrupt_status_after_command or hide_response)
        ):
            return bytes(len(response))
        return response


class FakeAckClock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds


class TimingAwareSparseSpi(FakeSparseSpi):
    """Record acknowledgement queries that arrive before the queue can refill."""

    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.next_query_at = None
        self.query_times = []
        self.premature_queries = []

    def xfer2(self, packet):
        command = (
            int(packet[protocol.ALIGNED_ENVELOPE_HEADER_BYTES])
            if int(packet[0]) == protocol.CMD_ALIGNED_ENVELOPE
            and int(packet[1]) == protocol.ALIGNED_ENVELOPE_VERSION
            else int(packet[0])
        )
        now = self.clock.now
        if command == protocol.CMD_STATUS_QUERY:
            self.query_times.append(now)
            if self.next_query_at is not None and now < self.next_query_at:
                self.premature_queries.append((now, self.next_query_at))
        self.next_query_at = now + protocol.COMMAND_ACK_POLL_INTERVAL_SECONDS
        return super().xfer2(packet)


def controller(
    *,
    sparse_capable=True,
    acknowledge=True,
    acknowledge_after_status_queries=0,
    overlay_result=1,
    crc_on_command=False,
    corrupt_status_after_command=False,
    reset_on_command=False,
    advance_packets_after_command=False,
    fec_capable=False,
    drop_sparse_attempts=0,
    hide_sparse_ack_attempts=0,
    drift_sparse_authority=False,
    drift_sparse_generation=False,
    protected_current=True,
):
    spi = FakeSparseSpi(
        sparse_capable=sparse_capable,
        acknowledge=acknowledge,
        acknowledge_after_status_queries=acknowledge_after_status_queries,
        overlay_result=overlay_result,
        crc_on_command=crc_on_command,
        corrupt_status_after_command=corrupt_status_after_command,
        reset_on_command=reset_on_command,
        advance_packets_after_command=advance_packets_after_command,
        fec_capable=fec_capable,
        drop_sparse_attempts=drop_sparse_attempts,
        hide_sparse_ack_attempts=hide_sparse_ack_attempts,
        drift_sparse_authority=drift_sparse_authority,
        drift_sparse_generation=drift_sparse_generation,
        protected_current=protected_current,
    )
    with mock.patch.object(protocol.spidev, "SpiDev", return_value=spi):
        item = protocol.LEDController(strips=8, leds_per_strip=138)
    spi.packets.clear()
    spi.last_command = 0
    spi.operation_sequence = 0
    spi.pending_ack = None
    spi.mutation_seen = False
    spi.packet_counter = 100
    spi.crc_errors = 0
    spi.sparse_attempts = 0
    spi.effective_sparse_mutations = 0
    spi.last_sparse_payload = None
    spi.hide_sparse_status = False
    spi.hidden_status_queries = 0
    spi.overlay_session = bytes(16)
    spi.overlay_staged_generation = 1
    spi.queued = [(0, 0), (0, 0)]
    spi.queued_status_bytes = [
        protocol.RECEIVER_STATUS_BYTES_V8 if protected_current
        else protocol.RECEIVER_STATUS_BYTES_V3,
        protocol.RECEIVER_STATUS_BYTES_V8 if protected_current
        else protocol.RECEIVER_STATUS_BYTES_V3,
    ]
    if protected_current:
        item._receiver_capabilities |= (
            protocol.CAPABILITY_STATUS_CRC32_V8
            | (
                protocol.CAPABILITY_SPARSE_OVERLAY_V1
                | protocol.CAPABILITY_SPARSE_OVERLAY_BATCH_V1
                if sparse_capable else 0
            )
        )
        item._receiver_status_integrity_required = True
        item._receiver_status_integrity_established = True
        item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V8
        item._hardware_serial = "02:00:00:00:00:01"
    return item


def zeros(count):
    return np.zeros((count, 4), dtype=np.uint8)


def _wire_command(packet):
    packet = bytes(packet)
    if packet[0] != protocol.CMD_ALIGNED_ENVELOPE:
        return packet[0]
    if packet[1] == protocol.FEC_ENVELOPE_VERSION:
        return _decode_clean_v7(packet)[0]
    return packet[protocol.ALIGNED_ENVELOPE_HEADER_BYTES]


def _decode_clean_v7(packet):
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


class SparseOverlaySerializerTests(unittest.TestCase):
    def assert_lengths(self, packets):
        expected = (58, 66, 30 + 8, 50, 34, 30)
        self.assertEqual(tuple(map(len, packets)), expected)

    def test_all_fixed_packets_are_exact_big_endian_bytes(self):
        packets = (
            protocol.LEDController.serialize_controller_session_begin(
                controller_session_id=SESSION,
                desired_revision=0x0102030405060708,
                authoritative_snapshot_digest=DIGEST,
            ),
            protocol.LEDController.serialize_overlay_begin(
                controller_session_id=SESSION,
                generation=0x0102030405060708,
                prior_generation=0x1112131415161718,
                scene_revision=0x2122232425262728,
                scene_epoch=0x3132333435363738,
                base_revision=0x4142434445464748,
                update_kind=protocol.OVERLAY_UPDATE_DELTA,
                expected_patches=0x5152,
                lease_ms=0x61626364,
            ),
            protocol.LEDController.serialize_overlay_patch(
                controller_session_id=SESSION,
                generation=0x0102030405060708,
                start=0x0102,
                premultiplied_rgba=bytes((1, 2, 3, 4, 0, 0, 0, 0)),
            ),
            protocol.LEDController.serialize_overlay_commit(
                controller_session_id=SESSION,
                generation=0x0102030405060708,
                scene_epoch=0x1112131415161718,
                base_revision=0x2122232425262728,
                present_at_scene_time_us=0x3132333435363738,
            ),
            protocol.LEDController.serialize_overlay_clear(
                controller_session_id=SESSION,
                generation=0x0102030405060708,
                scene_revision=0x1112131415161718,
            ),
            protocol.LEDController.serialize_overlay_renew(
                controller_session_id=SESSION,
                generation=0x0102030405060708,
                lease_ms=0x11121314,
            ),
        )
        self.assert_lengths(packets)
        self.assertEqual(
            packets[0],
            b"\x20\x01" + SESSION + b"\x01\x02\x03\x04\x05\x06\x07\x08" + DIGEST,
        )
        self.assertEqual(packets[1][:2], b"\x30\x01")
        self.assertEqual(packets[1][2:18], SESSION)
        self.assertEqual(
            struct.unpack(">QQQQQBBHI", packets[1][18:]),
            (
                0x0102030405060708,
                0x1112131415161718,
                0x2122232425262728,
                0x3132333435363738,
                0x4142434445464748,
                1,
                2,
                0x5152,
                0x61626364,
            ),
        )
        self.assertEqual(
            packets[2],
            b"\x31\x01" + SESSION
            + b"\x01\x02\x03\x04\x05\x06\x07\x08\x01\x02\x00\x02"
            + bytes((1, 2, 3, 4, 0, 0, 0, 0)),
        )
        self.assertEqual(
            packets[3],
            b"\x32\x01" + SESSION
            + bytes.fromhex(
                "0102030405060708 1112131415161718 "
                "2122232425262728 3132333435363738"
            ),
        )
        self.assertEqual(
            packets[4],
            b"\x33\x01" + SESSION
            + bytes.fromhex("0102030405060708 1112131415161718"),
        )
        self.assertEqual(
            packets[5],
            b"\x34\x01" + SESSION
            + bytes.fromhex("0102030405060708 11121314"),
        )

    def test_delta_may_declare_zero_patches_but_full_snapshot_may_not(self):
        common = dict(
            controller_session_id=SESSION,
            generation=1,
            prior_generation=0,
            scene_revision=2,
            scene_epoch=3,
            base_revision=2,
            expected_patches=0,
            lease_ms=3000,
        )
        packet = protocol.LEDController.serialize_overlay_begin(
            **common, update_kind=protocol.OVERLAY_UPDATE_DELTA
        )
        self.assertEqual(len(packet), protocol.OVERLAY_BEGIN_BYTES)
        with self.assertRaisesRegex(ValueError, "at least one patch"):
            protocol.LEDController.serialize_overlay_begin(
                **common, update_kind=protocol.OVERLAY_UPDATE_FULL_SNAPSHOT
            )

    def test_multi_span_batch_is_exact_big_endian_bytes(self):
        packet = protocol.LEDController.serialize_overlay_patch_batch(
            controller_session_id=SESSION,
            generation=0x0102030405060708,
            spans=(
                (0x0010, bytes((1, 2, 3, 4))),
                (0x0200, bytes((5, 6, 7, 8, 0, 0, 0, 0))),
            ),
        )
        self.assertEqual(
            packet,
            b"\x35\x01" + SESSION
            + bytes.fromhex("0102030405060708 0002")
            + bytes.fromhex("0010 0001") + bytes((1, 2, 3, 4))
            + bytes.fromhex("0200 0002") + bytes((5, 6, 7, 8, 0, 0, 0, 0)),
        )
        self.assertEqual(len(packet), 48)

    def test_generated_batch_packets_match_host_serializer_and_crc(self):
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "animation_pipeline_v1.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        vectors = [
            item for item in fixture["firmware_protocol"]["wire_packet_vectors"]
            if item["command"] == protocol.CMD_OVERLAY_PATCH_BATCH
        ]
        self.assertGreaterEqual(len(vectors), 4)
        for vector in vectors:
            with self.subTest(vector=vector["id"]):
                expected = bytes.fromhex(vector["packet_hex"])
                span_count = int.from_bytes(expected[26:28], "big")
                offset = protocol.OVERLAY_PATCH_BATCH_HEADER_BYTES
                spans = []
                for _ in range(span_count):
                    start, count = struct.unpack(">HH", expected[offset:offset + 4])
                    offset += protocol.OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES
                    rgba_end = offset + count * 4
                    spans.append((start, expected[offset:rgba_end]))
                    offset = rgba_end
                self.assertEqual(offset, len(expected) - protocol.CRC_BYTES)
                packet = protocol.LEDController.serialize_overlay_patch_batch(
                    controller_session_id=expected[2:18],
                    generation=int.from_bytes(expected[18:26], "big"),
                    spans=spans,
                )
                self.assertEqual(packet, expected[:-2])
                self.assertEqual(
                    binascii.crc_hqx(packet, 0xFFFF).to_bytes(2, "big"),
                    expected[-2:],
                )

    def test_batch_packer_fills_maximum_packet_and_splits_tail(self):
        packets = protocol.LEDController.serialize_overlay_patch_batches(
            controller_session_id=SESSION,
            generation=1,
            patches=(
                (0, zeros(protocol.MAX_RGBA_PIXELS_PER_BATCH_SPAN)),
                (
                    protocol.MAX_RGBA_PIXELS_PER_BATCH_SPAN,
                    zeros(
                        protocol.OVERLAY_LOCAL_PIXELS
                        - protocol.MAX_RGBA_PIXELS_PER_BATCH_SPAN
                    ),
                ),
            ),
            update_kind=protocol.OVERLAY_UPDATE_FULL_SNAPSHOT,
        )
        self.assertEqual(len(packets), 2)
        self.assertEqual(len(packets[0]), 4088)
        self.assertEqual(
            len(protocol._encode_aligned_envelope(packets[0])),
            protocol.MAX_SPI_TRANSFER,
        )
        self.assertEqual(packets[0][26:28], b"\x00\x01")
        self.assertEqual(packets[0][28:32], b"\x00\x00\x03\xf6")
        self.assertEqual(packets[1][28:32], b"\x03\xf6\x00\x5a")

    def test_batch_rejects_empty_unsorted_overlap_out_of_bounds_and_overflow(self):
        common = dict(controller_session_id=SESSION, generation=1)
        invalid = (
            (),
            ((5, zeros(1)), (4, zeros(1))),
            ((5, zeros(2)), (6, zeros(1))),
            ((1104, zeros(1)),),
            tuple((index * 2, zeros(1)) for index in range(509)),
        )
        for spans in invalid:
            with self.subTest(spans=len(spans)), self.assertRaises(ValueError):
                protocol.LEDController.serialize_overlay_patch_batch(
                    **common, spans=spans
                )

    def test_maximum_aligned_patch_exactly_fills_wire_transfer(self):
        payload = protocol.LEDController.serialize_overlay_patch(
            controller_session_id=SESSION,
            generation=1,
            start=0,
            premultiplied_rgba=zeros(protocol.MAX_RGBA_PIXELS_PER_PATCH),
        )
        packet = protocol._encode_aligned_envelope(payload)
        self.assertEqual(len(packet), protocol.MAX_SPI_TRANSFER)
        self.assertEqual(packet[30:34], b"\x00\x00\x03\xf7")
        self.assertEqual(
            packet[-2:],
            binascii.crc_hqx(packet[:-2], 0xFFFF).to_bytes(2, "big"),
        )

    def test_scalar_identity_format_and_rgba_validation_is_strict(self):
        begin = dict(
            controller_session_id=SESSION,
            generation=1,
            prior_generation=0,
            scene_revision=1,
            scene_epoch=1,
            base_revision=1,
            update_kind=protocol.OVERLAY_UPDATE_DELTA,
            expected_patches=1,
            lease_ms=1,
        )
        for key, bad in (
            ("controller_session_id", bytearray(16)),
            ("controller_session_id", b"short"),
            ("generation", True),
            ("prior_generation", -1),
            ("scene_revision", 2**64),
            ("scene_epoch", 1.5),
            ("base_revision", -1),
            ("format", 2),
            ("update_kind", 3),
            ("expected_patches", 2**16),
            ("lease_ms", 2**32),
        ):
            with self.subTest(key=key, bad=bad), self.assertRaises(
                (TypeError, ValueError)
            ):
                protocol.LEDController.serialize_overlay_begin(
                    **{**begin, key: bad}
                )

        patch = dict(
            controller_session_id=SESSION,
            generation=1,
            start=0,
        )
        bad_rgba = (
            b"",
            b"\x00\x00\x00",
            bytes((2, 0, 0, 1)),
            bytearray((0, 0, 0, 0)),
            np.zeros((1, 4), dtype=np.uint16),
            np.zeros((4,), dtype=np.uint8),
            np.zeros((4, 4), dtype=np.uint8)[::2],
            zeros(protocol.MAX_RGBA_PIXELS_PER_PATCH + 1),
        )
        for rgba in bad_rgba:
            with self.subTest(rgba=type(rgba).__name__), self.assertRaises(
                (TypeError, ValueError)
            ):
                protocol.LEDController.serialize_overlay_patch(
                    **patch, premultiplied_rgba=rgba
                )
        for start, rgba in ((-1, zeros(1)), (1104, zeros(1)), (1016, zeros(89))):
            with self.subTest(start=start), self.assertRaises((TypeError, ValueError)):
                protocol.LEDController.serialize_overlay_patch(
                    **{**patch, "start": start}, premultiplied_rgba=rgba
                )

    def test_session_digest_and_all_other_unsigned_fields_reject_bad_values(self):
        valid = dict(
            controller_session_id=SESSION,
            desired_revision=1,
            authoritative_snapshot_digest=DIGEST,
        )
        for key, bad in (
            ("controller_session_id", b"x" * 15),
            ("desired_revision", -1),
            ("desired_revision", 2**64),
            ("authoritative_snapshot_digest", bytearray(32)),
            ("authoritative_snapshot_digest", b"x" * 31),
        ):
            with self.subTest(key=key), self.assertRaises((TypeError, ValueError)):
                protocol.LEDController.serialize_controller_session_begin(
                    **{**valid, key: bad}
                )

        invalid_calls = (
            lambda: protocol.LEDController.serialize_overlay_patch(
                controller_session_id=SESSION,
                generation=True,
                start=0,
                premultiplied_rgba=zeros(1),
            ),
            lambda: protocol.LEDController.serialize_overlay_patch(
                controller_session_id=SESSION,
                generation=1,
                start=True,
                premultiplied_rgba=zeros(1),
            ),
            lambda: protocol.LEDController.serialize_overlay_commit(
                controller_session_id=SESSION,
                generation=-1,
                scene_epoch=0,
                base_revision=0,
                present_at_scene_time_us=0,
            ),
            lambda: protocol.LEDController.serialize_overlay_commit(
                controller_session_id=SESSION,
                generation=0,
                scene_epoch=0,
                base_revision=0,
                present_at_scene_time_us=2**64,
            ),
            lambda: protocol.LEDController.serialize_overlay_clear(
                controller_session_id=SESSION,
                generation=0,
                scene_revision=True,
            ),
            lambda: protocol.LEDController.serialize_overlay_renew(
                controller_session_id=SESSION,
                generation=0,
                lease_ms=-1,
            ),
        )
        for invalid_call in invalid_calls:
            with self.subTest(call=invalid_call), self.assertRaises(
                (TypeError, ValueError)
            ):
                invalid_call()


class SparseOverlayDriverTests(unittest.TestCase):

    def test_protected_sparse_drop_resends_once_with_original_sequence(self):
        item = controller(protected_current=True, drop_sparse_attempts=1)
        status = item.send_overlay_patch_batch(
            controller_session_id=SESSION,
            generation=1,
            spans=[(0, bytes((1, 2, 3, 4)))],
        )

        self.assertEqual(status["receiver_operation_sequence"], 1)
        self.assertEqual(item.spi.sparse_attempts, 2)
        self.assertEqual(item.spi.effective_sparse_mutations, 1)
        diagnostics = item.command_transfer_diagnostics()[-2:]
        self.assertEqual(diagnostics[0]["expected_operation_sequence"], 1)
        self.assertEqual(
            diagnostics[1]["retry_of_diagnostic_id"],
            diagnostics[0]["diagnostic_id"],
        )
        self.assertTrue(
            diagnostics[1]["outcome"].startswith("acknowledged")
        )

    def test_protected_sparse_lost_ack_replays_without_second_mutation(self):
        item = controller(
            protected_current=True, hide_sparse_ack_attempts=1
        )
        status = item.send_overlay_patch_batch(
            controller_session_id=SESSION,
            generation=1,
            spans=[(0, bytes((1, 2, 3, 4)))],
        )

        self.assertEqual(status["receiver_operation_sequence"], 1)
        self.assertEqual(status["receiver_overlay_operation_result"], 2)
        self.assertEqual(item.spi.sparse_attempts, 2)
        self.assertEqual(item.spi.effective_sparse_mutations, 1)
        retry = item.command_transfer_diagnostics()[-1]
        self.assertEqual(retry["expected_operation_sequence"], 1)
        self.assertTrue(retry["requires_idempotent_result"])

    def test_known_exact_repeat_accepts_same_sequence_without_stale_payload(self):
        item = controller(protected_current=True)
        fields = {
            "controller_session_id": SESSION,
            "generation": 1,
            "start": 7,
            "premultiplied_rgba": bytes((1, 2, 3, 4)),
        }
        first = item.send_overlay_patch(**fields)
        repeated = item.send_overlay_patch(**fields)

        self.assertEqual(first["receiver_operation_sequence"], 1)
        self.assertEqual(repeated["receiver_operation_sequence"], 1)
        self.assertEqual(repeated["receiver_overlay_operation_result"], 2)
        self.assertEqual(item.spi.effective_sparse_mutations, 1)
        diagnostic = item.command_transfer_diagnostics()[-1]
        self.assertEqual(diagnostic["expected_operation_sequence"], 1)
        self.assertTrue(diagnostic["requires_idempotent_result"])

    def test_sparse_retry_rejects_reset_and_authority_drift(self):
        reset = controller(protected_current=True, reset_on_command=True)
        with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
            reset.send_overlay_patch_batch(
                controller_session_id=SESSION, generation=1,
                spans=[(0, bytes((1, 2, 3, 4)))],
            )
        self.assertEqual(reset.spi.sparse_attempts, 1)
        self.assertTrue(
            reset.command_transfer_diagnostics()[-1]["receiver_reset_observed"]
        )

        drift = controller(
            protected_current=True, hide_sparse_ack_attempts=1,
            drift_sparse_authority=True,
        )
        with self.assertRaisesRegex(RuntimeError, "authority drift"):
            drift.send_overlay_patch_batch(
                controller_session_id=SESSION, generation=1,
                spans=[(0, bytes((1, 2, 3, 4)))],
            )
        self.assertEqual(drift.spi.sparse_attempts, 1)

        generation_drift = controller(
            protected_current=True, hide_sparse_ack_attempts=1,
            drift_sparse_generation=True,
        )
        with self.assertRaisesRegex(RuntimeError, "authority drift"):
            generation_drift.send_overlay_patch_batch(
                controller_session_id=SESSION, generation=1,
                spans=[(0, bytes((1, 2, 3, 4)))],
            )
        self.assertEqual(generation_drift.spi.sparse_attempts, 1)

    def test_transient_protected_reset_and_authority_drift_stay_latched(self):
        mutations = {
            "reset": lambda response: response.__setitem__(
                slice(12, 16), (1).to_bytes(4, "big")
            ),
            "session": lambda response: response.__setitem__(
                slice(384, 400), bytes((0xA5,)) * 16
            ),
            "generation": lambda response: response.__setitem__(
                slice(336, 344), (2).to_bytes(8, "big")
            ),
            "ownership": lambda response: response.__setitem__(73, 3),
        }
        for violation, mutate in mutations.items():
            with self.subTest(violation=violation):
                item = controller(protected_current=True)
                original_xfer = item.spi.xfer2
                injected = False

                def xfer(packet):
                    nonlocal injected
                    response = original_xfer(packet)
                    if (
                        not injected
                        and item.spi.sparse_attempts > 0
                        and bytes(packet)[4] == protocol.CMD_STATUS_QUERY
                        and len(response) >= protocol.RECEIVER_STATUS_BYTES_V8
                        and response[:5] == b"LGS8\x08"
                    ):
                        protected = bytearray(response)
                        mutate(protected)
                        protected[protocol.RECEIVER_STATUS_BYTES_V7:
                                  protocol.RECEIVER_STATUS_BYTES_V8] = (
                            binascii.crc32(
                                protected[:protocol.RECEIVER_STATUS_BYTES_V7]
                            ).to_bytes(4, "big")
                        )
                        response = bytes(protected)
                        injected = True
                    return response

                item.spi.xfer2 = xfer
                with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
                    item.send_overlay_patch_batch(
                        controller_session_id=SESSION, generation=1,
                        spans=[(0, bytes((1, 2, 3, 4)))],
                    )
                self.assertTrue(injected)
                self.assertEqual(item.spi.sparse_attempts, 1)
                diagnostic = item.command_transfer_diagnostics()[-1]
                self.assertIsNone(diagnostic["acknowledged_query_index"])
                self.assertTrue(any(
                    sample["operation_sequence"] == 1
                    and sample["last_processed_command"]
                        == protocol.CMD_OVERLAY_PATCH_BATCH
                    and sample["sparse_authority"]
                        == diagnostic["prior_status"]["sparse_authority"]
                    for sample in diagnostic["status_samples"]
                ))
                if violation == "reset":
                    self.assertTrue(diagnostic["receiver_reset_observed"])
                else:
                    self.assertTrue(
                        diagnostic["sparse_authority_drift_observed"]
                    )

    def test_permanent_sparse_failure_exhausts_one_retry(self):
        item = controller(protected_current=True, acknowledge=False)
        with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
            item.send_overlay_patch_batch(
                controller_session_id=SESSION, generation=1,
                spans=[(0, bytes((1, 2, 3, 4)))],
            )
        self.assertEqual(item.spi.sparse_attempts, 2)
        self.assertEqual(item.spi.effective_sparse_mutations, 0)
        diagnostics = item.command_transfer_diagnostics()[-2:]
        self.assertIsNone(diagnostics[0]["retry_of_diagnostic_id"])
        self.assertEqual(
            diagnostics[1]["retry_of_diagnostic_id"],
            diagnostics[0]["diagnostic_id"],
        )

    def test_negotiated_fec_batch_keeps_exact_ack_and_wire_diagnostic(self):
        item = controller(fec_capable=True)
        item._transport_envelope_enabled = True
        item._fec_transport_requested = True
        item._fec_transport_enabled = True
        item._receiver_capabilities |= (
            protocol.CAPABILITY_SPARSE_OVERLAY_V1
            | protocol.CAPABILITY_SPARSE_OVERLAY_BATCH_V1
            | protocol.CAPABILITY_FEC_ENVELOPE_V7
        )
        status = item.send_overlay_patch_batch(
            controller_session_id=SESSION,
            generation=1,
            spans=[(0, bytes((1, 2, 3, 4)) * 32)],
        )

        self.assertEqual(
            status["receiver_last_processed_command"],
            protocol.CMD_OVERLAY_PATCH_BATCH,
        )
        self.assertEqual(status["receiver_operation_sequence"], 1)
        fec_wires = [
            packet for packet in item.spi.packets
            if packet[:2] == b"\x0b\x07"
        ]
        self.assertEqual(len(fec_wires), 1)
        self.assertEqual(
            _decode_clean_v7(fec_wires[0])[0],
            protocol.CMD_OVERLAY_PATCH_BATCH,
        )
        diagnostic = item.command_transfer_diagnostics()[-1]
        self.assertEqual(diagnostic["command"], protocol.CMD_OVERLAY_PATCH_BATCH)
        self.assertEqual(diagnostic["wire_bytes"], len(fec_wires[0]))
        self.assertEqual(diagnostic["outcome"], "acknowledged")
        self.assertEqual(item._fec_sparse_packets_sent, 1)
        self.assertEqual(item._fec_frames_sent, 0)


    def test_planned_sparse_capacity_fails_before_io_if_negotiation_changes(self):
        item = controller(fec_capable=True)
        item._transport_envelope_enabled = True
        item._fec_transport_requested = True
        item._fec_transport_enabled = True
        before = len(item.spi.packets)
        with self.assertRaisesRegex(RuntimeError, "capacity changed"):
            item.send_overlay_patches(
                controller_session_id=SESSION,
                generation=1,
                patches=[(0, zeros(1))],
                update_kind=protocol.OVERLAY_UPDATE_DELTA,
                maximum_semantic_bytes=protocol.MAX_ALIGNED_SEMANTIC_BYTES,
            )
        self.assertEqual(len(item.spi.packets), before)

    def test_every_command_uses_queued_exact_ack_and_crc_envelope(self):
        item = controller()
        calls = (
            lambda: item.begin_controller_session(
                controller_session_id=SESSION,
                desired_revision=1,
                authoritative_snapshot_digest=DIGEST,
            ),
            lambda: item.begin_overlay(
                controller_session_id=SESSION,
                generation=1,
                prior_generation=0,
                scene_revision=2,
                scene_epoch=3,
                base_revision=2,
                update_kind=protocol.OVERLAY_UPDATE_DELTA,
                expected_patches=1,
                lease_ms=3000,
            ),
            lambda: item.send_overlay_patch(
                controller_session_id=SESSION,
                generation=1,
                start=7,
                premultiplied_rgba=bytes((1, 1, 1, 1)),
            ),
            lambda: item.commit_overlay(
                controller_session_id=SESSION,
                generation=1,
                scene_epoch=3,
                base_revision=2,
                present_at_scene_time_us=99,
            ),
            lambda: item.clear_overlay(
                controller_session_id=SESSION,
                generation=2,
                scene_revision=3,
            ),
            lambda: item.renew_overlay(
                controller_session_id=SESSION,
                generation=2,
                lease_ms=3000,
            ),
        )
        statuses = [call() for call in calls]
        commands = tuple(range(0x20, 0x21)) + tuple(range(0x30, 0x35))
        self.assertEqual(
            [status["receiver_last_processed_command"] for status in statuses],
            list(commands),
        )
        self.assertEqual(
            [status["receiver_operation_sequence"] for status in statuses],
            list(range(1, 7)),
        )
        command_packets = [
            packet for packet in item.spi.packets if _wire_command(packet) in commands
        ]
        self.assertEqual([_wire_command(packet) for packet in command_packets], list(commands))
        for packet in command_packets:
            self.assertEqual(
                packet[-2:],
                binascii.crc_hqx(packet[:-2], 0xFFFF).to_bytes(2, "big"),
            )


    def test_ack_wait_is_bounded_but_allows_delayed_receiver_processing(self):
        item = controller(
            acknowledge_after_status_queries=8,
            advance_packets_after_command=True,
        )
        item.spi.queued_status_bytes = [
            protocol.RECEIVER_STATUS_BYTES_V8,
            protocol.RECEIVER_STATUS_BYTES_V8,
        ]
        item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V8
        status = item.renew_overlay(
            controller_session_id=SESSION, generation=1, lease_ms=1
        )
        queries = [
            packet for packet in item.spi.packets
            if _wire_command(packet) == protocol.CMD_STATUS_QUERY
        ]
        self.assertEqual(status["receiver_last_processed_command"], 0x34)
        self.assertEqual(status["receiver_operation_sequence"], 1)
        self.assertGreater(len(queries), 5)
        self.assertLessEqual(
            len(queries),
            protocol.SPI_RESPONSE_QUEUE_DEPTH
            + protocol.COMMAND_ACK_MAX_STATUS_QUERIES,
        )
        diagnostic = item.command_transfer_diagnostics()[-1]
        self.assertEqual(diagnostic["outcome"], "delayed_processing")
        self.assertEqual(diagnostic["command"], protocol.CMD_OVERLAY_RENEW)
        self.assertEqual(diagnostic["payload_bytes"], protocol.OVERLAY_RENEW_BYTES)
        self.assertEqual(diagnostic["route"]["bus"], protocol.SPI_BUS)
        self.assertEqual(diagnostic["prior_status"]["operation_sequence"], 0)
        self.assertEqual(diagnostic["expected_operation_sequence"], 1)
        self.assertEqual(
            diagnostic["status_samples"][-1]["operation_sequence"], 1
        )
        self.assertLessEqual(
            len(diagnostic["status_samples"]),
            protocol.COMMAND_ACK_MAX_STATUS_QUERIES,
        )

    def test_command_crc_growth_is_distinct_from_missing_status_response(self):
        item = controller(acknowledge=False, crc_on_command=True)
        item._receiver_capabilities |= protocol.CAPABILITY_SPARSE_OVERLAY_BATCH_V1
        with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
            item.send_overlay_patch_batch(
                controller_session_id=SESSION,
                generation=1,
                spans=((0, zeros(1)),),
            )
        diagnostic = item.command_transfer_diagnostics()[-1]
        self.assertEqual(diagnostic["outcome"], "command_drop_or_corruption")
        self.assertEqual(diagnostic["command"], protocol.CMD_OVERLAY_PATCH_BATCH)
        prior_crc = diagnostic["prior_status"]["receiver_crc_errors"]
        self.assertEqual(
            diagnostic["status_samples"][-1]["receiver_crc_errors"],
            prior_crc + 1,
        )

    def test_corrupt_status_responses_never_become_acknowledgement(self):
        item = controller(corrupt_status_after_command=True)
        with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
            item.renew_overlay(
                controller_session_id=SESSION, generation=1, lease_ms=1
            )
        diagnostic = item.command_transfer_diagnostics()[-1]
        self.assertEqual(
            diagnostic["outcome"], "status_response_loss_or_corruption"
        )
        self.assertFalse(any(
            sample["fresh"] for sample in diagnostic["status_samples"]
        ))

    def test_status_query_exception_is_retained_before_rollback(self):
        item = controller()
        query = item.query_receiver_status
        injected = False

        def fail_first_post_command_query():
            nonlocal injected
            if item.spi.mutation_seen and not injected:
                injected = True
                raise OSError("status response failed")
            return query()

        item.query_receiver_status = fail_first_post_command_query
        with self.assertRaisesRegex(OSError, "status response failed"):
            item.renew_overlay(
                controller_session_id=SESSION, generation=1, lease_ms=1
            )
        diagnostic = item.command_transfer_diagnostics()[-1]
        self.assertEqual(diagnostic["outcome"], "status_response_error")
        self.assertEqual(diagnostic["status_query_exception_index"], 1)
        self.assertEqual(diagnostic["command"], protocol.CMD_OVERLAY_RENEW)

    def test_receiver_counter_rollback_is_classified_as_reset(self):
        item = controller(reset_on_command=True)
        item.spi.operation_sequence = 5
        item.spi.queued = [(0, 5), (0, 5)]
        with self.assertRaisesRegex(RuntimeError, "expected 6"):
            item.renew_overlay(
                controller_session_id=SESSION, generation=1, lease_ms=1
            )
        diagnostic = item.command_transfer_diagnostics()[-1]
        self.assertEqual(diagnostic["outcome"], "receiver_reset")
        self.assertGreaterEqual(
            diagnostic["prior_status"]["receiver_packets"], 100
        )
        self.assertTrue(any(
            sample["fresh"] and sample["receiver_packets"] == 0
            for sample in diagnostic["status_samples"]
        ))

    def test_command_diagnostic_history_is_bounded_and_detached(self):
        item = controller()
        for generation in range(protocol.COMMAND_TRANSFER_DIAGNOSTIC_HISTORY + 3):
            item.renew_overlay(
                controller_session_id=SESSION,
                generation=generation + 1,
                lease_ms=1,
            )
        diagnostics = item.command_transfer_diagnostics()
        self.assertEqual(
            len(diagnostics), protocol.COMMAND_TRANSFER_DIAGNOSTIC_HISTORY
        )
        self.assertEqual(diagnostics[0]["diagnostic_id"], 4)
        diagnostics[-1]["status_samples"][-1]["operation_sequence"] = -99
        self.assertNotEqual(
            item.command_transfer_diagnostics()[-1]["status_samples"][-1][
                "operation_sequence"
            ],
            -99,
        )
        latest_id = diagnostics[-1]["diagnostic_id"]
        self.assertEqual(
            item.command_transfer_diagnostics(since_id=latest_id), []
        )

    def test_ack_queries_are_paced_without_weakening_exact_ack(self):
        clock = FakeAckClock()
        spi = TimingAwareSparseSpi(clock)
        with (
            mock.patch.object(protocol.spidev, "SpiDev", return_value=spi),
            mock.patch.object(protocol.time, "sleep", side_effect=clock.sleep),
        ):
            item = protocol.LEDController(strips=8, leds_per_strip=138)
            spi.packets.clear()
            spi.last_command = 0
            spi.operation_sequence = 0
            spi.pending_ack = None
            spi.queued = [(0, 0), (0, 0)]
            spi.queued_status_bytes = [
                protocol.RECEIVER_STATUS_BYTES_V8,
                protocol.RECEIVER_STATUS_BYTES_V8,
            ]
            spi.next_query_at = None
            spi.query_times.clear()
            spi.premature_queries.clear()

            status = item.renew_overlay(
                controller_session_id=SESSION, generation=1, lease_ms=1
            )

        self.assertEqual(spi.premature_queries, [])
        self.assertGreaterEqual(len(spi.query_times), 5)
        self.assertTrue(all(
            later + 1e-12
            >= earlier + protocol.COMMAND_ACK_POLL_INTERVAL_SECONDS
            for earlier, later in zip(spi.query_times, spi.query_times[1:])
        ))
        self.assertEqual(status["receiver_last_processed_command"], 0x34)
        self.assertEqual(status["receiver_operation_sequence"], 1)

    def test_exact_retry_produces_identical_wire_packets(self):
        item = controller()
        arguments = dict(
            controller_session_id=SESSION,
            generation=9,
            start=12,
            premultiplied_rgba=bytes((1, 0, 1, 1)),
        )
        first = item.send_overlay_patch(**arguments)
        second = item.send_overlay_patch(**arguments)
        packets = [
            packet for packet in item.spi.packets
            if _wire_command(packet) == protocol.CMD_OVERLAY_PATCH
        ]
        self.assertEqual(packets, [packets[0], packets[0]])
        self.assertEqual(first["receiver_operation_sequence"], 1)
        self.assertEqual(second["receiver_operation_sequence"], 1)

    def test_exact_batch_retry_produces_identical_wire_and_one_result_each(self):
        item = controller()
        item._receiver_capabilities |= protocol.CAPABILITY_SPARSE_OVERLAY_BATCH_V1
        arguments = dict(
            controller_session_id=SESSION,
            generation=9,
            spans=((12, bytes((1, 0, 1, 1))), (20, bytes((2, 2, 2, 2)))),
        )
        first = item.send_overlay_patch_batch(**arguments)
        second = item.send_overlay_patch_batch(**arguments)
        packets = [
            packet for packet in item.spi.packets
            if _wire_command(packet) == protocol.CMD_OVERLAY_PATCH_BATCH
        ]
        self.assertEqual(packets, [packets[0], packets[0]])
        self.assertEqual(first["receiver_operation_sequence"], 1)
        self.assertEqual(second["receiver_operation_sequence"], 1)

    def test_direct_batch_send_requires_negotiated_capability_without_io(self):
        item = controller(sparse_capable=False)
        before = len(item.spi.packets)
        with self.assertRaisesRegex(RuntimeError, "has not advertised"):
            item.send_overlay_patch_batch(
                controller_session_id=SESSION,
                generation=1,
                spans=((0, zeros(1)),),
            )
        self.assertEqual(len(item.spi.packets), before)

    def test_lost_or_wrong_ack_is_not_accepted(self):
        item = controller(acknowledge=False)
        with self.assertRaisesRegex(
            RuntimeError,
            r"did not acknowledge.*command 0x00, sequence 0 .*CRC errors 0",
        ):
            item.renew_overlay(
                controller_session_id=SESSION, generation=1, lease_ms=1
            )

    def test_specific_overlay_rejection_is_not_hidden_by_valid_sequence_ack(self):
        item = controller(overlay_result=9)
        with self.assertRaisesRegex(RuntimeError, r"stale_generation \(9\)"):
            item.renew_overlay(
                controller_session_id=SESSION, generation=1, lease_ms=1
            )

    def test_batch_validates_before_io_and_missing_current_capability_fails_closed(self):
        item = controller()
        invalid_sets = (
            (protocol.OVERLAY_UPDATE_DELTA, [(8, zeros(2)), (7, zeros(1))]),
            (protocol.OVERLAY_UPDATE_DELTA, [(8, zeros(2)), (9, zeros(1))]),
            (protocol.OVERLAY_UPDATE_FULL_SNAPSHOT, [(1, zeros(1103))]),
            (protocol.OVERLAY_UPDATE_FULL_SNAPSHOT, [(0, zeros(10))]),
        )
        for kind, patches in invalid_sets:
            before = len(item.spi.packets)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                item.send_overlay_patches(
                    controller_session_id=SESSION,
                    generation=1,
                    patches=patches,
                    update_kind=kind,
                )
            self.assertEqual(len(item.spi.packets), before)

        statuses = item.send_overlay_patches(
            controller_session_id=SESSION,
            generation=1,
            patches=[(0, zeros(1014)), (1014, zeros(90))],
            update_kind=protocol.OVERLAY_UPDATE_FULL_SNAPSHOT,
        )
        self.assertEqual(len(statuses), 2)
        self.assertEqual(
            sum(_wire_command(packet) == protocol.CMD_OVERLAY_PATCH_BATCH
                for packet in item.spi.packets),
            2,
        )

        item._receiver_capabilities &= ~protocol.CAPABILITY_SPARSE_OVERLAY_BATCH_V1
        before = len(item.spi.packets)
        with self.assertRaisesRegex(RuntimeError, "batch capability required"):
            item.send_overlay_patches(
                controller_session_id=SESSION,
                generation=2,
                patches=[(0, zeros(1104))],
                update_kind=protocol.OVERLAY_UPDATE_FULL_SNAPSHOT,
            )
        self.assertEqual(len(item.spi.packets), before)




if __name__ == "__main__":
    unittest.main()
