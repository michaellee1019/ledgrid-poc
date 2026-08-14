import sys
import types
import unittest

import numpy as np


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from drivers.spi_controller import (
    CMD_CONTROLLER_SESSION_BEGIN,
    CMD_LOCAL_BACKGROUND_START,
    CMD_OVERLAY_BEGIN,
    CMD_OVERLAY_COMMIT,
    CMD_OVERLAY_PATCH_BATCH,
    CMD_PING,
    CMD_PRESENTATION_CONTEXT_BEGIN,
    CMD_PRESENTATION_CONTEXT_COMMIT,
    CMD_PRESENTATION_CONTEXT_SET,
    CMD_SET_ALL,
    OVERLAY_UPDATE_DELTA,
    OVERLAY_UPDATE_FULL_SNAPSHOT,
)
from tools.benchmarks.phase3b_single_receiver_canary import (
    BASE_HOST_FULL_SCENE,
    BASE_LOCAL_BACKGROUND,
    FOREGROUND_ACTIVE,
    FOREGROUND_CLEARED,
    LOCAL_PIXELS,
    OVERLAY_RESULT_LEASE_EXPIRED,
    REQUIRED_CAPABILITIES,
    TRANSITION_HOST_TAKEOVER,
    TRANSITION_LOCAL_START,
    CanaryConfig,
    SingleReceiverPhase3BCanary,
    evaluate_identity_status,
    make_foreground_frames,
    summarize_timing_samples,
)


def status_v4(*, logical_id=0, capabilities=REQUIRED_CAPABILITIES):
    return {
        "receiver_status_version": 4,
        "receiver_capabilities": capabilities,
        "receiver_logical_device": logical_id,
        "receiver_base_mode": 0,
        "receiver_foreground_state": FOREGROUND_CLEARED,
        "receiver_transition_reason": 0,
        "receiver_last_result": 1,
        "receiver_context_state": 0,
        "receiver_component_id": 0,
        "receiver_declared_cadence_hz": 0,
        "receiver_global_strip_offset": 0,
        "receiver_common_seed": 0,
        "receiver_scene_epoch": 0,
        "receiver_active_scene_revision": 0,
        "receiver_active_context_digest": None,
        "receiver_vibe_revision": 0,
        "receiver_vibe_digest": None,
        "receiver_plant_modifier_revision": 0,
        "receiver_plant_modifier_digest": None,
        "receiver_active_session_id": None,
        "receiver_last_processed_command": 0,
        "receiver_operation_sequence": 0,
        "receiver_local_cadence_deadlines": 10,
        "receiver_local_frames_rendered": 10,
        "receiver_local_missed_deadlines": 0,
        "receiver_last_local_render_us": 80,
        "receiver_max_local_render_us": 100,
        "receiver_last_frame_scene_time_us": 1_000,
        "receiver_crc_errors": 0,
        "receiver_spi_queue_errors": 0,
        "receiver_display_errors": 0,
        "receiver_frames_displayed": 10,
        "receiver_last_encode_us": 40,
        "receiver_last_show_us": 30,
        "receiver_overlay_operation_result": 0,
        "receiver_overlay_update_kind": 0,
        "receiver_overlay_expected_patches": 0,
        "receiver_overlay_accepted_patches": 0,
        "receiver_overlay_committed_coverage_pixels": 0,
        "receiver_overlay_committed_generation": 0,
        "receiver_overlay_staged_generation": 0,
        "receiver_foreground_scene_revision": 0,
        "receiver_foreground_scene_epoch": 0,
        "receiver_foreground_base_revision": 0,
        "receiver_foreground_present_at_scene_time_us": 0,
        "receiver_overlay_lease_ms": 0,
        "receiver_overlay_lease_remaining_ms": 0,
        "receiver_overlay_session_id": bytes(16).hex(),
        "receiver_overlay_composite_frames": 0,
        "receiver_overlay_last_composite_us": 0,
        "receiver_overlay_max_composite_us": 0,
        "receiver_overlay_commits": 0,
        "receiver_overlay_expirations": 0,
    }


