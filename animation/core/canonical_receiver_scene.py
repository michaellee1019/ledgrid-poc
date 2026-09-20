"""Canonical Scene v2 adapter over the managed receiver transaction transport.

The legacy SceneState object here is private transport bookkeeping only. Its
mutable revision is never used as the authored Scene v2 identity, and its
fallback field is never activated for a canonical transaction.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import hashlib
import uuid

import numpy as np

from animation.core.component_catalog import AlphaBehavior
from animation.core.presentation_contracts import ComponentRef, SceneState, ResolvedVibe, VibeProfile
from animation.core.receiver_presentation import CanonicalFinalPresentation, ReceiverPresentationContext
from animation.core.plant_awareness import PlantModifierState
from ipc.scene_contract import normalize_composer_scene


class CanonicalReceiverSceneMixin:
    def _clear_unverified_host_full_frame(self):
        """Prove black on the pinned roster after a possibly accepted first send."""
        pixels = np.zeros((self.controller.total_leds, 3), dtype=np.uint8)
        request_id = f"host-full-clear-{uuid.uuid4().hex}"
        with self._presentation_io_guard():
            receipt = self.controller.present_displayed_host_full_frame(request_id, pixels)
        if (not isinstance(receipt, dict)
                or receipt.get("request_id") != request_id
                or receipt.get("frame_digest") != hashlib.sha256(pixels.tobytes()).hexdigest()
                or receipt.get("authority_digest") != getattr(
                    self.controller, "receiver_identity_authority_digest", None
                )
                or {item.get("logical_device") for item in receipt.get("displayed_receivers", ())}
                != set(range(5))):
            raise RuntimeError("host-full black restoration proof is incomplete or misattributed")
        self._host_full_first_frame_uncertain = False

    def _host_full_sparkle_eligible(self, canonical):
        """Only the current opaque, widget-free Sparkle has a hidden base."""
        scene = canonical.scene
        animation = scene["animation"]
        background = scene["background"]
        if (animation["component_id"] != "sparkle"
                or background["component_id"] != "native_aurora"
                or scene["widgets"]
                or not callable(getattr(self.controller, "present_displayed_host_full_frame", None))):
            return False
        descriptor = self.scene_v2_component_catalog().require(
            provider=animation["provider"], component_id="sparkle",
            version=animation["version"],
        )
        return descriptor.alpha_behavior is AlphaBehavior.OPAQUE

    def scene_v2_component_catalog(self):
        from web.composer_final_preview import current_component_catalog
        return current_component_catalog()

    def _prepare_canonical_receiver_scene(self, payload, *, installation_profile_digest=None):
        from web.composer_final_preview import InstalledFinalSceneRuntime
        canonical = normalize_composer_scene({"origin": "composer", "scene": payload}, self.scene_v2_component_catalog())
        error = self._receiver_hybrid_capability_error(managed_native=True)
        if error:
            raise ValueError(error)
        if not self.feature_flags.receiver_geometry_profile or not all(callable(getattr(self.controller, name, None)) for name in ('installation_profile_wall', 'install_installation_profile')):
            raise ValueError("canonical receiver Scene requires managed receiver geometry support")
        from animation.core.installation_profile_runtime import InstallationProfileSelection
        profile = (self.get_installation_profile_runtime_view() if installation_profile_digest is None else InstallationProfileSelection(library=self._installation_profile_library, topology=self._installation_profile_topology, selected_digest=installation_profile_digest).view)
        if profile is None:
            raise ValueError("canonical receiver Scene requires a verified managed installation profile")
        self._validate_installation_profile_geometry(profile)
        background = canonical.scene["background"]
        library = self._native_background_library
        if library is None:
            raise ValueError("canonical Background requires the managed native library")
        native = library.resolve_package(background["component_id"], bundle_digest=background["bundle_digest"])
        ref = ComponentRef(background["component_id"], "receiver_native", resolved_parameters=background["parameters"], bundle_digest=native.bundle_digest, expected_payload_digest=native.payload_digest)
        self._resolve_managed_native(ref)
        # A separate candidate runtime ensures failed construction cannot touch
        # any currently playing Animation or Widget.
        runtime = InstalledFinalSceneRuntime(self.scene_v2_component_catalog(), Path(__file__).resolve().parents[2], controller=self.controller, foreground_only=True)
        runtime.set_installation_profile(profile)
        from animation.core.scene_runtime import ScenePresentationContext
        runtime.render(ScenePresentationContext(canonical, 0.0, datetime.now().astimezone()))
        animation = canonical.scene["animation"]
        transport = SceneState(
            revision=self._next_receiver_context_revision(canonical.identity.revision),
            background=ref, overlays=(),
            known_python_fallback=ComponentRef(animation["component_id"], "python"),
        )
        return canonical, runtime, transport

    def preflight_scene(self, payload, *, installation_profile_digest=None):
        if isinstance(payload, dict) and payload.get("schema") == "ledgrid.scene.v2":
            self._prepare_canonical_receiver_scene(payload, installation_profile_digest=installation_profile_digest)
        else:
            self._resolve_scene_state(payload)

    def start_canonical_scene(self, payload):
        from animation.core.scene_runtime import ScenePresentationContext

        canonical, runtime, transport = self._prepare_canonical_receiver_scene(payload)
        if self._host_full_sparkle_eligible(canonical):
            try:
                runtime.render_opaque_full(ScenePresentationContext(
                    canonical, 0.0, datetime.now().astimezone()
                ))
            except ValueError:
                # A non-covering renderer retains the existing native/sparse path.
                pass
            else:
                return self._start_canonical_host_full_scene(canonical, runtime, transport)
        return self._start_receiver_hybrid_scene(transport, canonical_candidate=(canonical, runtime))

    def _start_canonical_host_full_scene(self, canonical, runtime, transport):
        from animation.core.scene_runtime import ScenePresentationContext

        frame = runtime.render_opaque_full(ScenePresentationContext(
            canonical, 0.0, datetime.now().astimezone()
        ))
        pixels = frame.pixels.copy()
        digest = hashlib.sha256(pixels.tobytes()).hexdigest()
        request_id = f"scene-{canonical.identity.digest}-{uuid.uuid4().hex}"
        prior_thread = self.animation_thread
        if (self.stop_animation(clear_leds=True) is False
                or (prior_thread is not None and prior_thread.is_alive())):
            raise RuntimeError("previous display ownership could not be cleared")
        try:
            with self._presentation_io_guard():
                # A failed proof may follow an accepted SET_ALL. Until black or
                # a known prior scene is proven, idle is not a truthful state.
                self._host_full_first_frame_uncertain = True
                receipt = self.controller.present_displayed_host_full_frame(
                    request_id, pixels
                )
            if (not isinstance(receipt, dict)
                    or receipt.get("request_id") != request_id
                    or receipt.get("frame_digest") != digest
                    or receipt.get("authority_digest") != getattr(
                        self.controller, "receiver_identity_authority_digest", None
                    )
                    or {item.get("logical_device") for item in receipt.get("displayed_receivers", ())}
                    != set(range(5))):
                raise RuntimeError("host-full first-frame proof is incomplete or misattributed")
        except Exception as exc:
            clear_error = None
            if self._host_full_first_frame_uncertain:
                try:
                    self._clear_unverified_host_full_frame()
                except Exception as clear_exc:
                    clear_error = str(clear_exc)
            self._receiver_last_failure = {
                "operation": "host_full_first_frame", "scene_digest": canonical.identity.digest,
                "error": str(exc), "clear_error": clear_error,
            }
            # The guarded activation coordinator owns exact prior-state rollback.
            # Do not publish this Scene as running after a partial first write.
            raise
        self._host_full_first_frame_uncertain = False
        self._reset_run_counters()
        with self._scene_state_guard():
            self._canonical_receiver_scene = canonical
            self._canonical_receiver_runtime = runtime
            self._canonical_host_full_mode = True
            self._canonical_host_full_receipt = receipt
            self._receiver_last_status = None
            self._scene_mode = True
            self._receiver_hybrid_mode = False
            self._scene_background = {
                "name": canonical.scene["background"]["component_id"],
                "animation": None, "config": dict(canonical.scene["background"]["parameters"]),
                "ref": transport.background, "frame_index": 0, "calls": 0,
                "changed_calls": 0, "render_count": 0,
            }
            self._scene_overlay = None
            self._scene_compositor = None
            self._active_scene_state = transport
            self.current_animation = None
            self.current_animation_name = "sparkle"
            self.current_animation_hash = canonical.scene["background"]["bundle_digest"]
            self.current_preset = None
            self.frames_presented = 1
            self.frame_count = 1
            with self.frame_data_lock:
                self.current_frame_data = pixels
        self._launch_animation_loop()
        return True

    def _canonical_presentation_context(self, publisher, *, revision, present_at_scene_time_us):
        canonical = self._canonical_receiver_scene
        scene = canonical.scene
        # The native module and the Python renderers consume the same semantic
        # palette. Legacy Vibe luminance/tempo never grade either plane again.
        from animation.native.aurora import canonical_palette_roles
        roles = canonical_palette_roles(scene["look"]["palette_id"])
        profile = VibeProfile("neutral", 1, roles, 1.0, 1.0, {"chroma_scale": 1.0, "energy": 1.0})
        effects = scene["plants"]["effects"]["strengths"]
        final = CanonicalFinalPresentation(canonical.identity.digest, scene["look"]["pace"], scene["look"]["presentation_brightness"], effects.get("shadow", 0), effects.get("illuminate", 0), effects.get("hue_shift", 0))
        return ReceiverPresentationContext(
            publisher.controller_session_id, revision, self._scene_epoch,
            present_at_scene_time_us, ResolvedVibe(profile, profile.to_state()),
            PlantModifierState.empty(), self._receiver_plant_revision, final,
        )

    def _render_canonical_receiver_foreground(self, now, *, force_refresh=False):
        from animation.core.scene_runtime import ScenePresentationContext
        from animation.core.compositing import OverlayFrame
        runtime = self._canonical_receiver_runtime
        runtime.set_installation_profile(self.get_installation_profile_runtime_view())
        frame = runtime.render(ScenePresentationContext(self._canonical_receiver_scene, max(0.0, now - self.start_time), datetime.now().astimezone()))
        foreground = frame.foreground
        if foreground is None:
            raise RuntimeError("canonical runtime omitted its aggregate foreground")
        # This is explicitly foreground-only software telemetry. The real
        # receiver-native base and final optics are not framebuffer readback.
        self._canonical_receiver_preview = foreground.pixels[:, :3].copy()
        return OverlayFrame(foreground.pixels, revision=foreground.revision, changed=foreground.changed or force_refresh, dirty_ranges=None if force_refresh else foreground.dirty_ranges)
