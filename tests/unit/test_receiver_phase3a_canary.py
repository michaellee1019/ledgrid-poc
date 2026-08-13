import sys
import types
import unittest
from pathlib import Path

import numpy as np


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from tools.benchmarks.phase3a_single_receiver_canary import (
    BASE_HOST_FULL_SCENE,
    BASE_LOCAL_BACKGROUND,
    CMD_SET_ALL,
    REQUIRED_CAPABILITIES,
    CanaryConfig,
    SingleReceiverPhase3ACanary,
    evaluate_disconnect_window,
    evaluate_exact_status,
    evaluate_host_takeover,
    evaluate_identity_status,
)


ROOT = Path(__file__).resolve().parents[2]


def status_v3(*, logical_id=0xFF, capabilities=REQUIRED_CAPABILITIES):
    return {
        "receiver_status_version": 3,
        "receiver_capabilities": capabilities,
        "receiver_logical_device": logical_id,
        "receiver_base_mode": 0,
        "receiver_last_result": 0,
        "receiver_transition_reason": 0,
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
        "receiver_local_cadence_deadlines": 10,
        "receiver_local_frames_rendered": 10,
        "receiver_local_missed_deadlines": 0,
        "receiver_last_local_render_us": 75,
        "receiver_max_local_render_us": 100,
        "receiver_last_frame_scene_time_us": 1_000,
        "receiver_crc_errors": 0,
        "receiver_spi_queue_errors": 0,
        "receiver_display_errors": 0,
        "receiver_last_processed_command": 0,
    }


class FakeClock:
    def __init__(self):
        self.seconds = 100.0

    def monotonic(self):
        return self.seconds

    def monotonic_ns(self):
        return int(self.seconds * 1_000_000_000)


class FakeReceiver:
    def __init__(self, config):
        self.config = config
        self.status = status_v3()
        self.staged_context = None
        self.controller_opens = 0
        self.controller_closes = 0
        self.black_takeovers = 0
        self.fail_at = None
        self.bad_ack_at = None
        self.ignore_parameter_update = False
        self.factory_failures = set()
        self.after_sleep = None

    def factory(self):
        self.controller_opens += 1
        if self.controller_opens in self.factory_failures:
            raise OSError(f"open {self.controller_opens} failed")
        return FakeController(self)

    def sleep(self, seconds):
        cadence = self.status["receiver_declared_cadence_hz"]
        frames = int(round(seconds * cadence))
        self.status["receiver_local_cadence_deadlines"] += frames
        self.status["receiver_local_frames_rendered"] += frames
        self.status["receiver_last_local_render_us"] = 80
        self.status["receiver_max_local_render_us"] = 110
        self.status["receiver_last_frame_scene_time_us"] += int(seconds * 1_000_000)
        if self.after_sleep:
            self.after_sleep(self.status)


