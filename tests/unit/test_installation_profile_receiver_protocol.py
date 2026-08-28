from __future__ import annotations

import struct
import sys
import threading
import types
import unittest
from unittest import mock

if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from drivers import spi_controller as protocol
from tests.unit.test_firmware_host_phase3a_protocol import controller


GLOBAL = "11" * 32
PAYLOAD = "22" * 32
ACTIVE_GLOBAL = "33" * 32
ACTIVE_PAYLOAD = "44" * 32
ROLLBACK_GLOBAL = "55" * 32
ROLLBACK_PAYLOAD = "66" * 32


def status_v5(*, sequence=0, command=0, flags=0x09):
    response = bytearray(protocol.RECEIVER_STATUS_BYTES_V5)
    response[:5] = b"LGS5\x05"
    capabilities = (
        protocol.CAPABILITY_INSTALLATION_PROFILE_V1
        | protocol.CAPABILITY_STATUS_V5
        | protocol.CAPABILITY_SPARSE_OVERLAY_V1
    )
    response[64:68] = capabilities.to_bytes(4, "big")
    response[312] = 2
    response[313] = command
    response[316:320] = sequence.to_bytes(4, "big")
    response[416:420] = bytes((1, 4, 7, flags))
    response[420:448] = struct.pack(">IIIIIII", 100000, 30000, 70000, 10000,
                                           5000, 10264, 10264)
    response[448:464] = struct.pack(">QQ", 9, 1234)
    response[464:496] = bytes.fromhex(PAYLOAD)
    response[496:528] = bytes.fromhex(GLOBAL)
    response[528:560] = bytes.fromhex(PAYLOAD)
    response[560:592] = bytes.fromhex(ACTIVE_GLOBAL)
    response[592:624] = bytes.fromhex(ACTIVE_PAYLOAD)
    response[688:720] = bytes.fromhex(ROLLBACK_GLOBAL)
    response[720:752] = bytes.fromhex(ROLLBACK_PAYLOAD)
    response[752:768] = struct.pack(">IIHHHH", 3, 4, 5, 6, 7, 8)
    return response


