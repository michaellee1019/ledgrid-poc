"""Whole-wall brightness must not spam CONFIG or redundant SPI commands."""
import sys
import types
from unittest.mock import patch

if "spidev" not in sys.modules:
    sys.modules["spidev"] = types.SimpleNamespace(SpiDev=object)

from drivers.spi_controller import CMD_CONFIG, CMD_SET_BRIGHTNESS, LEDController


class RecordingSPI:
    def __init__(self):
        self.packets = []

    def open(self, *_args, **_kwargs):
        return None

    def close(self):
        return None

    def _record(self, packet):
        self.packets.append(bytes(packet))

    def xfer2(self, packet):
        self._record(packet)
        return bytes(len(packet))

    def writebytes2(self, packet):
        self._record(packet)


def test_set_brightness_sends_only_brightness_command():
    spi = RecordingSPI()
    with patch("drivers.spi_controller.spidev.SpiDev", return_value=spi):
        controller = LEDController(bus=1, device=0, strips=8, leds_per_strip=138)
        controller.set_brightness(26)
    assert spi.packets
    assert any(CMD_SET_BRIGHTNESS in p and 26 in p for p in spi.packets)
    assert not any(p[0] == CMD_CONFIG or CMD_CONFIG in p[:4] for p in spi.packets)


def test_configure_skips_redundant_all_lanes_mask():
    from drivers.multi_device import MultiDeviceLEDController
    from unittest.mock import patch

    class Device:
        def __init__(self, **kwargs):
            self.lane_masks = []

        def configure(self):
            pass

        def set_lane_mask(self, mask):
            self.lane_masks.append(mask)

        def close(self):
            pass

    with patch("drivers.multi_device.LEDController", Device):
        controller = MultiDeviceLEDController(
            device_map=[(1, 0), (1, 1), (0, 0), (0, 1), (0, 3)],
            parallel=False,
            receiver_lane_masks=(255, 255, 255, 255, 255),
        )
        controller.configure()
        assert all(len(d.lane_masks) == 0 for d in controller.devices)
        controller.close()


def test_set_output_brightness_skips_when_already_applied():
    from animation.core.manager import AnimationManager
    from unittest.mock import MagicMock

    controller = MagicMock()
    controller.current_brightness = 26
    controller.strip_count = 8
    controller.leds_per_strip = 138
    controller.debug = False
    controller.brightness_is_applied.return_value = True
    with patch("animation.core.manager.PreviewLEDController"):
        manager = AnimationManager(controller, auto_start=False)
    manager.output_brightness = 26
    manager.set_output_brightness(26)
    controller.set_brightness.assert_not_called()
