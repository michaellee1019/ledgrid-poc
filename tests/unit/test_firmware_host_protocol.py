import struct
import sys
import types
import unittest

if 'spidev' not in sys.modules:
    module = types.ModuleType('spidev')
    module.SpiDev = object
    sys.modules['spidev'] = module

from drivers import spi_controller as protocol


class FakeSpi:
    def __init__(self):
        self.packets = []
        self.max_speed_hz = 20_000_000
        self.mode = 0

    def xfer2(self, packet):
        self.packets.append(bytes(packet))
        return [0] * len(packet)


def controller():
    item = protocol.LEDController.__new__(protocol.LEDController)
    item.spi = FakeSpi()
    item.total_leds = 8 * 138
    item.strip_count = 8
    item.leds_per_strip = 138
    item._bytes_sent = item._crc_bytes_sent = item._spi_transfers = item._errors = 0
    item._receiver_status_seen = False
    item._receiver_status_version = 0
    item._receiver_status_responses = item._receiver_status_misses = 0
    for name in (
        '_receiver_packets', '_receiver_crc_errors', '_receiver_crc_ok_packets',
        '_receiver_frames_rendered', '_receiver_last_crc_us', '_receiver_last_copy_us',
        '_receiver_last_show_us', '_receiver_active_strips', '_receiver_leds_per_strip',
        '_receiver_queued_transactions', '_receiver_frames_accepted',
        '_receiver_frames_displayed', '_receiver_frames_superseded',
        '_receiver_publish_drops', '_receiver_spi_queue_errors', '_receiver_display_errors',
        '_receiver_last_encode_us', '_receiver_last_accepted_sequence',
        '_receiver_last_displayed_sequence', '_receiver_capabilities',
        '_receiver_display_mode', '_receiver_asset_kind', '_receiver_upload_state',
        '_receiver_last_result', '_receiver_cache_free_bytes', '_receiver_cache_used_bytes',
        '_receiver_upload_received_bytes', '_receiver_upload_total_bytes',
        '_receiver_last_render_or_decode_us', '_receiver_max_render_or_decode_us',
        '_receiver_missed_deadlines', '_receiver_watchdog_events',
        '_receiver_quarantine_state', '_frames_sent'):
        setattr(item, name, 0)
    item._receiver_active_digest = None
    item._receiver_logical_device = None
    item._last_frame_duration = item._total_frame_duration = 0.0
    return item


class Envelope:
    def __init__(self, device_index=2):
        self.payload_size = 0x01020304
        self.payload_digest = bytes(range(32))
        self.kind = 'frames'
        self.device_index = device_index
        self.key_id = 'key-' + '1' * 16
        self.signed_index = bytes(index & 0xff for index in range(176))
        self.signature = bytes((255 - index) & 0xff for index in range(64))

    def asset_begin_command(self):
        body = (
            struct.pack('>I', self.payload_size) + self.payload_digest
            + struct.pack('>BHHBHB', 2, 1, 1, 8, 138, self.device_index)
            + bytes((20,)) + self.key_id.encode('ascii')
            + struct.pack('>H', 176) + self.signed_index
            + bytes((64,)) + self.signature
        )
        return bytes((protocol.CMD_ASSET_BEGIN, 1)) + struct.pack('>H', len(body)) + body


