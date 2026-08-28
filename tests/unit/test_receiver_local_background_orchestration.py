import contextlib
import io
import sys
import threading
import types
import unittest

if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from animation.core.plant_awareness import PlantModifierState
from animation.core.presentation_contracts import resolve_vibe
from animation.core.receiver_presentation import ReceiverPresentationContext
from drivers.multi_device import MultiDeviceLEDController
from drivers.spi_controller import (
    CAPABILITY_ALIGNED_ENVELOPE_V1,
    CAPABILITY_EXPLICIT_BASE_OWNERSHIP,
    CAPABILITY_PRESENTATION_CONTEXT_V1,
    CAPABILITY_STATIC_LOCAL_BACKGROUND,
    CAPABILITY_STATUS_V3,
)

ALL_LOCAL_CAPABILITIES = (
    CAPABILITY_ALIGNED_ENVELOPE_V1
    | CAPABILITY_STATIC_LOCAL_BACKGROUND
    | CAPABILITY_PRESENTATION_CONTEXT_V1
    | CAPABILITY_STATUS_V3
    | CAPABILITY_EXPLICIT_BASE_OWNERSHIP
)


def context(*, vibe_revision=7, plant_revision=9, scene_epoch=11, scene_revision=5):
    return ReceiverPresentationContext(
        controller_session_id=bytes(range(16)),
        scene_revision=scene_revision,
        scene_epoch=scene_epoch,
        present_at_scene_time_us=1000,
        vibe=resolve_vibe("vivid", revision=vibe_revision),
        plant_modifiers=PlantModifierState.from_payload({
            "active": ["illuminate"], "strengths": {"illuminate": 0.5}
        }),
        plant_revision=plant_revision,
    )


class Device:
    def __init__(self, *, capable=True, logical_device=None, status_version=3):
        self.calls = []
        self.capabilities = ALL_LOCAL_CAPABILITIES if capable else 0
        self.last_result = 0
        self.base_mode = 0
        self.active_context_digest = None
        self.vibe_revision = None
        self.vibe_digest = None
        self.plant_revision = None
        self.plant_digest = None
        self.fail = set()
        self.parameter_calls = 0
        self.fail_parameter_calls = set()
        self.logical_device = logical_device
        self.component_id = 0
        self.cadence = 0
        self.offset = 0
        self.seed = 0
        self.epoch = 0
        self.scene_revision = 0
        self.active_session_id = None
        self.logical_device_id = logical_device
        self.status_version = status_version
        self.status_responses = 0

    def _result(self, operation):
        if operation in self.fail:
            raise OSError(f"{operation} failed")
        self.last_result = 1
        return self.query_receiver_status(record=False)

    def query_receiver_status(self, record=True):
        if record:
            if "status" in self.fail:
                raise OSError("status failed")
            self.calls.append(("status",))
            self.status_responses += 1
        return {
            "receiver_status_seen": True,
            "receiver_status_version": self.status_version,
            "receiver_capabilities": self.capabilities,
            "receiver_last_result": self.last_result,
            "receiver_base_mode": self.base_mode,
            "receiver_active_context_digest": self.active_context_digest,
            "receiver_vibe_revision": self.vibe_revision,
            "receiver_vibe_digest": self.vibe_digest,
            "receiver_plant_modifier_revision": self.plant_revision,
            "receiver_plant_modifier_digest": self.plant_digest,
            "receiver_component_id": self.component_id,
            "receiver_declared_cadence_hz": self.cadence,
            "receiver_global_strip_offset": self.offset,
            "receiver_common_seed": self.seed,
            "receiver_scene_epoch": self.epoch,
            "receiver_active_scene_revision": self.scene_revision,
            "receiver_active_session_id": self.active_session_id,
            "receiver_logical_device": self.logical_device,
        }

    def configure(self):
        extended = self.status_version >= 3
        self.calls.append(("configure", self.logical_device_id, extended))
        if extended:
            self.logical_device = self.logical_device_id

    def begin_presentation_context(self, value):
        self.calls.append(("context_begin", value.context_digest.hex()))
        return self._result("context_begin")

    def set_presentation_context(self, value):
        self.calls.append(("context_set", value.context_digest.hex()))
        return self._result("context_set")

    def commit_presentation_context(self, value, **_timing):
        self.calls.append(("context_commit", value.context_digest.hex()))
        status = self._result("context_commit")
        self.active_context_digest = value.context_digest.hex()
        self.vibe_revision = value.vibe.state.revision
        self.vibe_digest = value.vibe.state.resolved_profile_digest
        self.plant_revision = value.plant_revision
        self.plant_digest = value.plant_digest.hex()
        self.scene_revision = value.scene_revision
        self.active_session_id = value.controller_session_id.hex()
        return {**status,
                "receiver_active_context_digest": self.active_context_digest,
                "receiver_vibe_revision": self.vibe_revision,
                "receiver_vibe_digest": self.vibe_digest,
                "receiver_plant_modifier_revision": self.plant_revision,
                "receiver_plant_modifier_digest": self.plant_digest,
                "receiver_active_scene_revision": self.scene_revision,
                "receiver_active_session_id": self.active_session_id}

    def start_local_background(self, **parameters):
        self.calls.append(("start", dict(parameters)))
        status = self._result("start")
        self.base_mode = 1
        self.component_id = parameters["component_id"]
        self.cadence = parameters["preferred_cadence_hz"]
        self.offset = parameters["global_strip_offset"]
        self.seed = parameters["common_seed"]
        self.epoch = parameters["scene_epoch"]
        return {**status, "receiver_base_mode": 1}

    def stop_local_background(self):
        self.calls.append(("stop",))
        status = self._result("stop")
        self.base_mode = 0
        return {**status, "receiver_base_mode": 0}

    def update_local_background_params(self, **parameters):
        self.calls.append(("params", dict(parameters)))
        self.parameter_calls += 1
        if self.parameter_calls in self.fail_parameter_calls:
            raise OSError("params failed")
        self.cadence = parameters["preferred_cadence_hz"]
        self.offset = parameters["global_strip_offset"]
        self.seed = parameters["common_seed"]
        return self._result("params")

    def set_all_pixels(self, colors, *, wall_frame_sequence=None):
        self.calls.append(("set_all", len(colors)))
        if "set_all" in self.fail:
            raise OSError("set_all failed")
        self.base_mode = 2

    def get_stats(self):
        return {
            **self.query_receiver_status(record=False),
            "receiver_status_responses": self.status_responses,
        }


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
        item.device_map = [(0, index) for index in range(len(devices))]
    item.debug = False
    item.parallel = False
    item._executor = None
    item._logical_frames_sent = 0
    item._transport_lock = threading.RLock()
    item._local_background_active = False
    item._local_background_context_digest = None
    item._local_background_parameters = {}
    item._local_background_status = {"state": "stopped", "operation": "test"}
    item._receiver_status_refresh = {
        "request_id": None, "completed_at": None, "passed": False, "errors": [],
    }
    item._display_ownership_known = False
    for index, device in enumerate(devices):
        if device.logical_device is None:
            device.logical_device = index
    return item


