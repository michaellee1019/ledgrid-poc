"""Regression coverage for the finalized heterogeneous receiver wall."""

from __future__ import annotations

import unittest
from unittest import mock
import sys
import types

import numpy as np

if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from drivers.multi_device import (
    MultiDeviceLEDController,
    OVERLAY_UPDATE_DELTA,
    OVERLAY_UPDATE_FULL_SNAPSHOT,
)


WIDTHS = (8, 8, 8, 8, 1)
OFFSETS = (0, 8, 24, 16, 32)
ROUTES = [(0, 0), (0, 1), (1, 1), (1, 0), (1, 2)]


class _Device:
    def __init__(self, **kwargs):
        self.strip_count = kwargs["strips"]
        self.leds_per_strip = kwargs["leds_per_strip"]
        self.logical_device_id = kwargs["logical_device_id"]
        self.global_strip_offset = kwargs["global_strip_offset"]
        self.spi_speed_hz = kwargs["speed"]
        self.spi_mode = kwargs["mode"]
        self.reverse_native_strip_order = kwargs["reverse_native_strip_order"]
        self.frames = []
        self.pixels = []
        self.partial = []
        self.fail_native_stop = False

    def set_all_pixels(self, frame):
        self.frames.append(np.asarray(frame).copy())

    def set_partial_frame(self, frame, ranges):
        self.partial.append((np.asarray(frame).copy(), tuple(ranges)))

    def set_pixel(self, pixel, red, green, blue):
        self.pixels.append((pixel, red, green, blue))

    def get_stats(self):
        return {
            "total_leds": self.strip_count * self.leds_per_strip,
            "frames_sent": len(self.frames),
            "receiver_capabilities": 0,
            "spi_speed_hz": self.spi_speed_hz,
            "spi_mode": self.spi_mode,
        }

    def native_stop(self):
        if self.fail_native_stop:
            raise OSError("injected native stop failure")
        return {
            "receiver_status_version": 6,
            "receiver_native_result": 1,
            "receiver_native_result_name": "ok",
        }

    def close(self):
        pass


def _controller():
    with (
        mock.patch("drivers.multi_device.LEDController", _Device),
        mock.patch.object(
            MultiDeviceLEDController,
            "_initialize_receiver_identity_observability",
            lambda _self: None,
        ),
    ):
        return MultiDeviceLEDController(
            num_devices=5,
            device_map=ROUTES,
            receiver_strip_counts=WIDTHS,
            receiver_global_strip_offsets=OFFSETS,
            reverse_host_strips_by_logical_receiver=(
                False,
                False,
                True,
                True,
                False,
            ),
            reverse_native_strips_by_logical_receiver=(
                False,
                False,
                True,
                True,
                False,
            ),
            parallel=False,
        )


