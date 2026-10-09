"""Only intact CRC-protected status may update receiver connectivity/counters."""
import binascii
import hashlib
from copy import deepcopy
from unittest.mock import patch
from tests.unit.test_host_full_controller import status,SPI
from drivers import spi_controller as protocol


def test_independent_status_ieee_vector_preserved():
    packet=bytearray(1252)
    packet[:5]=b'LGS8\x08';packet[7]=255
    packet[12:16]=(123456).to_bytes(4,'big');packet[64:68]=(1<<21).to_bytes(4,'big')
    packet[78:80]=(256).to_bytes(2,'big');packet[313]=0x35;packet[314]=packet[321]=1
    packet[316:320]=(191895).to_bytes(4,'big')
    packet[-4:]=(binascii.crc32(packet[:-4])&0xffffffff).to_bytes(4,'big')
    assert packet[-4:].hex()=='bc94929e'
    assert hashlib.sha256(packet).hexdigest()=='ce560fc7c4d9ea7a3d82063352d023337378f89ce007173b89b9a79836457cd4'


def test_each_snapshot_byte_is_protected_before_counters_change():
    with patch.object(protocol.spidev,'SpiDev',SPI):
        receiver=protocol.LEDController()
        good=status(123)
        assert receiver._update_receiver_status(good)
        for offset in range(len(good)):
            bad=bytearray(good);bad[offset]^=1
            assert receiver._update_receiver_status(bad,full_status_expected=True) is False
            assert receiver._receiver_packets==123
        receiver.close()


def test_integrity_diagnostic_is_detached_and_never_treated_as_status():
    with patch.object(protocol.spidev,'SpiDev',SPI):
        receiver=protocol.LEDController()
        assert receiver._update_receiver_status(status(123))
        assert receiver._update_receiver_status(bytes(1252),full_status_expected=True) is False
        stats=receiver.get_stats()
        assert stats['receiver_packets']==123
        assert stats['receiver_status_empty_responses']==1
        assert stats['receiver_status_integrity_last_failure']['all_zero']
        stats['receiver_status_integrity_last_failure']['reason']='edited'
        assert receiver.get_stats()['receiver_status_integrity_last_failure']['reason']=='invalid_header'
        receiver.close()
