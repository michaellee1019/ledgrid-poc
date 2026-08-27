"""Installed 33-strip / 5-receiver layout contracts."""

import sys
import types
import unittest

import numpy as np


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from drivers.led_layout import (
    DEFAULT_LEDS_PER_STRIP,
    DEFAULT_STRIP_COUNT,
    EXTRA_STRIP_LANE,
    MIRROR_EXTRA_STRIP_ON_ALL_LANES,
    STRIPS_PER_DEVICE,
    WALL_PHYSICAL_LANE_ORDER,
    WALL_PHYSICAL_OUTPUT_LANE_MASKS,
    WALL_RECEIVER_GLOBAL_STRIP_OFFSETS,
    WALL_RECEIVER_STRIP_COUNTS,
    WALL_REVERSE_HOST_STRIPS_BY_LOGICAL_RECEIVER,
    WALL_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER,
    device_count_for_strips,
    logical_strip_count,
    wall_device_map,
)
from drivers.multi_device import MultiDeviceLEDController
from tools.deployment.receiver_hybrid_config import (
    DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS,
    DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS,
    DEFAULT_RECEIVER_STRIP_COUNTS,
)


class LedLayoutTests(unittest.TestCase):
    def test_installed_wall_is_thirty_three_strips_on_five_receivers(self):
        self.assertEqual(DEFAULT_STRIP_COUNT, 33)
        self.assertEqual(DEFAULT_LEDS_PER_STRIP, 138)
        self.assertEqual(device_count_for_strips(DEFAULT_STRIP_COUNT), 5)
        self.assertEqual(
            logical_strip_count(5, STRIPS_PER_DEVICE, DEFAULT_STRIP_COUNT),
            33,
        )

    def test_logical_strip_count_rejects_overflow(self):
        with self.assertRaises(ValueError):
            logical_strip_count(4, 8, 33)

    def test_fifth_receiver_prefers_spi1_ce2(self):
        self.assertEqual(
            wall_device_map(4),
            [(0, 0), (0, 1), (1, 1), (1, 0)],
        )
        self.assertEqual(
            wall_device_map(5),
            [(0, 0), (0, 1), (1, 1), (1, 0), (1, 2)],
        )

    def test_rightmost_extra_strip_broadcasts_one_semantic_column(self):
        self.assertEqual(EXTRA_STRIP_LANE, 0)
        self.assertTrue(MIRROR_EXTRA_STRIP_ON_ALL_LANES)
        self.assertEqual(DEFAULT_RECEIVER_STRIP_COUNTS, (8, 8, 8, 8, 1))
        self.assertEqual(
            DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS, (0, 8, 16, 24, 32)
        )
        self.assertEqual(
            DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS,
            (0xFF, 0xFF, 0xFF, 0xFF, 0xFF),
        )

    def test_camera_measured_runtime_topology_keeps_domains_explicit(self):
        self.assertEqual(WALL_PHYSICAL_LANE_ORDER, (0, 1, 2, 3, 4))
        self.assertEqual(WALL_RECEIVER_STRIP_COUNTS, (8, 8, 8, 8, 1))
        self.assertEqual(
            WALL_RECEIVER_GLOBAL_STRIP_OFFSETS, (0, 8, 16, 24, 32)
        )
        self.assertEqual(
            WALL_REVERSE_HOST_STRIPS_BY_LOGICAL_RECEIVER,
            (False, False, False, False, False),
        )
        self.assertEqual(
            WALL_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER,
            (False, False, True, True, False),
        )
        self.assertEqual(
            WALL_PHYSICAL_OUTPUT_LANE_MASKS,
            (0xFF, 0xFF, 0xFF, 0xFF, 0xFF),
        )

    def test_layout_has_no_ce3_fallback(self):
        with self.assertRaisesRegex(ValueError, "supports 1 through 5"):
            wall_device_map(6)


class MultiDeviceLayoutTests(unittest.TestCase):
    def test_thirty_three_strips_send_one_lane_to_the_fifth_receiver(self):
        controller = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        controller.num_devices = 5
        controller.strips_per_device = 8
        controller.leds_per_strip = 2
        controller.strip_count = 33
        controller.total_leds = 66
        controller.receiver_strip_counts = (8, 8, 8, 8, 1)
        controller.receiver_global_strip_offsets = (0, 8, 16, 24, 32)
        controller.receiver_pixel_counts = (16, 16, 16, 16, 2)
        controller.receiver_pixel_offsets = (0, 16, 32, 48, 64)
        controller.reverse_host_strips_by_logical_receiver = (
            False, False, True, True, False,
        )

        colors = np.full((66, 3), 7, dtype=np.uint8)
        frames = controller._split_frame(colors)

        self.assertEqual(len(frames), 5)
        self.assertEqual(frames[0].shape, (16, 3))
        self.assertEqual(frames[4].shape, (2, 3))
        np.testing.assert_array_equal(frames[4], 7)

    def test_build_device_map_appends_spi1_ce2(self):
        controller = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        controller.debug = False
        existing = {(0, 0), (0, 1), (1, 0), (1, 1), (1, 2)}
        controller._device_exists = staticmethod(
            lambda bus, device: (bus, device) in existing
        )

        self.assertEqual(
            controller._build_device_map(5, 0),
            [(0, 0), (0, 1), (1, 1), (1, 0), (1, 2)],
        )


if __name__ == "__main__":
    unittest.main()