class FakeController:
    def __init__(self, receiver):
        self.receiver = receiver

    def _ack(self, operation):
        if self.receiver.fail_at == operation:
            raise OSError(f"{operation} failed")
        self.receiver.status["receiver_last_result"] = (
            5 if self.receiver.bad_ack_at == operation else 1
        )
        return dict(self.receiver.status)

    def query_receiver_status(self):
        return dict(self.receiver.status)

    def configure(self):
        self.receiver.status["receiver_logical_device"] = self.receiver.config.logical_id

    def begin_presentation_context(self, context):
        self.receiver.staged_context = context
        return self._ack("begin")

    def set_presentation_context(self, context):
        self.receiver.staged_context = context
        return self._ack("set")

    def commit_presentation_context(self, context, **_timing):
        status = self.receiver.status
        status.update({
            "receiver_active_scene_revision": context.scene_revision,
            "receiver_active_context_digest": context.context_digest.hex(),
            "receiver_vibe_revision": context.vibe.state.revision,
            "receiver_vibe_digest": context.vibe.state.resolved_profile_digest,
            "receiver_plant_modifier_revision": context.plant_revision,
            "receiver_plant_modifier_digest": context.plant_digest.hex(),
            "receiver_active_session_id": context.controller_session_id.hex(),
        })
        return self._ack("commit")

    def start_local_background(self, **kwargs):
        status = self.receiver.status
        status.update({
            "receiver_base_mode": BASE_LOCAL_BACKGROUND,
            "receiver_transition_reason": 1,
            "receiver_component_id": kwargs["component_id"],
            "receiver_declared_cadence_hz": kwargs["preferred_cadence_hz"],
            "receiver_global_strip_offset": kwargs["global_strip_offset"],
            "receiver_common_seed": kwargs["common_seed"],
            "receiver_scene_epoch": kwargs["scene_epoch"],
        })
        return self._ack("start")

    def update_local_background_params(self, **kwargs):
        if not self.receiver.ignore_parameter_update:
            self.receiver.status.update({
                "receiver_declared_cadence_hz": kwargs["preferred_cadence_hz"],
                "receiver_global_strip_offset": kwargs["global_strip_offset"],
                "receiver_common_seed": kwargs["common_seed"],
            })
        return self._ack("params")

    def set_all_pixels(self, colors):
        self._ack("set_all")
        array = np.asarray(colors)
        if array.shape != (8 * 138, 3) or array.dtype != np.uint8 or np.any(array):
            raise AssertionError("takeover must be one complete uint8 black frame")
        self.receiver.black_takeovers += 1
        self.receiver.status["receiver_base_mode"] = BASE_HOST_FULL_SCENE
        self.receiver.status["receiver_last_processed_command"] = CMD_SET_ALL

    def close(self):
        self.receiver.controller_closes += 1


