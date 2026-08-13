"""Focused Phase 2C scene-state and manager lifecycle acceptance."""

from __future__ import annotations

import contextlib
import io
import unittest

import numpy as np

from animation.core.manager import AnimationManager
from animation.core.presentation_contracts import (
    AGGREGATE_OVERLAY_SLOT_ID,
    ComponentProvider,
    ComponentRef,
    ForegroundStalePolicy,
    OverlayPlacement,
    OverlayRef,
    SceneState,
    StalePolicy,
)
from ipc.scene_contract import normalize_scene_payload
from tests.unit.test_host_scene_manager import (
    _Controller,
    _FullSceneProbe,
    _ProbeBase,
    _ProbeOverlay,
)


_DIGEST = "a" * 64


class _PlantPreviewProbe(_ProbeBase):
    instances = []

    def generate_frame(self, _elapsed, _frame_index):
        active = self.params.get("plant_modifiers", {}).get("active", ())
        color = 180 if "illuminate" in active else 20
        self._cached[:] = (color, color, color)
        return self._cached


class _FailingOverlay(_ProbeOverlay):
    instances = []

    def start(self):
        raise RuntimeError("authored start failure")


class Phase2CSceneManagerTests(unittest.TestCase):
    def setUp(self):
        _ProbeBase.instances.clear()
        _ProbeOverlay.instances.clear()
        _PlantPreviewProbe.instances.clear()
        _FailingOverlay.instances.clear()
        self.controller = _Controller()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager = AnimationManager(self.controller, auto_start=False)
        self.manager._launch_animation_loop = lambda: None
        self.manager.plugin_loader.loaded_plugins.update({
            "probe_base": _ProbeBase,
            "probe_overlay": _ProbeOverlay,
            "probe_overlay_alt": _ProbeOverlay,
            "full_scene_probe": _FullSceneProbe,
            "plant_preview_probe": _PlantPreviewProbe,
            "failing_overlay": _FailingOverlay,
        })
        descriptors = {
            "probe_base": self._descriptor("probe_base", "background", {"color": 40}),
            "probe_overlay": self._descriptor(
                "probe_overlay", "overlay", {"position": 0, "alpha": 255}
            ),
            "probe_overlay_alt": self._descriptor(
                "probe_overlay_alt", "overlay", {"position": 1, "alpha": 128}
            ),
            "full_scene_probe": self._descriptor("full_scene_probe", "full_scene", {}),
            "plant_preview_probe": self._descriptor(
                "plant_preview_probe", "background", {}
            ),
            "failing_overlay": self._descriptor("failing_overlay", "overlay", {}),
        }
        self.manager.plugin_loader.get_component_descriptor = descriptors.get
        self.manager.plugin_loader.component_catalog = lambda provider=None, role=None: [
            value for value in descriptors.values()
            if (provider is None or value["provider"] == provider)
            and (role is None or value["role"] == role)
        ]

        def validate(plugin_id, values):
            allowed = set(descriptors[plugin_id]["parameter_schema"])
            unknown = sorted(set(values) - allowed)
            if unknown:
                raise ValueError(f"undeclared controls: {', '.join(unknown)}")
            return dict(values)

        self.manager.plugin_loader.validate_component_parameters = validate

    def tearDown(self):
        self.manager.stop_animation(clear_leds=False)

    @staticmethod
    def _descriptor(plugin_id, role, defaults):
        return {
            "plugin_id": plugin_id,
            "provider": "python",
            "role": role,
            "defaults": defaults,
            "parameter_schema": {key: {} for key in defaults},
            "compatibility": {
                "classification": "explicit_component",
                "composable": role in {"background", "overlay"},
            },
        }

    @staticmethod
    def component(plugin_id, params=None, **kwargs):
        return ComponentRef(
            plugin_id=plugin_id,
            provider=ComponentProvider.PYTHON,
            resolved_parameters=params or {},
            **kwargs,
        )

    def scene(self, *, background=None, overlays=None, revision=1):
        background = background or self.component("probe_base", {"color": 20})
        if overlays is None:
            overlays = (OverlayRef(
                slot_id=AGGREGATE_OVERLAY_SLOT_ID,
                component=self.component(
                    "probe_overlay", {"position": 0, "alpha": 255}
                ),
                enabled=True,
                opacity=255,
                placement=OverlayPlacement(),
                stale_policy=StalePolicy(ForegroundStalePolicy.HOLD),
            ),)
        return SceneState(revision, background, tuple(overlays), background)

    def test_scene_contract_round_trip_is_strict_and_scene_only(self):
        scene = self.scene()
        payload = scene.to_dict()
        self.assertEqual(SceneState.from_payload(payload), scene)
        self.assertFalse({"vibe", "output", "plant_modifiers"} & set(payload))

        with self.assertRaisesRegex(ValueError, "unknown scene state fields"):
            SceneState.from_payload({**payload, "vibe": {}})
        with self.assertRaisesRegex(ValueError, "unknown component reference fields"):
            SceneState.from_payload({
                **payload,
                "background": {**payload["background"], "output": {}},
            })
        with self.assertRaisesRegex(ValueError, "scene-external state"):
            self.component("probe_base", {"vibe": "quiet"})

    def test_live_resolver_rejects_provider_role_slot_count_and_controls_preflight(self):
        self.assertTrue(self.manager.start_scene(self.scene()))
        active_background = self.manager._scene_background["animation"]

        native = ComponentRef(
            "native", ComponentProvider.RECEIVER_NATIVE,
            bundle_digest=_DIGEST, expected_payload_digest=_DIGEST,
        )
        bad_cases = (
            SceneState(2, native, (), self.component("probe_base")),
            self.scene(background=self.component("full_scene_probe")),
            self.scene(overlays=(OverlayRef(
                "alert", self.component("probe_overlay"), True, 255,
                OverlayPlacement(), StalePolicy(ForegroundStalePolicy.HOLD),
            ),)),
            self.scene(overlays=(
                self.scene().overlays[0],
                OverlayRef(
                    "alert", self.component("probe_overlay_alt"), True, 255,
                    OverlayPlacement(), StalePolicy(ForegroundStalePolicy.HOLD),
                ),
            )),
            self.scene(background=self.component("probe_base", {"mystery": 1})),
        )
        for bad in bad_cases:
            with self.subTest(scene=bad):
                self.assertFalse(self.manager.start_scene(bad))
                self.assertIs(
                    self.manager._scene_background["animation"], active_background
                )

    def test_round_trip_targeted_updates_and_status_diagnostics(self):
        preset_background = self.component(
            "probe_base", {"color": 20},
            preset_id="evening", preset_fingerprint=_DIGEST,
        )
        scene = self.scene(background=preset_background)
        self.assertTrue(self.manager.start_scene(scene.to_dict()))
        self.assertEqual(self.manager.get_scene_state(), scene.to_dict())
        status = self.manager.get_current_status()["scene"]
        self.assertFalse(status["background"]["preset"]["is_dirty"])
        self.assertEqual(status["background"]["preset"]["preset_id"], "evening")

        background = self.manager._scene_background["animation"]
        self.assertTrue(self.manager.update_scene_component(
            "background", {"params": {"color": 77}}
        ))
        self.assertTrue(self.manager.update_scene_component(
            AGGREGATE_OVERLAY_SLOT_ID,
            {
                "params": {"position": 2},
                "opacity": 128,
                "placement": {
                    "strip_translation": 1,
                    "led_translation": -1,
                    "clip_policy": "clip_to_wall",
                },
            },
        ))
        self.assertIs(self.manager._scene_background["animation"], background)
        state = SceneState.from_payload(self.manager.get_scene_state())
        self.assertEqual(state.background.resolved_parameters["color"], 77)
        self.assertEqual(state.overlays[0].component.resolved_parameters["position"], 2)
        self.assertEqual(state.overlays[0].opacity, 128)
        status = self.manager.get_current_status()["scene"]
        self.assertTrue(status["background"]["preset"]["is_dirty"])
        self.assertEqual(
            len(status["background"]["preset"]["resolved_fingerprint"]), 64
        )

    def test_overlay_replace_remove_and_apply_preserve_background_identity(self):
        first = self.scene()
        self.assertTrue(self.manager.start_scene(first))
        background = self.manager._scene_background["animation"]
        old_overlay = self.manager._scene_overlay["animation"]

        replacement = self.component(
            "probe_overlay_alt", {"position": 1, "alpha": 128}
        )
        self.assertTrue(self.manager.update_scene_component(
            "overlay", component=replacement
        ))
        self.assertIs(self.manager._scene_background["animation"], background)
        self.assertIsNot(self.manager._scene_overlay["animation"], old_overlay)
        self.assertTrue(self.manager.remove_overlay())
        self.assertIsNone(self.manager._scene_overlay)
        self.assertIs(self.manager._scene_background["animation"], background)

        applied = self.scene(
            background=self.component("probe_base", {"color": 88}), overlays=()
        )
        self.assertTrue(self.manager.apply_scene(applied))
        self.assertIs(self.manager._scene_background["animation"], background)
        self.assertEqual(self.manager.get_scene_state(), applied.to_dict())

    def test_structured_preview_uses_live_resolver_and_never_mutates_live(self):
        scene = self.scene()
        self.assertTrue(self.manager.start_scene(scene))
        background = self.manager._scene_background["animation"]
        overlay = self.manager._scene_overlay["animation"]
        before = self.manager.get_scene_state()
        live = np.asarray(self.manager.current_frame_data, dtype=np.uint8).copy()

        preview = self.manager.get_scene_preview(scene.to_dict(), elapsed=0.0)

        np.testing.assert_array_equal(preview["frame_data"], live)
        self.assertEqual(self.manager.get_scene_state(), before)
        self.assertIs(self.manager._scene_background["animation"], background)
        self.assertIs(self.manager._scene_overlay["animation"], overlay)
        self.assertFalse(self.controller.full_frames)

    def test_preview_plant_state_changes_bytes_without_mutating_live_authority(self):
        background = self.component("plant_preview_probe")
        scene = SceneState(1, background, (), background)
        live_state = self.manager.plant_modifier_state.to_dict()

        plain = self.manager.get_scene_preview(
            scene.to_dict(),
            plant_modifiers={"version": 1, "active": [], "strengths": {}},
            elapsed_seconds=0.0,
        )
        illuminated = self.manager.get_scene_preview(
            scene.to_dict(),
            plant_modifiers={
                "version": 1,
                "active": ["illuminate"],
                "strengths": {"illuminate": 1.0},
            },
            elapsed_seconds=0.0,
        )

        self.assertNotEqual(plain["frame_data"], illuminated["frame_data"])
        self.assertEqual(self.manager.plant_modifier_state.to_dict(), live_state)
        self.assertIsNone(self.manager.get_scene_state())

    def test_failed_overlay_replacement_rolls_back_both_live_identities(self):
        self.assertTrue(self.manager.start_scene(self.scene()))
        background = self.manager._scene_background["animation"]
        overlay = self.manager._scene_overlay["animation"]
        state = self.manager.get_scene_state()

        self.assertFalse(self.manager.update_scene_component(
            "overlay", component=self.component("failing_overlay")
        ))

        self.assertIs(self.manager._scene_background["animation"], background)
        self.assertIs(self.manager._scene_overlay["animation"], overlay)
        self.assertEqual(self.manager.get_scene_state(), state)

    def test_failed_complete_scene_start_keeps_prior_scene_running(self):
        self.assertTrue(self.manager.start_scene(self.scene()))
        background = self.manager._scene_background["animation"]
        overlay = self.manager._scene_overlay["animation"]
        state = self.manager.get_scene_state()
        failing = self.scene(overlays=(OverlayRef(
            AGGREGATE_OVERLAY_SLOT_ID,
            self.component("failing_overlay"),
            True,
            255,
            OverlayPlacement(),
            StalePolicy(ForegroundStalePolicy.HOLD),
        ),))

        self.assertFalse(self.manager.start_scene(failing))

        self.assertTrue(self.manager.is_running)
        self.assertIs(self.manager._scene_background["animation"], background)
        self.assertIs(self.manager._scene_overlay["animation"], overlay)
        self.assertEqual(self.manager.get_scene_state(), state)

    def test_legacy_start_translates_to_scene_without_changing_legacy_status(self):
        self.assertTrue(self.manager.start_animation("probe_base", {"color": 9}))
        status = self.manager.get_current_status()
        self.assertEqual(status["mode"], "animation")
        self.assertNotIn("scene", status)
        self.assertEqual(status["scene_state"]["background"]["plugin_id"], "probe_base")
        self.assertEqual(self.manager.list_components(role="overlay")[0]["role"], "overlay")


