"""Manager-level acceptance for Phase 2A vibe timing and presentation."""

import threading
import unittest

import numpy as np

from animation.core.base import AnimationBase, RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.presentation_contracts import TimingAdapter, resolve_vibe


class _Controller:
    strip_count = 2
    leds_per_strip = 3
    total_leds = 6
    debug = False


class _Probe(AnimationBase):
    VIBE_CAPABILITIES = frozenset(("tempo", "luminance", "palette_roles"))
    VIBE_COLOR_POLICY = "grade"

    def __init__(self, controller, config=None):
        super().__init__(controller, config)
        self.params = {**self.default_params, "speed": 2.0, **(config or {})}
        self.observed = []

    def generate_frame(self, time_elapsed, frame_count):
        self.observed.append((time_elapsed, self.params["speed"], frame_count))
        frame = self.next_frame_buffer(clear=False)
        frame[:] = (100, 50, 25)
        return RenderedFrame(frame, changed=True)


class _ScaledProbe(_Probe):
    TIMING_ADAPTER = TimingAdapter.SCALED_CONTEXT


class _WallClockProbe(_Probe):
    TIMING_ADAPTER = TimingAdapter.WALL_CLOCK
    VIBE_CAPABILITIES = frozenset(("luminance", "palette_roles"))


class _PreserveProbe(_Probe):
    VIBE_COLOR_POLICY = "preserve"
    VIBE_CAPABILITIES = frozenset(("luminance",))