class CanaryConfigTests(unittest.TestCase):
    def test_accepts_explicit_receiver_and_defaults_to_sixty_seconds(self):
        config = CanaryConfig(bus=1, device=2, logical_id=3)
        self.assertEqual((config.bus, config.device, config.logical_id), (1, 2, 3))
        self.assertEqual(config.disconnect_seconds, 60.0)
        self.assertEqual(config.global_strip_offset, 24)

    def test_rejects_every_invalid_bound(self):
        invalid = (
            {"bus": -1}, {"bus": True}, {"device": -1}, {"device": 1.5},
            {"logical_id": -1}, {"logical_id": 4}, {"logical_id": True},
            {"logical_id": 1.5}, {"disconnect_seconds": 0},
            {"disconnect_seconds": float("inf")}, {"disconnect_seconds": True},
            {"initial_cadence_hz": 0}, {"initial_cadence_hz": 201},
            {"updated_cadence_hz": True}, {"initial_seed": -1},
            {"updated_seed": 0x1_0000_0000}, {"updated_seed": 1.5},
        )
        for change in invalid:
            values = {"bus": 0, "device": 0, "logical_id": 0, **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                CanaryConfig(**values)


class CanaryEvaluatorTests(unittest.TestCase):
    def test_fresh_status_reads_past_two_prequeued_snapshots(self):
        class QueuedStatusController:
            def __init__(self):
                self.calls = 0
                self.responses = [
                    {"counter": 10},
                    {"counter": 10},
                    {"counter": 310},
                    {"counter": 999},
                ]

            def query_receiver_status(self):
                response = self.responses[self.calls]
                self.calls += 1
                return response

        controller = QueuedStatusController()
        status = SingleReceiverPhase3ACanary._fresh_status(controller)
        self.assertEqual(controller.calls, 3)
        self.assertEqual(status["counter"], 310)

    def test_identity_requires_exact_v3_capabilities_and_logical_id(self):
        self.assertEqual(evaluate_identity_status(status_v3(logical_id=2), 2), [])
        cases = (
            (None, "unavailable"),
            ({**status_v3(logical_id=2), "receiver_status_version": 2}, "exactly 3"),
            (status_v3(logical_id=2, capabilities=REQUIRED_CAPABILITIES & ~1), "capabilities"),
            (status_v3(logical_id=2, capabilities=REQUIRED_CAPABILITIES | 0x10), "capabilities"),
            (status_v3(logical_id=1), "expected 2"),
            ({**status_v3(logical_id=2), "receiver_status_version": "corrupt"}, "exactly 3"),
        )
        for value, message in cases:
            with self.subTest(message=message):
                self.assertTrue(any(message in item for item in evaluate_identity_status(value, 2)))

    def test_exact_status_reports_missing_mismatched_and_failed_results(self):
        self.assertEqual(evaluate_exact_status({"a": 1, "receiver_last_result": 1}, {"a": 1}, require_ok=True), [])
        self.assertIn("unavailable", evaluate_exact_status(None, {"a": 1})[0])
        self.assertIn("expected 1", evaluate_exact_status({"a": 2}, {"a": 1})[0])
        self.assertIn("receiver_last_result", evaluate_exact_status({"a": 1}, {"a": 1}, require_ok=True)[0])

    def _disconnect_pair(self):
        expected = {"receiver_base_mode": BASE_LOCAL_BACKGROUND, "binding": "same"}
        before = status_v3(logical_id=1)
        before.update(expected)
        before["receiver_last_result"] = 1
        after = dict(before)
        after.update({
            "receiver_transition_reason": 1,
            "receiver_local_cadence_deadlines": 310,
            "receiver_local_frames_rendered": 310,
            "receiver_last_local_render_us": 80,
            "receiver_max_local_render_us": 110,
            "receiver_last_frame_scene_time_us": 10_001_000,
        })
        return before, after, expected

    def test_disconnect_window_accepts_stable_healthy_local_rendering(self):
        before, after, expected = self._disconnect_pair()
        result = evaluate_disconnect_window(
            before, after, elapsed_seconds=10, cadence_hz=30,
            expected_binding=expected,
        )
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["rendered_delta"], 300)
        self.assertEqual(result["deadline_delta"], 300)

    def test_disconnect_window_fails_closed_for_each_drift_and_counter_fault(self):
        mutations = (
            (lambda s: s.update(binding="wrong"), "binding"),
            (lambda s: s.update(receiver_base_mode=0), "receiver_base_mode"),
            (lambda s: s.update(receiver_last_result=8), "receiver_last_result"),
            (lambda s: s.update(receiver_transition_reason=2), "transition reason"),
            (lambda s: s.update(receiver_local_frames_rendered=10), "rendered-frame"),
            (lambda s: s.update(receiver_local_cadence_deadlines=10), "cadence-deadline"),
            (lambda s: s.update(receiver_local_missed_deadlines=1), "cadence misses"),
            (lambda s: s.update(receiver_crc_errors=1), "CRC errors"),
            (lambda s: s.update(receiver_spi_queue_errors=1), "SPI queue errors"),
            (lambda s: s.update(receiver_display_errors=1), "display errors"),
            (lambda s: s.update(receiver_last_local_render_us=0), "render duration"),
            (lambda s: s.update(receiver_max_local_render_us=50), "maximum local render"),
            (lambda s: s.update(receiver_last_frame_scene_time_us=1_000), "scene time"),
            (lambda s: s.update(receiver_local_frames_rendered=9), "regressed"),
        )
        for mutate, message in mutations:
            before, after, expected = self._disconnect_pair()
            mutate(after)
            result = evaluate_disconnect_window(
                before, after, elapsed_seconds=10, cadence_hz=30,
                expected_binding=expected,
            )
            with self.subTest(message=message):
                self.assertFalse(result["passed"])
                self.assertTrue(any(message in item for item in result["failures"]), result)

    def test_takeover_requires_host_base_and_exact_set_all_command(self):
        good = {"receiver_base_mode": BASE_HOST_FULL_SCENE,
                "receiver_last_processed_command": CMD_SET_ALL,
                "receiver_last_result": 1}
        self.assertEqual(evaluate_host_takeover(good), [])
        self.assertTrue(evaluate_host_takeover({**good, "receiver_base_mode": 1}))
        self.assertTrue(evaluate_host_takeover({**good, "receiver_last_processed_command": 5}))
        self.assertTrue(evaluate_host_takeover({**good, "receiver_last_result": 8}))


