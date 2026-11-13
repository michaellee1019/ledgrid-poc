"""Canonical Composer Scenes rendered entirely by the host."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import time

from ipc.scene_contract import normalize_composer_scene
from animation.core.scene_runtime import ScenePresentationContext


class CanonicalReceiverSceneMixin:
    """Retain the public manager adapter while receivers accept full RGB only."""

    def scene_v2_component_catalog(self):
        from web.composer_final_preview import current_component_catalog
        return current_component_catalog()

    def _prepare_canonical_receiver_scene(self, payload):
        from web.composer_final_preview import InstalledFinalSceneRuntime
        canonical = normalize_composer_scene(
            {'origin': 'composer', 'scene': payload}, self.scene_v2_component_catalog())
        runtime = InstalledFinalSceneRuntime(
            self.scene_v2_component_catalog(), Path(__file__).resolve().parents[2],
            controller=self.controller)
        runtime.set_installation_profile(self.get_installation_profile_runtime_view())
        runtime.render(ScenePresentationContext(canonical, 0.0, datetime.now().astimezone()))
        return canonical, runtime

    def preflight_scene(self, payload, **_unused):
        if isinstance(payload, dict) and payload.get('schema') == 'ledgrid.scene.v2':
            self._prepare_canonical_receiver_scene(payload)
        else:
            self._resolve_scene_state(payload)

    def start_canonical_scene(self, payload, *, tetris_player=False):
        if tetris_player:
            # A failed game preflight must leave the current scene playing.
            try:
                canonical, runtime = self._prepare_canonical_receiver_scene(payload)
                if canonical.scene['animation']['component_id'] != 'tetris':
                    raise ValueError('Controller play requires a Tetris scene')
                runtime.dispatch_tetris_gamepad(canonical.identity.digest, ('toggle-player',))
            except Exception as exc:
                raise ValueError(f'Tetris player mode could not be prepared: {exc}') from exc
        self._requested_scene = deepcopy(payload)
        self._playback_error = None
        try:
            if not tetris_player:
                canonical, runtime = self._prepare_canonical_receiver_scene(payload)
            if self.stop_animation(clear_leds=False) is False:
                raise RuntimeError('previous host playback did not stop')
            self._reset_run_counters()
            with self._scene_state_guard():
                self._canonical_receiver_scene = canonical
                self._canonical_receiver_runtime = runtime
                self._canonical_host_full_mode = True
                self._scene_mode = True
                self.current_animation_name = canonical.scene['animation']['component_id']
                self.current_animation_hash = canonical.identity.digest
                self.current_preset = None
            first = runtime.render(ScenePresentationContext(canonical, 0.0, datetime.now().astimezone()))
            self._present_frame(first.pixels.copy(), None, False, getattr(self.controller, 'inline_show', False))
            with self.frame_data_lock:
                self.current_frame_data = first.pixels
            self.frames_presented = 1
            self._launch_animation_loop()
            return True
        except Exception as exc:
            self._playback_error = str(exc)
            self.is_running = False
            self.stop_event.set()
            return False

    def tune_canonical_scene(self, payload):
        canonical = normalize_composer_scene(
            {'origin': 'composer', 'scene': payload}, self.scene_v2_component_catalog())
        with self._scene_state_guard():
            previous = self._canonical_receiver_scene
            if (not self.is_running or previous is None
                    or previous.scene['animation']['component_id'] != canonical.scene['animation']['component_id']):
                raise ValueError('Choose an active Scene before tuning')
            self._canonical_receiver_runtime.update_canonical_scene(canonical, self._scene_v2_monotonic_elapsed())
            self._canonical_receiver_scene = canonical
            self.current_animation_hash = canonical.identity.digest
            self._requested_scene = deepcopy(canonical.scene)
            self._playback_error = None
        return True

    def tetris_input_target(self, *, player_only=False):
        with self._scene_state_guard():
            canonical = self._canonical_receiver_scene
            if (self.is_running and self._canonical_host_full_mode and canonical is not None
                    and canonical.scene['animation']['component_id'] == 'tetris'):
                runtime = self._canonical_receiver_runtime
                if not player_only or runtime.tetris_player_active():
                    return runtime
            return None

    def dispatch_tetris_input(self, actions):
        with self._scene_state_guard():
            target = self.tetris_input_target()
            if target is not None and actions:
                target.dispatch_tetris_gamepad(self._canonical_receiver_scene.identity.digest, actions)

    def dispatch_canonical_interaction(self, component_id, scene_digest, kind, x, y, strength=1.0):
        """Gesture only the exact active host renderer, without activating it."""
        with self._scene_state_guard():
            canonical = self._canonical_receiver_scene
            runtime = self._canonical_receiver_runtime
            if not self.is_running or not self._canonical_host_full_mode or canonical is None or runtime is None:
                raise ValueError('No active canonical Scene renderer is available')
            if canonical.identity.digest != scene_digest or canonical.scene['animation']['component_id'] != component_id:
                raise ValueError('Active Scene or animation component changed')
            return runtime.dispatch_animation_interaction(canonical, kind, x, y, strength)
