"""Exact host wire coverage for managed receiver-native modules."""

from __future__ import annotations

import binascii
import hashlib
import struct
import sys
import types
import unittest
from unittest.mock import patch


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from animation.core.native_background_operation import encode_native_parameters
from drivers import spi_controller as protocol
from tests.unit.test_firmware_host_phase3a_protocol import controller


BUNDLE = "11" * 32
PAYLOAD = "22" * 32


def descriptor(**overrides):
    value = {
        "bundle_digest": BUNDLE,
        "payload_digest": PAYLOAD,
        "payload_size": 4097,
        "abi_version": 2,
        "target": 1,
        "global_strips": 33,
        "local_strips": 1,
        "leds_per_strip": 138,
        "global_strip_offset": 32,
        "cadence_hz": 30,
        "parameter_schema_revision": 0xA1B2C3D4,
        "flags": 0,
    }
    value.update(overrides)
    return value


def status_v6(*, command=0, sequence=0, result=1, flags=0xFF,
              probe_payload_digest=None):
    response = bytearray(protocol.RECEIVER_STATUS_BYTES_V6)
    response[:5] = b"LGS6\x06"
    capabilities = (
        protocol.CAPABILITY_STATUS_V3
        | protocol.CAPABILITY_EXPLICIT_BASE_OWNERSHIP
        | protocol.CAPABILITY_PRESENTATION_CONTEXT_V1
        | protocol.CAPABILITY_STATUS_V6
        | protocol.CAPABILITY_NATIVE_MODULE_V2
        | protocol.CAPABILITY_NATIVE_CACHE_V1
        | protocol.CAPABILITY_NATIVE_TYPED_PARAMETERS_V1
        | protocol.CAPABILITY_NATIVE_QUARANTINE_V1
        | protocol.CAPABILITY_NATIVE_GUARDED_LOADER_V1
    )
    response[64:68] = capabilities.to_bytes(4, "big")
    response[312] = 4
    response[313] = command
    response[316:320] = sequence.to_bytes(4, "big")
    response[768] = result
    response[769] = 2
    response[770] = 5
    response[771] = flags
    for offset, value in zip(range(772, 800, 4), range(101, 108)):
        response[offset:offset + 4] = value.to_bytes(4, "big")
    response[800:808] = (0x0102030405060708).to_bytes(8, "big")
    response[808:816] = (0x1112131415161718).to_bytes(8, "big")
    for index, offset in enumerate(range(816, 1136, 32), start=1):
        response[offset:offset + 32] = bytes((index,)) * 32
    if probe_payload_digest is not None:
        response[816:848] = bytes.fromhex(probe_payload_digest)
    response[1136:1140] = (0xA1B2C3D4).to_bytes(4, "big")
    response[1140:1152] = struct.pack(">HBBHHHH", 30, 1, 1, 33, 138, 32, 17)
    response[1152:1184] = b"\xAB" * 32
    response[1184:1196] = struct.pack(">HHHHHH", 1, 2, 3, 4, 5, 6)
    response[1196:1204] = struct.pack(">II", 7, 8)
    response[1204:1214] = struct.pack(">HHHHH", 9, 10, 11, 12, 13)
    return response


class _QueuedNativeSpi:
    def __init__(self, *, result=1):
        self.max_speed_hz = 20_000_000
        self.mode = 0
        self.command = 0
        self.sequence = 0
        self.result = result
        self.packets = []
        self.queued = [status_v6(), status_v6()]

    def xfer2(self, packet):
        wire = bytes(packet)
        self.packets.append(wire)
        response = self.queued.pop(0)[:len(wire)]
        if wire[0] != protocol.CMD_STATUS_QUERY:
            self.command = wire[0]
            self.sequence += 1
        snapshot = status_v6(
            command=self.command, sequence=self.sequence, result=self.result,
        )
        snapshot[12:16] = len(self.packets).to_bytes(4, "big")
        self.queued.append(snapshot)
        return response


