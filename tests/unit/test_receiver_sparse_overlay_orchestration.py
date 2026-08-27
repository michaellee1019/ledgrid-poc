import sys
import threading
import types
import unittest

import numpy as np

if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from drivers.multi_device import MultiDeviceLEDController
from drivers.spi_controller import (
    CAPABILITY_EXPLICIT_BASE_OWNERSHIP,
    CAPABILITY_PRESENTATION_CONTEXT_V1,
    CAPABILITY_SPARSE_OVERLAY_V1,
    CAPABILITY_SPARSE_OVERLAY_BATCH_V1,
    CAPABILITY_STATIC_LOCAL_BACKGROUND,
    CAPABILITY_STATUS_V3,
    OVERLAY_UPDATE_DELTA,
    OVERLAY_UPDATE_FULL_SNAPSHOT,
)


ALL_CAPABILITIES = (
    CAPABILITY_STATIC_LOCAL_BACKGROUND
    | CAPABILITY_PRESENTATION_CONTEXT_V1
    | CAPABILITY_STATUS_V3
    | CAPABILITY_EXPLICIT_BASE_OWNERSHIP
    | CAPABILITY_SPARSE_OVERLAY_V1
    | CAPABILITY_SPARSE_OVERLAY_BATCH_V1
)
SESSION = bytes(range(16))


class Device:
    def __init__(self, logical_device, *, capabilities=ALL_CAPABILITIES):
        self.logical_device = logical_device
        self.capabilities = capabilities
        self.calls = []
        self.base_mode = 1
        self.foreground_state = 0
        self.committed_generation = 0
        self.staged_generation = 0
        self.overlay_session = None
        self.scene_revision = 1
        self.scene_epoch = 1
        self.base_revision = 1
        self.present_at_scene_time_us = 0
        self.fail = set()
        self.reject = set()
        self.schedule_commit = False

    def _status(self, operation=None):
        if operation in self.fail:
            raise OSError(f"{operation} failed")
        overlay_result = 10 if operation in self.reject else 1
        return {
            "receiver_status_version": 4,
            "receiver_capabilities": self.capabilities,
            "receiver_logical_device": self.logical_device,
            "receiver_base_mode": self.base_mode,
            "receiver_foreground_state": self.foreground_state,
            "receiver_last_result": 1,
            "receiver_overlay_operation_result": overlay_result,
            "receiver_overlay_committed_generation": self.committed_generation,
            "receiver_overlay_staged_generation": self.staged_generation,
            "receiver_overlay_session_id": self.overlay_session,
            "receiver_foreground_scene_revision": self.scene_revision,
            "receiver_foreground_scene_epoch": self.scene_epoch,
            "receiver_foreground_base_revision": self.base_revision,
            "receiver_foreground_present_at_scene_time_us": (
                self.present_at_scene_time_us
            ),
        }

    def query_receiver_status(self):
        self.calls.append(("status",))
        return self._status()

    def begin_controller_session(self, **fields):
        self.calls.append(("session", fields))
        self.overlay_session = fields["controller_session_id"].hex()
        return self._status("session")

    def begin_overlay(self, **fields):
        self.calls.append(("begin", fields))
        self.foreground_state = 1
        self.scene_revision = fields["scene_revision"]
        self.scene_epoch = fields["scene_epoch"]
        self.base_revision = fields["base_revision"]
        return self._status("begin")

    def send_overlay_patches(self, *, patches, **fields):
        materialized = [
            (start, np.asarray(pixels).copy()) for start, pixels in patches
        ]
        self.calls.append(("patches", fields, materialized))
        return [self._status("patch") for _ in materialized]

    def commit_overlay(self, **fields):
        self.calls.append(("commit", fields))
        self.present_at_scene_time_us = fields["present_at_scene_time_us"]
        status = self._status("commit")
        if status["receiver_overlay_operation_result"] in (1, 2):
            if self.schedule_commit:
                self.staged_generation = fields["generation"]
                self.foreground_state = 1
            else:
                self.committed_generation = fields["generation"]
                self.staged_generation = 0
                self.foreground_state = 2
        return self._status()

    def clear_overlay(self, **fields):
        self.calls.append(("clear", fields))
        status = self._status("clear")
        generation = fields["generation"]
        if (
            status["receiver_overlay_operation_result"] in (1, 2)
            and generation == self.committed_generation
            and self.foreground_state == 2
        ):
            status["receiver_overlay_operation_result"] = 10
        if status["receiver_overlay_operation_result"] in (1, 2):
            self.foreground_state = 0
            self.staged_generation = 0
            self.committed_generation = generation
        return status

    def renew_overlay(self, **fields):
        self.calls.append(("renew", fields))
        return self._status("renew")

    def set_all_pixels(self, colors):
        self.calls.append(("set_all", len(colors)))
        if "set_all" in self.fail:
            raise OSError("set_all failed")
        self.base_mode = 2
        self.foreground_state = 0