class RealSceneProductIntegrationTests(unittest.TestCase):
    def test_normalized_gradient_clock_scene_start_update_preview_and_stop(self):
        controller = _Controller(strips=32, leds_per_strip=138)
        with contextlib.redirect_stdout(io.StringIO()):
            manager = AnimationManager(controller, auto_start=False)
        manager._launch_animation_loop = lambda: None
        scene = normalize_scene_payload({
            "revision": 4,
            "background": {
                "plugin_id": "gradient",
                "provider": "python",
                "resolved_parameters": {"animated": False},
                "parameter_overrides": {},
            },
            "overlays": [{
                "slot_id": AGGREGATE_OVERLAY_SLOT_ID,
                "component": {
                    "plugin_id": "clock_overlay",
                    "provider": "python",
                    "resolved_parameters": {"show_seconds": True},
                    "parameter_overrides": {},
                },
                "enabled": True,
                "opacity": 210,
                "placement": {
                    "strip_translation": 0,
                    "led_translation": 0,
                    "clip_policy": "clip_to_wall",
                },
                "stale_policy": {"policy": "hold"},
            }],
        }, catalog=manager.list_components())
        try:
            self.assertTrue(manager.start_scene(scene))
            background = manager._scene_background["animation"]
            self.assertTrue(manager.update_scene_component(
                AGGREGATE_OVERLAY_SLOT_ID,
                {
                    "enabled": False,
                    "component": scene["overlays"][0]["component"],
                },
            ))
            self.assertIs(manager._scene_background["animation"], background)
            preview = manager.get_scene_preview(scene, elapsed=0.0)
            self.assertEqual(preview["scene"]["background"], "gradient")
            self.assertIs(manager._scene_background["animation"], background)
            self.assertEqual(manager.get_scene_state()["revision"], 5)
            self.assertTrue(manager.stop_scene(clear_leds=False))
        finally:
            manager.stop_animation(clear_leds=False)


if __name__ == "__main__":
    unittest.main()