class FakeClock:
    def __init__(self):
        self.seconds = 100.0

    def monotonic(self):
        return self.seconds

    def monotonic_ns(self):
        return int(self.seconds * 1_000_000_000)


class FakeReceiver:
    def __init__(self, config, clock):
        self.config = config
        self.clock = clock
        self.status = status_v4(logical_id=config.logical_id)
        self.opens = 0
        self.closes = 0
        self.configures = 0
        self.black_takeovers = 0
        self.fail_at = None
        self.bad_ack_at = None
        self.factory_failures = set()
        self.after_operation = None
        self.retain_foreground_on_expiry = False
        self.freeze_frames = False
        self._frame_fraction = 0.0
        self._lease_deadline = None
        self._staging = None
        self._staging_kind = None
        self._staging_generation = 0
        self._staging_patch_count = 0
        self._staging_frame = None

    def factory(self):
        self.opens += 1
        if self.opens in self.factory_failures:
            raise OSError(f"open {self.opens} failed")
        # LEDController.__init__ performs this ownership-neutral liveness ping.
        self.status["receiver_operation_sequence"] += 1
        self.status["receiver_last_processed_command"] = CMD_PING
        self.status["receiver_last_result"] = 1
        return FakeController(self)

    def _render(self, frames):
        if frames <= 0:
            return
        status = self.status
        status["receiver_local_cadence_deadlines"] += frames
        status["receiver_local_frames_rendered"] += frames
        status["receiver_overlay_composite_frames"] += frames
        status["receiver_last_frame_scene_time_us"] += int(
            frames * 1_000_000 / self.config.cadence_hz
        )
        status["receiver_last_local_render_us"] = 85
        status["receiver_max_local_render_us"] = 105
        status["receiver_overlay_last_composite_us"] = 21
        status["receiver_overlay_max_composite_us"] = max(
            status["receiver_overlay_max_composite_us"], 21
        )
        status["receiver_last_encode_us"] = 41
        status["receiver_last_show_us"] = 31

    def sleep(self, seconds):
        self.clock.seconds += seconds
        if (
            self.status["receiver_base_mode"] == BASE_LOCAL_BACKGROUND
            and not self.freeze_frames
        ):
            self._frame_fraction += seconds * self.config.cadence_hz
            frames = int(self._frame_fraction)
            self._frame_fraction -= frames
            self._render(frames)
        if (
            self._lease_deadline is not None
            and self.clock.seconds >= self._lease_deadline
            and self.status["receiver_foreground_state"] == FOREGROUND_ACTIVE
            and not self.retain_foreground_on_expiry
        ):
            status = self.status
            status["receiver_foreground_state"] = FOREGROUND_CLEARED
            status["receiver_overlay_operation_result"] = OVERLAY_RESULT_LEASE_EXPIRED
            status["receiver_overlay_committed_coverage_pixels"] = 0
            for key in (
                "receiver_foreground_scene_revision",
                "receiver_foreground_scene_epoch",
                "receiver_foreground_base_revision",
                "receiver_foreground_present_at_scene_time_us",
                "receiver_overlay_lease_ms",
                "receiver_overlay_lease_remaining_ms",
            ):
                status[key] = 0
            status["receiver_overlay_expirations"] += 1
            status["receiver_overlay_composite_frames"] += 1
            self._lease_deadline = None


