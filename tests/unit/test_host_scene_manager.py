"""Focused Phase 2B acceptance for manager-owned host scenes."""

from __future__ import annotations

import contextlib
import io
import threading
import time
import unittest

import numpy as np

from animation.core.base import AnimationBase, RenderedFrame, StatefulAnimationBase
from animation.core.manager import AnimationManager
from animation.core.presentation_contracts import OverlayFrame


class _Controller:
    debug = False
    inline_show = True

    def __init__(self, strips=2, leds_per_strip=4):
        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip
        self.full_frames = []
        self.partial_frames = []
        self.clear_calls = 0

    def configure(self):
        pass

    def set_all_pixels(self, frame):
        self.full_frames.append(np.asarray(frame).copy())

    def set_frame(self, frame, *, dirty_ranges):
        self.partial_frames.append((np.asarray(frame).copy(), dirty_ranges))

    def show(self):
        pass

    def clear(self):
        self.clear_calls += 1


class _BlockingController(_Controller):
    def __init__(self):
        super().__init__()
        self.send_started = threading.Event()
        self.release_send = threading.Event()
        self._send_lock = threading.Lock()
        self.active_sends = 0
        self.max_active_sends = 0
        self.block_first_send = True

    def set_all_pixels(self, frame):
        with self._send_lock:
            self.active_sends += 1
            self.max_active_sends = max(self.max_active_sends, self.active_sends)
        try:
            if self.block_first_send:
                self.block_first_send = False
                self.send_started.set()
                self.release_send.wait(timeout=4.0)
            super().set_all_pixels(frame)
        finally:
            with self._send_lock:
                self.active_sends -= 1


class _ProbeBase(AnimationBase):
    instances = []
    INTERACTION_TYPES = frozenset(("primary",))
    VIBE_CAPABILITIES = frozenset(("luminance",))
    VIBE_COLOR_POLICY = "preserve"

    def __init__(self, controller, config=None):
        super().__init__(controller, config)
        self.starts = self.stops = self.cleanups = 0
        self.render_calls = 0
        self.interactions = []
        self._cached = np.zeros((controller.total_leds, 3), dtype=np.uint8)
        self._last_color = None
        type(self).instances.append(self)

    def start(self):
        self.starts += 1
        super().start()

    def stop(self):
        self.stops += 1
        super().stop()

    def cleanup(self):
        self.cleanups += 1
        super().cleanup()

    def generate_frame(self, _elapsed, _frame_index):
        color = int(self.params.get("color", 40))
        if color == self._last_color:
            return RenderedFrame(self._cached, changed=False)
        self.render_calls += 1
        self._last_color = color
        self._cached[:] = (color, color, color)
        return RenderedFrame(self._cached, dirty_ranges=((0, self.get_pixel_count()),))

    def handle_interaction(self, kind, x, y, strength=1.0):
        self.interactions.append((kind, x, y, strength))
        return True


class _OpticsProbe(_ProbeBase):
    instances = []

    def __init__(self, controller, config=None):
        super().__init__(controller, config)
        self.optics_calls = 0

    def framework_plant_modifiers_active(self):
        return bool(self.params.get("optics", False))

    def framework_plant_modifier_refresh_pending(self):
        return self.framework_plant_modifiers_active() and self.optics_calls == 0

    def apply_framework_plant_modifiers(self, pixels, *, changed=True):
        self.optics_calls += 1
        if not self.framework_plant_modifiers_active():
            return pixels
        output = np.asarray(pixels).copy()
        output += 1
        return output


class _ChangingBase(_ProbeBase):
    instances = []

    def generate_frame(self, _elapsed, _frame_index):
        self.render_calls += 1
        color = self.render_calls * 20
        self._cached[:] = (color, color, color)
        return RenderedFrame(
            self._cached, dirty_ranges=((0, self.get_pixel_count()),)
        )


