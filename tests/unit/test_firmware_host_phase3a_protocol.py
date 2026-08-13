import binascii
from dataclasses import replace
import struct
import sys
import threading
import time
import types
import unittest


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from animation.core.plant_awareness import PlantModifierState
from animation.core.presentation_contracts import resolve_vibe
from animation.core.receiver_presentation import ReceiverPresentationContext
from drivers import spi_controller as protocol
from drivers.multi_device import MultiDeviceLEDController


class FakeSpi:
    def __init__(self, responses=None, *, acknowledge=True):
        self.packets = []
        self.responses = list(responses or [])
        self.max_speed_hz = 20_000_000
        self.mode = 0
        self.acknowledge = acknowledge
        self.last_command = 0
        self.operation_sequence = 0
        self.queued = [self.status(), self.status()]

    def status(self):
        status = bytearray(protocol.RECEIVER_STATUS_BYTES_V3)
        status[:5] = b"LGS3\x03"
        status[64:68] = (0xF).to_bytes(4, "big")
        status[72] = 1 if self.operation_sequence else 0
        status[313] = self.last_command
        status[316:320] = self.operation_sequence.to_bytes(4, "big")
        return status

    def xfer2(self, packet):
        wire = bytes(packet)
        self.packets.append(wire)
        if self.responses:
            return self.responses.pop(0)[:len(wire)]
        response = self.queued.pop(0)[:len(wire)]
        if wire[0] != protocol.CMD_STATUS_QUERY and self.acknowledge:
            self.last_command = wire[0]
            self.operation_sequence += 1
        self.queued.append(self.status())
        return response


def controller(spi=None):
    item = protocol.LEDController.__new__(protocol.LEDController)
    item.spi = spi or FakeSpi()
    item.total_leds = 8 * 138
    item.strip_count = 8
    item.leds_per_strip = 138
    item._bytes_sent = item._crc_bytes_sent = item._spi_transfers = item._errors = 0
    item._receiver_status_seen = False
    item._receiver_status_version = 0
    item._receiver_status_responses = item._receiver_status_misses = 0
    for name in (
        "_receiver_packets", "_receiver_crc_errors", "_receiver_crc_ok_packets",
        "_receiver_frames_rendered", "_receiver_last_crc_us", "_receiver_last_copy_us",
        "_receiver_last_show_us", "_receiver_active_strips", "_receiver_leds_per_strip",
        "_receiver_queued_transactions", "_receiver_frames_accepted",
        "_receiver_frames_displayed", "_receiver_frames_superseded",
        "_receiver_publish_drops", "_receiver_spi_queue_errors", "_receiver_display_errors",
        "_receiver_last_encode_us", "_receiver_last_accepted_sequence",
        "_receiver_last_displayed_sequence", "_receiver_capabilities",
        "_receiver_base_mode", "_receiver_foreground_state",
        "_receiver_maintenance_state", "_receiver_last_result",
        "_receiver_transition_reason", "_receiver_declared_cadence_hz",
        "_receiver_context_state", "_receiver_component_id",
        "_receiver_luminance_q8_8",
        "_receiver_global_strip_offset", "_receiver_common_seed",
        "_receiver_scene_epoch", "_receiver_active_scene_revision",
        "_receiver_local_frames_rendered", "_receiver_local_cadence_deadlines",
        "_receiver_local_missed_deadlines",
        "_receiver_last_local_render_us", "_receiver_max_local_render_us",
        "_receiver_last_frame_scene_time_us", "_receiver_staged_scene_revision",
        "_receiver_vibe_revision", "_receiver_plant_modifier_revision",
        "_frames_sent",
    ):
        setattr(item, name, 0)
    for name in (
        "_receiver_active_context_digest", "_receiver_staged_context_digest",
        "_receiver_vibe_digest", "_receiver_plant_modifier_digest",
        "_receiver_active_session_id", "_receiver_staged_session_id",
        "_receiver_logical_device",
        "_receiver_last_processed_command", "_receiver_operation_sequence",
    ):
        setattr(item, name, None)
    item._last_frame_duration = item._total_frame_duration = 0.0
    item._last_config_refresh = 0.0
    item._config_refresh_interval = 30.0
    item._last_sent_config = (8, 138)
    item.debug = False
    item._transport_lock = threading.RLock()
    item._frame_packet = bytearray(1 + item.total_leds * 3 + protocol.CRC_BYTES)
    return item


