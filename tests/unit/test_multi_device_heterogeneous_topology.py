"""Regression coverage for the finalized heterogeneous receiver wall."""

from __future__ import annotations

import contextlib
import io
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
from drivers.spi_controller import (
    SPI_RESPONSE_QUEUE_DEPTH,
    TRANSPORT_ENVELOPE_NEGOTIATION_OBSERVATIONS,
)


WIDTHS = (8, 8, 8, 8, 1)
OFFSETS = (0, 8, 16, 24, 32)
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
        self.wall_frame_sequences = []
        self.pixels = []
        self.partial = []
        self.fail_wall_frame_sequences = set()
        self.fail_native_stop = False

    def set_all_pixels(self, frame, *, wall_frame_sequence=None):
        self.wall_frame_sequences.append(wall_frame_sequence)
        if wall_frame_sequence in self.fail_wall_frame_sequences:
            raise OSError("injected frame failure")
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


class _ObservableDevice:
    def __init__(self, logical_id, status):
        self.logical_device_id = logical_id
        self._status = dict(status)
        self.configures = 0
        self.lane_masks = []
        self.queries = 0

    def query_receiver_status(self):
        self.queries += 1
        return dict(self._status)

    def get_stats(self):
        return dict(self._status)

    def configure(self):
        self.configures += 1

    def set_lane_mask(self, lane_mask):
        self.lane_masks.append(lane_mask)


class _QueuedObservableDevice:
    """Model the receiver's two-deep, reply-before-command SPI queue."""

    def __init__(self, logical_id, width, offset, lane_mask):
        self.logical_device_id = logical_id
        self.width = width
        self.offset = offset
        self.requested_lane_mask = lane_mask
        self.configures = 0
        self.extended_configurations = []
        self.lane_masks = []
        self.queries = 0
        self._configured = False
        self._configured_logical_id = 0xFF
        self._post_config_queries = 0
        self._latest_status = {
            "receiver_status_version": 0,
            "receiver_logical_device": None,
        }

    def query_receiver_status(self):
        self.queries += 1
        if not self._configured:
            if self.queries <= SPI_RESPONSE_QUEUE_DEPTH:
                status = {
                    "receiver_status_version": 0,
                    "receiver_logical_device": None,
                }
            else:
                status = {
                    "receiver_status_version": 3,
                    "receiver_logical_device": 0xFF,
                    "receiver_active_strips": 8,
                    "receiver_global_strip_offset": 0,
                    "receiver_lane_mask": 0xFF,
                }
        else:
            self._post_config_queries += 1
            if self._post_config_queries <= SPI_RESPONSE_QUEUE_DEPTH:
                status = {
                    "receiver_status_version": 3,
                    "receiver_logical_device": 0xFF,
                    "receiver_active_strips": 8,
                    "receiver_global_strip_offset": 0,
                    "receiver_lane_mask": 0xFF,
                }
            else:
                status = {
                    "receiver_status_version": 3,
                    "receiver_logical_device": self._configured_logical_id,
                    "receiver_active_strips": self.width,
                    "receiver_global_strip_offset": self.offset,
                    "receiver_lane_mask": self.lane_masks[-1],
                }
        self._latest_status = status
        return dict(status)

    def get_stats(self):
        return dict(self._latest_status)

    def configure(self):
        self.configures += 1
        extended = self._latest_status.get("receiver_status_version", 0) >= 3
        self.extended_configurations.append(extended)
        self._configured_logical_id = (
            self.logical_device_id if extended else 0xFF
        )
        self._configured = True

    def set_lane_mask(self, lane_mask):
        self.lane_masks.append(lane_mask)