class _ProbeOverlay(AnimationBase):
    instances = []
    INTERACTION_TYPES = frozenset(("secondary",))
    VIBE_CAPABILITIES = frozenset(("luminance",))
    VIBE_COLOR_POLICY = "preserve"

    def __init__(self, controller, config=None):
        super().__init__(controller, config)
        self.starts = self.stops = self.cleanups = 0
        self.calls = self.render_calls = 0
        self.interactions = []
        self._cached = np.zeros((controller.total_leds, 4), dtype=np.uint8)
        self._key = None
        self._revision = 0
        type(self).instances.append(self)

    def start(self):
        self.starts += 1
        super().start()

    def stop(self):
        self.stops += 1
        super().stop()

    def cleanup(self):
        self.cleanups += 1
        super().cleanup()

    def generate_frame(self, elapsed, _frame_index):
        self.calls += 1
        tick = int(elapsed)
        position = int(self.params.get("position", 0))
        key = (tick, position, int(self.params.get("alpha", 255)))
        if key == self._key:
            return OverlayFrame(self._cached, revision=self._revision, changed=False)
        self.render_calls += 1
        previous = np.flatnonzero(self._cached[:, 3])
        self._cached = np.zeros_like(self._cached)
        alpha = key[2]
        self._cached[position] = (alpha, 0, 0, alpha)
        current = np.flatnonzero(self._cached[:, 3])
        dirty = sorted(set(previous.tolist()) | set(current.tolist()))
        ranges = tuple((index, index + 1) for index in dirty)
        self._key = key
        self._revision += 1
        return OverlayFrame(
            self._cached, revision=self._revision, dirty_ranges=ranges
        )

    def handle_interaction(self, kind, x, y, strength=1.0):
        self.interactions.append((kind, x, y, strength))
        return True


class _BadOverlay(_ProbeOverlay):
    instances = []

    def generate_frame(self, _elapsed, _frame_index):
        return np.zeros((self.controller.total_leds, 3), dtype=np.uint8)


class _StatefulProbe(StatefulAnimationBase):
    def run_animation(self):
        pass


class _FullSceneProbe(_ProbeBase):
    instances = []