class CanaryRunnerTests(unittest.TestCase):
    def setUp(self):
        self.config = CanaryConfig(
            bus=2, device=1, logical_id=3, disconnect_seconds=10,
            initial_cadence_hz=30, updated_cadence_hz=47,
        )
        self.clock = FakeClock()
        self.receiver = FakeReceiver(self.config)

    def runner(self):
        def sleep(seconds):
            self.receiver.sleep(seconds)
            self.clock.seconds += seconds

        return SingleReceiverPhase3ACanary(
            self.config,
            controller_factory=self.receiver.factory,
            clock=self.clock.monotonic,
            monotonic_ns=self.clock.monotonic_ns,
            sleeper=sleep,
            session_factory=lambda length: bytes(range(length)),
        )

    def test_complete_canary_disconnects_updates_and_finishes_host_owned_black(self):
        result = self.runner().run()
        self.assertTrue(result["passed"], result)
        self.assertTrue(result["disconnect"]["passed"])
        self.assertEqual(result["disconnect"]["rendered_delta"], 300)
        self.assertEqual(self.receiver.controller_opens, 2)
        self.assertEqual(self.receiver.controller_closes, 2)
        # The normal acceptance step and the mandatory finally path both prove black.
        self.assertEqual(self.receiver.black_takeovers, 2)
        self.assertEqual(self.receiver.status["receiver_base_mode"], BASE_HOST_FULL_SCENE)
        self.assertEqual(self.receiver.status["receiver_common_seed"], self.config.updated_seed)

    def test_stage_ack_failure_is_reported_and_still_forces_black(self):
        self.receiver.bad_ack_at = "set"
        result = self.runner().run()
        self.assertFalse(result["passed"])
        self.assertIn("presentation SET acknowledgement", result["failure"])
        self.assertTrue(result["finally_black_takeover"])
        self.assertEqual(self.receiver.black_takeovers, 1)

    def test_disconnect_drift_is_reported_and_still_forces_black(self):
        self.receiver.after_sleep = lambda status: status.update(receiver_base_mode=0)
        result = self.runner().run()
        self.assertFalse(result["passed"])
        self.assertIn("disconnect/reopen window", result["failure"])
        self.assertEqual(self.receiver.black_takeovers, 1)

    def test_parameter_ack_without_application_fails_exact_postverify(self):
        self.receiver.ignore_parameter_update = True
        result = self.runner().run()
        self.assertFalse(result["passed"])
        self.assertIn("live parameter update", result["failure"])
        self.assertEqual(self.receiver.black_takeovers, 1)

    def test_reopen_failure_gets_one_final_reopen_for_black_takeover(self):
        self.receiver.factory_failures.add(2)
        result = self.runner().run()
        self.assertFalse(result["passed"])
        self.assertIn("open 2 failed", result["failure"])
        self.assertEqual(self.receiver.controller_opens, 3)
        self.assertEqual(self.receiver.black_takeovers, 1)
        self.assertTrue(result["finally_black_takeover"])

    def test_capability_failure_is_mutation_free_except_final_black_takeover(self):
        self.receiver.status["receiver_capabilities"] &= ~1
        result = self.runner().run()
        self.assertFalse(result["passed"])
        self.assertIn("exact local-canary capability set", result["failure"])
        self.assertEqual(self.receiver.black_takeovers, 1)

    def test_cleanup_failure_cannot_be_reported_as_passed(self):
        self.receiver.fail_at = "set_all"
        result = self.runner().run()
        self.assertFalse(result["passed"])
        self.assertIn("set_all failed", result["failure"])
        self.assertIn("finally black takeover failed", result["cleanup_failure"])

    def test_just_recipe_requires_explicit_physical_address_and_never_manages_services(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        recipe = justfile.split("receiver-phase3a-physical-canary", 1)[1].split("\n\n", 1)[0]
        self.assertIn(" bus device logical_id", recipe)
        self.assertIn("--bus {{bus}} --device {{device}} --logical-id {{logical_id}}", recipe)
        self.assertIn('disconnect_seconds="60"', recipe)
        self.assertNotIn("systemctl", recipe)
        self.assertNotIn("flash", recipe.lower())


if __name__ == "__main__":
    unittest.main()