def _observability_controller(*, receiver_4_status=None):
    controller = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
    controller.receiver_strip_counts = WIDTHS
    controller.receiver_global_strip_offsets = OFFSETS
    controller.receiver_lane_masks = (0xFF, 0xFF, 0xFF, 0xFF, 0xFF)
    controller.devices = []
    for logical_id, (width, offset, lane_mask) in enumerate(
        zip(
            controller.receiver_strip_counts,
            controller.receiver_global_strip_offsets,
            controller.receiver_lane_masks,
        )
    ):
        status = {
            "receiver_status_version": 3,
            "receiver_logical_device": logical_id,
            "receiver_active_strips": width,
            "receiver_global_strip_offset": offset,
            "receiver_lane_mask": lane_mask,
        }
        if logical_id == 4 and receiver_4_status is not None:
            status.update(receiver_4_status)
        controller.devices.append(_ObservableDevice(logical_id, status))
    return controller


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
            receiver_lane_masks=(0xFF, 0xFF, 0xFF, 0xFF, 0xFF),
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
    def test_status_sample_and_write_only_counters_aggregate_exactly(self):
        controller = _controller()
        for logical_id, device in enumerate(controller.devices):
            original = device.get_stats
            device.get_stats = (
                lambda original=original, logical_id=logical_id: {
                    **original(),
                    "full_frame_transfers": 100 + logical_id,
                    "full_frame_status_transfers": 2 + 2 * logical_id,
                    "full_frame_status_samples": 2 + logical_id,
                    "full_frame_status_sample_misses": logical_id,
                    "full_frame_write_only_transfers": 98 - logical_id,
                    "full_frame_frames_since_status_sample": 4 + logical_id,
                    "full_frame_max_status_sample_gap": 120 + logical_id,
                    "spidev_buffer_size": 4096 - logical_id,
                    "full_frame_write_only_supported": True,
                }
            )

        aggregate = controller.get_stats()["aggregate"]

        self.assertEqual(aggregate["full_frame_transfers"], 510)
        self.assertEqual(aggregate["full_frame_status_transfers"], 30)
        self.assertEqual(aggregate["full_frame_status_samples"], 20)
        self.assertEqual(aggregate["full_frame_status_sample_misses"], 10)
        self.assertEqual(aggregate["full_frame_write_only_transfers"], 480)
        self.assertEqual(aggregate["full_frame_frames_since_status_sample"], 8)
        self.assertEqual(aggregate["full_frame_max_status_sample_gap"], 124)
        self.assertEqual(aggregate["spidev_buffer_size"], 4092)
        self.assertTrue(aggregate["full_frame_write_only_supported"])
        self.assertEqual(
            aggregate["full_frame_transfers"],
            aggregate["full_frame_status_transfers"]
            + aggregate["full_frame_write_only_transfers"],
        )
        self.assertEqual(
            aggregate["full_frame_status_transfers"],
            aggregate["full_frame_status_samples"]
            + aggregate["full_frame_status_sample_misses"],
        )
        controller.devices[3].get_stats = lambda: {
            "spidev_buffer_size": None,
            "full_frame_write_only_supported": False,
        }
        degraded = controller.get_stats()["aggregate"]
        self.assertEqual(degraded["spidev_buffer_size"], 4092)
        self.assertFalse(degraded["full_frame_write_only_supported"])

    def test_wall_frame_sequence_stays_shared_after_one_receiver_failure(self):
        controller = _controller()
        controller._display_ownership_known = True
        controller.devices[2].fail_wall_frame_sequences.add(1)
        frame = np.zeros((33 * 138, 3), dtype=np.uint8)

        controller.set_all_pixels(frame)
        controller.set_all_pixels(frame)
        controller.set_all_pixels(frame)

        for device in controller.devices:
            self.assertEqual(device.wall_frame_sequences, [0, 1, 2])
        self.assertEqual(len(controller.devices[2].frames), 2)
        self.assertEqual(controller._logical_wall_frame_sequence, 3)

    def test_dense_partial_fallbacks_share_global_sequence_across_subsets(self):
        controller = _controller()
        controller._display_ownership_known = True
        frame = np.zeros((33 * 138, 3), dtype=np.uint8)

        controller.set_frame(frame, dirty_ranges=((0, 8 * 138),))
        controller.set_frame(frame, dirty_ranges=((8 * 138, 16 * 138),))
        controller.set_all_pixels(frame)

        self.assertEqual(controller.devices[0].wall_frame_sequences, [0, 2])
        self.assertEqual(controller.devices[1].wall_frame_sequences, [1, 2])
        for device in controller.devices[2:]:
            self.assertEqual(device.wall_frame_sequences, [2])
        self.assertEqual(controller._logical_wall_frame_sequence, 3)

    def test_startup_observability_verifies_exact_fifth_receiver_topology(self):
        controller = _observability_controller()
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            controller._initialize_receiver_identity_observability()

        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(controller.devices[4].logical_device_id, 4)
        self.assertEqual(controller.devices[4].configures, 1)
        self.assertEqual(controller.devices[4].lane_masks, [0xFF])
        self.assertEqual(
            controller.devices[4].queries,
            2 * SPI_RESPONSE_QUEUE_DEPTH
            + TRANSPORT_ENVELOPE_NEGOTIATION_OBSERVATIONS
            + 1,
        )

    def test_startup_drains_queued_status_before_explicit_config_and_validation(self):
        controller = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        controller.receiver_strip_counts = WIDTHS
        controller.receiver_global_strip_offsets = OFFSETS
        controller.receiver_lane_masks = (0xFF, 0xFF, 0xFF, 0xFF, 0xFF)
        controller.devices = [
            _QueuedObservableDevice(logical_id, width, offset, lane_mask)
            for logical_id, (width, offset, lane_mask) in enumerate(zip(
                controller.receiver_strip_counts,
                controller.receiver_global_strip_offsets,
                controller.receiver_lane_masks,
            ))
        ]
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            controller._initialize_receiver_identity_observability()

        self.assertEqual(stderr.getvalue(), "")
        for logical_id, device in enumerate(controller.devices):
            self.assertEqual(device.extended_configurations, [True])
            self.assertEqual(device._configured_logical_id, logical_id)
            self.assertEqual(
                device.queries,
                2 * SPI_RESPONSE_QUEUE_DEPTH
                + TRANSPORT_ENVELOPE_NEGOTIATION_OBSERVATIONS
                + 1,
            )
        self.assertEqual(controller.devices[4].lane_masks, [0xFF])
        self.assertEqual(
            controller.devices[4].get_stats(),
            {
                "receiver_status_version": 3,
                "receiver_logical_device": 4,
                "receiver_active_strips": 1,
                "receiver_global_strip_offset": 32,
                "receiver_lane_mask": 0xFF,
            },
        )

    def test_startup_observability_surfaces_exact_topology_mismatch_and_continues(self):
        controller = _observability_controller(
            receiver_4_status={
                "receiver_logical_device": 3,
                "receiver_active_strips": 8,
                "receiver_global_strip_offset": 24,
                "receiver_lane_mask": 0x01,
            }
        )
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            controller._initialize_receiver_identity_observability()

        error = stderr.getvalue()
        self.assertIn("Receiver 4 topology observability unavailable", error)
        self.assertIn("receiver_logical_device=3, expected 4", error)
        self.assertIn("receiver_active_strips=8, expected 1", error)
        self.assertIn("receiver_global_strip_offset=24, expected 32", error)
        self.assertIn("receiver_lane_mask=1, expected 255", error)
        self.assertIn("continuing with ordinary host streaming", error)
        self.assertEqual(controller.devices[4].configures, 1)
        self.assertEqual(controller.devices[4].lane_masks, [0xFF])

    def test_startup_observability_keeps_legacy_status_streaming_best_effort(self):
        controller = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        controller.receiver_strip_counts = (8,)
        controller.receiver_global_strip_offsets = (0,)
        controller.receiver_lane_masks = (0xFF,)
        legacy = _ObservableDevice(
            0,
            {
                "receiver_status_version": 2,
                "receiver_logical_device": None,
            },
        )
        controller.devices = [legacy]
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            controller._initialize_receiver_identity_observability()

        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(legacy.configures, 1)
        self.assertEqual(legacy.lane_masks, [0xFF])
        self.assertEqual(
            legacy.queries,
            SPI_RESPONSE_QUEUE_DEPTH
            + TRANSPORT_ENVELOPE_NEGOTIATION_OBSERVATIONS,
        )

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
            [0xFF, 0xFF, 0xFF, 0xFF, 0xFF],
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

        expected = ((0, 7), (8, 15), (23, 16), (31, 24), (32, 32))
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
            controller.devices[3].pixels,
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
        partial_frame, ranges = controller.devices[3].partial[-1]
        self.assertEqual(ranges, ((7 * 138 + 5, 7 * 138 + 9),))
        self.assertEqual(int(partial_frame[7 * 138 + 5, 0]), 24)
        self.assertEqual(
            controller._receiver_local_dirty_ranges(
                2,
                ((16 * 138 + 136, 17 * 138 + 2),),
            ),
            [(6 * 138, 6 * 138 + 2), (8 * 138 - 2, 8 * 138)],
        )
        self.assertEqual(
            controller._receiver_local_dirty_ranges(
                3,
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
        self.assertEqual(int(full_pixels[0, 0]), 23)
        self.assertEqual(int(full_pixels[-1, 0]), 16)

        delta = controller._local_overlay_patches(
            overlay,
            receiver_id=2,
            update_kind=OVERLAY_UPDATE_DELTA,
            dirty_ranges=((16 * 138 + 5, 16 * 138 + 9),),
        )
        self.assertEqual(len(delta), 1)
        self.assertEqual(delta[0][0], 7 * 138 + 5)
        self.assertTrue(np.all(delta[0][1][:, 0] == 16))

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