class HostSceneManagerTests(unittest.TestCase):
    def setUp(self):
        for cls in (
            _ProbeBase, _OpticsProbe, _ProbeOverlay, _BadOverlay,
            _FullSceneProbe, _ChangingBase,
        ):
            cls.instances.clear()
        self.controller = _Controller()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager = AnimationManager(self.controller, auto_start=False)
        self.manager._launch_animation_loop = lambda: None
        self.manager.plugin_loader.loaded_plugins.update({
            "probe_base": _ProbeBase,
            "optics_base": _OpticsProbe,
            "probe_overlay": _ProbeOverlay,
            "bad_overlay": _BadOverlay,
            "stateful_probe": _StatefulProbe,
            "full_scene_probe": _FullSceneProbe,
            "changing_base": _ChangingBase,
        })
        self.manager.plugin_loader.plugin_manifests.update({
            "probe_base": {"role": "background"},
            "optics_base": {"role": "background"},
            "probe_overlay": {"role": "overlay"},
            "bad_overlay": {"role": "overlay"},
            "stateful_probe": {"role": "background"},
            "full_scene_probe": {"role": "full_scene"},
            "changing_base": {"role": "background"},
        })

    def tearDown(self):
        self.manager.stop_animation(clear_leds=False)

    def start(self, background="probe_base", **kwargs):
        self.assertTrue(self.manager.start_composed_scene(
            background, overlay_name="probe_overlay", **kwargs
        ))
        return self.manager._scene_background, self.manager._scene_overlay

    @staticmethod
    def component_time(component, elapsed):
        return component["started_at"] + elapsed

    def test_independent_lifecycle_updates_and_remove_do_not_restart_background(self):
        background, overlay = self.start()
        base_animation = background["animation"]
        overlay_animation = overlay["animation"]

        self.assertTrue(self.manager.update_overlay_parameters({"position": 2}))
        self.assertTrue(self.manager.disable_overlay())
        self.assertTrue(self.manager.enable_overlay())
        self.assertEqual((base_animation.starts, base_animation.stops), (1, 0))
        self.assertTrue(self.manager.remove_overlay())
        self.assertEqual((overlay_animation.stops, overlay_animation.cleanups), (2, 1))
        self.assertEqual((base_animation.starts, base_animation.stops), (1, 0))

        self.manager.stop_animation(clear_leds=False)
        self.assertEqual((base_animation.stops, base_animation.cleanups), (2, 1))

    def test_one_hz_overlay_caches_at_two_hundred_manager_calls(self):
        background, overlay = self.start()
        start = overlay["started_at"]
        changed = 0
        for index in range(200):
            frame = self.manager.render_composed_scene_frame(
                now=start + index / 200.0
            )
            changed += int(frame.changed)

        animation = overlay["animation"]
        self.assertEqual(animation.calls, 201)
        self.assertEqual(animation.render_calls, 1)
        self.assertEqual(overlay["changed_calls"], 1)
        self.assertEqual(changed, 0)

        rolled = self.manager.render_composed_scene_frame(now=start + 1.0)
        self.assertTrue(rolled.changed)
        self.assertEqual(animation.render_calls, 2)

    def test_base_change_recomposes_stable_foreground(self):
        background, overlay = self.start(background_config={"color": 20})
        overlay_animation = overlay["animation"]
        first = self.manager.render_composed_scene_frame(
            now=self.component_time(background, 0.1)
        )
        self.assertFalse(first.changed)

        self.assertTrue(self.manager.update_animation_parameters({"color": 80}))
        changed = self.manager.render_composed_scene_frame(
            now=self.component_time(background, 0.2)
        )
        self.assertTrue(changed.changed)
        np.testing.assert_array_equal(changed.pixels[0], (255, 0, 0))
        np.testing.assert_array_equal(changed.pixels[1], (80, 80, 80))
        self.assertEqual(overlay_animation.render_calls, 1)

    def test_overlay_update_preserves_sparse_dirty_transport(self):
        background, overlay = self.start()
        self.assertTrue(self.manager.update_overlay_parameters({"position": 3}))
        frame = self.manager.render_composed_scene_frame(
            now=self.component_time(overlay, 0.2)
        )
        self.assertTrue(frame.changed)
        self.assertEqual(frame.dirty_ranges, ((0, 1), (3, 4)))

        self.manager._present_frame(
            frame.pixels, frame.dirty_ranges, True, self.controller.inline_show
        )
        self.assertEqual(self.controller.partial_frames[-1][1], ((0, 1), (3, 4)))

    def test_disable_and_remove_clear_previous_overlay_coverage(self):
        background, overlay = self.start(background_config={"color": 20})
        self.assertTrue(self.manager.disable_overlay())
        disabled = self.manager.render_composed_scene_frame(
            now=self.component_time(background, 0.1)
        )
        self.assertEqual(disabled.dirty_ranges, ((0, 1),))
        np.testing.assert_array_equal(disabled.pixels[0], (20, 20, 20))

        self.assertTrue(self.manager.enable_overlay())
        restored = self.manager.render_composed_scene_frame(
            now=self.component_time(overlay, 0.2)
        )
        self.assertEqual(restored.dirty_ranges, ((0, 1),))
        np.testing.assert_array_equal(restored.pixels[0], (255, 0, 0))

        self.assertTrue(self.manager.remove_overlay())
        removed = self.manager.render_composed_scene_frame(
            now=self.component_time(background, 0.3)
        )
        self.assertEqual(removed.dirty_ranges, ((0, 1),))
        np.testing.assert_array_equal(removed.pixels[0], (20, 20, 20))

    def test_overlay_update_cannot_override_manager_plant_authority(self):
        _background, overlay = self.start()
        manager_state = {
            "version": 1,
            "active": ["illuminate"],
            "strengths": {"illuminate": 0.7},
        }
        self.manager.set_plant_modifiers(manager_state)

        self.assertTrue(self.manager.update_overlay_parameters({
            "position": 2,
            "plant_aware": True,
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
        }))

        animation = overlay["animation"]
        self.assertFalse(animation.params["plant_aware"])
        self.assertEqual(animation.params["plant_modifiers"], manager_state)
        self.assertNotIn("plant_aware", overlay["config"])
        self.assertNotIn("plant_modifiers", overlay["config"])

    def test_plant_optics_and_vibe_luminance_are_each_applied_once(self):
        background, _overlay = self.start(
            background="optics_base",
            background_config={"color": 100, "optics": True},
            overlay_config={"alpha": 0},
        )
        animation = background["animation"]
        # Initial scene render owns exactly one framework-optics call.
        self.assertEqual(animation.optics_calls, 1)
        self.manager.set_vibe("quiet")
        frame = self.manager.render_composed_scene_frame(
            now=self.component_time(background, 0.1)
        )
        self.assertEqual(animation.optics_calls, 2)
        # (100 + one optics pass) * quiet's 0.55 luminance, rounded once.
        np.testing.assert_array_equal(frame.pixels[1], (56, 56, 56))

    def test_optics_off_preserves_neutral_compositor_bytes(self):
        background, _overlay = self.start(
            background="optics_base",
            background_config={"color": 77, "optics": False},
            overlay_config={"alpha": 0},
        )
        frame = self.manager.render_composed_scene_frame(
            now=self.component_time(background, 0.1)
        )
        np.testing.assert_array_equal(
            frame.pixels, np.full((self.controller.total_leds, 3), 77, np.uint8)
        )

    def test_preview_is_isolated_no_io_and_matches_neutral_live_rules(self):
        preview = self.manager.get_scene_preview(
            "probe_base", {"color": 33},
            "probe_overlay", {"alpha": 128, "position": 2},
            elapsed=0.0,
        )
        self.assertFalse(self.controller.full_frames)
        self.assertFalse(self.controller.partial_frames)
        self.assertEqual(preview["mode"], "scene")

        background, _overlay = self.start(
            background_config={"color": 33},
            overlay_config={"alpha": 128, "position": 2},
        )
        live = self.manager.current_frame_data
        np.testing.assert_array_equal(live, np.asarray(preview["frame_data"], np.uint8))
        self.assertEqual(background["animation"].starts, 1)

    def test_real_clock_scene_runs_on_three_backgrounds_and_preview_matches_live(self):
        self.manager.stop_animation(clear_leds=False)
        controller = _Controller(strips=32, leds_per_strip=138)
        with contextlib.redirect_stdout(io.StringIO()):
            manager = AnimationManager(controller, auto_start=False)
        manager._launch_animation_loop = lambda: None
        wall_time = [1_786_543_210.25]
        manager._wall_time = lambda: wall_time[0]
        try:
            preview = manager.get_scene_preview(
                "gradient", {"animated": False},
                "clock_overlay", {"show_seconds": True},
                elapsed=0.0,
            )
            self.assertTrue(manager.start_composed_scene(
                "gradient", {"animated": False},
                "clock_overlay", {"show_seconds": True},
            ))
            np.testing.assert_array_equal(
                manager.current_frame_data,
                np.asarray(preview["frame_data"], dtype=np.uint8),
            )

            for background_name in ("gradient", "aurora_curtains", "sparkle"):
                with self.subTest(background=background_name):
                    if manager._scene_background["name"] != background_name:
                        self.assertTrue(manager.start_composed_scene(
                            background_name,
                            overlay_name="clock_overlay",
                            overlay_config={"show_seconds": True},
                        ))
                    background = manager._scene_background
                    overlay = manager._scene_overlay
                    animation = background["animation"]
                    render_count = overlay["render_count"]
                    now = max(background["started_at"], overlay["started_at"])
                    manager.render_composed_scene_frame(now=now + 0.1)
                    wall_time[0] += 1.0
                    manager.render_composed_scene_frame(now=now + 0.105)
                    self.assertIs(background["animation"], animation)
                    self.assertEqual(overlay["render_count"], render_count + 1)
                    self.assertEqual(
                        manager.get_current_status()["scene"]["background"]["name"],
                        background_name,
                    )
        finally:
            manager.stop_animation(clear_leds=False)

    def test_targeted_interactions_never_broadcast(self):
        background, overlay = self.start()
        self.assertTrue(self.manager.dispatch_interaction(
            "primary", 0.0, 0.0, target="background"
        ))
        self.assertTrue(self.manager.dispatch_interaction(
            "secondary", 1.0, 1.0, target="overlay"
        ))
        self.assertEqual(len(background["animation"].interactions), 1)
        self.assertEqual(len(overlay["animation"].interactions), 1)
        with self.assertRaisesRegex(ValueError, "target"):
            self.manager.dispatch_interaction("primary", 0, 0, target="both")

    def test_invalid_overlay_output_fails_start_and_cleans_both_components(self):
        self.assertFalse(self.manager.start_composed_scene(
            "probe_base", overlay_name="bad_overlay"
        ))
        self.assertFalse(self.manager.is_running)
        self.assertFalse(self.manager._scene_mode)
        self.assertGreaterEqual(_ProbeBase.instances[-1].cleanups, 1)
        self.assertGreaterEqual(_BadOverlay.instances[-1].cleanups, 1)

    def test_stateful_components_are_rejected_before_construction(self):
        self.assertFalse(self.manager.start_composed_scene(
            "stateful_probe", overlay_name="probe_overlay"
        ))
        self.assertFalse(self.manager.is_running)

    def test_compatibility_full_scene_is_rejected_as_a_composed_background(self):
        self.assertFalse(self.manager.start_composed_scene(
            "full_scene_probe", overlay_name="probe_overlay"
        ))
        with self.assertRaisesRegex(ValueError, "invalid scene background"):
            self.manager.get_scene_preview(
                "full_scene_probe", overlay_name="probe_overlay"
            )

    def test_legacy_start_and_catalog_remain_background_only(self):
        names = {item["plugin_name"] for item in self.manager.list_animations()}
        self.assertNotIn("probe_overlay", names)
        self.assertIsNone(self.manager.get_animation_info("probe_overlay"))
        self.assertFalse(self.manager.start_animation("probe_overlay"))
        with self.assertRaisesRegex(ValueError, "get_scene_preview"):
            self.manager.get_animation_preview("probe_overlay")
        with self.assertRaisesRegex(ValueError, "composed scene"):
            self.manager.apply_device_state({"animation": "probe_overlay"})
        self.assertTrue(self.manager.start_animation("probe_base", {"color": 9}))
        self.assertEqual(self.manager.get_current_status()["mode"], "animation")
        self.assertNotIn("scene", self.manager.get_current_status())
        self.assertNotIn("clock_overlay", self.manager.refresh_plugins())

    def test_blocked_old_presentation_cannot_overlap_or_rejoin_a_restart(self):
        self.manager.stop_animation(clear_leds=False)
        controller = _BlockingController()
        with contextlib.redirect_stdout(io.StringIO()):
            manager = AnimationManager(controller, auto_start=False)
        manager.plugin_loader.loaded_plugins.update({
            "probe_base": _ProbeBase,
            "changing_base": _ChangingBase,
            "probe_overlay": _ProbeOverlay,
        })
        manager.plugin_loader.plugin_manifests.update({
            "probe_base": {"role": "background"},
            "changing_base": {"role": "background"},
            "probe_overlay": {"role": "overlay"},
        })
        restart_result = []
        try:
            manager.set_vibe("quiet")
            self.assertTrue(manager.start_composed_scene(
                "changing_base", overlay_name="probe_overlay",
                overlay_config={"alpha": 0},
            ))
            old_thread = manager.animation_thread
            self.assertTrue(controller.send_started.wait(timeout=1.0))

            restart = threading.Thread(target=lambda: restart_result.append(
                manager.start_composed_scene(
                    "changing_base", overlay_name="probe_overlay",
                    overlay_config={"alpha": 0},
                )
            ))
            restart.start()
            time.sleep(1.1)
            self.assertTrue(restart.is_alive())
            controller.release_send.set()
            restart.join(timeout=3.0)
            self.assertFalse(restart.is_alive())
            self.assertEqual(restart_result, [True])
            old_thread.join(timeout=1.0)
            self.assertFalse(old_thread.is_alive())
            self.assertEqual(controller.max_active_sends, 1)
            # Initial synchronous render is color 20. The blocked first loop
            # presentation is color 40, reduced once by Quiet luminance to 22.
            # The loop generates color 60 while that presentation is blocked;
            # rotating compositor/final buffers must keep the pending bytes at 22.
            np.testing.assert_array_equal(controller.full_frames[0][1], (22, 22, 22))
        finally:
            controller.release_send.set()
            manager.stop_animation(clear_leds=False)


if __name__ == "__main__":
    unittest.main()
