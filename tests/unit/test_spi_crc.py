import binascii
import sys
import types
import unittest


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from drivers.spi_controller import _crc16_ccitt


class SpiCrcTests(unittest.TestCase):
    def test_matches_ccitt_false_check_value(self):
        self.assertEqual(_crc16_ccitt(b"123456789"), 0x29B1)

    def test_accepts_frame_bytearray(self):
        frame = bytearray((index * 17) & 0xFF for index in range(3361))
        self.assertEqual(_crc16_ccitt(frame), binascii.crc_hqx(frame, 0xFFFF))


if __name__ == "__main__":
    unittest.main()
