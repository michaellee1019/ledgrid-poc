"""Manager acceptance for Phase 3C read-only installation-profile context."""

from __future__ import annotations

import json
from pathlib import Path
import random
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

from animation.core.base import AnimationBase, RenderedFrame
from animation.core.compositing import HostForegroundCompositor, HostSceneCompositor
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.installation_profile_runtime import (
    EMPTY_INSTALLATION_PROFILE_DIGEST,
    InstallationProfileRuntimeError,
    InstallationProfileRuntimeView,
)
from animation.core.manager import AnimationManager
from animation.core.presentation_contracts import OverlayFrame


ROOT = Path(__file__).resolve().parents[2]
PROFILE_FIXTURE = ROOT / "tests" / "fixtures" / "installation_profile_v1.bin"


class _Controller:
    debug = False
    inline_show = True

    def __init__(self, strips: int = 33, leds_per_strip: int = 138):
        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip
        self.receiver_commands: list[str] = []

    def configure(self):
        pass

    def update_presentation_context(self, *_args, **_kwargs):
        self.receiver_commands.append("update_presentation_context")
        return True

    def start_local_background(self, *_args, **_kwargs):
        self.receiver_commands.append("start_local_background")
        return True

    def update_local_background_params(self, *_args, **_kwargs):
        self.receiver_commands.append("update_local_background_params")
        return True

    def publish_sparse_overlay(self, *_args, **_kwargs):
        self.receiver_commands.append("publish_sparse_overlay")
        return True

    def renew_sparse_overlay(self, *_args, **_kwargs):
        self.receiver_commands.append("renew_sparse_overlay")
        return True

    def clear_sparse_overlay(self, *_args, **_kwargs):
        self.receiver_commands.append("clear_sparse_overlay")
        return True

    def set_all_pixels(self, *_args, **_kwargs):
        self.receiver_commands.append("set_all_pixels")

    def show(self):
        self.receiver_commands.append("show")

    def clear(self):
        self.receiver_commands.append("clear")


class _ProfileProbe(AnimationBase):
    instances: list["_ProfileProbe"] = []

    def __init__(self, controller, config=None):
        super().__init__(controller, config)
        self.params = {**self.default_params, "speed": 1.75, **(config or {})}
        self.semantic_state = {"steps": 11, "token": object()}
        self.rng = random.Random(0xC0FFEE)
        self.context_changes = []
        self._frame = np.zeros((controller.total_leds, 3), dtype=np.uint8)
        type(self).instances.append(self)

    def on_presentation_context_changed(self, old, new):
        self.context_changes.append((old, new))

    def generate_frame(self, _elapsed, _frame_index):
        return RenderedFrame(self._frame, changed=False, dirty_ranges=())


class _ProfileOverlay(_ProfileProbe):
    instances: list["_ProfileOverlay"] = []

    def __init__(self, controller, config=None):
        super().__init__(controller, config)
        self._overlay = np.zeros((controller.total_leds, 4), dtype=np.uint8)

    def generate_frame(self, _elapsed, _frame_index):
        return OverlayFrame(
            self._overlay, revision=0, changed=False, dirty_ranges=()
        )


class _AutoStartProbeManager(AnimationManager):
    def refresh_plugins(self):
        return {}

    def start_animation(self, *_args, **_kwargs):
        self.selection_seen_by_auto_start = self._installation_profile_selection.view
        return True


class ManagerInstallationProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.library = InstallationProfileLibrary(
            Path(cls.temporary_directory.name) / "installation_profile_library"
        )
        cls.receipt = cls.library.publish(PROFILE_FIXTURE.read_bytes())
        cls.digest = cls.receipt.content_digest

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    def setUp(self):
        _ProfileProbe.instances.clear()
        _ProfileOverlay.instances.clear()

    def manager(self, controller=None, **kwargs):
        with patch.object(AnimationManager, "refresh_plugins", return_value={}):
            return AnimationManager(
                controller or _Controller(), auto_start=False, **kwargs
            )

    @staticmethod
    def install_probe_plugins(manager):
        manager.plugin_loader.loaded_plugins.update({
            "profile_probe": _ProfileProbe,
            "profile_overlay": _ProfileOverlay,
        })
        manager.plugin_loader.plugin_manifests.update({
            "profile_probe": {"role": "background"},
            "profile_overlay": {"role": "overlay"},
        })

    def test_default_is_legacy_parity_and_initial_profile_precedes_auto_start(self):
        legacy = self.manager()
        context = legacy._runtime_context(
            _ProfileProbe(legacy.controller),
            unscaled_elapsed=0.0,
            scaled_elapsed=0.0,
            frame_index=0,
        )
        self.assertEqual(dict(context.installation_profile_view), {})
        self.assertEqual(
            legacy.get_current_status()["installation_profile_digest"],
            EMPTY_INSTALLATION_PROFILE_DIGEST,
        )

        manager = _AutoStartProbeManager(
            _Controller(),
            default_animation="profile_probe",
            installation_profile_library=self.library,
            installation_profile_digest=self.digest,
        )
        self.assertIsInstance(
            manager.selection_seen_by_auto_start, InstallationProfileRuntimeView
        )
        self.assertEqual(
            manager.selection_seen_by_auto_start.profile_digest, self.digest
        )

    def test_strict_digest_library_and_geometry_validation(self):
        with self.assertRaisesRegex(
            InstallationProfileRuntimeError, "lowercase SHA-256"
        ):
            self.manager(installation_profile_digest="not-a-digest")
        with self.assertRaisesRegex(
            InstallationProfileRuntimeError, "managed InstallationProfileLibrary"
        ):
            self.manager(installation_profile_digest=self.digest)
        with self.assertRaisesRegex(
            InstallationProfileRuntimeError, "does not match controller geometry"
        ):
            self.manager(
                _Controller(2, 3),
                installation_profile_library=self.library,
                installation_profile_digest=self.digest,
            )

        manager = self.manager(
            _Controller(2, 3), installation_profile_library=self.library
        )
        before = manager.get_current_status()["installation_profile"]
        with self.assertRaisesRegex(
            InstallationProfileRuntimeError, "does not match controller geometry"
        ):
            manager.select_installation_profile(self.digest)
        self.assertEqual(manager.get_current_status()["installation_profile"], before)

    def test_preflight_is_read_only_and_failed_switch_is_atomic(self):
        manager = self.manager(installation_profile_library=self.library)
        before = manager.get_current_status()["installation_profile"]
        preview = manager.preflight_installation_profile(self.digest)
        self.assertTrue(preview["selected"])
        self.assertEqual(preview["selected_digest"], self.digest)
        self.assertEqual(manager.get_current_status()["installation_profile"], before)

        manager.select_installation_profile(self.digest)
        selected_status = manager.get_current_status()["installation_profile"]
        selected_view = manager._installation_profile_selection.view
        missing = "1" * 64
        with self.assertRaisesRegex(
            InstallationProfileRuntimeError, "failed to resolve managed"
        ):
            manager.select_installation_profile(missing)
        self.assertEqual(
            manager.get_current_status()["installation_profile"], selected_status
        )
        self.assertIs(manager._installation_profile_selection.view, selected_view)

    def test_same_digest_is_idempotent_and_status_is_json_safe(self):
        manager = self.manager(installation_profile_library=self.library)
        first = manager.select_installation_profile(self.digest)
        presentation_revision = manager._presentation_revision
        view = manager._installation_profile_selection.view
        second = manager.select_installation_profile(self.digest)

        self.assertEqual(second, first)
        self.assertEqual(manager._presentation_revision, presentation_revision)
        self.assertIs(manager._installation_profile_selection.view, view)
        status = manager.get_current_status()
        self.assertEqual(status["installation_profile_digest"], self.digest)
        self.assertEqual(
            status["installation_profile"]["view"]["profile_digest"], self.digest
        )
        json.dumps(status["installation_profile"])

        cleared = manager.select_installation_profile(None)
        self.assertFalse(cleared["selected"])
        cleared_revision = manager._presentation_revision
        self.assertEqual(
            manager.get_current_status()["installation_profile_digest"],
            EMPTY_INSTALLATION_PROFILE_DIGEST,
        )
        manager.select_installation_profile(EMPTY_INSTALLATION_PROFILE_DIGEST)
        self.assertEqual(manager._presentation_revision, cleared_revision)

    def test_runtime_context_reads_one_atomic_profile_view_during_switches(self):
        manager = self.manager(installation_profile_library=self.library)
        animation = _ProfileProbe(manager.controller)
        observed = []
        failures = []

        def read_contexts():
            try:
                for frame_index in range(200):
                    context = manager._runtime_context(
                        animation,
                        unscaled_elapsed=0.0,
                        scaled_elapsed=0.0,
                        frame_index=frame_index,
                    )
                    observed.append(context.installation_profile_view)
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        reader = threading.Thread(target=read_contexts)
        reader.start()
        for _ in range(4):
            manager.select_installation_profile(self.digest)
            manager.select_installation_profile(None)
        reader.join(2.0)

        self.assertFalse(reader.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(observed), 200)
        for view in observed:
            self.assertTrue(
                isinstance(view, InstallationProfileRuntimeView)
                or dict(view) == {}
            )

    def test_racing_selectors_serialize_status_and_finish_with_matching_context(self):
        manager = self.manager(installation_profile_library=self.library)
        animation = _ProfileProbe(manager.controller)
        manager.current_animation = animation
        manager.current_animation_name = "profile_probe"
        manager._presentation_refresh_pending = False

        first_selected = threading.Event()
        release_first = threading.Event()
        second_entered_selection = threading.Event()
        status_completed = threading.Event()
        original_select = manager._installation_profile_selection.select

        def blocking_select(digest):
            changed = original_select(digest)
            if digest == self.digest:
                first_selected.set()
                self.assertTrue(release_first.wait(2.0))
            else:
                second_entered_selection.set()
            return changed

        manager._installation_profile_selection.select = blocking_select
        results = {}

        first = threading.Thread(
            target=lambda: results.setdefault(
                "first", manager.select_installation_profile(self.digest)
            )
        )
        second = threading.Thread(
            target=lambda: results.setdefault(
                "second", manager.select_installation_profile(None)
            )
        )

        def read_status():
            results["status"] = manager.get_current_status()
            status_completed.set()

        first.start()
        self.assertTrue(first_selected.wait(1.0))
        # Context reads remain allocation-free and see the immutable selection
        # atomically even while dependent presentation invalidation is pending.
        in_flight_context = manager._runtime_context(
            animation,
            unscaled_elapsed=0.0,
            scaled_elapsed=0.0,
            frame_index=0,
        )
        self.assertEqual(
            in_flight_context.installation_profile_view.profile_digest,
            self.digest,
        )

        second.start()
        status_reader = threading.Thread(target=read_status)
        status_reader.start()
        self.assertFalse(second_entered_selection.wait(0.05))
        self.assertFalse(status_completed.wait(0.05))

        release_first.set()
        for thread in (first, second, status_reader):
            thread.join(2.0)
            self.assertFalse(thread.is_alive())

        self.assertEqual(results["first"]["selected_digest"], self.digest)
        self.assertEqual(
            results["second"]["selected_digest"],
            EMPTY_INSTALLATION_PROFILE_DIGEST,
        )
        published = results["status"]["installation_profile"]
        if published["selected"]:
            self.assertEqual(
                published["selected_digest"],
                published["view"]["profile_digest"],
            )
        final_status = manager.get_current_status()
        self.assertEqual(
            final_status["installation_profile_digest"],
            EMPTY_INSTALLATION_PROFILE_DIGEST,
        )
        self.assertEqual(
            dict(animation.presentation_context.installation_profile_view), {}
        )
        self.assertEqual(manager.controller.receiver_commands, [])

    def test_live_switch_preserves_semantics_rng_time_params_and_marks_frame_dirty(self):
        controller = _Controller()
        manager = self.manager(
            controller, installation_profile_library=self.library
        )
        animation = _ProfileProbe(controller, {"custom": 9})
        manager.current_animation = animation
        manager.current_animation_name = "profile_probe"
        manager.frame_count = 23
        manager._scaled_elapsed = 4.25
        manager._last_unscaled_elapsed = 3.5
        manager._presentation_refresh_pending = False
        manager._live_presentation_state = manager._empty_presentation_state()

        _, before_changed, _ = manager._render_compatibility_frame(3.5)
        self.assertTrue(before_changed)
        _, steady_changed, steady_dirty = manager._render_compatibility_frame(3.5)
        self.assertFalse(steady_changed)
        self.assertEqual(steady_dirty, ())
        identity = id(animation)
        semantic_state = dict(animation.semantic_state)
        semantic_token = animation.semantic_state["token"]
        rng_state = animation.rng.getstate()
        authored = animation.authored_params_snapshot()
        counters = (
            manager.frame_count,
            manager._scaled_elapsed,
            manager._last_unscaled_elapsed,
            animation.frame_count,
        )

        manager.select_installation_profile(self.digest)

        self.assertEqual(id(manager.current_animation), identity)
        self.assertEqual(animation.semantic_state, semantic_state)
        self.assertIs(animation.semantic_state["token"], semantic_token)
        self.assertEqual(animation.rng.getstate(), rng_state)
        self.assertEqual(animation.authored_params_snapshot(), authored)
        self.assertEqual(
            (
                manager.frame_count,
                manager._scaled_elapsed,
                manager._last_unscaled_elapsed,
                animation.frame_count,
            ),
            counters,
        )
        self.assertIs(
            animation.presentation_context.installation_profile_view,
            manager._installation_profile_selection.view,
        )
        refreshed, changed, dirty = manager._render_compatibility_frame(3.5)
        self.assertEqual(refreshed.shape, (controller.total_leds, 3))
        self.assertTrue(changed)
        self.assertIsNone(dirty)
        self.assertEqual(controller.receiver_commands, [])

    def test_preview_session_keeps_identity_and_next_frame_uses_new_context(self):
        manager = self.manager(installation_profile_library=self.library)
        self.install_probe_plugins(manager)
        first = manager.get_animation_preview_with_params(
            "profile_probe", {"custom": 17}
        )
        session = manager._preview_session
        animation = session["animation"]
        identity = id(animation)
        semantic_token = animation.semantic_state["token"]
        rng_state = animation.rng.getstate()
        clocks = (
            session["frame_count"],
            session["scaled_elapsed"],
            session["last_unscaled_elapsed"],
        )

        manager.select_installation_profile(self.digest)

        self.assertEqual(id(manager._preview_session["animation"]), identity)
        self.assertIs(animation.semantic_state["token"], semantic_token)
        self.assertEqual(animation.rng.getstate(), rng_state)
        self.assertEqual(
            (
                session["frame_count"],
                session["scaled_elapsed"],
                session["last_unscaled_elapsed"],
            ),
            clocks,
        )
        self.assertIs(
            animation.presentation_context.installation_profile_view,
            manager._installation_profile_selection.view,
        )
        second = manager.get_animation_preview_with_params(
            "profile_probe", {"custom": 17}
        )
        self.assertEqual(second["frame_count"], first["frame_count"] + 1)
        self.assertTrue(second["changed"])
        self.assertEqual(id(manager._preview_session["animation"]), identity)

    def test_composed_background_overlay_and_scene_preview_receive_same_view(self):
        manager = self.manager(installation_profile_library=self.library)
        self.install_probe_plugins(manager)
        background = _ProfileProbe(manager.controller)
        overlay = _ProfileOverlay(manager.controller)
        manager._scene_mode = True
        manager._scene_background = manager._new_scene_component(
            "profile_probe", background, {}, started_at=0.0
        )
        manager._scene_overlay = manager._new_scene_component(
            "profile_overlay", overlay, {}, started_at=0.0
        )
        manager._scene_overlay.update({
            "enabled": True,
            "opacity": 255,
            "strip_offset": 0,
            "led_offset": 0,
        })
        manager._scene_compositor = HostSceneCompositor(33, 138)
        manager._presentation_refresh_pending = False
        manager._scene_background["force_changed"] = False
        manager._scene_overlay["force_changed"] = False

        manager.select_installation_profile(self.digest)

        view = manager._installation_profile_selection.view
        self.assertIs(background.presentation_context.installation_profile_view, view)
        self.assertIs(overlay.presentation_context.installation_profile_view, view)
        self.assertIs(manager._scene_background["animation"], background)
        self.assertIs(manager._scene_overlay["animation"], overlay)
        frame = manager.render_composed_scene_frame(now=0.0)
        self.assertTrue(frame.changed)
        self.assertIsNone(frame.dirty_ranges)

        preview = manager.get_scene_preview(
            "profile_probe", overlay_name="profile_overlay", elapsed=1.0
        )
        self.assertTrue(preview["changed"])
        preview_background = _ProfileProbe.instances[-1]
        preview_overlay = _ProfileOverlay.instances[-1]
        self.assertIs(
            preview_background.presentation_context.installation_profile_view, view
        )
        self.assertIs(
            preview_overlay.presentation_context.installation_profile_view, view
        )

    def test_receiver_foreground_context_refresh_emits_no_receiver_command(self):
        controller = _Controller()
        manager = self.manager(
            controller, installation_profile_library=self.library
        )
        overlay = _ProfileOverlay(controller)
        manager._scene_mode = True
        manager._receiver_hybrid_mode = True
        manager._scene_background = {
            "name": "compiled_rainbow",
            "animation": None,
        }
        manager._scene_overlay = manager._new_scene_component(
            "profile_overlay", overlay, {}, started_at=0.0
        )
        manager._scene_overlay.update({
            "enabled": True,
            "opacity": 255,
            "strip_offset": 0,
            "led_offset": 0,
        })
        manager._receiver_foreground_compositor = HostForegroundCompositor(33, 138)

        manager.select_installation_profile(self.digest)

        self.assertEqual(controller.receiver_commands, [])
        self.assertIs(
            overlay.presentation_context.installation_profile_view,
            manager._installation_profile_selection.view,
        )
        foreground = manager._render_receiver_foreground(now=0.0)
        self.assertTrue(foreground.changed)
        self.assertIsNone(foreground.dirty_ranges)
        self.assertEqual(controller.receiver_commands, [])


if __name__ == "__main__":
    unittest.main()