def controller(devices):
    item = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
    item.devices = devices
    item.num_devices = len(devices)
    item.strips_per_device = 8
    item.leds_per_strip = 138
    item.leds_per_device = 1104
    if len(devices) == 5:
        item.receiver_strip_counts = (8, 8, 8, 8, 1)
        item.receiver_global_strip_offsets = (0, 8, 16, 24, 32)
        item.receiver_lane_masks = (0xFF, 0xFF, 0xFF, 0xFF, 0xFF)
        item.receiver_pixel_counts = (1104, 1104, 1104, 1104, 138)
        item.receiver_pixel_offsets = (0, 1104, 2208, 3312, 4416)
        item.reverse_host_strips_by_logical_receiver = (
            False, False, True, True, False,
        )
        item.reverse_native_strips_by_logical_receiver = (
            False, False, True, True, False,
        )
        item.strip_count = 33
        item.total_leds = 4554
        item._devices_by_bus = {0: [0, 1], 1: [2, 3, 4]}
        item.device_map = [(0, 0), (0, 1), (1, 1), (1, 0), (1, 2)]
    else:
        item.strip_count = len(devices) * item.strips_per_device
        item.total_leds = item.strip_count * item.leds_per_strip
        item._devices_by_bus = {0: list(range(len(devices)))}
    item._executor = None
    item._logical_frames_sent = 0
    item._transport_lock = threading.RLock()
    item._local_background_active = True
    item._display_ownership_known = True
    item._local_background_context_digest = "context"
    item._local_background_parameters = {"component_id": 1}
    item._local_background_status = {"state": "active"}
    return item


def transparent_wall(strip_count=32):
    return np.zeros((strip_count * 138, 4), dtype=np.uint8)