class ReceiverLocalBackgroundOrchestrationTests(unittest.TestCase):
    def test_live_context_update_preserves_base_and_requires_foreground_repair(self):
        devices = [Device(logical_device=index) for index in range(4)]
        item = controller(devices)
        initial = context(vibe_revision=7)
        self.assertTrue(item.start_local_background(initial))
        item._sparse_overlay_session_id = bytes(range(16))
        item._sparse_overlay_generation = 9
        item._sparse_overlay_snapshot_digest = b"x" * 32

        changed = context(vibe_revision=8, scene_revision=6)
        self.assertTrue(item.update_presentation_context(changed))

        self.assertTrue(item._local_background_active)
        self.assertEqual([device.base_mode for device in devices], [1] * 4)
        self.assertEqual(
            [[call[0] for call in device.calls].count("start") for device in devices],
            [1] * 4,
        )
        self.assertFalse(any(
            call[0] == "stop" for device in devices for call in device.calls
        ))
        self.assertEqual(item._local_background_status["state"], "foreground_repair_required")
        self.assertIsNone(item._sparse_overlay_session_id)
        self.assertEqual(item._sparse_overlay_generation, 0)
        self.assertEqual(
            item.sparse_overlay_authority()["controller_session_id"], None
        )

    def test_failed_live_context_update_marks_degraded_without_base_restart(self):
        devices = [Device(logical_device=index) for index in range(4)]
        item = controller(devices)
        self.assertTrue(item.start_local_background(context(vibe_revision=7)))
        devices[2].fail.add("context_set")

        self.assertFalse(item.update_presentation_context(
            context(vibe_revision=8, scene_revision=6)
        ))

        self.assertEqual(item._local_background_status["state"], "degraded")
        self.assertTrue(item._local_background_status["receivers_touched"])
        self.assertEqual([device.base_mode for device in devices], [1] * 4)
        self.assertIsNone(item._sparse_overlay_session_id)

    def test_explicit_status_refresh_drains_every_board_and_records_failures(self):
        devices = [Device(logical_device=index) for index in range(4)]
        item = controller(devices)

        result = item.refresh_receiver_status("fresh-1")
        self.assertTrue(result["passed"])
        self.assertEqual(result["request_id"], "fresh-1")
        self.assertIsInstance(result["completed_at"], float)
        self.assertEqual([device.status_responses for device in devices], [3] * 4)
        self.assertEqual(
            item.get_stats()["aggregate"]["receiver_status_refresh"]["request_id"],
            "fresh-1",
        )

        devices[2].fail.add("status")
        failed = item.refresh_receiver_status("fresh-2")
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["errors"][0]["logical_device"], 2)

        for invalid in (None, "", "x" * 129):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                item.refresh_receiver_status(invalid)

    def test_initial_observability_provisions_every_v3_identity_after_construction(self):
        devices = [Device(logical_device=0) for _ in range(4)]
        item = controller(devices)
        item._initialize_receiver_identity_observability()
        self.assertEqual([device.logical_device for device in devices], [0, 1, 2, 3])
        self.assertTrue(all(
            ("configure", index, True) in device.calls
            for index, device in enumerate(devices)
        ))

    def test_initial_observability_keeps_v2_legacy_config_and_streaming(self):
        devices = [
            Device(status_version=3), Device(status_version=2),
            Device(status_version=3), Device(status_version=2),
        ]
        item = controller(devices)
        item._initialize_receiver_identity_observability()
        self.assertEqual(
            [next(call[2] for call in device.calls if call[0] == "configure")
             for device in devices],
            [True, False, True, False],
        )
        item.set_all_pixels([(1, 2, 3)] * item.total_leds)
        self.assertTrue(all(any(call[0] == "set_all" for call in device.calls)
                            for device in devices))

    def test_initial_identity_failure_does_not_block_streaming(self):
        devices = [Device() for _ in range(4)]

        def fail_configure():
            raise OSError("identity config failed")

        devices[2].configure = fail_configure
        item = controller(devices)
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            item._initialize_receiver_identity_observability()
        self.assertIn("continuing with ordinary host streaming", stderr.getvalue())
        item.set_all_pixels([(1, 2, 3)] * item.total_leds)
        self.assertTrue(all(any(call[0] == "set_all" for call in device.calls)
                            for device in devices))

    def test_stages_identical_context_then_starts_with_board_offsets(self):
        devices = [Device() for _ in range(4)]
        item = controller(devices)
        presentation = context()
        self.assertTrue(item.start_local_background(
            presentation, preferred_cadence_hz=40, common_seed=0x12345678
        ))
        expected_digest = presentation.context_digest.hex()
        for index, device in enumerate(devices):
            self.assertEqual(
                [call[0] for call in device.calls
                 if call[0] not in ("status", "configure")],
                ["context_begin", "context_set", "context_commit", "start"],
            )
            start = next(call for call in device.calls if call[0] == "start")
            self.assertEqual(start[1]["global_strip_offset"], index * 8)
            self.assertEqual(start[1]["scene_epoch"], 11)
            self.assertEqual(device.active_context_digest, expected_digest)
        self.assertEqual(item._local_background_status["state"], "active")

    def test_fifth_receiver_starts_once_at_its_single_column_offset(self):
        devices = [Device() for _ in range(5)]
        item = controller(devices)
        presentation = context()

        self.assertTrue(item.start_local_background(presentation))

        self.assertEqual(item.receiver_strip_counts, (8, 8, 8, 8, 1))
        self.assertEqual(
            [
                next(call[1]["global_strip_offset"] for call in device.calls
                     if call[0] == "start")
                for device in devices
            ],
            [0, 8, 16, 24, 32],
        )
        self.assertEqual(
            [sum(call[0] == "context_begin" for call in device.calls)
             for device in devices],
            [1, 1, 1, 1, 1],
        )
        self.assertEqual(
            [sum(call[0] == "start" for call in device.calls)
             for device in devices],
            [1, 1, 1, 1, 1],
        )

    def test_missing_capability_starts_no_subset_and_falls_back_every_board(self):
        devices = [Device(), Device(), Device(capable=False), Device()]
        item = controller(devices)
        self.assertFalse(item.start_local_background(context()))
        self.assertFalse(any(call[0] == "start" for device in devices for call in device.calls))
        self.assertFalse(any(("stop",) in device.calls for device in devices))
        self.assertEqual(item._local_background_status["state"], "rejected")
        self.assertIn("lacks", item._local_background_status["start_error"])

    def test_mixed_capability_fleet_refuses_local_mode_but_still_streams(self):
        devices = [Device(), Device(), Device(capable=False), Device()]
        item = controller(devices)
        self.assertFalse(item.start_local_background(context()))
        item.set_all_pixels([(3, 2, 1)] * item.total_leds)
        self.assertTrue(all(any(call[0] == "set_all" for call in device.calls)
                            for device in devices))
        self.assertEqual(item._local_background_status["state"], "host_full_scene")

    def test_unprovisioned_identity_is_configured_before_context_staging(self):
        devices = [Device(logical_device=0) for _ in range(4)]
        self.assertTrue(controller(devices).start_local_background(context()))
        self.assertEqual([device.logical_device for device in devices], [0, 1, 2, 3])
        for device in devices:
            self.assertLess(
                next(index for index, call in enumerate(device.calls)
                     if call[0] == "configure"),
                next(index for index, call in enumerate(device.calls)
                     if call[0] == "context_begin"),
            )

    def test_dirty_first_host_frame_after_restart_forces_complete_takeover(self):
        devices = [Device() for _ in range(4)]
        for device in devices:
            device.base_mode = 1
        item = controller(devices)
        item.set_frame(
            [(1, 2, 3)] * item.total_leds,
            dirty_ranges=((10, 12),),
        )
        self.assertTrue(all(any(call[0] == "set_all" for call in device.calls)
                            for device in devices))
        self.assertFalse(any(call[0] == "params" for device in devices for call in device.calls))
        self.assertEqual(item._local_background_status["state"], "host_full_scene")

    def test_mixed_prior_vibe_digest_is_reconciled_by_authoritative_staging(self):
        devices = [Device() for _ in range(4)]
        for device in devices:
            device.vibe_revision = 3
            device.vibe_digest = "a" * 64
        devices[2].vibe_digest = "b" * 64
        item = controller(devices)
        self.assertTrue(item.start_local_background(context()))
        self.assertTrue(all(any(call[0] == "context_commit" for call in device.calls)
                            for device in devices))
        self.assertEqual(len({device.vibe_digest for device in devices}), 1)

    def test_mixed_prior_plant_revision_is_reconciled_by_authoritative_staging(self):
        devices = [Device() for _ in range(4)]
        for device in devices:
            device.plant_revision = 5
            device.plant_digest = "c" * 64
        devices[3].plant_revision = 6
        item = controller(devices)
        self.assertTrue(item.start_local_background(context()))
        self.assertEqual({device.plant_revision for device in devices}, {9})

    def test_receiver_refusing_context_commit_is_rejected_after_staging(self):
        devices = [Device() for _ in range(4)]
        original_commit = devices[2].commit_presentation_context

        def dishonest_commit(value, **timing):
            status = original_commit(value, **timing)
            devices[2].vibe_digest = "f" * 64
            return status

        devices[2].commit_presentation_context = dishonest_commit
        item = controller(devices)
        self.assertFalse(item.start_local_background(context()))
        self.assertFalse(any(call[0] == "start" for device in devices for call in device.calls))
        self.assertTrue(all(("stop",) in device.calls for device in devices))
        self.assertIn("receiver_vibe_digest", item._local_background_status["start_error"])

    def test_context_partial_failure_never_starts_and_stops_all(self):
        devices = [Device() for _ in range(4)]
        devices[2].fail.add("context_set")
        item = controller(devices)
        self.assertFalse(item.start_local_background(context()))
        self.assertFalse(any(call[0] == "start" for device in devices for call in device.calls))
        self.assertTrue(all(("stop",) in device.calls for device in devices))

    def test_start_ack_failure_stops_even_receivers_not_yet_started(self):
        devices = [Device() for _ in range(4)]
        devices[1].fail.add("start")
        item = controller(devices)
        self.assertFalse(item.start_local_background(context()))
        self.assertTrue(all(("stop",) in device.calls for device in devices))
        self.assertFalse(item._local_background_active)

    def test_mixed_post_start_status_is_compensated_before_claiming_active(self):
        devices = [Device() for _ in range(4)]
        original_status = devices[2].query_receiver_status

        def wrong_offset_status(record=True):
            status = original_status(record=record)
            if devices[2].base_mode == 1:
                status["receiver_global_strip_offset"] = 17
            return status

        devices[2].query_receiver_status = wrong_offset_status
        item = controller(devices)
        self.assertFalse(item.start_local_background(context()))
        self.assertTrue(all(("stop",) in device.calls for device in devices))
        self.assertFalse(item._local_background_active)
        self.assertIn("receiver_global_strip_offset", item._local_background_status["start_error"])

    def test_stop_ack_is_insufficient_when_receiver_remains_local(self):
        devices = [Device() for _ in range(4)]
        item = controller(devices)
        self.assertTrue(item.start_local_background(context()))

        def sticky_stop():
            devices[1].calls.append(("stop",))
            devices[1].last_result = 1
            return devices[1].query_receiver_status(record=False)

        devices[1].stop_local_background = sticky_stop
        self.assertFalse(item.stop_local_background())
        self.assertTrue(item._local_background_active)
        self.assertEqual(item._local_background_status["state"], "degraded")

    def test_parameter_failure_rolls_back_already_updated_receivers(self):
        devices = [Device() for _ in range(4)]
        item = controller(devices)
        self.assertTrue(item.start_local_background(
            context(), preferred_cadence_hz=30, common_seed=10
        ))
        devices[2].fail_parameter_calls.add(1)
        self.assertFalse(item.update_local_background_params(
            preferred_cadence_hz=60, common_seed=20
        ))
        for index, device in enumerate(devices[:2]):
            params = [call[1] for call in device.calls if call[0] == "params"]
            self.assertEqual(params[-2]["preferred_cadence_hz"], 60)
            self.assertEqual(params[-1]["preferred_cadence_hz"], 30)
            self.assertEqual(params[-1]["global_strip_offset"], index * 8)
        self.assertTrue(item._local_background_active)

    def test_parameter_rollback_failure_forces_safe_fallback(self):
        devices = [Device() for _ in range(4)]
        item = controller(devices)
        self.assertTrue(item.start_local_background(context()))
        devices[0].fail_parameter_calls.add(2)
        devices[2].fail_parameter_calls.add(1)
        self.assertFalse(item.update_local_background_params(
            preferred_cadence_hz=60, common_seed=20
        ))
        self.assertTrue(all(("stop",) in device.calls for device in devices))
        self.assertFalse(item._local_background_active)
        self.assertEqual(item._local_background_status["state"], "fallback")

    def test_stop_is_idempotent_and_safe_when_not_active(self):
        devices = [Device() for _ in range(4)]
        item = controller(devices)
        self.assertTrue(item.stop_local_background())
        self.assertTrue(item.stop_local_background())
        self.assertTrue(all(device.calls.count(("stop",)) == 2 for device in devices))

    def test_complete_set_all_is_direct_universal_takeover(self):
        devices = [Device() for _ in range(4)]
        item = controller(devices)
        item._local_background_active = True
        item.set_all_pixels([(1, 2, 3)] * item.total_leds)
        self.assertTrue(all(device.calls[0] == ("set_all", 1104) for device in devices))
        self.assertTrue(all(("stop",) not in device.calls for device in devices))
        self.assertFalse(item._local_background_active)
        self.assertEqual(item._local_background_status["state"], "host_full_scene")

    def test_partial_set_all_failure_compensates_to_fallback(self):
        devices = [Device() for _ in range(4)]
        devices[2].fail.add("set_all")
        item = controller(devices)
        item._local_background_active = True
        item.set_all_pixels([(1, 2, 3)] * item.total_leds)
        self.assertTrue(all(("stop",) in device.calls for device in devices))
        self.assertFalse(item._local_background_active)
        self.assertEqual(item._local_background_status["operation"], "set_all_partial_failure")

    def test_partial_set_all_after_restart_detects_retained_local_and_falls_back(self):
        devices = [Device() for _ in range(4)]
        devices[2].fail.add("set_all")
        devices[2].base_mode = 1
        item = controller(devices)
        self.assertFalse(item._local_background_active)
        item.set_all_pixels([(1, 2, 3)] * item.total_leds)
        self.assertTrue(all(("stop",) in device.calls for device in devices))
        self.assertEqual(item._local_background_status["state"], "fallback")

    def test_aggregate_stats_expose_cross_board_local_status(self):
        item = controller([Device() for _ in range(4)])
        item._local_background_status = {"state": "degraded", "operation": "test"}
        self.assertEqual(
            item.get_stats()["aggregate"]["local_background"],
            {"state": "degraded", "operation": "test"},
        )

    def test_invalid_start_bounds_touch_no_receiver(self):
        devices = [Device() for _ in range(4)]
        item = controller(devices)
        with self.assertRaises(ValueError):
            item.start_local_background(context(), preferred_cadence_hz=201)
        self.assertTrue(all(device.calls == [] for device in devices))


if __name__ == "__main__":
    unittest.main()