class FakeController:
    def __init__(self, receiver):
        self.receiver = receiver

    def _ack(self, operation, command, *, overlay=False):
        receiver = self.receiver
        if receiver.fail_at == operation:
            raise OSError(f"{operation} failed")
        status = receiver.status
        status["receiver_operation_sequence"] += 1
        status["receiver_last_processed_command"] = command
        status["receiver_last_result"] = 5 if receiver.bad_ack_at == operation else 1
        if overlay:
            status["receiver_overlay_operation_result"] = (
                5 if receiver.bad_ack_at == operation else 1
            )
        if receiver.after_operation:
            receiver.after_operation(operation, status)
        return dict(status)

    def query_receiver_status(self):
        status = dict(self.receiver.status)
        if self.receiver._lease_deadline is not None:
            remaining = max(
                0, int(round((self.receiver._lease_deadline - self.receiver.clock.seconds) * 1000))
            )
            status["receiver_overlay_lease_remaining_ms"] = remaining
            self.receiver.status["receiver_overlay_lease_remaining_ms"] = remaining
        return status

    def configure(self):
        if self.receiver.fail_at == "configure":
            raise OSError("configure failed")
        self.receiver.configures += 1

    def begin_presentation_context(self, context):
        self.receiver._context = context
        return self._ack("context_begin", CMD_PRESENTATION_CONTEXT_BEGIN)

    def set_presentation_context(self, context):
        self.receiver._context = context
        return self._ack("context_set", CMD_PRESENTATION_CONTEXT_SET)

    def commit_presentation_context(self, context, **_timing):
        self.receiver.status.update({
            "receiver_context_state": 3,
            "receiver_scene_epoch": context.scene_epoch,
            "receiver_active_scene_revision": context.scene_revision,
            "receiver_active_context_digest": context.context_digest.hex(),
            "receiver_vibe_revision": context.vibe.state.revision,
            "receiver_vibe_digest": context.vibe.state.resolved_profile_digest,
            "receiver_plant_modifier_revision": context.plant_revision,
            "receiver_plant_modifier_digest": context.plant_digest.hex(),
            "receiver_active_session_id": context.controller_session_id.hex(),
        })
        return self._ack("context_commit", CMD_PRESENTATION_CONTEXT_COMMIT)

    def start_local_background(self, **fields):
        self.receiver.status.update({
            "receiver_base_mode": BASE_LOCAL_BACKGROUND,
            "receiver_transition_reason": TRANSITION_LOCAL_START,
            "receiver_component_id": fields["component_id"],
            "receiver_declared_cadence_hz": fields["preferred_cadence_hz"],
            "receiver_global_strip_offset": fields["global_strip_offset"],
            "receiver_common_seed": fields["common_seed"],
            "receiver_scene_epoch": fields["scene_epoch"],
        })
        return self._ack("base_start", CMD_LOCAL_BACKGROUND_START)

    def begin_controller_session(self, **fields):
        self.receiver.status["receiver_overlay_session_id"] = fields[
            "controller_session_id"
        ].hex()
        return self._ack("session_begin", CMD_CONTROLLER_SESSION_BEGIN, overlay=True)

    def begin_overlay(self, **fields):
        receiver = self.receiver
        receiver._staging_kind = fields["update_kind"]
        receiver._staging_generation = fields["generation"]
        receiver._staging_patch_count = fields["expected_patches"]
        receiver._staging_frame = (
            np.zeros((LOCAL_PIXELS, 4), dtype=np.uint8)
            if fields["update_kind"] == OVERLAY_UPDATE_FULL_SNAPSHOT
            else receiver._committed.copy()
        )
        receiver.status.update({
            "receiver_foreground_state": 1,
            "receiver_overlay_update_kind": fields["update_kind"],
            "receiver_overlay_expected_patches": fields["expected_patches"],
            "receiver_overlay_accepted_patches": 0,
            "receiver_overlay_staged_generation": fields["generation"],
            "receiver_foreground_scene_revision": fields["scene_revision"],
            "receiver_foreground_scene_epoch": fields["scene_epoch"],
            "receiver_foreground_base_revision": fields["base_revision"],
            "receiver_overlay_lease_ms": fields["lease_ms"],
            "receiver_overlay_lease_remaining_ms": fields["lease_ms"],
        })
        name = "snapshot_begin" if fields["generation"] == 1 else "delta_begin"
        return self._ack(name, CMD_OVERLAY_BEGIN, overlay=True)

    def send_overlay_patches(self, *, patches, update_kind, **_fields):
        receiver = self.receiver
        for start, rgba in patches:
            values = np.asarray(rgba, dtype=np.uint8)
            receiver._staging_frame[start : start + len(values)] = values
        receiver.status["receiver_overlay_accepted_patches"] = len(patches)
        packet_count = len(patches) if update_kind == OVERLAY_UPDATE_FULL_SNAPSHOT else 1
        prefix = "snapshot_patch" if update_kind == OVERLAY_UPDATE_FULL_SNAPSHOT else "delta_patch"
        return [
            self._ack(f"{prefix}_{index}", CMD_OVERLAY_PATCH_BATCH, overlay=True)
            for index in range(packet_count)
        ]

    def commit_overlay(self, **fields):
        receiver = self.receiver
        receiver._committed = receiver._staging_frame.copy()
        receiver.status.update({
            "receiver_foreground_state": FOREGROUND_ACTIVE,
            "receiver_overlay_committed_coverage_pixels": int(
                np.count_nonzero(receiver._committed[:, 3])
            ),
            "receiver_overlay_committed_generation": fields["generation"],
            "receiver_overlay_staged_generation": 0,
            "receiver_foreground_present_at_scene_time_us": fields[
                "present_at_scene_time_us"
            ],
            "receiver_overlay_commits": receiver.status["receiver_overlay_commits"] + 1,
            "receiver_overlay_composite_frames": (
                receiver.status["receiver_overlay_composite_frames"] + 1
            ),
            "receiver_overlay_last_composite_us": 22,
            "receiver_overlay_max_composite_us": max(
                receiver.status["receiver_overlay_max_composite_us"], 22
            ),
            "receiver_last_encode_us": 42,
            "receiver_last_show_us": 32,
        })
        receiver._lease_deadline = receiver.clock.seconds + receiver.config.lease_ms / 1000
        name = "snapshot_commit" if fields["generation"] == 1 else "delta_commit"
        return self._ack(name, CMD_OVERLAY_COMMIT, overlay=True)

    def set_all_pixels(self, colors):
        array = np.asarray(colors)
        if array.shape != (LOCAL_PIXELS, 3) or array.dtype != np.uint8 or np.any(array):
            raise AssertionError("takeover must be one complete uint8 black receiver frame")
        self._ack("set_all", CMD_SET_ALL)
        self.receiver.black_takeovers += 1
        self.receiver._lease_deadline = None
        self.receiver.status.update({
            "receiver_base_mode": BASE_HOST_FULL_SCENE,
            "receiver_foreground_state": FOREGROUND_CLEARED,
            "receiver_transition_reason": TRANSITION_HOST_TAKEOVER,
            "receiver_overlay_update_kind": 0,
            "receiver_overlay_expected_patches": 0,
            "receiver_overlay_accepted_patches": 0,
            "receiver_overlay_committed_coverage_pixels": 0,
            "receiver_overlay_committed_generation": 0,
            "receiver_overlay_staged_generation": 0,
            "receiver_foreground_scene_revision": 0,
            "receiver_foreground_scene_epoch": 0,
            "receiver_foreground_base_revision": 0,
            "receiver_foreground_present_at_scene_time_us": 0,
            "receiver_overlay_lease_ms": 0,
            "receiver_overlay_lease_remaining_ms": 0,
            "receiver_overlay_session_id": bytes(16).hex(),
        })

    def close(self):
        self.receiver.closes += 1


