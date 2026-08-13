"""Host-side acceptance for the frozen sparse-overlay SPI contract."""

from __future__ import annotations

import binascii
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

    def __init__(self, *, sparse_capable=True, acknowledge=True, overlay_result=1):
        self.max_speed_hz = 20_000_000
        self.mode = 0
        self.bits_per_word = 8
        self.sparse_capable = sparse_capable
        self.acknowledge = acknowledge
        self.overlay_result = overlay_result
        self.packets = []
        self.last_command = 0
        self.operation_sequence = 0
        self.queued = [(0, 0), (0, 0)]
        self.queued_status_bytes = [
            protocol.RECEIVER_STATUS_BYTES_V3,
            protocol.RECEIVER_STATUS_BYTES_V3,
        ]

    def open(self, _bus, _device):
        pass

    def close(self):
        pass

    def _status(self, length, state):
        if length < protocol.RECEIVER_STATUS_BYTES_V3:
            return bytes(length)
        status_bytes = (
            protocol.RECEIVER_STATUS_BYTES_V4
            if self.sparse_capable and length >= protocol.RECEIVER_STATUS_BYTES_V4
            else protocol.RECEIVER_STATUS_BYTES_V3
        )
        response = bytearray(status_bytes)
        response[:5] = b"LGS4\x04" if status_bytes == 416 else b"LGS3\x03"
        capabilities = (
            protocol.CAPABILITY_STATUS_V3
            | protocol.CAPABILITY_EXPLICIT_BASE_OWNERSHIP
        )
        if self.sparse_capable:
            capabilities |= protocol.CAPABILITY_SPARSE_OVERLAY_V1
        response[64:68] = capabilities.to_bytes(4, "big")
        response[313] = state[0]
        response[316:320] = state[1].to_bytes(4, "big")
        if status_bytes == protocol.RECEIVER_STATUS_BYTES_V4 and state[1]:
            response[320] = self.overlay_result
        return bytes(response[:length])

    def xfer2(self, packet):
        wire = bytes(packet)
        self.packets.append(wire)
        prior = self.queued.pop(0)
        prior_status_bytes = self.queued_status_bytes.pop(0)
        response = self._status(prior_status_bytes, prior)
        if len(response) < len(wire):
            response += bytes(len(wire) - len(response))
        else:
            response = response[:len(wire)]
        if wire[0] != protocol.CMD_STATUS_QUERY and self.acknowledge:
            self.last_command = wire[0]
            self.operation_sequence += 1
        self.queued.append((self.last_command, self.operation_sequence))
        requested_v4 = (
            wire[0] == protocol.CMD_STATUS_QUERY
            and self.sparse_capable
            and len(wire) >= protocol.RECEIVER_STATUS_BYTES_V4
        )
        self.queued_status_bytes.append(
            protocol.RECEIVER_STATUS_BYTES_V4
            if requested_v4 else protocol.RECEIVER_STATUS_BYTES_V3
        )
        return response


def controller(*, sparse_capable=True, acknowledge=True, overlay_result=1):
    spi = FakeSparseSpi(
        sparse_capable=sparse_capable,
        acknowledge=acknowledge,
        overlay_result=overlay_result,
    )
    with mock.patch.object(protocol.spidev, "SpiDev", return_value=spi):
        item = protocol.LEDController(strips=8, leds_per_strip=138)
    spi.packets.clear()
    spi.last_command = 0
    spi.operation_sequence = 0
    spi.queued = [(0, 0), (0, 0)]
    spi.queued_status_bytes = [
        protocol.RECEIVER_STATUS_BYTES_V3,
        protocol.RECEIVER_STATUS_BYTES_V3,
    ]
    return item