class ReceiverSparseOverlayOrchestrationTests(unittest.TestCase):
    def test_full_snapshot_is_two_canonical_batch_spans_per_receiver(self):
        devices = [Device(index) for index in range(4)]
        item = controller(devices)
        pixels = transparent_wall()
        pixels[:, :] = (2, 3, 4, 8)

        self.assertTrue(item.publish_sparse_overlay(
            pixels,
            controller_session_id=SESSION,
            generation=1,
            prior_generation=0,
            scene_revision=7,
            scene_epoch=11,
            base_revision=13,
            lease_ms=3000,
            present_at_scene_time_us=17,
            full_snapshot=True,
        ))

        for device in devices:
            begin = next(call[1] for call in device.calls if call[0] == "begin")
            self.assertEqual(begin["update_kind"], OVERLAY_UPDATE_FULL_SNAPSHOT)
            self.assertEqual(begin["expected_patches"], 2)
            patches = next(call[2] for call in device.calls if call[0] == "patches")
            self.assertEqual([(start, len(data)) for start, data in patches], [
                (0, 1015), (1015, 89),
            ])
            self.assertEqual(device.committed_generation, 1)
        self.assertEqual(
            item._local_background_status["foreground_patch_counts"], [2, 2, 2, 2]
        )

    def test_fifth_receiver_snapshot_has_one_lane_then_transparent_padding(self):
        devices = [Device(index) for index in range(5)]
        item = controller(devices)
        pixels = transparent_wall(33)
        pixels[32 * 138:33 * 138] = (2, 3, 4, 8)

        self.assertTrue(item.publish_sparse_overlay(
            pixels,
            controller_session_id=SESSION,
            generation=1,
            prior_generation=0,
            scene_revision=7,
            scene_epoch=11,
            base_revision=13,
            lease_ms=3000,
            present_at_scene_time_us=17,
            full_snapshot=True,
        ))

        fifth_patches = next(
            call[2] for call in devices[4].calls if call[0] == "patches"
        )
        reassembled = np.zeros((1104, 4), dtype=np.uint8)
        for start, data in fifth_patches:
            reassembled[start:start + len(data)] = data
        self.assertTrue(np.all(reassembled[:138] == (2, 3, 4, 8)))
        self.assertFalse(np.any(reassembled[138:]))
        self.assertEqual(
            item._local_background_status["foreground_patch_counts"],
            [2, 2, 2, 2, 2],
        )

    def test_boundary_delta_slices_two_boards_and_noop_commits_the_rest(self):
        devices = [Device(index) for index in range(4)]
        item = controller(devices)
        item._sparse_overlay_session_id = SESSION
        item._sparse_overlay_generation = 1
        item._sparse_overlay_snapshot_digest = b"prior"
        for device in devices:
            device.overlay_session = SESSION.hex()
            device.committed_generation = 1
            device.foreground_state = 2
        pixels = transparent_wall()
        pixels[1103:1105] = (0, 0, 9, 9)

        self.assertTrue(item.publish_sparse_overlay(
            pixels,
            controller_session_id=SESSION,
            generation=2,
            prior_generation=1,
            scene_revision=8,
            scene_epoch=11,
            base_revision=13,
            lease_ms=3000,
            present_at_scene_time_us=19,
            dirty_ranges=((1103, 1105),),
        ))

        expected = [[(1103, 1)], [(0, 1)], [], []]
        for device, expected_patches in zip(devices, expected):
            begin = next(call[1] for call in device.calls if call[0] == "begin")
            self.assertEqual(begin["update_kind"], OVERLAY_UPDATE_DELTA)
            self.assertEqual(begin["expected_patches"], len(expected_patches))
            patches = next(call[2] for call in device.calls if call[0] == "patches")
            self.assertEqual(
                [(start, len(data)) for start, data in patches], expected_patches
            )
            self.assertEqual(device.committed_generation, 2)

    def test_preflight_rejects_missing_capability_without_mutation(self):
        for missing in (
            CAPABILITY_SPARSE_OVERLAY_V1,
            CAPABILITY_SPARSE_OVERLAY_BATCH_V1,
        ):
            with self.subTest(missing=f"0x{missing:08x}"):
                devices = [Device(index) for index in range(4)]
                devices[2].capabilities &= ~missing
                item = controller(devices)

                self.assertFalse(item.publish_sparse_overlay(
                    transparent_wall(),
                    controller_session_id=SESSION,
                    generation=1,
                    prior_generation=0,
                    scene_revision=1,
                    scene_epoch=1,
                    base_revision=1,
                    lease_ms=3000,
                    present_at_scene_time_us=1,
                    full_snapshot=True,
                ))
                self.assertFalse(any(
                    call[0] != "status" for device in devices for call in device.calls
                ))
                self.assertEqual(item._local_background_status["state"], "degraded")

    def test_rejected_patch_clears_every_board_and_never_claims_success(self):
        devices = [Device(index) for index in range(4)]
        devices[2].reject.add("patch")
        item = controller(devices)

        self.assertFalse(item.publish_sparse_overlay(
            transparent_wall(),
            controller_session_id=SESSION,
            generation=1,
            prior_generation=0,
            scene_revision=1,
            scene_epoch=1,
            base_revision=1,
            lease_ms=3000,
            present_at_scene_time_us=1,
            full_snapshot=True,
        ))
        self.assertTrue(all(any(call[0] == "clear" for call in device.calls)
                            for device in devices))
        self.assertEqual(
            item._local_background_status["state"], "foreground_cleared"
        )
        self.assertEqual(
            item._local_background_status["operation"],
            "foreground_publish_failed",
        )
        self.assertEqual(item._sparse_overlay_session_id, SESSION)
        self.assertEqual(item._sparse_overlay_generation, 2)

    def test_invalid_rgba_and_unknown_delta_ranges_fail_before_io(self):
        devices = [Device(index) for index in range(4)]
        item = controller(devices)
        item._sparse_overlay_session_id = SESSION
        item._sparse_overlay_generation = 4
        item._sparse_overlay_snapshot_digest = b"prior"
        invalid = transparent_wall()
        invalid[0] = (2, 0, 0, 1)
        with self.assertRaisesRegex(ValueError, "RGB must not exceed alpha"):
            item.publish_sparse_overlay(
                invalid,
                controller_session_id=SESSION,
                generation=1,
                prior_generation=0,
                scene_revision=1,
                scene_epoch=1,
                base_revision=1,
                lease_ms=3000,
                present_at_scene_time_us=1,
                full_snapshot=True,
            )
        with self.assertRaisesRegex(ValueError, "requires dirty_ranges"):
            item.publish_sparse_overlay(
                transparent_wall(),
                controller_session_id=SESSION,
                generation=1,
                prior_generation=0,
                scene_revision=1,
                scene_epoch=1,
                base_revision=1,
                lease_ms=3000,
                present_at_scene_time_us=1,
            )
        self.assertFalse(any(device.calls for device in devices))

    def test_renew_and_clear_require_all_board_acknowledgements(self):
        devices = [Device(index) for index in range(4)]
        item = controller(devices)
        item._sparse_overlay_session_id = SESSION
        item._sparse_overlay_generation = 4
        item._sparse_overlay_snapshot_digest = b"prior"
        for device in devices:
            device.foreground_state = 2
            device.committed_generation = 4
            device.overlay_session = SESSION.hex()

        self.assertTrue(item.renew_sparse_overlay(
            controller_session_id=SESSION, generation=4, lease_ms=3000
        ))
        self.assertTrue(item.clear_sparse_overlay(
            controller_session_id=SESSION, generation=5, scene_revision=9
        ))
        self.assertTrue(all(device.foreground_state == 0 for device in devices))

    def test_renew_failure_compensates_partial_deadline_extension_with_clear(self):
        devices = [Device(index) for index in range(4)]
        item = controller(devices)
        item._sparse_overlay_session_id = SESSION
        item._sparse_overlay_generation = 4
        item._sparse_overlay_snapshot_digest = b"prior"
        for device in devices:
            device.foreground_state = 2
            device.committed_generation = 4
            device.overlay_session = SESSION.hex()
        devices[2].fail.add("renew")

        self.assertFalse(item.renew_sparse_overlay(
            controller_session_id=SESSION, generation=4, lease_ms=3000
        ))

        self.assertTrue(all(
            any(call[0] == "clear" and call[1]["generation"] == 5
                for call in device.calls)
            for device in devices
        ))
        self.assertTrue(all(device.foreground_state == 0 for device in devices))
        self.assertEqual(item._sparse_overlay_generation, 5)
        self.assertEqual(
            item._local_background_status["state"], "foreground_cleared"
        )

    def test_delta_after_expiry_requires_repair_without_mutating_receivers(self):
        devices = [Device(index) for index in range(4)]
        item = controller(devices)
        item._sparse_overlay_session_id = SESSION
        item._sparse_overlay_generation = 4
        for device in devices:
            device.foreground_state = 0
            device.committed_generation = 4
            device.overlay_session = SESSION.hex()

        self.assertFalse(item.publish_sparse_overlay(
            transparent_wall(),
            controller_session_id=SESSION,
            generation=5,
            prior_generation=4,
            scene_revision=1,
            scene_epoch=1,
            base_revision=1,
            lease_ms=3000,
            present_at_scene_time_us=1,
            dirty_ranges=((0, 1),),
        ))

        self.assertTrue(all(
            all(call[0] == "status" for call in device.calls)
            for device in devices
        ))
        self.assertEqual(item._sparse_overlay_session_id, SESSION)
        self.assertEqual(item._sparse_overlay_generation, 4)
        self.assertEqual(
            item._local_background_status["state"],
            "foreground_repair_required",
        )

        self.assertTrue(item.publish_sparse_overlay(
            transparent_wall(),
            controller_session_id=SESSION,
            generation=5,
            prior_generation=4,
            scene_revision=1,
            scene_epoch=1,
            base_revision=1,
            lease_ms=3000,
            present_at_scene_time_us=2,
            full_snapshot=True,
        ))
        self.assertTrue(all(device.committed_generation == 5 for device in devices))

    def test_restart_requires_full_snapshot_before_new_session_delta(self):
        devices = [Device(index) for index in range(4)]
        item = controller(devices)
        with self.assertRaisesRegex(ValueError, "new controller session"):
            item.publish_sparse_overlay(
                transparent_wall(),
                controller_session_id=SESSION,
                generation=1,
                prior_generation=0,
                scene_revision=1,
                scene_epoch=1,
                base_revision=1,
                lease_ms=3000,
                present_at_scene_time_us=1,
                dirty_ranges=((0, 1),),
            )
        self.assertFalse(any(device.calls for device in devices))

    def test_partial_commit_and_failed_compensation_are_explicitly_degraded(self):
        devices = [Device(index) for index in range(4)]
        devices[2].reject.add("commit")
        devices[1].fail.add("clear")
        item = controller(devices)

        self.assertFalse(item.publish_sparse_overlay(
            transparent_wall(),
            controller_session_id=SESSION,
            generation=1,
            prior_generation=0,
            scene_revision=1,
            scene_epoch=1,
            base_revision=1,
            lease_ms=3000,
            present_at_scene_time_us=1,
            full_snapshot=True,
        ))
        self.assertEqual(item._local_background_status["state"], "degraded")
        self.assertEqual(
            item._local_background_status["cleanup_errors"][0]["logical_device"],
            1,
        )
        self.assertIsNone(item._sparse_overlay_session_id)

    def test_partial_commit_compensation_uses_a_newer_generation_everywhere(self):
        devices = [Device(index) for index in range(4)]
        devices[2].reject.add("commit")
        item = controller(devices)

        self.assertFalse(item.publish_sparse_overlay(
            transparent_wall(),
            controller_session_id=SESSION,
            generation=7,
            prior_generation=0,
            scene_revision=1,
            scene_epoch=1,
            base_revision=1,
            lease_ms=3000,
            present_at_scene_time_us=1,
            full_snapshot=True,
        ))

        for device in devices:
            clear = next(
                call[1] for call in reversed(device.calls) if call[0] == "clear"
            )
            self.assertEqual(clear["generation"], 8)
            self.assertEqual(device.foreground_state, 0)
        self.assertEqual(
            item._local_background_status["state"], "foreground_cleared"
        )
        self.assertEqual(item._sparse_overlay_session_id, SESSION)
        self.assertEqual(item._sparse_overlay_generation, 8)

        devices[2].reject.remove("commit")
        self.assertTrue(item.publish_sparse_overlay(
            transparent_wall(),
            controller_session_id=SESSION,
            generation=9,
            prior_generation=8,
            scene_revision=1,
            scene_epoch=1,
            base_revision=1,
            lease_ms=3000,
            present_at_scene_time_us=2,
            full_snapshot=True,
        ))

    def test_scalars_ranges_and_compensation_headroom_validate_before_io(self):
        invalid_cases = (
            {"generation": 0},
            {"generation": 0xFFFFFFFFFFFFFFFF},
            {"scene_epoch": -1},
            {"dirty_ranges": ((3, 3),), "full_snapshot": False},
            {"dirty_ranges": ((5, 7), (4, 6)), "full_snapshot": False},
            {"dirty_ranges": ((0.5, 2),), "full_snapshot": False},
        )
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides):
                devices = [Device(index) for index in range(4)]
                item = controller(devices)
                fields = {
                    "controller_session_id": SESSION,
                    "generation": 1,
                    "prior_generation": 0,
                    "scene_revision": 1,
                    "scene_epoch": 1,
                    "base_revision": 1,
                    "lease_ms": 3000,
                    "present_at_scene_time_us": 1,
                    "full_snapshot": True,
                }
                fields.update(overrides)
                with self.assertRaises((TypeError, ValueError)):
                    item.publish_sparse_overlay(transparent_wall(), **fields)
                self.assertFalse(any(device.calls for device in devices))

    def test_complete_host_frame_is_the_universal_takeover_and_authority_reset(self):
        devices = [Device(index) for index in range(4)]
        item = controller(devices)
        item._sparse_overlay_session_id = SESSION
        item._sparse_overlay_generation = 4
        item._sparse_overlay_snapshot_digest = b"snapshot"
        item._local_background_active = False

        item.set_all_pixels(np.zeros((4416, 3), dtype=np.uint8))

        self.assertTrue(all(device.base_mode == 2 for device in devices))
        self.assertTrue(all(device.foreground_state == 0 for device in devices))
        self.assertIsNone(item._sparse_overlay_session_id)
        self.assertEqual(item._sparse_overlay_generation, 0)
        self.assertIsNone(item._sparse_overlay_snapshot_digest)
        self.assertTrue(all(
            sum(call[0] == "status" for call in device.calls) >= 2
            for device in devices
        ))

    def test_future_commit_is_healthy_only_when_every_receiver_is_scheduled(self):
        devices = [Device(index) for index in range(4)]
        for device in devices:
            device.schedule_commit = True
        item = controller(devices)

        self.assertTrue(item.publish_sparse_overlay(
            transparent_wall(),
            controller_session_id=SESSION,
            generation=1,
            prior_generation=0,
            scene_revision=1,
            scene_epoch=1,
            base_revision=1,
            lease_ms=3000,
            present_at_scene_time_us=1_000_000,
            full_snapshot=True,
        ))
        self.assertEqual(item._local_background_status["state"], "scheduled")
        self.assertEqual(
            item._local_background_status["operation"], "foreground_scheduled"
        )

        mixed_devices = [Device(index) for index in range(4)]
        mixed_devices[0].schedule_commit = True
        mixed = controller(mixed_devices)
        self.assertFalse(mixed.publish_sparse_overlay(
            transparent_wall(),
            controller_session_id=SESSION,
            generation=1,
            prior_generation=0,
            scene_revision=1,
            scene_epoch=1,
            base_revision=1,
            lease_ms=3000,
            present_at_scene_time_us=1_000_000,
            full_snapshot=True,
        ))
        self.assertEqual(
            mixed._local_background_status["operation"],
            "foreground_publish_failed",
        )


if __name__ == "__main__":
    unittest.main()