class _DelayedRefillNativeSpi:
    """Two DMA slots; receiver dispatch/refill runs after the SPI clock ends."""

    def __init__(
        self, *, acknowledge=True, command_delay=0.003, ack_command=None,
        ack_increment=1, frozen_counter=False,
    ):
        self.now = 0.0
        self.command = protocol.CMD_SET_ALL
        self.sequence = 4412
        self.received = 0
        self.acknowledge = acknowledge
        self.command_delay = command_delay
        self.ack_command = ack_command
        self.ack_increment = ack_increment
        self.frozen_counter = frozen_counter
        self.attempts = []
        self.attempt_times = []
        self.dropped = []
        self.pending = []
        self.ready = [self.status(6), self.status(6)]

    def status(self, version):
        result = status_v6(command=self.command, sequence=self.sequence)
        packets = 0 if self.frozen_counter else self.received
        result[12:16] = packets.to_bytes(4, "big")
        result[314] = 1
        capabilities = int.from_bytes(result[64:68], "big")
        result[64:68] = (
            capabilities | protocol.CAPABILITY_ALIGNED_ENVELOPE_V1
        ).to_bytes(4, "big")
        if version == 3:
            result = result[:protocol.RECEIVER_STATUS_BYTES_V3]
            result[:5] = b"LGS3\x03"
        return result

    def sleep(self, seconds):
        self.now += seconds
        while self.pending and self.pending[0][0] <= self.now:
            _, command = self.pending.pop(0)
            self.received += 1
            status_query = command == protocol.CMD_STATUS_QUERY
            if not status_query and self.acknowledge:
                self.command = command if self.ack_command is None else self.ack_command
                self.sequence += self.ack_increment
            self.ready.append(self.status(6 if status_query else 3))

    def xfer2(self, packet):
        wire = bytes(packet)
        assert wire[:2] == bytes((protocol.CMD_ALIGNED_ENVELOPE, 1))
        assert binascii.crc_hqx(wire[:-2], 0xFFFF) == int.from_bytes(wire[-2:], "big")
        semantic_size = int.from_bytes(wire[2:4], "big")
        semantic = wire[4:4 + semantic_size]
        self.attempts.append(semantic[0])
        self.attempt_times.append(self.now)
        duration = len(wire) * 8 / 20_000_000
        if not self.ready:
            self.dropped.append(semantic[0])
            self.sleep(duration)
            return bytes(len(wire))
        response = self.ready.pop(0)[:len(wire)]
        # A completed transaction waits for the receiver task, including its
        # status snapshot, before that slot can receive another transaction.
        last_completion = self.pending[-1][0] if self.pending else self.now
        processing = 0.003 if semantic[0] == protocol.CMD_STATUS_QUERY else self.command_delay
        completion = max(last_completion, self.now + duration) + processing
        self.pending.append((completion, semantic[0]))
        self.sleep(duration)
        return response