def zeros(count):
    return np.zeros((count, 4), dtype=np.uint8)


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

    def test_maximum_patch_exactly_fills_transfer_after_crc(self):
        item = controller()
        item.send_overlay_patch(
            controller_session_id=SESSION,
            generation=1,
            start=0,
            premultiplied_rgba=zeros(protocol.MAX_RGBA_PIXELS_PER_PATCH),
        )
        packet = next(
            packet for packet in item.spi.packets
            if packet[0] == protocol.CMD_OVERLAY_PATCH
        )
        self.assertEqual(len(packet), protocol.MAX_SPI_TRANSFER)
        self.assertEqual(packet[26:30], b"\x00\x00\x03\xf8")
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
            packet for packet in item.spi.packets if packet[0] in commands
        ]
        self.assertEqual([packet[0] for packet in command_packets], list(commands))
        for packet in command_packets:
            self.assertEqual(
                packet[-2:],
                binascii.crc_hqx(packet[:-2], 0xFFFF).to_bytes(2, "big"),
            )

    def test_real_two_deep_queue_clocks_fresh_v4_after_command_queued_v3(self):
        item = controller()
        item.renew_overlay(
            controller_session_id=SESSION, generation=1, lease_ms=1
        )
        queries = [
            packet for packet in item.spi.packets
            if packet[0] == protocol.CMD_STATUS_QUERY
        ]
        self.assertEqual(len(queries), 5)
        self.assertEqual([len(packet) for packet in queries], [322] + [418] * 4)
        self.assertEqual(item.get_stats()["receiver_status_version"], 4)

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
            if packet[0] == protocol.CMD_OVERLAY_PATCH
        ]
        self.assertEqual(packets, [packets[0], packets[0]])
        self.assertEqual(first["receiver_operation_sequence"], 1)
        self.assertEqual(second["receiver_operation_sequence"], 2)

    def test_lost_or_wrong_ack_is_not_accepted(self):
        item = controller(acknowledge=False)
        with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
            item.renew_overlay(
                controller_session_id=SESSION, generation=1, lease_ms=1
            )

    def test_specific_overlay_rejection_is_not_hidden_by_valid_sequence_ack(self):
        item = controller(overlay_result=9)
        with self.assertRaisesRegex(RuntimeError, r"stale_generation \(9\)"):
            item.renew_overlay(
                controller_session_id=SESSION, generation=1, lease_ms=1
            )

    def test_batch_validates_order_overlap_and_full_coverage_before_sending(self):
        item = controller()
        invalid_sets = (
            (protocol.OVERLAY_UPDATE_DELTA, [(8, zeros(2)), (7, zeros(1))]),
            (protocol.OVERLAY_UPDATE_DELTA, [(8, zeros(2)), (9, zeros(1))]),
            (protocol.OVERLAY_UPDATE_FULL_SNAPSHOT, [(1, zeros(1103))]),
            (protocol.OVERLAY_UPDATE_FULL_SNAPSHOT, [(0, zeros(10))]),
        )
        for kind, patches in invalid_sets:
            before = len(item.spi.packets)
            with self.subTest(kind=kind, patches=len(patches)), self.assertRaises(
                ValueError
            ):
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
            patches=[(0, zeros(1016)), (1016, zeros(88))],
            update_kind=protocol.OVERLAY_UPDATE_FULL_SNAPSHOT,
        )
        self.assertEqual(len(statuses), 2)
        self.assertEqual(
            item.send_overlay_patches(
                controller_session_id=SESSION,
                generation=2,
                patches=[],
                update_kind=protocol.OVERLAY_UPDATE_DELTA,
            ),
            [],
        )