class ProfileProtocolTests(unittest.TestCase):
    def test_exact_frozen_command_bytes(self):
        preflight = protocol.LEDController.serialize_profile_preflight(
            profile_id=GLOBAL, payload_digest=PAYLOAD, payload_size=10264
        )
        self.assertEqual(
            preflight,
            b"\x40" + bytes.fromhex(GLOBAL + PAYLOAD) + struct.pack(">I", 10264),
        )
        self.assertEqual(len(preflight), 69)

        begin = protocol.LEDController.serialize_profile_begin(
            preflight_token=0x0102030405060708,
            profile_id=GLOBAL,
            payload_digest=PAYLOAD,
            payload_size=10264,
            logical_receiver_id=2,
            strip_origin=24,
            reversed_strip_order=True,
        )
        self.assertEqual(
            begin,
            b"\x41"
            + bytes.fromhex("0102030405060708")
            + bytes.fromhex(GLOBAL + PAYLOAD)
            + struct.pack(">IBH", 10264, 2, 24)
            + b"\x01",
        )
        self.assertEqual(len(begin), 81)
        self.assertEqual(
            protocol.LEDController.serialize_profile_finalize(
                profile_id=GLOBAL, payload_digest=PAYLOAD
            ),
            b"\x43" + bytes.fromhex(GLOBAL + PAYLOAD),
        )
        self.assertEqual(
            protocol.LEDController.serialize_profile_verify(
                profile_id=GLOBAL, payload_digest=PAYLOAD
            ),
            b"\x44" + bytes.fromhex(GLOBAL + PAYLOAD),
        )
        self.assertEqual(
            protocol.LEDController.serialize_profile_activate(
                expected_generation=9,
                profile_id=GLOBAL,
                payload_digest=PAYLOAD,
            ),
            b"\x45" + struct.pack(">Q", 9) + bytes.fromhex(GLOBAL + PAYLOAD),
        )
        restore = protocol.LEDController.serialize_profile_restore(
            expected_generation=10,
            active_binding=(ACTIVE_GLOBAL, ACTIVE_PAYLOAD),
            staged_binding=None,
            rollback_binding=(ROLLBACK_GLOBAL, ROLLBACK_PAYLOAD),
        )
        self.assertEqual(
            restore,
            b"\x46"
            + struct.pack(">Q", 10)
            + b"\x01"
            + bytes.fromhex(ACTIVE_GLOBAL + ACTIVE_PAYLOAD)
            + b"\x00"
            + bytes(64)
            + b"\x01"
            + bytes.fromhex(ROLLBACK_GLOBAL + ROLLBACK_PAYLOAD),
        )
        self.assertEqual(len(restore), protocol.PROFILE_RESTORE_BYTES)
        self.assertEqual(
            protocol.LEDController.serialize_profile_chunk(
                offset=0x10203040, data=b"wire"
            ),
            b"\x42\x10\x20\x30\x40wire",
        )

    def test_maximum_chunk_exactly_fills_spi_transfer_with_crc(self):
        payload = protocol.LEDController.serialize_profile_chunk(
            offset=0x01020304, data=b"x" * protocol.MAX_PROFILE_CHUNK_BYTES
        )
        self.assertEqual(len(payload), protocol.MAX_ALIGNED_SEMANTIC_BYTES)
        self.assertEqual(payload[:5], b"\x42\x01\x02\x03\x04")
        item = controller()
        item._transport_envelope_enabled = True
        item._xfer(payload)
        self.assertEqual(len(item.spi.packets[-1]), protocol.MAX_SPI_TRANSFER)

    def test_serializers_reject_invalid_values_before_io(self):
        invalid = (
            lambda: protocol.LEDController.serialize_profile_preflight(
                profile_id="AA" * 32, payload_digest=PAYLOAD, payload_size=1
            ),
            lambda: protocol.LEDController.serialize_profile_preflight(
                profile_id=GLOBAL, payload_digest=PAYLOAD, payload_size=0
            ),
            lambda: protocol.LEDController.serialize_profile_begin(
                preflight_token=0, profile_id=GLOBAL, payload_digest=PAYLOAD,
                payload_size=1, logical_receiver_id=0, strip_origin=0,
                reversed_strip_order=False,
            ),
            lambda: protocol.LEDController.serialize_profile_chunk(offset=0, data=b""),
            lambda: protocol.LEDController.serialize_profile_chunk(
                offset=0, data=b"x" * (protocol.MAX_PROFILE_CHUNK_BYTES + 1)
            ),
            lambda: protocol.LEDController.serialize_profile_chunk(
                offset=0xFFFFFFFF, data=b"xx"
            ),
        )
        for call in invalid:
            with self.subTest(call=call), self.assertRaises((TypeError, ValueError)):
                call()

    def test_v5_parses_exact_extension_and_preserved_v4_prefix(self):
        item = controller()
        item._update_receiver_status(status_v5(flags=0x29))
        stats = item.get_stats()
        self.assertEqual(stats["receiver_status_version"], 5)
        self.assertEqual(item._receiver_status_query_bytes, 768)
        self.assertEqual(stats["receiver_profile_result_name"], "ok")
        self.assertEqual(stats["receiver_profile_transfer_state_name"], "staged")
        self.assertEqual(stats["receiver_profile_decoder_error"], 7)
        self.assertEqual(stats["receiver_profile_capacity_bytes"], 100000)
        self.assertEqual(stats["receiver_profile_state_generation"], 9)
        self.assertEqual(stats["receiver_profile_preflight_token"], 1234)
        self.assertEqual(stats["receiver_profile_active_global_digest"], ACTIVE_GLOBAL)
        self.assertEqual(stats["receiver_profile_active_payload_digest"], ACTIVE_PAYLOAD)
        self.assertEqual(stats["receiver_profile_rollback_global_digest"], ROLLBACK_GLOBAL)
        self.assertEqual(stats["receiver_profile_restores"], 8)

    def test_real_v4_and_v3_downgrades_clear_only_newer_extensions(self):
        item = controller()
        item._update_receiver_status(status_v5(flags=0x09))
        v4 = bytearray(protocol.RECEIVER_STATUS_BYTES_V4)
        v4[:5] = b"LGS4\x04"
        v4[64:68] = protocol.CAPABILITY_SPARSE_OVERLAY_V1.to_bytes(4, "big")
        v4[320] = 1
        item._update_receiver_status(v4)
        stats = item.get_stats()
        self.assertEqual(stats["receiver_status_version"], 4)
        self.assertEqual(stats["receiver_overlay_operation_result"], 1)
        self.assertIsNone(stats["receiver_profile_active_global_digest"])
        self.assertEqual(stats["receiver_profile_state_generation"], 0)

        v3 = bytearray(protocol.RECEIVER_STATUS_BYTES_V3)
        v3[:5] = b"LGS3\x03"
        item._update_receiver_status(v3)
        stats = item.get_stats()
        self.assertEqual(stats["receiver_overlay_operation_result"], 0)
        self.assertIsNone(stats["receiver_profile_active_global_digest"])

    def test_truncated_v5_never_partially_replaces_atomic_profile_status(self):
        item = controller()
        item._update_receiver_status(status_v5(flags=0x09))
        before = item.get_stats()
        truncated = status_v5(flags=0x00)[:767]
        truncated[448:456] = (99).to_bytes(8, "big")
        item._update_receiver_status(truncated)
        after = item.get_stats()
        self.assertEqual(after["receiver_status_version"], 5)
        self.assertEqual(after["receiver_profile_state_generation"], 9)
        self.assertEqual(
            after["receiver_profile_active_global_digest"],
            before["receiver_profile_active_global_digest"],
        )

    def test_profile_command_requires_exact_next_v5_queued_ack(self):
        item = protocol.LEDController.__new__(protocol.LEDController)
        item._transport_lock = threading.RLock()
        statuses = [
            {"receiver_status_version": 5, "receiver_operation_sequence": 7},
            {"receiver_status_version": 5, "receiver_operation_sequence": 7},
            {"receiver_status_version": 3, "receiver_operation_sequence": 7,
             "receiver_last_processed_command": 0},
            {"receiver_status_version": 3, "receiver_operation_sequence": 8,
             "receiver_last_processed_command": protocol.CMD_PROFILE_ABORT},
            {"receiver_status_version": 5, "receiver_operation_sequence": 8,
             "receiver_last_processed_command": protocol.CMD_PROFILE_ABORT,
             "receiver_profile_result": 1},
        ]
        item.query_receiver_status = mock.Mock(side_effect=statuses)
        item._xfer = mock.Mock()
        with mock.patch("drivers.spi_controller.time.sleep"):
            status = item.profile_abort()
        self.assertEqual(status["receiver_status_version"], 5)
        self.assertEqual(item.query_receiver_status.call_count, 5)
        item._xfer.assert_called_once_with(bytes((protocol.CMD_PROFILE_ABORT,)))


if __name__ == "__main__":
    unittest.main()