class HeterogeneousTopologyTests(unittest.TestCase):
    def test_constructor_preserves_exact_width_origin_and_visible_geometry(self):
        controller = _controller()
        self.assertEqual(controller.strip_count, 33)
        self.assertEqual(controller.total_leds, 33 * 138)
        self.assertEqual(
            [device.strip_count for device in controller.devices], list(WIDTHS)
        )
        self.assertEqual(
            [device.global_strip_offset for device in controller.devices],
            list(OFFSETS),
        )
        topology = controller.get_stats()["aggregate"]["device_map"]
        self.assertEqual(
            [entry["local_strip_count"] for entry in topology], list(WIDTHS)
        )
        self.assertEqual(
            [entry["global_strip_offset"] for entry in topology], list(OFFSETS)
        )
        self.assertEqual(
            [entry["physical_output_lane_mask"] for entry in topology],
            [0xFF, 0xFF, 0xFF, 0xFF, 0x01],
        )
        self.assertEqual(
            [(entry["bus"], entry["chip_select"]) for entry in topology],
            ROUTES,
        )
        self.assertEqual(
            [entry["reverse_host_strip_order"] for entry in topology],
            [False, False, True, True, False],
        )
        self.assertEqual(
            [entry["reverse_native_strip_order"] for entry in topology],
            [False, False, True, True, False],
        )

    def test_complete_frame_routes_offsets_and_host_direction_without_tail_mirroring(self):
        controller = _controller()
        frame = np.zeros((33 * 138, 3), dtype=np.uint8)
        for strip in range(33):
            frame[strip * 138 : (strip + 1) * 138, 0] = strip

        split = controller._split_frame(frame)

        expected = ((0, 7), (8, 15), (31, 24), (23, 16), (32, 32))
        for local, (first, last) in zip(split, expected):
            self.assertEqual(local.shape, ((abs(last - first) + 1) * 138, 3))
            self.assertEqual(int(local[0, 0]), first)
            self.assertEqual(int(local[-1, 0]), last)
        self.assertEqual(split[4].shape, (138, 3))

        listed = [
            (strip, 0, 0)
            for strip in range(33)
            for _led in range(138)
        ]
        listed_split = controller._split_frame(listed)
        for local, (first, last) in zip(listed_split, expected):
            self.assertEqual(local[0][0], first)
            self.assertEqual(local[-1][0], last)

    def test_pixel_and_dirty_ranges_use_explicit_origins_and_host_direction(self):
        controller = _controller()
        controller.set_pixel(32 * 138 + 17, 1, 2, 3)
        self.assertEqual(controller.devices[4].pixels, [(17, 1, 2, 3)])
        self.assertTrue(all(not device.pixels for device in controller.devices[:4]))

        controller.set_pixel(24 * 138 + 17, 4, 5, 6)
        self.assertEqual(
            controller.devices[2].pixels,
            [(7 * 138 + 17, 4, 5, 6)],
        )

        controller._display_ownership_known = True
        frame = np.zeros((33 * 138, 3), dtype=np.uint8)
        for strip in range(33):
            frame[strip * 138 : (strip + 1) * 138, 0] = strip
        controller.set_frame(
            frame,
            dirty_ranges=((24 * 138 + 5, 24 * 138 + 9),),
        )
        partial_frame, ranges = controller.devices[2].partial[-1]
        self.assertEqual(ranges, ((7 * 138 + 5, 7 * 138 + 9),))
        self.assertEqual(int(partial_frame[7 * 138 + 5, 0]), 24)
        self.assertEqual(
            controller._receiver_local_dirty_ranges(
                2,
                ((24 * 138 + 136, 25 * 138 + 2),),
            ),
            [(6 * 138, 6 * 138 + 2), (8 * 138 - 2, 8 * 138)],
        )
        self.assertEqual(
            controller._receiver_local_dirty_ranges(
                2,
                ((31 * 138 + 137, 32 * 138 + 1),),
            ),
            [(137, 138)],
        )
        self.assertEqual(
            controller._receiver_local_dirty_ranges(
                4,
                ((31 * 138 + 137, 32 * 138 + 1),),
            ),
            [(0, 1)],
        )

        for device in controller.devices:
            device.partial.clear()
        controller.set_frame(frame, dirty_ranges=((32 * 138, 33 * 138),))
        self.assertTrue(all(not device.partial for device in controller.devices[:4]))
        # A complete one-strip tail is above the dense-update threshold and is
        # therefore sent as one exact 138-pixel frame, never as eight lanes.
        self.assertEqual(controller.devices[4].frames[-1].shape, (138, 3))

    def test_sparse_overlay_maps_full_and_delta_pixels_through_host_direction(self):
        controller = _controller()
        overlay = np.zeros((33 * 138, 4), dtype=np.uint8)
        for strip in range(33):
            overlay[strip * 138 : (strip + 1) * 138, 0] = strip
            overlay[strip * 138 : (strip + 1) * 138, 3] = 255

        full = controller._local_overlay_patches(
            overlay,
            receiver_id=2,
            update_kind=OVERLAY_UPDATE_FULL_SNAPSHOT,
            dirty_ranges=None,
        )
        full_pixels = np.concatenate([pixels for _start, pixels in full])
        self.assertEqual(int(full_pixels[0, 0]), 31)
        self.assertEqual(int(full_pixels[-1, 0]), 24)

        delta = controller._local_overlay_patches(
            overlay,
            receiver_id=2,
            update_kind=OVERLAY_UPDATE_DELTA,
            dirty_ranges=((24 * 138 + 5, 24 * 138 + 9),),
        )
        self.assertEqual(len(delta), 1)
        self.assertEqual(delta[0][0], 7 * 138 + 5)
        self.assertTrue(np.all(delta[0][1][:, 0] == 24))

    def test_invalid_overlap_gap_and_legacy_uniform_default(self):
        with self.assertRaisesRegex(TypeError, "1 through 8"):
            MultiDeviceLEDController(
                num_devices=1,
                device_map=[(0, 0)],
                receiver_strip_counts=(9,),
                receiver_global_strip_offsets=(0,),
            )
        with self.assertRaisesRegex(TypeError, "reverse_host.*2 booleans"):
            MultiDeviceLEDController(
                num_devices=2,
                device_map=[(0, 0), (0, 1)],
                reverse_host_strips_by_logical_receiver=(False,),
            )
        with self.assertRaisesRegex(ValueError, "exactly one.*pair"):
            MultiDeviceLEDController(
                num_devices=2,
                device_map=[(0, 0)],
            )
        with self.assertRaisesRegex(ValueError, "fewer physical lanes"):
            MultiDeviceLEDController(
                num_devices=1,
                device_map=[(0, 0)],
                receiver_strip_counts=(2,),
                receiver_global_strip_offsets=(0,),
                receiver_lane_masks=(0x01,),
            )
        with self.assertRaisesRegex(ValueError, "partition"):
            MultiDeviceLEDController(
                num_devices=2,
                device_map=[(0, 0), (0, 1)],
                receiver_strip_counts=(8, 1),
                receiver_global_strip_offsets=(0, 9),
            )
        with (
            mock.patch("drivers.multi_device.LEDController", _Device),
            mock.patch.object(
                MultiDeviceLEDController,
                "_initialize_receiver_identity_observability",
                lambda _self: None,
            ),
        ):
            legacy = MultiDeviceLEDController(
                num_devices=4, device_map=ROUTES[:4], parallel=False
            )
        self.assertEqual(legacy.receiver_strip_counts, (8, 8, 8, 8))
        self.assertEqual(legacy.receiver_global_strip_offsets, (0, 8, 16, 24))
        self.assertEqual(legacy.strip_count, 32)

    def test_automatic_route_includes_spi1_ce2_tail(self):
        item = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        item.debug = False
        with (
            mock.patch.object(item, "_parse_device_map_env", return_value=None),
            mock.patch.object(
                item,
                "_device_exists",
                side_effect=lambda bus, chip: (bus, chip) in {(1, 0), (1, 1), (1, 2)},
            ),
        ):
            self.assertEqual(item._build_device_map(5, 0), ROUTES)

    def test_per_bus_speed_override_is_positive_fail_safe_and_observable(self):
        with mock.patch.dict(
            "os.environ", {"LEDGRID_SPI1_SPEED": "12000000"}, clear=False
        ):
            controller = _controller()
        self.assertEqual(
            [device.spi_speed_hz for device in controller.devices],
            [20_000_000, 20_000_000, 12_000_000, 12_000_000, 12_000_000],
        )
        self.assertEqual(
            controller.get_stats()["aggregate"]["spi_speeds_hz"],
            [20_000_000, 20_000_000, 12_000_000, 12_000_000, 12_000_000],
        )
        with mock.patch.dict(
            "os.environ", {"LEDGRID_SPI1_SPEED": "0"}, clear=False
        ):
            self.assertEqual(
                MultiDeviceLEDController._resolve_speed(1, 20_000_000),
                20_000_000,
            )

    def test_failed_partial_host_takeover_remains_degraded_when_native_stop_fails(self):
        controller = _controller()
        controller._native_background_active = True
        controller.devices[4].fail_native_stop = True
        frame = np.zeros((33 * 138, 3), dtype=np.uint8)

        with mock.patch.object(
            controller,
            "_send_to_device",
            side_effect=[True, True, True, False, True],
        ):
            controller.set_all_pixels(frame)

        self.assertTrue(controller._native_background_active)
        self.assertEqual(controller._native_background_status["state"], "degraded")
        self.assertEqual(
            controller._native_background_status["operation"],
            "set_all_takeover_failed",
        )
        self.assertEqual(
            [entry["logical_device"] for entry in
             controller._native_background_status["compensation_errors"]],
            [4],
        )


if __name__ == "__main__":
    unittest.main()
