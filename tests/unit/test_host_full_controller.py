"""Full RGB transport preserves wall mapping and reports partial failures honestly."""
import binascii
import sys
import types
if "spidev" not in sys.modules:
    sys.modules["spidev"] = types.SimpleNamespace(SpiDev=object)
from unittest.mock import patch
import numpy as np
from drivers.multi_device import MultiDeviceLEDController
from drivers import spi_controller as protocol


def status(packets=1):
    reply=bytearray(protocol.RECEIVER_STATUS_BYTES_V8)
    reply[:5]=b'LGS8\x08'
    reply[6]=8
    reply[7]=255
    reply[8:10]=(138).to_bytes(2,'big')
    reply[12:16]=packets.to_bytes(4,'big')
    reply[64:68]=(protocol.CAPABILITY_ALIGNED_ENVELOPE_V1|protocol.CAPABILITY_FEC_ENVELOPE_V7|protocol.CAPABILITY_STATUS_CRC32_V8).to_bytes(4,'big')
    reply[314]=3
    reply[-4:]=(binascii.crc32(reply[:-4])&0xffffffff).to_bytes(4,'big')
    return reply


class SPI:
    def __init__(self):
        self.packets=[]
    def open(self,*args):pass
    def xfer2(self,packet):
        self.packets.append(bytes(packet))
        return bytes(status(len(self.packets))).ljust(len(packet),b'\0')[:len(packet)]
    def close(self):pass


class Receiver:
    def __init__(self,**kwargs):
        self.options=kwargs
        self.frames=[]
        self.brightness=[]
        self.fail=False
    def set_all_pixels(self,frame,**kwargs):
        self.frames.append(frame.copy())
        return not self.fail
    def set_brightness(self,value):self.brightness.append(value)
    def clear(self):pass
    def close(self):pass
    def get_stats(self):return {'receiver_connected':True,'frames_sent':len(self.frames)}


def wall(**kwargs):
    with patch('drivers.multi_device.LEDController',Receiver):
        return MultiDeviceLEDController(device_map=[(0,0),(0,1),(1,1),(1,0),(1,2)],parallel=False,**kwargs)


def test_exact_33x138_five_receiver_routing_and_broadcast_tail():
    controller=wall()
    pixels=np.zeros((4554,3),dtype=np.uint8)
    for strip in range(33):pixels[strip*138:(strip+1)*138,0]=strip
    assert controller.set_all_pixels(pixels)
    assert [len(x.frames[0]) for x in controller.devices]==[1104,1104,1104,1104,138]
    assert [int(x.frames[0][0,0]) for x in controller.devices]==[0,8,16,24,32]
    assert controller.receiver_lane_masks==(255,255,255,255,255)
    assert controller.devices[3].options['speed']==8_000_000
    assert controller.devices[3].options['fec_transport'] is True
    controller.close()


def test_partial_transfer_failure_never_restores_or_clears_other_receivers():
    controller=wall()
    controller.devices[2].fail=True
    assert controller.set_all_pixels(np.full((4554,3),29,dtype=np.uint8)) is False
    assert all(len(device.frames)==1 for device in controller.devices)
    assert controller.get_stats()['aggregate']['last_full_frame_failure']['wall_frame_sequence']==0
    controller.close()


def test_brightness_zero_and_strict_frame_shape_are_preserved():
    controller=wall()
    controller.set_brightness(0)
    assert all(device.brightness==[0] for device in controller.devices)
    assert controller.brightness_is_applied(0)
    import pytest
    with pytest.raises(ValueError):controller.set_all_pixels(np.zeros((4553,3),dtype=np.uint8))
    controller.close()


def test_only_complete_crc_checked_status_affects_connectivity_and_fec():
    with patch.object(protocol.spidev,'SpiDev',SPI):
        receiver=protocol.LEDController(fec_transport=True)
        good=status(100)
        good[1216:1220]=(8).to_bytes(4,'big')
        good[1232:1236]=(2).to_bytes(4,'big')
        good[-4:]=(binascii.crc32(good[:-4])&0xffffffff).to_bytes(4,'big')
        assert receiver._update_receiver_status(good)
        assert receiver.get_stats()['receiver_connected']
        assert receiver._fec_transport_enabled
        assert receiver._receiver_fec_packets_received==8
        assert receiver._receiver_fec_uncorrectable_packets==2
        for index in (12,64,1000,1232,1248):
            corrupt=bytearray(good);corrupt[index]^=1
            assert receiver._update_receiver_status(corrupt) is False
            assert receiver._receiver_packets==100
            assert receiver._receiver_fec_packets_received==8
        assert receiver._update_receiver_status(good[:100]) is False
        receiver.close()


def test_configure_uses_current_host_layout_and_never_native_direction():
    with patch.object(protocol.spidev,'SpiDev',SPI):
        receiver=protocol.LEDController(strips=1,leds_per_strip=138,logical_device_id=4,global_strip_offset=32)
        receiver.configure()
        packet=receiver.spi.packets[-1]
        semantic_length=int.from_bytes(packet[2:4],'big')
        semantic=packet[4:4+semantic_length]
        assert semantic==bytes([protocol.CMD_CONFIG,1,0,138,0,4,0,32])
        receiver.close()