def status_v1():
    response = bytearray(protocol.RECEIVER_STATUS_BYTES)
    response[:4] = b"LGS1"
    response[4:8] = (101).to_bytes(4, "big")
    response[8:12] = (2).to_bytes(4, "big")
    response[12:16] = (99).to_bytes(4, "big")
    response[16:20] = (77).to_bytes(4, "big")
    response[20:26] = struct.pack(">HHH", 3, 4, 5)
    response[26] = 8
    response[27:29] = (138).to_bytes(2, "big")
    return response


def status_v2():
    response = bytearray(protocol.RECEIVER_STATUS_BYTES_V2)
    response[:5] = b"LGS2\x02"
    response[6] = 8
    response[8:12] = struct.pack(">HH", 138, 2)
    response[12:44] = struct.pack(">IIIIIIII", 101, 2, 99, 70, 69, 3, 4, 5)
    response[44:52] = struct.pack(">HHHH", 6, 7, 8, 9)
    response[52:64] = struct.pack(">III", 10, 11, 12)
    return response


def context():
    return ReceiverPresentationContext(
        controller_session_id=bytes(range(16)),
        scene_revision=0x0102030405060708,
        scene_epoch=0x1112131415161718,
        present_at_scene_time_us=123456,
        vibe=resolve_vibe("cozy", revision=9),
        plant_modifiers=PlantModifierState.from_payload({
            "active": ["illuminate", "obstacle"],
            "strengths": {"illuminate": 0.5, "obstacle": 1.0},
        }),
        plant_revision=11,
    )


