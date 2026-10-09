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

    def start_canonical_scene(self, payload):
        self._requested_scene = deepcopy(payload)
        self._playback_error = None
        try:
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
            self._present_frame(first.pixels, None, False, getattr(self.controller, 'inline_show', False))
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