class FirmwareHostProtocolTests(unittest.TestCase):
    def test_exact_4096_byte_total_transfer_and_crc(self):
        item = controller()
        payload = bytes([protocol.CMD_ASSET_CHUNK]) + bytes(4093)
        item._xfer(payload)
        packet = item.spi.packets[-1]
        self.assertEqual(len(packet), 4096)
        self.assertEqual(packet[-2:], protocol._crc16_ccitt(payload).to_bytes(2, 'big'))
        with self.assertRaises(ValueError):
            item._xfer(payload + b'x')

    def test_lgs3_stable_fields_parse(self):
        item = controller()
        status = bytearray(128)
        status[:5] = b'LGS3\x03'
        status[6] = 8
        status[8:10] = (138).to_bytes(2, 'big')
        status[12:16] = (101).to_bytes(4, 'big')
        status[64:68] = (0x600C7).to_bytes(4, 'big')
        status[68:72] = bytes((2, 1, 3, 1))
        status[72:104] = bytes(range(32))
        status[104:108] = (9000).to_bytes(4, 'big')
        status[112:116] = (4096).to_bytes(4, 'big')
        status[116:120] = (8192).to_bytes(4, 'big')
        status[120:128] = struct.pack('>HHHBB', 123, 456, 7, 8, 1)
        item._update_receiver_status(status)
        stats = item.get_stats()
        self.assertEqual(stats['receiver_status_version'], 3)
        self.assertEqual(stats['receiver_packets'], 101)
        self.assertEqual(stats['receiver_leds_per_strip'], 138)
        self.assertEqual(stats['receiver_capabilities'], 0x600C7)
        self.assertEqual(stats['receiver_logical_device'], 2)
        self.assertEqual(stats['receiver_active_digest'], bytes(range(32)).hex())
        self.assertEqual(stats['receiver_upload_received_bytes'], 4096)
        self.assertEqual(stats['receiver_max_render_or_decode_us'], 456)
        self.assertEqual(stats['receiver_quarantine_state'], 1)

    def test_non_lgs3_status_is_ignored(self):
        item = controller()
        for prefix in (b'NOPE\x03', b'LGS3\x02'):
            status = bytearray(128)
            status[:5] = prefix
            status[12:16] = (12).to_bytes(4, 'big')
            item._update_receiver_status(status)
        self.assertFalse(item.get_stats()['receiver_status_seen'])
        self.assertEqual(item.get_stats()['receiver_packets'], 0)

    def test_typed_parameters_are_canonical_and_bounded(self):
        blob = protocol.LEDController._parameter_blob({
            'speed': 1.5, 'reverse': True, 'count': 3,
            'palette': 'rainbow', 'color': '#12aBff',
        })
        self.assertEqual(blob[:2], bytes((1, 5)))
        self.assertIn(b'color\x05\x12\xab\xff', blob)
        with self.assertRaises(ValueError):
            protocol.LEDController._parameter_blob({'x': object()})

    def test_command_ack_is_observed_on_followup_status_transfer(self):
        item = controller()
        class QueuedSpi(FakeSpi):
            def __init__(self):
                super().__init__()
                self.last_result = 0
                self.queued = [self.status(), self.status()]

            def status(self):
                status = bytearray(protocol.RECEIVER_STATUS_BYTES)
                status[:5] = b'LGS3\x03'
                status[71] = self.last_result
                return status

            def xfer2(self, packet):
                self.packets.append(bytes(packet))
                response = self.queued.pop(0)[:len(packet)]
                if packet[0] in (
                    protocol.CMD_ASSET_PROBE,
                    protocol.CMD_CAPABILITIES_QUERY,
                ):
                    self.last_result = 1
                self.queued.append(self.status())
                return response
        item.spi = QueuedSpi()
        result = item.asset_probe('ab' * 32)
        self.assertEqual([packet[0] for packet in item.spi.packets], [
            protocol.CMD_ASSET_PROBE, protocol.CMD_CAPABILITIES_QUERY,
            protocol.CMD_CAPABILITIES_QUERY,
        ])
        self.assertEqual(result['receiver_last_result_name'], 'ok')

    def test_one_followup_cannot_observe_depth_two_command_result(self):
        item = controller()

        class DepthTwoSpi(FakeSpi):
            def __init__(self):
                super().__init__()
                self.last_result = 0
                self.queued = [self.status(), self.status()]

            def status(self):
                status = bytearray(protocol.RECEIVER_STATUS_BYTES)
                status[:5] = b'LGS3\x03'
                status[71] = self.last_result
                return status

            def xfer2(self, packet):
                self.packets.append(bytes(packet))
                response = self.queued.pop(0)[:len(packet)]
                self.last_result = 15 if packet[0] == protocol.CMD_ASSET_PROBE else 1
                self.queued.append(self.status())
                return response

        item.spi = DepthTwoSpi()
        item._xfer(bytes([protocol.CMD_ASSET_PROBE]) + bytes(32))
        first = item.query_receiver_status()
        second = item.query_receiver_status()

        self.assertEqual(first['receiver_last_result_name'], 'none')
        self.assertEqual(second['receiver_last_result_name'], 'not_found')

    def test_asset_begin_v1_matches_firmware_byte_offsets_and_crc(self):
        item = controller()
        envelope = Envelope()
        item.asset_begin(envelope)
        packet = item.spi.packets[0]
        command = packet[:-2]
        self.assertEqual(len(command), 313)
        self.assertEqual(len(packet), 315)
        self.assertEqual(command[:4], b'\x22\x01\x01\x35')
        self.assertEqual(command[4:8], b'\x01\x02\x03\x04')
        self.assertEqual(command[8:40], bytes(range(32)))
        self.assertEqual(command[40:49], struct.pack('>BHHBHB', 2, 1, 1, 8, 138, 2))
        self.assertEqual(command[49:72], b'\x14' + envelope.key_id.encode() + b'\x00\xb0')
        self.assertEqual(command[72:248], envelope.signed_index)
        self.assertEqual(command[248:313], b'\x40' + envelope.signature)
        self.assertEqual(packet[-2:], protocol._crc16_ccitt(command).to_bytes(2, 'big'))

    def test_asset_begin_rejects_noncanonical_envelope_before_spi(self):
        item = controller()
        for mutation in ('missing', 'oversize', 'tampered'):
            envelope = Envelope()
            if mutation == 'missing':
                envelope = None
            elif mutation == 'oversize':
                envelope.signed_index += b'x'
            else:
                envelope.asset_begin_command = lambda: b'\x22' * 313
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                item.asset_begin(envelope)
        self.assertEqual(item.spi.packets, [])

    def test_asset_abort_is_a_canonical_status_drained_command(self):
        item = controller()
        item.asset_abort()
        self.assertEqual(
            [packet[0] for packet in item.spi.packets],
            [protocol.CMD_ASSET_ABORT, protocol.CMD_CAPABILITIES_QUERY,
             protocol.CMD_CAPABILITIES_QUERY],
        )
        self.assertEqual(item.spi.packets[0][:-2], bytes((protocol.CMD_ASSET_ABORT,)))


if __name__ == '__main__':
    unittest.main()