class Phase3BCanaryTests(unittest.TestCase):
    def config(self, **changes):
        values = dict(
            bus=2,
            device=1,
            logical_id=1,
            cadence_hz=20,
            lease_ms=200,
            disconnect_seconds=0.35,
            observation_timeout_seconds=0.2,
            poll_interval_seconds=0.005,
            timing_sample_seconds=0.1,
        )
        values.update(changes)
        return CanaryConfig(**values)

    def rig(self, **config_changes):
        config = self.config(**config_changes)
        clock = FakeClock()
        receiver = FakeReceiver(config, clock)
        runner = SingleReceiverPhase3BCanary(
            config,
            controller_factory=receiver.factory,
            clock=clock.monotonic,
            monotonic_ns=clock.monotonic_ns,
            sleeper=receiver.sleep,
            session_factory=lambda length: bytes(range(length)),
        )
        return runner, receiver

    def test_foreground_vectors_cover_alpha_black_snapshot_and_sparse_movement(self):
        snapshot, delta, dirty = make_foreground_frames()
        self.assertEqual(snapshot.shape, (LOCAL_PIXELS, 4))
        self.assertEqual(snapshot.dtype, np.uint8)
        self.assertEqual(np.count_nonzero(snapshot[:, 3]), 12)
        self.assertEqual(np.count_nonzero(delta[:, 3]), 7)
        self.assertEqual(tuple(snapshot[80]), (0, 0, 0, 255))
        self.assertEqual(dirty, ((16, 24), (80, 87)))
        self.assertTrue(np.all(snapshot[:, :3] <= snapshot[:, 3:4]))
        self.assertTrue(np.all(delta[:, :3] <= delta[:, 3:4]))

    def test_exact_canary_capability_is_required(self):
        self.assertEqual(evaluate_identity_status(status_v4(logical_id=2), 2), [])
        for changed, message in (
            ({"receiver_status_version": 3}, "exactly 4"),
            ({"receiver_capabilities": REQUIRED_CAPABILITIES & ~(1 << 4)}, "canary"),
            ({"receiver_capabilities": REQUIRED_CAPABILITIES | (1 << 8)}, "canary"),
            ({"receiver_logical_device": 3}, "expected 2"),
        ):
            status = status_v4(logical_id=2)
            status.update(changed)
            with self.subTest(changed=changed):
                self.assertTrue(any(message in item for item in evaluate_identity_status(status, 2)))

    def test_complete_run_proves_snapshot_delta_independence_expiry_and_takeover(self):
        runner, receiver = self.rig()
        result = runner.run()
        self.assertTrue(result["passed"], result)
        self.assertTrue(result["cadence_independence"]["passed"])
        self.assertEqual(result["cadence_independence"]["deltas"]["receiver_local_frames_rendered"], 0)
        self.assertEqual(result["cadence_independence"]["deltas"]["receiver_overlay_composite_frames"], 1)
        self.assertTrue(result["expiry"]["passed"])
        self.assertEqual(result["expiry"]["expiration_delta"], 1)
        self.assertGreater(result["expiry"]["rendered_delta"], 0)
        self.assertEqual(result["snapshot"]["coverage_pixels"], 12)
        self.assertEqual(result["delta"]["coverage_pixels"], 7)
        timing = result["receiver_timing_window"]
        self.assertEqual(
            timing["sampled_on"],
            "receiver_local_frames_rendered_after_display_completion",
        )
        self.assertEqual(timing["sample_count"], 2)
        self.assertEqual(
            timing["sample_count"], timing["completed_local_frame_delta"]
        )
        self.assertEqual(receiver.status["receiver_frames_displayed"], 10)
        self.assertLessEqual(timing["poll_count"], timing["max_poll_count"])
        self.assertEqual(timing["cadence_miss_delta"], 0)
        self.assertEqual(
            timing["metrics"]["base_render_us"],
            {"mean": 85.0, "p50": 85.0, "p95": 85.0, "p99": 85.0, "max": 85.0},
        )
        self.assertEqual(
            set(timing["metrics"]),
            {"base_render_us", "foreground_composite_us", "encode_us", "display_us"},
        )
        self.assertEqual(receiver.opens, 2)
        self.assertEqual(receiver.closes, 2)
        self.assertEqual(receiver.black_takeovers, 2)
        self.assertTrue(result["finally_black_takeover"])
        self.assertEqual(receiver.status["receiver_base_mode"], BASE_HOST_FULL_SCENE)

    def test_every_intermediate_command_failure_still_forces_complete_black(self):
        operations = (
            "configure",
            "context_begin",
            "context_set",
            "context_commit",
            "base_start",
            "session_begin",
            "snapshot_begin",
            "snapshot_patch_0",
            "snapshot_commit",
            "delta_begin",
            "delta_patch_0",
            "delta_commit",
        )
        for operation in operations:
            runner, receiver = self.rig()
            receiver.fail_at = operation
            result = runner.run()
            with self.subTest(operation=operation):
                self.assertFalse(result["passed"], result)
                self.assertIn(operation, result["failure"])
                self.assertTrue(result["finally_black_takeover"], result)
                self.assertEqual(receiver.status["receiver_base_mode"], BASE_HOST_FULL_SCENE)
                self.assertEqual(receiver.black_takeovers, 1)

    def test_bad_overlay_ack_is_detected_and_cleaned(self):
        runner, receiver = self.rig()
        receiver.bad_ack_at = "snapshot_begin"
        result = runner.run()
        self.assertFalse(result["passed"])
        self.assertIn("receiver_overlay_operation_result", result["failure"])
        self.assertEqual(receiver.black_takeovers, 1)

    def test_non_overlay_ack_refreshes_queued_v3_to_strict_v4(self):
        runner, receiver = self.rig()
        controller = receiver.factory()
        controller._ack("context_begin", CMD_PRESENTATION_CONTEXT_BEGIN)
        queued_v3 = dict(receiver.status, receiver_status_version=3)

        refreshed = runner._require_ack(
            "presentation BEGIN acknowledgement",
            queued_v3,
            CMD_PRESENTATION_CONTEXT_BEGIN,
            controller=controller,
        )

        self.assertEqual(refreshed["receiver_status_version"], 4)
        self.assertEqual(
            refreshed["receiver_last_processed_command"],
            CMD_PRESENTATION_CONTEXT_BEGIN,
        )

    def test_black_takeover_waits_for_async_receiver_completion(self):
        runner, receiver = self.rig()

        class DelayedTakeoverController:
            def __init__(self):
                self.reads = 0
                self.status = status_v4(logical_id=runner.config.logical_id)
                self.status.update({
                    "receiver_base_mode": BASE_LOCAL_BACKGROUND,
                    "receiver_foreground_state": FOREGROUND_ACTIVE,
                    "receiver_last_processed_command": CMD_OVERLAY_COMMIT,
                })

            def set_all_pixels(self, colors):
                self.reads = 0

            def query_receiver_status(self):
                self.reads += 1
                if self.reads >= 6:
                    self.status.update({
                        "receiver_base_mode": BASE_HOST_FULL_SCENE,
                        "receiver_foreground_state": FOREGROUND_CLEARED,
                        "receiver_transition_reason": TRANSITION_HOST_TAKEOVER,
                        "receiver_last_processed_command": CMD_SET_ALL,
                        "receiver_last_result": 1,
                    })
                return dict(self.status)

        controller = DelayedTakeoverController()
        status = runner._black_takeover(controller)

        self.assertGreaterEqual(controller.reads, 6)
        self.assertEqual(status["receiver_base_mode"], BASE_HOST_FULL_SCENE)

    def test_new_fault_and_cadence_advance_during_delta_are_detected(self):
        for mutation, message in (
            (lambda status: status.__setitem__("receiver_crc_errors", 1), "CRC errors"),
            (
                lambda status: (
                    status.__setitem__(
                        "receiver_local_frames_rendered",
                        status["receiver_local_frames_rendered"] + 1,
                    ),
                    status.__setitem__(
                        "receiver_local_cadence_deadlines",
                        status["receiver_local_cadence_deadlines"] + 1,
                    ),
                ),
                "receiver_local_frames_rendered",
            ),
        ):
            runner, receiver = self.rig()
            receiver.after_operation = lambda operation, status, fn=mutation: (
                fn(status) if operation == "delta_commit" else None
            )
            result = runner.run()
            with self.subTest(message=message):
                self.assertFalse(result["passed"], result)
                self.assertIn(message, result["failure"])
                self.assertEqual(receiver.black_takeovers, 1)

    def test_expiry_must_clear_foreground_while_base_continues(self):
        runner, receiver = self.rig()
        receiver.retain_foreground_on_expiry = True
        result = runner.run()
        self.assertFalse(result["passed"])
        self.assertIn("lease expiry/base continuation", result["failure"])
        self.assertEqual(receiver.black_takeovers, 1)

    def test_timing_summary_is_deterministic_for_every_receiver_metric(self):
        samples = []
        for value in (10, 20, 30, 40):
            samples.append({
                "receiver_last_local_render_us": value,
                "receiver_overlay_last_composite_us": value + 1,
                "receiver_last_encode_us": value + 2,
                "receiver_last_show_us": value + 3,
            })
        summary = summarize_timing_samples(samples)
        self.assertEqual(
            summary["base_render_us"],
            {"mean": 25.0, "p50": 25.0, "p95": 38.5, "p99": 39.7, "max": 40.0},
        )
        self.assertEqual(summary["display_us"]["mean"], 28.0)
        with self.assertRaisesRegex(ValueError, "at least one"):
            summarize_timing_samples([])

    def test_timing_window_is_bounded_and_requires_a_completed_local_frame(self):
        runner, receiver = self.rig()
        receiver.after_operation = lambda operation, _status: (
            setattr(receiver, "freeze_frames", True)
            if operation == "delta_commit"
            else None
        )
        result = runner.run()
        self.assertFalse(result["passed"], result)
        self.assertIn("no newly completed local receiver frames", result["failure"])
        self.assertLessEqual(receiver.clock.seconds, 100.3)
        self.assertEqual(receiver.black_takeovers, 1)

    def test_reopen_failure_gets_one_final_reopen_for_black_takeover(self):
        runner, receiver = self.rig()
        receiver.factory_failures.add(2)
        result = runner.run()
        self.assertFalse(result["passed"])
        self.assertIn("open 2 failed", result["failure"])
        self.assertEqual(receiver.opens, 3)
        self.assertEqual(receiver.black_takeovers, 1)
        self.assertTrue(result["finally_black_takeover"])

    def test_preflight_failure_is_observation_only(self):
        runner, receiver = self.rig()
        receiver.status["receiver_capabilities"] &= ~(1 << 5)
        result = runner.run()
        self.assertFalse(result["passed"])
        self.assertTrue(result["preflight_non_mutating"])
        self.assertEqual(receiver.configures, 0)
        self.assertEqual(receiver.black_takeovers, 0)

    def test_cleanup_failure_cannot_pass(self):
        runner, receiver = self.rig()
        receiver.fail_at = "set_all"
        result = runner.run()
        self.assertFalse(result["passed"])
        self.assertIn("finally black takeover failed", result["cleanup_failure"])

    def test_config_rejects_unsafe_expiry_and_invalid_bounds(self):
        for changes in (
            {"logical_id": 4},
            {"cadence_hz": 0},
            {"lease_ms": 0},
            {"disconnect_seconds": 0.1, "lease_ms": 100},
            {"poll_interval_seconds": 0.2},
            {"timing_sample_seconds": 0.195},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.config(**changes)


if __name__ == "__main__":
    unittest.main()
