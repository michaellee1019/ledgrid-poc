"""Parsing of the receiver's MISO status snapshot, including stale firmware.

tools/diagnostics/lane_mask_remote.sh syncs drivers/ but never reflashes, so
pairing a newer host against boards flashed before a snapshot field existed is
a routine occurrence rather than an exotic one.
"""

import io
import sys
import types
import unittest
from contextlib import redirect_stderr
from unittest import mock


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from drivers.spi_controller import (  # noqa: E402
    RECEIVER_STATUS_BYTES_V2,
    STAGGER_OFF,
    LEDController,
)

# One CMD_SET_ALL frame for 8 strips of 138 LEDs, which is what the controller
# actually sends and therefore the transfer that carries a full snapshot back.
FRAME_TRANSFER_BYTES = 1 + 8 * 138 * 3 + 2


class FakeSpiDev:
    """Loopback stand-in that returns a caller-supplied MISO buffer."""

    def __init__(self):
        self.max_speed_hz = 0
        self.mode = 0
        self.bits_per_word = 8
        self.response = None

    def open(self, bus, device):
        self.bus = bus
        self.device = device

    def close(self):
        pass

    def xfer2(self, buf):
        if self.response is None:
            return [0] * len(buf)
        return list(self.response)


def build_snapshot(stagger_phases, transfer_bytes=FRAME_TRANSFER_BYTES):
    """A v2 snapshot at the head of a frame-sized response.

    Passing stagger_phases=None models firmware that stops at 64 bytes: the
    receiver's zero-initialized DMA buffer supplies the remaining bytes.
    """
    response = bytearray(transfer_bytes)
    response[0:4] = b"LGS2"
    response[4] = 2
    response[5] = 0x01
    response[6] = 8
    response[7] = 0xFF
    response[8:10] = (138).to_bytes(2, "big")
    response[12:16] = (353528).to_bytes(4, "big")
    response[44:46] = (236).to_bytes(2, "big")
    response[48:50] = (242).to_bytes(2, "big")
    response[50:52] = (4421).to_bytes(2, "big")
    if stagger_phases is not None:
        response[64] = stagger_phases
    return response


class ReceiverStatusTests(unittest.TestCase):
    def setUp(self):
        self.spi = FakeSpiDev()
        patcher = mock.patch.object(
            sys.modules["spidev"], "SpiDev", lambda: self.spi, create=True
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.controller = LEDController(bus=0, device=0, strips=8, leds_per_strip=138)

    def parse(self, response):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.controller._update_receiver_status(response)
        return stderr.getvalue()

    def test_legacy_firmware_keeps_reporting_and_is_named(self):
        warning = self.parse(build_snapshot(None))
        stats = self.controller.get_stats()

        # The pre-existing bytes are unaffected by the widened layout, so the
        # board must not look absent or corrupted.
        self.assertTrue(stats["receiver_status_seen"])
        self.assertEqual(stats["receiver_status_version"], 2)
        self.assertEqual(stats["receiver_status_misses"], 0)
        self.assertEqual(stats["receiver_lane_mask"], 0xFF)
        self.assertEqual(stats["receiver_packets"], 353528)
        self.assertEqual(stats["receiver_last_encode_us"], 242)
        self.assertEqual(stats["receiver_last_show_us"], 4421)

        self.assertTrue(stats["receiver_status_legacy"])
        self.assertEqual(stats["receiver_stagger_phases"], 0)
        self.assertIn("stagger_phases", warning)
        self.assertIn("just deploy", warning)
        self.assertIn("spidev0.0", warning)

    def test_legacy_warning_is_not_repeated_per_frame(self):
        self.parse(build_snapshot(None))
        self.assertEqual(self.parse(build_snapshot(None)), "")

    def test_current_firmware_is_not_flagged(self):
        warning = self.parse(build_snapshot(3))
        stats = self.controller.get_stats()

        self.assertFalse(stats["receiver_status_legacy"])
        self.assertEqual(stats["receiver_stagger_phases"], 3)
        self.assertEqual(warning, "")

    def test_flag_clears_once_reflashed_firmware_reports(self):
        self.parse(build_snapshot(None))
        self.parse(build_snapshot(STAGGER_OFF))

        stats = self.controller.get_stats()
        self.assertFalse(stats["receiver_status_legacy"])
        self.assertEqual(stats["receiver_stagger_phases"], STAGGER_OFF)

    def test_transfer_too_short_for_the_field_is_not_a_miss(self):
        self.parse(build_snapshot(3))
        # Small partial-frame writes cannot carry the whole snapshot back, so
        # they must leave the last good reading in place untouched.
        self.parse(build_snapshot(3, transfer_bytes=RECEIVER_STATUS_BYTES_V2 - 2))

        stats = self.controller.get_stats()
        self.assertEqual(stats["receiver_status_misses"], 0)
        self.assertEqual(stats["receiver_status_responses"], 1)
        self.assertTrue(stats["receiver_status_seen"])


if __name__ == "__main__":
    unittest.main()