class VibeManagerTests(unittest.TestCase):
    def manager(self, **kwargs):
        return AnimationManager(_Controller(), auto_start=False, **kwargs)

    def test_revision_is_manager_owned_and_identical_selection_is_idempotent(self):
        manager = self.manager(animation_speed_scale=1.0)
        self.assertEqual(manager.get_vibe_state()["revision"], 0)

        first = manager.set_vibe("cozy")["state"]
        second = manager.set_vibe(resolve_vibe("cozy", revision=999).state.to_dict())[
            "state"
        ]
        vivid = manager.set_vibe("vivid")["state"]

        self.assertEqual(first["revision"], 1)
        self.assertEqual(second["revision"], 1)
        self.assertEqual(vivid["revision"], 2)
        with self.assertRaisesRegex(ValueError, "unknown vibe ID"):
            manager.set_vibe("missing")

    def test_incompatible_persisted_state_falls_back_visibly_to_neutral(self):
        stale = resolve_vibe("cozy", revision=7).state.to_dict()
        stale["profile_version"] = 99
        manager = self.manager(vibe=stale)

        status = manager.get_vibe_status()
        self.assertEqual(status["state"]["vibe_id"], "neutral")
        self.assertEqual(status["state"]["revision"], 7)
        self.assertEqual(status["diagnostic"]["code"], "vibe_profile_fallback")

        live_restore = self.manager().set_vibe(stale)
        self.assertEqual(live_restore["state"]["vibe_id"], "neutral")
        self.assertEqual(live_restore["state"]["revision"], 7)
        self.assertEqual(
            live_restore["diagnostic"]["code"], "vibe_profile_fallback"
        )

        for malformed_revision in (True, 2**64):
            with self.subTest(revision=malformed_revision):
                malformed = dict(stale, revision=malformed_revision)
                fallback = self.manager(vibe=malformed).get_vibe_status()
                self.assertEqual(fallback["state"]["vibe_id"], "neutral")
                self.assertEqual(fallback["state"]["revision"], 0)
                self.assertEqual(
                    fallback["diagnostic"]["code"], "vibe_profile_fallback"
                )

    def test_restart_default_preserves_selected_preset_identity(self):
        preset = {
            "preset_id": "diagnostic",
            "name": "Diagnostic",
            "animation": "simple_test",
            "is_dirty": False,
        }
        manager = AnimationManager(
            PreviewLEDController(2, 3),
            default_animation="simple_test",
            default_animation_preset=preset,
        )
        self.addCleanup(manager.stop_animation)

        self.assertEqual(manager.get_current_status()["current_preset"], preset)

    def test_context_change_hook_cannot_run_during_frame_generation(self):
        animation = _Probe(_Controller())
        manager = self.manager(animation_speed_scale=1.0)
        neutral = manager._runtime_context(
            animation, unscaled_elapsed=0.0, scaled_elapsed=0.0, frame_index=0
        )
        vivid = manager._runtime_context(
            animation,
            unscaled_elapsed=0.0,
            scaled_elapsed=0.0,
            frame_index=0,
            resolved_vibe=resolve_vibe("vivid"),
        )
        entered = threading.Event()
        release = threading.Event()
        hook_ran = threading.Event()
        original_generate = animation.generate_frame

        def blocking_generate(time_elapsed, frame_count):
            entered.set()
            self.assertTrue(release.wait(1.0))
            return original_generate(time_elapsed, frame_count)

        animation.generate_frame = blocking_generate
        animation.on_presentation_context_changed = lambda _old, _new: hook_ran.set()
        render = threading.Thread(
            target=animation.generate_frame_with_context, args=(neutral,)
        )
        render.start()
        self.assertTrue(entered.wait(1.0))
        hook_ran.clear()

        update = threading.Thread(
            target=animation.set_presentation_context, args=(vivid,)
        )
        update.start()
        self.assertFalse(hook_ran.wait(0.05))
        self.assertTrue(update.is_alive())

        release.set()
        render.join(1.0)
        update.join(1.0)
        self.assertFalse(render.is_alive())
        self.assertFalse(update.is_alive())
        self.assertTrue(hook_ran.is_set())

    def test_context_change_does_not_jump_continuous_scaled_clock(self):
        manager = self.manager(animation_speed_scale=1.0)
        animation = _ScaledProbe(_Controller())
        manager.current_animation = animation
        manager.frame_count = 0

        first = manager._advance_runtime_context(animation, 1.0, 0)
        self.assertAlmostEqual(first.scaled_elapsed, 2.0)
        manager.set_vibe("vivid")
        self.assertAlmostEqual(animation.presentation_context.scaled_elapsed, 2.0)
        same_time = manager._advance_runtime_context(animation, 1.0, 1)
        self.assertAlmostEqual(same_time.scaled_elapsed, 2.0)
        next_time = manager._advance_runtime_context(animation, 2.0, 2)
        self.assertAlmostEqual(next_time.scaled_elapsed, 4.3)

    def test_all_timing_adapters_apply_authored_vibe_and_operator_tempo_once(self):
        manager = self.manager(animation_speed_scale=0.5)
        manager.set_vibe("vivid")
        expected_scale = 2.0 * 1.15 * 0.5

        cases = (
            (_Probe, 2.0, expected_scale),
            (_ScaledProbe, 2.0 * expected_scale, 1.0),
            (_WallClockProbe, 2.0, 2.0),
        )
        for probe_type, expected_elapsed, expected_speed in cases:
            with self.subTest(adapter=probe_type.TIMING_ADAPTER):
                animation = probe_type(_Controller())
                context = manager._runtime_context(
                    animation,
                    unscaled_elapsed=2.0,
                    scaled_elapsed=2.0 * expected_scale,
                    frame_index=4,
                )
                animation.generate_frame_with_context(context)
                elapsed, speed, frame_index = animation.observed[-1]
                self.assertAlmostEqual(elapsed, expected_elapsed)
                self.assertAlmostEqual(speed, expected_speed)
                self.assertEqual(frame_index, 4)
                self.assertAlmostEqual(animation.authored_params["speed"], 2.0)

    def test_luminance_is_applied_once_and_preserve_skips_color_grade(self):
        animation = _PreserveProbe(_Controller())
        source = np.full((_Controller.total_leds, 3), (100, 50, 20), dtype=np.uint8)
        state = AnimationManager._empty_presentation_state()
        quiet = resolve_vibe("quiet").profile

        output, changed = AnimationManager._apply_vibe_presentation(
            animation, source, profile=quiet, changed=True, state=state
        )
        cached, cached_changed = AnimationManager._apply_vibe_presentation(
            animation, source, profile=quiet, changed=False, state=state
        )

        np.testing.assert_array_equal(output[0], (55, 28, 11))
        self.assertTrue(changed)
        self.assertIs(cached, output)
        self.assertFalse(cached_changed)

    def test_neutral_is_byte_exact_and_non_neutral_grade_is_visible(self):
        animation = _Probe(_Controller())
        source = np.tile(np.asarray((110, 40, 20), dtype=np.uint8), (6, 1))
        state = AnimationManager._empty_presentation_state()
        vivid, _ = AnimationManager._apply_vibe_presentation(
            animation,
            source,
            profile=resolve_vibe("vivid").profile,
            changed=True,
            state=state,
        )
        neutral, neutral_changed = AnimationManager._apply_vibe_presentation(
            animation,
            source,
            profile=resolve_vibe("neutral").profile,
            changed=False,
            state=state,
        )
        steady, steady_changed = AnimationManager._apply_vibe_presentation(
            animation,
            source,
            profile=resolve_vibe("neutral").profile,
            changed=False,
            state=state,
        )

        self.assertIs(neutral, source)
        self.assertTrue(neutral_changed)
        self.assertIs(steady, source)
        self.assertFalse(steady_changed)
        self.assertFalse(np.array_equal(vivid, source))

    def test_real_preview_vibe_is_isolated_and_uses_framework_luminance(self):
        manager = self.manager()
        quiet = manager.get_animation_preview("simple_test", vibe="quiet")
        neutral = manager.get_animation_preview("simple_test", vibe="neutral")

        self.assertEqual(quiet["frame_data"][0], [140, 0, 0])
        self.assertEqual(neutral["frame_data"][0], [255, 0, 0])
        self.assertEqual(manager.get_vibe_state()["vibe_id"], "neutral")


if __name__ == "__main__":
    unittest.main()
