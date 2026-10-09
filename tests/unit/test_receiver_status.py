"""Current protected status supersedes unsupported legacy snapshots."""
from unittest.mock import patch
from tests.unit.test_host_full_controller import status, SPI
from drivers import spi_controller as protocol


def test_legacy_status_cannot_replace_current_receiver_counters():
    with patch.object(protocol.spidev, 'SpiDev', SPI):
        receiver = protocol.LEDController()
        try:
            assert receiver._update_receiver_status(status(123))
            legacy = bytearray(status(999))
            legacy[:5] = b'LGS2\x02'
            assert not receiver._update_receiver_status(legacy)
            stats = receiver.get_stats()
            assert stats['receiver_packets'] == 123
            assert stats['receiver_status_unprotected_rejections'] == 1
        finally:
            receiver.close()


def test_short_transfer_keeps_last_valid_snapshot_without_counting_a_miss():
    with patch.object(protocol.spidev, 'SpiDev', SPI):
        receiver = protocol.LEDController()
        try:
            assert receiver._update_receiver_status(status(123))
            before = receiver.get_stats()
            assert not receiver._update_receiver_status(status(999)[:64])
            after = receiver.get_stats()
            for key in ('receiver_packets', 'receiver_status_misses', 'receiver_status_responses'):
                assert after[key] == before[key]
        finally:
            receiver.close()