class ReceiverNativeHostProtocolTests(unittest.TestCase):
    def test_descriptor_and_all_fixed_command_layouts_are_exact(self):
        preflight = protocol.LEDController.serialize_native_preflight(
            **descriptor()
        )
        begin = protocol.LEDController.serialize_native_begin(
            preflight_token=0x0102030405060708, **descriptor()
        )
        self.assertEqual(len(preflight), 86)
        self.assertEqual(len(begin), 94)
        self.assertEqual(preflight[0], 0x51)
        self.assertEqual(preflight[1:33], bytes.fromhex(BUNDLE))
        self.assertEqual(preflight[33:65], bytes.fromhex(PAYLOAD))
        self.assertEqual(
            preflight[65:],
            struct.pack(">IHBHBHHHIB", 4097, 2, 1, 33, 1, 138, 32, 30,
                        0xA1B2C3D4, 0),
        )
        self.assertEqual(begin[1:9], bytes.fromhex("0102030405060708"))
        binding = {"bundle_digest": BUNDLE, "payload_digest": PAYLOAD}
        self.assertEqual(
            len(protocol.LEDController.serialize_native_finalize(**binding)), 65
        )
        self.assertEqual(
            len(protocol.LEDController.serialize_native_verify(**binding)), 65
        )
        self.assertEqual(
            len(protocol.LEDController.serialize_native_remove(**binding)), 65
        )
        self.assertEqual(
            len(protocol.LEDController.serialize_native_restore(
                expected_generation=9,
                active_binding=(BUNDLE, PAYLOAD),
                staged_binding=None,
                rollback_binding=(BUNDLE, PAYLOAD),
            )),
            204,
        )
        with self.assertRaisesRegex(ValueError, "between 1 and 8"):
            protocol.LEDController.serialize_native_preflight(
                **descriptor(local_strips=9)
            )

    def test_max_chunk_fits_4096_bytes_only_with_crc(self):
        item = controller()
        payload = protocol.LEDController.serialize_native_chunk(
            offset=7, data=b"x" * protocol.MAX_NATIVE_CHUNK_BYTES
        )
        self.assertEqual(len(payload), protocol.MAX_ALIGNED_SEMANTIC_BYTES)
        item._transport_envelope_enabled = True
        item._xfer(payload)
        packet = item.spi.packets[-1]
        self.assertEqual(len(packet), 4096)
        self.assertEqual(
            packet[-2:],
            binascii.crc_hqx(packet[:-2], 0xFFFF).to_bytes(2, "big"),
        )
        with self.assertRaises(ValueError):
            protocol.LEDController.serialize_native_chunk(
                offset=0, data=b"x" * (protocol.MAX_NATIVE_CHUNK_BYTES + 1)
            )

    def test_typed_parameters_have_stable_ids_types_and_digest(self):
        schema = {
            "enabled": {"type": "bool", "default": True},
            "gain": {"type": "float", "default": 0.5, "min": 0.0, "max": 1.0},
            "mode": {"type": "str", "default": "soft", "options": ["soft", "hard"]},
            "rate": {"type": "int", "default": -2, "min": -10, "max": 10},
        }
        encoded = encode_native_parameters(
            schema, {"enabled": False, "gain": 0.25, "mode": "hard", "rate": -3}
        )
        expected = (
            b"\x01\x04"
            b"\x00\x00\x03\x00\x00"
            b"\x00\x01\x02\x00\x3e\x80\x00\x00"
            b"\x00\x02\x04\x00\x00\x01"
            b"\x00\x03\x01\x00\xff\xff\xff\xfd"
        )
        self.assertEqual(encoded.blob, expected)
        self.assertEqual(encoded.digest, hashlib.sha256(expected).hexdigest())
        self.assertNotEqual(encoded.schema_revision, 0)
        with self.assertRaisesRegex(ValueError, "zero-based positions"):
            protocol.LEDController.serialize_native_parameters(
                bundle_digest=BUNDLE,
                payload_digest=PAYLOAD,
                parameter_schema_revision=encoded.schema_revision,
                parameter_blob=b"\x01\x01\x00\x01\x03\x00\x01",
            )
        with self.assertRaisesRegex(ValueError, "trailing bytes"):
            protocol.LEDController.serialize_native_parameters(
                bundle_digest=BUNDLE,
                payload_digest=PAYLOAD,
                parameter_schema_revision=encoded.schema_revision,
                parameter_blob=encoded.blob + b"\x00",
            )

    def test_status_v6_parses_every_native_extension_and_clears_on_v5(self):
        item = controller()
        item._update_receiver_status(status_v6(command=0x56, sequence=91))
        stats = item.get_stats()
        self.assertEqual(stats["receiver_status_version"], 6)
        self.assertEqual(stats["receiver_native_result_name"], "ok")
        self.assertEqual(stats["receiver_native_transfer_state_name"], "receiving")
        self.assertEqual(stats["receiver_native_watchdog_phase_name"], "render")
        self.assertEqual(stats["receiver_native_capacity_bytes"], 101)
        self.assertEqual(
            stats["receiver_native_state_generation"], 0x0102030405060708
        )
        self.assertEqual(stats["receiver_native_active_global_strips"], 33)
        self.assertEqual(stats["receiver_native_active_local_strips"], 1)
        self.assertEqual(stats["receiver_native_active_global_strip_offset"], 32)
        self.assertEqual(stats["receiver_native_active_parameter_digest"], "ab" * 32)
        self.assertEqual(stats["receiver_native_quarantines"], 13)
        self.assertEqual(item._receiver_status_query_bytes, 1216)

        downgraded = bytearray(protocol.RECEIVER_STATUS_BYTES_V5)
        downgraded[:5] = b"LGS5\x05"
        item._update_receiver_status(downgraded)
        cleared = item.get_stats()
        self.assertEqual(cleared["receiver_native_flags"], 0)
        self.assertIsNone(cleared["receiver_native_active_bundle_digest"])

    def test_native_ack_accepts_only_the_exact_next_queued_operation(self):
        spi = _QueuedNativeSpi()
        item = controller(spi)
        item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V6
        status = item.native_stop()
        self.assertEqual(status["receiver_last_processed_command"], 0x57)
        self.assertEqual(status["receiver_operation_sequence"], 1)
        native_packets = [packet for packet in spi.packets if packet[0] == 0x57]
        self.assertEqual(len(native_packets), 1)
        self.assertEqual(native_packets[0][:-2], b"\x57")

    def test_native_commands_wait_for_a_free_dma_slot_before_single_send(self):
        for command in (protocol.CMD_NATIVE_PREFLIGHT, protocol.CMD_NATIVE_RESTORE):
            with self.subTest(command=command):
                spi = _DelayedRefillNativeSpi()
                item = controller(spi)
                item._transport_envelope_enabled = True
                item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V6
                with patch.object(protocol.time, "sleep", side_effect=spi.sleep), \
                patch.object(protocol.time, "monotonic", side_effect=lambda: spi.now):
                    if command == protocol.CMD_NATIVE_PREFLIGHT:
                        result = item.native_preflight(**descriptor())
                    else:
                        result = item.native_restore(
                            expected_generation=0, active_binding=None,
                            staged_binding=None, rollback_binding=None,
                        )
                self.assertEqual(result["receiver_last_processed_command"], command)
                self.assertEqual(result["receiver_operation_sequence"], 4413)
                self.assertEqual(spi.attempts.count(command), 1)
                self.assertNotIn(command, spi.dropped)

    def test_native_ack_waits_for_storage_processing_and_fresh_extended_status(self):
        for delay in (0.2, 2.0):
            for command in (protocol.CMD_NATIVE_PREFLIGHT, protocol.CMD_NATIVE_ABORT):
                with self.subTest(delay=delay, command=command):
                    spi = _DelayedRefillNativeSpi(command_delay=delay)
                    item = controller(spi)
                    item._transport_envelope_enabled = True
                    item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V6
                    with patch.object(protocol.time, "sleep", side_effect=spi.sleep), \
                            patch.object(protocol.time, "monotonic", side_effect=lambda: spi.now):
                        result = (item.native_preflight(**descriptor())
                                  if command == protocol.CMD_NATIVE_PREFLIGHT
                                  else item.native_abort())
                    self.assertEqual(result["receiver_last_processed_command"], command)
                    self.assertEqual(result["receiver_operation_sequence"], 4413)
                    self.assertGreaterEqual(result["receiver_status_version"], 6)
                    self.assertTrue(item._last_transfer_status_sampled)
                    self.assertGreaterEqual(spi.now, delay)
                    self.assertEqual(spi.attempts.count(command), 1)
                    self.assertNotIn(command, spi.dropped)
                    self.assertIn(protocol.CMD_STATUS_QUERY, spi.dropped)

    def test_profile_preflight_uses_the_same_bounded_storage_ack_wait(self):
        spi = _DelayedRefillNativeSpi(command_delay=0.2)
        item = controller(spi)
        item._transport_envelope_enabled = True
        item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V6
        with patch.object(protocol.time, "sleep", side_effect=spi.sleep), \
                patch.object(protocol.time, "monotonic", side_effect=lambda: spi.now):
            result = item.profile_preflight(
                profile_id=BUNDLE, payload_digest=PAYLOAD, payload_size=4097,
            )
        self.assertEqual(result["receiver_last_processed_command"], protocol.CMD_PROFILE_PREFLIGHT)
        self.assertEqual(result["receiver_operation_sequence"], 4413)
        self.assertGreaterEqual(result["receiver_status_version"], 5)
        self.assertTrue(item._last_transfer_status_sampled)
        self.assertGreaterEqual(spi.now, 0.2)
        self.assertEqual(spi.attempts.count(protocol.CMD_PROFILE_PREFLIGHT), 1)

    def test_deferred_command_time_anchor_is_captured_after_refill_wait(self):
        spi = _DelayedRefillNativeSpi()
        item = controller(spi)
        item._transport_envelope_enabled = True
        item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V6
        serialized_at = []

        def serialize():
            serialized_at.append(spi.now)
            return item.serialize_native_preflight(**descriptor())

        with patch.object(protocol.time, "sleep", side_effect=spi.sleep), \
                patch.object(protocol.time, "monotonic", side_effect=lambda: spi.now):
            item._command_status(
                serialize, command=protocol.CMD_NATIVE_PREFLIGHT,
                required_status_version=6, storage_operation=True,
            )
        command_index = spi.attempts.index(protocol.CMD_NATIVE_PREFLIGHT)
        self.assertEqual(serialized_at, [spi.attempt_times[command_index]])
        self.assertEqual(spi.attempts.count(protocol.CMD_NATIVE_PREFLIGHT), 1)

    def test_delayed_refill_missing_native_ack_is_bounded_and_never_retried(self):
        spi = _DelayedRefillNativeSpi(acknowledge=False)
        item = controller(spi)
        item._transport_envelope_enabled = True
        item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V6
        with patch.object(protocol.time, "sleep", side_effect=spi.sleep), \
                patch.object(protocol.time, "monotonic", side_effect=lambda: spi.now):
            with self.assertRaisesRegex(RuntimeError, "command 0x06, sequence 4412"):
                item.native_preflight(**descriptor())
        self.assertEqual(spi.attempts.count(protocol.CMD_NATIVE_PREFLIGHT), 1)
        self.assertGreater(spi.attempts.count(protocol.CMD_STATUS_QUERY), 16)
        self.assertGreaterEqual(spi.now, protocol.STORAGE_COMMAND_ACK_TIMEOUT_SECONDS)
        self.assertLess(spi.now, protocol.STORAGE_COMMAND_ACK_TIMEOUT_SECONDS + 0.05)

    def test_native_ack_rejects_zero_miso_and_repeated_packet_counters(self):
        for kwargs in ({"command_delay": 10}, {"frozen_counter": True}):
            with self.subTest(kwargs=kwargs):
                spi = _DelayedRefillNativeSpi(**kwargs)
                item = controller(spi)
                item._transport_envelope_enabled = True
                item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V6
                with patch.object(protocol.time, "sleep", side_effect=spi.sleep), \
                        patch.object(protocol.time, "monotonic", side_effect=lambda: spi.now):
                    with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
                        item.native_preflight(**descriptor())
                self.assertEqual(spi.attempts.count(protocol.CMD_NATIVE_PREFLIGHT), 1)
                self.assertFalse(item._last_transfer_status_sampled)
                self.assertGreaterEqual(spi.now, protocol.STORAGE_COMMAND_ACK_TIMEOUT_SECONDS)
                self.assertLess(spi.now, protocol.STORAGE_COMMAND_ACK_TIMEOUT_SECONDS + 0.05)

    def test_native_ack_stops_on_a_fresh_contradictory_sequence(self):
        for kwargs in ({"ack_command": protocol.CMD_NATIVE_ABORT}, {"ack_increment": 2}):
            with self.subTest(kwargs=kwargs):
                spi = _DelayedRefillNativeSpi(**kwargs)
                item = controller(spi)
                item._transport_envelope_enabled = True
                item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V6
                with patch.object(protocol.time, "sleep", side_effect=spi.sleep), \
                        patch.object(protocol.time, "monotonic", side_effect=lambda: spi.now):
                    with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
                        item.native_preflight(**descriptor())
                self.assertEqual(spi.attempts.count(protocol.CMD_NATIVE_PREFLIGHT), 1)
                self.assertTrue(item._last_transfer_status_sampled)
                self.assertLess(spi.now, 0.05)

    def test_ordinary_ack_retains_fast_polling_without_native_refill_pause(self):
        item = controller()
        with patch.object(protocol.time, "sleep") as sleep:
            item.stop_local_background()
        self.assertTrue(sleep.call_args_list)
        self.assertTrue(all(
            call.args == (protocol.COMMAND_ACK_POLL_INTERVAL_SECONDS,)
            for call in sleep.call_args_list
        ))

    def test_probe_miss_is_ok_and_echoes_the_requested_payload_identity(self):
        item = controller()
        item._update_receiver_status(status_v6(
            command=protocol.CMD_NATIVE_PROBE,
            sequence=7,
            result=1,
            flags=0xFD,
            probe_payload_digest=PAYLOAD,
        ))
        stats = item.get_stats()
        self.assertEqual(stats["receiver_native_result_name"], "ok")
        self.assertFalse(stats["receiver_native_probe_found"])
        self.assertEqual(
            stats["receiver_native_last_probe_payload_digest"], PAYLOAD
        )

    def test_native_ack_uses_the_latched_result_for_the_exact_sequence(self):
        item = controller(_QueuedNativeSpi(result=23))
        item._receiver_status_query_bytes = protocol.RECEIVER_STATUS_BYTES_V6
        with self.assertRaisesRegex(RuntimeError, "render_failed"):
            item.native_stop()

    def test_production_v3_extended_config_carries_exact_topology_without_native(self):
        item = controller()
        item.strip_count = 1
        item.logical_device_id = 4
        item.global_strip_offset = 32
        item.reverse_native_strip_order = False
        item._receiver_status_version = 3
        item._receiver_capabilities = protocol.CAPABILITY_STATUS_V3
        item._last_sent_config = None
        item._refresh_configuration(force=True)
        config_packet = next(
            packet for packet in reversed(item.spi.packets)
            if packet[0] == protocol.CMD_CONFIG
        )
        self.assertEqual(config_packet[:-2], b"\x07\x01\x00\x8a\x00\x04\x00\x20")


if __name__ == "__main__":
    unittest.main()
