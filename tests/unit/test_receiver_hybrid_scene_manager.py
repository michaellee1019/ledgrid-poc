"""Focused manager acceptance for the receiver-hybrid scene lifecycle."""

from __future__ import annotations

import contextlib
import io
import unittest

import numpy as np

from animation.core.base import AnimationBase
from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.manager import AnimationManager
from animation.core.presentation_contracts import OverlayFrame
from animation.core.receiver_static_component import (
    COMPILED_RAINBOW_CONTRACT_DIGEST,
    COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST,
    COMPILED_RAINBOW_PLUGIN_ID,
)


_ENABLED = AnimationPipelineFeatureFlags(
    receiver_local_background=True,
    receiver_sparse_overlay=True,
)


class _ClockOverlay(AnimationBase):
    """One-pixel clock hand with a ten-second event cadence."""

    instances: list["_ClockOverlay"] = []
    VIBE_CAPABILITIES = frozenset(("luminance",))
    VIBE_COLOR_POLICY = "preserve"

    def __init__(self, controller, config=None):
        super().__init__(controller, config)
        self.starts = 0
        self.stops = 0
        self.cleanups = 0
        self._tick = None
        self._revision = 0
        self._pixels = np.zeros((controller.total_leds, 4), dtype=np.uint8)
        type(self).instances.append(self)

    def start(self):
        self.starts += 1
        self.is_running = True

    def stop(self):
        self.stops += 1
        self.is_running = False

    def cleanup(self):
        self.cleanups += 1

    def generate_frame(self, elapsed, _frame_index):
        tick = int(elapsed // 10.0)
        if tick == self._tick:
            return OverlayFrame(
                self._pixels,
                revision=self._revision,
                changed=False,
                dirty_ranges=(),
            )
        previous = () if self._tick is None else (self._tick % len(self._pixels),)
        current = tick % len(self._pixels)
        self._pixels.fill(0)
        self._pixels[current] = (96, 48, 0, 96)
        self._tick = tick
        self._revision += 1
        dirty = tuple((index, index + 1) for index in sorted(set((*previous, current))))
        return OverlayFrame(
            self._pixels,
            revision=self._revision,
            changed=True,
            dirty_ranges=dirty,
        )


class _HybridController:
    debug = False
    inline_show = True

    def __init__(self, *, start_behavior="ok", publish_behavior="ok"):
        self.strip_count = 2
        self.leds_per_strip = 4
        self.total_leds = self.strip_count * self.leds_per_strip
        self.current_brightness = 255
        self.operations: list[tuple] = []
        self.start_behavior = start_behavior
        self.publish_behavior = publish_behavior
        self.renew_behavior = "ok"
        self._session = None
        self._generation = 0
        self._context = None
        self._status = {"state": "stopped", "operation": "test"}

    def configure(self):
        self.operations.append(("configure",))

    def clear(self):
        self.operations.append(("clear",))

    def start_local_background(self, context, **parameters):
        self.operations.append(("start_local", context, dict(parameters)))
        if self.start_behavior == "fail_once":
            self.start_behavior = "ok"
            self._status = {
                "state": "degraded",
                "operation": "start_compensation",
                "start_error": "receiver start disagreement",
            }
            return False
        self._context = context
        self._session = None
        self._generation = 0
        self._status = {
            "state": "active",
            "operation": "start",
            "context_digest": context.context_digest.hex(),
        }
        return True

    def update_local_background_params(self, **parameters):
        self.operations.append(("update_base", dict(parameters)))
        return True

    def update_presentation_context(self, context):
        self.operations.append(("update_context", context))
        self._context = context
        self._session = None
        self._generation = 0
        self._status = {
            "state": "foreground_repair_required",
            "operation": "presentation_context_update",
            "context_digest": context.context_digest.hex(),
            "scene_revision": context.scene_revision,
        }
        return True

    def publish_sparse_overlay(self, pixels, **fields):
        self.operations.append(("publish", np.asarray(pixels).copy(), dict(fields)))
        if self.publish_behavior == "fail_once":
            self.publish_behavior = "ok"
            self._session = fields["controller_session_id"]
            self._generation = fields["generation"] + 1
            self._status = {
                "state": "foreground_cleared",
                "operation": "foreground_publish_failed",
                "error": "initial sparse snapshot disagreement",
                "cleanup_errors": [],
                "foreground_generation": self._generation,
            }
            return False
        session = fields["controller_session_id"]
        if session != self._session:
            if fields["prior_generation"] != 0:
                raise AssertionError("a new session must start at generation zero")
            self._session = session
            self._generation = 0
        if self._context is None or session != self._context.controller_session_id:
            raise AssertionError("foreground session was not staged in receiver context")
        if fields["prior_generation"] != self._generation:
            raise AssertionError("foreground prior generation diverged")
        self._generation = fields["generation"]
        self._status = {
            "state": "active",
            "operation": "foreground_publish",
            "foreground_generation": self._generation,
        }
        return True

    def renew_sparse_overlay(self, **fields):
        self.operations.append(("renew", dict(fields)))
        if self.renew_behavior == "fail_once":
            self.renew_behavior = "ok"
            self._status = {
                "state": "degraded",
                "operation": "foreground_renew_failed",
                "error": "renew compensation disagreement",
                "cleanup_errors": [{"logical_device": 1, "error": "offline"}],
            }
            return False
        if (
            fields["controller_session_id"] != self._session
            or fields["generation"] != self._generation
        ):
            raise AssertionError("renew did not bind the current foreground")
        self._status = {
            "state": "active",
            "operation": "foreground_renew",
            "foreground_generation": self._generation,
        }
        return True

    def clear_sparse_overlay(self, **fields):
        self.operations.append(("clear_sparse", dict(fields)))
        self._generation = fields["generation"]
        self._status = {
            "state": "active",
            "operation": "foreground_clear",
            "foreground_generation": self._generation,
        }
        return True

    def set_all_pixels(self, pixels):
        frame = np.asarray(pixels, dtype=np.uint8).copy()
        self.operations.append(("set_all", frame))
        self._session = None
        self._generation = 0
        self._status = {"state": "host_full_scene", "operation": "set_all"}
        return True

    def get_stats(self):
        return {"aggregate": {"local_background": dict(self._status)}}


def _scene(*, overlay=True, revision=9):
    overlays = []
    if overlay:
        overlays.append({
            "slot_id": "clock_overlay",
            "component": {
                "plugin_id": "clock_overlay",
                "provider": "python",
                "parameter_overrides": {},
                "resolved_parameters": {},
            },
            "enabled": True,
            "opacity": 255,
            "placement": {
                "strip_translation": 0,
                "led_translation": 0,
                "clip_policy": "clip_to_wall",
            },
            "stale_policy": {
                "policy": "clear_after_lease",
                "lease_ms": 3000,
            },
        })
    return {
        "schema": "ledgrid.scene-state",
        "schema_version": 1,
        "revision": revision,
        "background": {
            "plugin_id": COMPILED_RAINBOW_PLUGIN_ID,
            "provider": "receiver_native",
            "parameter_overrides": {},
            "resolved_parameters": {
                "preferred_cadence_hz": 30,
                "common_seed": 7,
            },
            "bundle_digest": COMPILED_RAINBOW_CONTRACT_DIGEST,
            "expected_payload_digest": COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST,
        },
        "overlays": overlays,
        "known_python_fallback": {
            "plugin_id": "gradient",
            "provider": "python",
            "parameter_overrides": {},
            "resolved_parameters": {},
        },
    }


def _python_fallback_scene(revision=20):
    fallback = _scene(revision=revision)["known_python_fallback"]
    return {
        "schema": "ledgrid.scene-state",
        "schema_version": 1,
        "revision": revision,
        "background": fallback,
        "overlays": [],
        "known_python_fallback": fallback,
    }


class ReceiverHybridSceneManagerTests(unittest.TestCase):
    def setUp(self):
        _ClockOverlay.instances.clear()
        self.managers: list[AnimationManager] = []

    def tearDown(self):
        for manager in self.managers:
            with contextlib.redirect_stdout(io.StringIO()):
                manager.stop_animation(clear_leds=False)

    def make_manager(self, controller=None, *, flags=_ENABLED):
        controller = controller or _HybridController()
        with contextlib.redirect_stdout(io.StringIO()):
            manager = AnimationManager(
                controller,
                feature_flags=flags,
                auto_start=False,
            )
        manager._launch_animation_loop = lambda: None
        manager.plugin_loader.loaded_plugins["clock_overlay"] = _ClockOverlay
        self.managers.append(manager)
        return controller, manager

    @staticmethod
    def operation_names(controller):
        return [operation[0] for operation in controller.operations]

    def test_feature_off_and_missing_capability_reject_before_mutation(self):
        off_controller, off = self.make_manager(
            flags=AnimationPipelineFeatureFlags()
        )
        self.assertFalse(off.start_scene(_scene()))
        self.assertEqual(off_controller.operations, [])
        self.assertEqual(_ClockOverlay.instances, [])

        missing_controller = _HybridController()
        missing_controller.update_presentation_context = None
        missing_controller, missing = self.make_manager(missing_controller)
        self.assertFalse(missing.start_scene(_scene()))
        self.assertEqual(missing_controller.operations, [])
        self.assertEqual(_ClockOverlay.instances, [])

    def test_preview_is_host_only_and_does_not_mutate_live_scene(self):
        controller, manager = self.make_manager()
        preview = manager.get_scene_preview(_scene(), elapsed=12.0)

        self.assertEqual(controller.operations, [])
        self.assertFalse(manager._scene_mode)
        self.assertEqual(preview["scene"]["provider_mode"], "receiver_hybrid")
        self.assertFalse(preview["framebuffer_readback"])
        self.assertFalse(preview["live_state_mutated"])
        self.assertEqual(_ClockOverlay.instances[-1].cleanups, 1)

    def test_start_orders_context_before_authoritative_snapshot_then_renew_and_delta(self):
        controller, manager = self.make_manager()
        self.assertTrue(manager.start_scene(_scene()))

        self.assertEqual(self.operation_names(controller), ["start_local", "publish"])
        context = controller.operations[0][1]
        initial = controller.operations[1][2]
        self.assertEqual(initial["controller_session_id"], context.controller_session_id)
        self.assertTrue(initial["full_snapshot"])
        self.assertIsNone(initial["dirty_ranges"])
        self.assertEqual((initial["prior_generation"], initial["generation"]), (0, 1))

        preview_calls = 0
        original_preview = manager._receiver_preview_frame

        def counted_preview(*args, **kwargs):
            nonlocal preview_calls
            preview_calls += 1
            return original_preview(*args, **kwargs)

        manager._receiver_preview_frame = counted_preview
        _preview, transmitted = manager._receiver_hybrid_tick(manager.start_time + 1.1)
        self.assertTrue(transmitted)
        self.assertEqual(controller.operations[-1][0], "renew")
        self.assertEqual(preview_calls, 0)

        _preview, transmitted = manager._receiver_hybrid_tick(manager.start_time + 10.1)
        self.assertTrue(transmitted)
        self.assertEqual(controller.operations[-1][0], "publish")
        delta = controller.operations[-1][2]
        self.assertFalse(delta["full_snapshot"])
        self.assertEqual(delta["dirty_ranges"], ((0, 2),))
        self.assertEqual(preview_calls, 1)

    def test_vibe_and_plant_context_refresh_keep_overlay_and_repair_full_snapshot(self):
        controller, manager = self.make_manager()
        self.assertTrue(manager.start_scene(_scene()))
        overlay = manager._scene_overlay["animation"]
        first_session = controller.operations[-1][2]["controller_session_id"]

        manager.set_vibe("quiet")

        self.assertIs(manager._scene_overlay["animation"], overlay)
        self.assertEqual(overlay.starts, 1)
        self.assertEqual(self.operation_names(controller)[-2:], [
            "update_context", "publish",
        ])
        context = controller.operations[-2][1]
        repair = controller.operations[-1][2]
        self.assertNotEqual(context.controller_session_id, first_session)
        self.assertEqual(repair["controller_session_id"], context.controller_session_id)
        self.assertTrue(repair["full_snapshot"])
        self.assertEqual((repair["prior_generation"], repair["generation"]), (0, 1))

        vibe_session = repair["controller_session_id"]
        manager.set_plant_modifiers({
            "version": 1,
            "active": ["illuminate"],
            "strengths": {"illuminate": 0.5},
        })
        self.assertIs(manager._scene_overlay["animation"], overlay)
        self.assertEqual(self.operation_names(controller)[-2:], [
            "update_context", "publish",
        ])
        plant_context = controller.operations[-2][1]
        plant_repair = controller.operations[-1][2]
        self.assertNotEqual(plant_context.controller_session_id, vibe_session)
        self.assertEqual(
            plant_repair["controller_session_id"],
            plant_context.controller_session_id,
        )
        self.assertTrue(plant_repair["full_snapshot"])

    def test_stop_and_python_switch_use_complete_takeover_and_cleanup_once(self):
        controller, manager = self.make_manager()
        self.assertTrue(manager.start_scene(_scene()))
        overlay = manager._scene_overlay["animation"]
        controller.operations.clear()

        self.assertTrue(manager.start_scene(_python_fallback_scene()))

        self.assertEqual(self.operation_names(controller)[0], "configure")
        self.assertIn("set_all", self.operation_names(controller))
        self.assertNotIn("clear_sparse", self.operation_names(controller))
        self.assertEqual(overlay.cleanups, 1)
        self.assertEqual(overlay.stops, 1)
        status = manager.get_current_status()
        self.assertEqual(status["scene"]["provider_mode"], "python_host")
        self.assertFalse(status["receiver_hybrid"]["healthy"])
        self.assertEqual(status["receiver_hybrid"]["operation"], "host_takeover")

        manager.stop_animation(clear_leds=False)
        self.assertEqual(overlay.cleanups, 1)

    def test_direct_stop_uses_one_black_complete_takeover_not_sparse_clear(self):
        controller, manager = self.make_manager()
        self.assertTrue(manager.start_scene(_scene()))
        overlay = manager._scene_overlay["animation"]
        controller.operations.clear()

        self.assertTrue(manager.stop_scene(clear_leds=True))

        self.assertEqual(self.operation_names(controller), ["set_all"])
        np.testing.assert_array_equal(
            controller.operations[0][1],
            np.zeros((controller.total_leds, 3), dtype=np.uint8),
        )
        self.assertEqual(overlay.cleanups, 1)
        self.assertEqual(overlay.stops, 1)
        self.assertFalse(manager._scene_mode)

    def test_start_and_initial_snapshot_failures_activate_known_python_fallback(self):
        for behavior in ("start", "snapshot"):
            with self.subTest(behavior=behavior):
                controller = _HybridController(
                    start_behavior="fail_once" if behavior == "start" else "ok",
                    publish_behavior="fail_once" if behavior == "snapshot" else "ok",
                )
                controller, manager = self.make_manager(controller)
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    self.assertTrue(manager.start_scene(_scene()))
                status = manager.get_current_status()
                self.assertEqual(status["scene"]["provider_mode"], "python_host")
                self.assertTrue(any(
                    operation[0] == "set_all"
                    and operation[1].shape == (controller.total_leds, 3)
                    for operation in controller.operations
                ))
                receiver = status["receiver_hybrid"]
                self.assertFalse(receiver["healthy"])
                self.assertTrue(receiver["fallback_active"])
                self.assertIn("not acknowledged", receiver["error"])
                self.assertEqual(_ClockOverlay.instances[-1].cleanups, 1)

    def test_status_reports_healthy_agreement_and_failed_renewal_degradation(self):
        controller, manager = self.make_manager()
        self.assertTrue(manager.start_scene(_scene()))
        healthy = manager.get_current_status()["receiver_hybrid"]
        self.assertTrue(healthy["healthy"])
        self.assertFalse(healthy["fallback_active"])
        self.assertEqual(healthy["driver"]["state"], "active")

        controller.renew_behavior = "fail_once"
        with self.assertRaisesRegex(RuntimeError, "renew compensation disagreement"):
            manager._receiver_hybrid_tick(manager.start_time + 1.1)
        degraded = manager.get_current_status()["receiver_hybrid"]
        self.assertFalse(degraded["healthy"])
        self.assertEqual(degraded["publisher"]["last_operation"], "renew_failed")
        self.assertIn("renew compensation disagreement", degraded["publisher"]["last_error"])

    def test_loop_runtime_failure_takes_over_once_and_exits_into_python_fallback(self):
        controller, manager = self.make_manager()
        self.assertTrue(manager.start_scene(_scene()))
        publisher = manager._receiver_sparse_publisher
        self.assertIsNotNone(publisher)
        # Make the first synchronous loop tick a lease-renewal boundary without
        # waiting in wall-clock time.
        publisher._last_lease_at = manager.start_time - 1.1
        controller.renew_behavior = "fail_once"
        controller.operations.clear()
        run_generation = manager._run_generation

        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            manager._animation_loop(run_generation)

        names = self.operation_names(controller)
        self.assertEqual(names.count("renew"), 1)
        self.assertGreaterEqual(names.count("set_all"), 1)
        for operation in controller.operations:
            if operation[0] == "set_all":
                self.assertEqual(
                    operation[1].shape,
                    (controller.total_leds, 3),
                )
        status = manager.get_current_status()
        self.assertEqual(status["scene"]["provider_mode"], "python_host")
        self.assertFalse(status["receiver_hybrid"]["healthy"])
        self.assertTrue(status["receiver_hybrid"]["fallback_active"])
        self.assertIn(
            "renew compensation disagreement",
            status["receiver_hybrid"]["error"],
        )
        # The failed hybrid loop returned; the Python fallback owns the current
        # run generation and no second hybrid renewal was attempted.
        self.assertGreater(manager._run_generation, run_generation)
        self.assertTrue(manager.is_running)


if __name__ == "__main__":
    unittest.main()