class FirmwareHostPhase3AProtocolTests(unittest.TestCase):
    def assert_packet_crc(self, packet):
        self.assertEqual(
            packet[-2:],
            binascii.crc_hqx(packet[:-2], 0xFFFF).to_bytes(2, "big"),
        )

    def test_local_start_stop_and_parameter_wire_bytes_are_exact(self):
        start = protocol.LEDController.serialize_local_background_start(
            component_id=1,
            preferred_cadence_hz=30,
            global_strip_offset=16,
            common_seed=0xA1A2A3A4,
            scene_epoch=0x0102030405060708,
        )
        params = protocol.LEDController.serialize_local_background_params(
            preferred_cadence_hz=200,
            global_strip_offset=24,
            common_seed=0xF1F2F3F4,
        )
        self.assertEqual(
            start,
            b"\x10\x00\x01\x00\x1e\x00\x00\x00\x10"
            b"\xa1\xa2\xa3\xa4\x01\x02\x03\x04\x05\x06\x07\x08",
        )
        self.assertEqual(
            params,
            b"\x12\x00\xc8\x00\x00\x00\x18\xf1\xf2\xf3\xf4",
        )
        item = controller()
        item.stop_local_background()
        stop_packet = next(packet for packet in item.spi.packets if packet[0] == 0x11)
        self.assertEqual(stop_packet[:-2], b"\x11")
        self.assert_packet_crc(stop_packet)

    def test_local_serializers_reject_bounds_and_types_before_spi(self):
        valid = dict(
            component_id=1, preferred_cadence_hz=30,
            global_strip_offset=0, common_seed=0, scene_epoch=0,
        )
        mutations = (
            ("component_id", 2), ("preferred_cadence_hz", 0),
            ("preferred_cadence_hz", 201), ("global_strip_offset", -1),
            ("global_strip_offset", 2**32), ("common_seed", True),
            ("common_seed", 2**32), ("scene_epoch", -1),
            ("scene_epoch", 2**64),
        )
        for key, value in mutations:
            args = dict(valid)
            args[key] = value
            with self.subTest(key=key, value=value), self.assertRaises((TypeError, ValueError)):
                protocol.LEDController.serialize_local_background_start(**args)

    def test_status_query_is_exact_320_byte_ownership_neutral_transfer(self):
        item = controller()
        item.query_receiver_status()
        packet = item.spi.packets[-1]
        self.assertEqual(len(packet), protocol.RECEIVER_STATUS_BYTES_V3 + 2)
        self.assertEqual(packet[:-2], b"\x08" + bytes(319))
        self.assert_packet_crc(packet)

    def test_v3_status_parses_preserved_counters_and_full_context_binding(self):
        item = controller()
        response = bytearray(protocol.RECEIVER_STATUS_BYTES_V3)
        response[:5] = b"LGS3\x03"
        response[6] = 8
        response[8:12] = struct.pack(">HH", 138, 2)
        response[12:44] = struct.pack(">IIIIIIII", 101, 2, 99, 70, 69, 3, 4, 5)
        response[44:52] = struct.pack(">HHHH", 6, 7, 8, 9)
        response[52:64] = struct.pack(">III", 10, 11, 12)
        response[64:68] = (0xF).to_bytes(4, "big")
        response[68:74] = bytes((1, 0, 0, 1, 1, 3))
        response[74:80] = struct.pack(">HHH", 1, 40, 224)
        response[80:88] = struct.pack(">II", 16, 0xAABBCCDD)
        response[88:120] = struct.pack(">QQQQ", 100, 101, 102, 103)
        response[120:136] = struct.pack(">IIIHH", 104, 105, 106, 107, 108)
        response[136:144] = (109).to_bytes(8, "big")
        response[144:176] = bytes(range(32))
        response[176:208] = bytes(range(32, 64))
        response[208:240] = bytes(range(64, 96))
        response[240:248] = (110).to_bytes(8, "big")
        response[248:280] = bytes(range(96, 128))
        response[280:296] = bytes(range(16))
        response[296:312] = bytes(range(16, 32))
        response[312] = 2
        item._update_receiver_status(response)
        stats = item.get_stats()
        self.assertEqual(stats["receiver_status_version"], 3)
        self.assertEqual(stats["receiver_packets"], 101)
        self.assertEqual(stats["receiver_display_errors"], 12)
        self.assertEqual(stats["receiver_capabilities"], 0xF)
        self.assertEqual(stats["receiver_base_mode"], 1)
        self.assertEqual(stats["receiver_context_state"], 3)
        self.assertEqual(stats["receiver_declared_cadence_hz"], 40)
        self.assertEqual(stats["receiver_scene_epoch"], 100)
        self.assertEqual(stats["receiver_active_scene_revision"], 101)
        self.assertEqual(stats["receiver_vibe_revision"], 102)
        self.assertEqual(stats["receiver_plant_modifier_revision"], 103)
        self.assertEqual(stats["receiver_local_cadence_deadlines"], 104)
        self.assertEqual(stats["receiver_local_frames_rendered"], 105)
        self.assertEqual(stats["receiver_local_missed_deadlines"], 106)
        self.assertEqual(stats["receiver_active_context_digest"], bytes(range(32)).hex())
        self.assertEqual(stats["receiver_staged_context_digest"], bytes(range(96, 128)).hex())
        self.assertEqual(stats["receiver_active_session_id"], bytes(range(16)).hex())
        self.assertEqual(stats["receiver_staged_session_id"], bytes(range(16, 32)).hex())
        self.assertEqual(stats["receiver_logical_device"], 2)
        self.assertEqual(stats["receiver_last_processed_command"], 0)
        self.assertEqual(stats["receiver_operation_sequence"], 0)

    def test_stale_ok_does_not_acknowledge_a_lost_command(self):
        item = controller(FakeSpi(acknowledge=False))
        with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
            item.stop_local_background()

    def test_ack_requires_exact_command_even_when_sequence_advances(self):
        class WrongCommandSpi(FakeSpi):
            def xfer2(self, packet):
                response = super().xfer2(packet)
                if packet[0] != protocol.CMD_STATUS_QUERY:
                    self.last_command = protocol.CMD_LOCAL_BACKGROUND_PARAMS
                    self.queued[-1] = self.status()
                return response

        item = controller(WrongCommandSpi())
        with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
            item.stop_local_background()

    def test_compensated_commits_share_anchor_bound_skew_and_retry_exactly(self):
        class Clock:
            now_ns = 1_000_000_000

            def __call__(self):
                return self.now_ns

            def advance_us(self, amount):
                self.now_ns += amount * 1000

        clock = Clock()

        class TimedSpi(FakeSpi):
            def __init__(self, transfer_us):
                super().__init__()
                self.transfer_us = transfer_us
                self.commit_receive_us = []

            def xfer2(self, packet):
                response = super().xfer2(packet)
                clock.advance_us(self.transfer_us)
                if packet[0] == protocol.CMD_PRESENTATION_CONTEXT_COMMIT:
                    self.commit_receive_us.append(clock.now_ns // 1000)
                return response

        delays_us = (500, 1000, 2500, 4000)
        devices = []
        transports = []
        for delay_us in delays_us:
            transport = TimedSpi(delay_us)
            device = controller(transport)
            device._monotonic_ns = clock
            devices.append(device)
            transports.append(transport)

        wall = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        wall.devices = devices
        wall._monotonic_ns = clock
        presentation = context()
        wall._commit_presentation_contexts(presentation)

        first_packets = []
        scene_at_receive = []
        elapsed_before_board_us = 0
        for transport in transports:
            packet = next(
                packet for packet in transport.packets
                if packet[0] == protocol.CMD_PRESENTATION_CONTEXT_COMMIT
            )
            first_packets.append(packet)
            present_at_us = int.from_bytes(packet[34:42], "big")
            self.assertEqual(
                present_at_us,
                presentation.present_at_scene_time_us
                + elapsed_before_board_us
                + 2 * transport.transfer_us,
                "COMMIT compensation must be sampled after that board's two-query pre-drain",
            )
            self.assertEqual(packet[42:74], presentation.context_digest)
            scene_at_receive.append((present_at_us, transport.commit_receive_us[0]))
            elapsed_before_board_us += 5 * transport.transfer_us

        final_host_us = clock.now_ns // 1000
        aligned_scene_times = [
            present_at_us + final_host_us - receive_us
            for present_at_us, receive_us in scene_at_receive
        ]
        self.assertLessEqual(
            max(aligned_scene_times) - min(aligned_scene_times), 5000
        )
        self.assertEqual(
            {packet[18:34] for packet in first_packets},
            {first_packets[0][18:34]},
            "scene revision and epoch must remain unchanged",
        )

        wall._commit_presentation_contexts(presentation)
        for transport, original in zip(transports, first_packets):
            commit_packets = [
                packet for packet in transport.packets
                if packet[0] == protocol.CMD_PRESENTATION_CONTEXT_COMMIT
            ]
            self.assertEqual(commit_packets, [original, original])

    def test_commit_compensation_cache_retains_only_latest_exact_retry(self):
        item = controller()
        base = context()
        latest = None
        for revision in range(1, 65):
            latest = replace(base, scene_revision=revision)
            item.commit_presentation_context(
                latest, host_monotonic_anchor_ns=time.monotonic_ns()
            )
            self.assertLessEqual(len(item._presentation_commit_context_cache), 1)

        latest_packet = [
            packet for packet in item.spi.packets
            if packet[0] == protocol.CMD_PRESENTATION_CONTEXT_COMMIT
        ][-1]
        item.commit_presentation_context(
            latest, host_monotonic_anchor_ns=time.monotonic_ns()
        )
        retry_packet = [
            packet for packet in item.spi.packets
            if packet[0] == protocol.CMD_PRESENTATION_CONTEXT_COMMIT
        ][-1]
        self.assertEqual(retry_packet, latest_packet)
        self.assertEqual(len(item._presentation_commit_context_cache), 1)
        self.assertEqual(
            next(iter(item._presentation_commit_context_cache)),
            (latest.controller_session_id, latest.scene_revision, latest.context_digest),
        )

    def test_v1_and_v2_status_remain_backward_capable(self):
        item = controller()
        item._update_receiver_status(status_v1())
        stats = item.get_stats()
        self.assertEqual(stats["receiver_status_version"], 1)
        self.assertEqual(stats["receiver_packets"], 101)
        self.assertEqual(stats["receiver_frames_rendered"], 77)
        self.assertEqual(stats["receiver_capabilities"], 0)

        item._update_receiver_status(status_v2())
        stats = item.get_stats()
        self.assertEqual(stats["receiver_status_version"], 2)
        self.assertEqual(stats["receiver_frames_accepted"], 70)
        self.assertEqual(stats["receiver_frames_displayed"], 69)
        self.assertEqual(stats["receiver_display_errors"], 12)
        self.assertEqual(stats["receiver_active_context_digest"], None)

    def test_truncated_v2_or_v3_never_overwrites_atomic_status(self):
        item = controller()
        item._update_receiver_status(status_v2())
        item._update_receiver_status(bytearray(b"LGS2\x02") + bytearray(24))
        item._update_receiver_status(bytearray(b"LGS3\x03") + bytearray(59))
        self.assertEqual(item.get_stats()["receiver_packets"], 101)
        self.assertEqual(item.get_stats()["receiver_status_version"], 2)

    def test_presentation_module_bytes_flow_unchanged_through_crc_transport(self):
        item = controller()
        presentation = context()
        item.begin_presentation_context(presentation)
        item.set_presentation_context(presentation)
        item.commit_presentation_context(presentation)
        commands = [
            packet for packet in item.spi.packets
            if packet[0] in (0x21, 0x22, 0x23)
        ]
        self.assertEqual([packet[0] for packet in commands], [0x21, 0x22, 0x23])
        self.assertEqual([len(packet) - 2 for packet in commands], [58, 151, 74])
        for packet in commands:
            self.assert_packet_crc(packet)

    def test_driver_rejects_malformed_presentation_module_output(self):
        item = controller()
        for payload, command, minimum, maximum in (
            (b"\x21\x01", 0x21, 58, None),
            (b"\x22\x02" + bytes(143), 0x22, 145, 187),
            (b"\x23\x01" + bytes(73), 0x23, 74, None),
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                item._validate_presentation_packet(payload, command, minimum, maximum)

    def test_legacy_commands_remain_byte_compatible_when_local_mode_unused(self):
        item = controller()
        item.set_brightness(42)
        item.set_range(0x0102, [(1, 2, 3), (4, 5, 6)])
        item.show()
        payloads = [packet[:-2] for packet in item.spi.packets]
        self.assertEqual(payloads, [
            b"\x07\x08\x00\x8a\x00",
            b"\x02\x2a",
            b"\x05\x01\x02\x02\x01\x02\x03\x04\x05\x06",
            b"\x03",
        ])
        self.assertFalse(any(payload[0] in range(0x10, 0x24) for payload in payloads))

    def test_config_appends_logical_identity_only_when_explicitly_provisioned(self):
        item = controller()
        item.logical_device_id = 2
        item._receiver_status_version = 3
        item._receiver_capabilities = protocol.CAPABILITY_STATUS_V3
        item.configure()
        self.assertEqual(item.spi.packets[0][:-2], b"\x07\x08\x00\x8a\x00\x02")
        self.assert_packet_crc(item.spi.packets[0])

    def test_config_preserves_legacy_five_bytes_until_v3_is_observed(self):
        item = controller()
        item.logical_device_id = 2
        for version, capabilities in ((0, 0), (1, 0), (2, 0), (3, 0)):
            with self.subTest(version=version, capabilities=capabilities):
                item.spi.packets.clear()
                item._receiver_status_version = version
                item._receiver_capabilities = capabilities
                item.configure()
                self.assertEqual(item.spi.packets[0][:-2], b"\x07\x08\x00\x8a\x00")

    def test_control_ack_transaction_blocks_interleaved_legacy_spi_traffic(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingSpi(FakeSpi):
            def __init__(self):
                super().__init__()
                self.block_once = True

            def xfer2(self, packet):
                if packet[0] == protocol.CMD_STATUS_QUERY and self.block_once:
                    self.block_once = False
                    entered.set()
                    self.assert_release(release)
                return super().xfer2(packet)

            @staticmethod
            def assert_release(event):
                if not event.wait(1.0):
                    raise AssertionError("test did not release blocked status query")

        item = controller(BlockingSpi())
        errors = []
        control = threading.Thread(
            target=lambda: self._capture(errors, item.stop_local_background)
        )
        legacy = threading.Thread(
            target=lambda: self._capture(errors, lambda: item._xfer((0x02, 42)))
        )
        control.start()
        self.assertTrue(entered.wait(1.0))
        legacy.start()
        time.sleep(0.02)
        self.assertFalse(any(packet[0] == 0x02 for packet in item.spi.packets))
        release.set()
        control.join(1.0)
        legacy.join(1.0)
        self.assertEqual(errors, [])
        commands = [packet[0] for packet in item.spi.packets]
        self.assertEqual(commands[-1], 0x02)
        self.assertEqual(commands[:5], [0x08, 0x08, 0x11, 0x08, 0x08])

    @staticmethod
    def _capture(errors, operation):
        try:
            operation()
        except Exception as exc:  # pragma: no cover - asserted by caller
            errors.append(exc)


if __name__ == "__main__":
    unittest.main()