class SparseOverlayStatusTests(unittest.TestCase):
    def test_v3_query_discovers_capability_then_v4_query_clocks_extension(self):
        item = controller()
        for _ in range(4):
            item.query_receiver_status()
        queries = [
            packet for packet in item.spi.packets
            if packet[0] == protocol.CMD_STATUS_QUERY
        ]
        self.assertEqual([len(packet) for packet in queries], [322, 418, 418, 418])
        self.assertEqual(item.get_stats()["receiver_status_version"], 4)

    def test_feature_off_v3_receiver_never_receives_a_v4_sized_query(self):
        item = controller(sparse_capable=False)
        item.query_receiver_status()
        item.query_receiver_status()
        queries = [
            packet for packet in item.spi.packets
            if packet[0] == protocol.CMD_STATUS_QUERY
        ]
        self.assertEqual([len(packet) for packet in queries], [322, 322])
        stats = item.get_stats()
        self.assertEqual(stats["receiver_status_version"], 3)
        self.assertEqual(stats["receiver_overlay_committed_generation"], 0)

    def test_live_v4_to_v3_downgrade_clears_the_overlay_extension(self):
        item = controller()
        response = bytearray(protocol.RECEIVER_STATUS_BYTES_V4)
        response[:5] = b"LGS4\x04"
        response[64:68] = (
            protocol.CAPABILITY_STATUS_V3
            | protocol.CAPABILITY_SPARSE_OVERLAY_V1
        ).to_bytes(4, "big")
        response[320] = 2
        response[328:336] = (7).to_bytes(8, "big")
        response[384:400] = SESSION
        item._update_receiver_status(response)

        downgraded = bytearray(protocol.RECEIVER_STATUS_BYTES_V3)
        downgraded[:5] = b"LGS3\x03"
        downgraded[64:68] = protocol.CAPABILITY_STATUS_V3.to_bytes(4, "big")
        item._update_receiver_status(downgraded)

        stats = item.get_stats()
        self.assertEqual(stats["receiver_status_version"], 3)
        self.assertEqual(stats["receiver_overlay_operation_result"], 0)
        self.assertEqual(stats["receiver_overlay_committed_generation"], 0)
        self.assertIsNone(stats["receiver_overlay_session_id"])

    def test_all_zero_overlay_session_remains_a_valid_opaque_identity(self):
        item = controller()
        response = bytearray(protocol.RECEIVER_STATUS_BYTES_V4)
        response[:5] = b"LGS4\x04"
        response[64:68] = (
            protocol.CAPABILITY_STATUS_V3
            | protocol.CAPABILITY_SPARSE_OVERLAY_V1
        ).to_bytes(4, "big")
        item._update_receiver_status(response)
        self.assertEqual(
            item.get_stats()["receiver_overlay_session_id"], "00" * 16
        )

    def test_v4_extension_offsets_parse_without_losing_v3_prefix(self):
        item = controller()
        response = bytearray(protocol.RECEIVER_STATUS_BYTES_V4)
        response[:5] = b"LGS4\x04"
        response[12:16] = (123).to_bytes(4, "big")
        response[64:68] = (
            protocol.CAPABILITY_STATUS_V3
            | protocol.CAPABILITY_SPARSE_OVERLAY_V1
        ).to_bytes(4, "big")
        response[320:328] = struct.pack(">BBHHH", 2, 1, 3, 2, 17)
        response[328:376] = struct.pack(">QQQQQQ", 10, 11, 12, 13, 14, 15)
        response[376:384] = struct.pack(">II", 3000, 987)
        response[384:400] = SESSION
        response[400:416] = struct.pack(">IHHII", 20, 21, 22, 23, 24)
        item._update_receiver_status(response)
        stats = item.get_stats()
        self.assertEqual(stats["receiver_status_version"], 4)
        self.assertEqual(stats["receiver_packets"], 123)
        self.assertEqual(stats["receiver_overlay_operation_result"], 2)
        self.assertEqual(stats["receiver_overlay_update_kind"], 1)
        self.assertEqual(stats["receiver_overlay_expected_patches"], 3)
        self.assertEqual(stats["receiver_overlay_accepted_patches"], 2)
        self.assertEqual(stats["receiver_overlay_committed_coverage_pixels"], 17)
        self.assertEqual(stats["receiver_overlay_committed_generation"], 10)
        self.assertEqual(stats["receiver_overlay_staged_generation"], 11)
        self.assertEqual(stats["receiver_foreground_scene_revision"], 12)
        self.assertEqual(stats["receiver_foreground_scene_epoch"], 13)
        self.assertEqual(stats["receiver_foreground_base_revision"], 14)
        self.assertEqual(stats["receiver_foreground_present_at_scene_time_us"], 15)
        self.assertEqual(stats["receiver_overlay_lease_ms"], 3000)
        self.assertEqual(stats["receiver_overlay_lease_remaining_ms"], 987)
        self.assertEqual(stats["receiver_overlay_session_id"], SESSION.hex())
        self.assertEqual(stats["receiver_overlay_composite_frames"], 20)
        self.assertEqual(stats["receiver_overlay_last_composite_us"], 21)
        self.assertEqual(stats["receiver_overlay_max_composite_us"], 22)
        self.assertEqual(stats["receiver_overlay_commits"], 23)
        self.assertEqual(stats["receiver_overlay_expirations"], 24)

    def test_truncated_v4_does_not_partially_replace_atomic_status(self):
        item = controller()
        complete = bytearray(protocol.RECEIVER_STATUS_BYTES_V4)
        complete[:5] = b"LGS4\x04"
        complete[320] = 2
        item._update_receiver_status(complete)
        truncated = bytearray(protocol.RECEIVER_STATUS_BYTES_V3)
        truncated[:5] = b"LGS4\x04"
        truncated[320 - 1] = 99
        item._update_receiver_status(truncated)
        self.assertEqual(item.get_stats()["receiver_overlay_operation_result"], 2)


if __name__ == "__main__":
    unittest.main()
