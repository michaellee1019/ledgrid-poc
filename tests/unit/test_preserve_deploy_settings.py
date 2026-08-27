import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from animation.core.receiver_static_component import (
    COMPILED_RAINBOW_BUNDLE_DIGEST,
    COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST,
)
from ipc.scene_contract import SceneProviderPolicy

from tools.deployment.preserve_deploy_settings import (
    _expected_restored_vibe,
    _restored_scene_proof,
    load_saved_state,
    receiver_hybrid_canary_enabled,
    record_deploy,
    restore,
    save,
    save_status,
)
from tools.deployment.receiver_hybrid_config import (
    resolve_receiver_hybrid_config,
    write_receiver_hybrid_config,
)


class PreserveDeploySettingsTests(unittest.TestCase):
    @staticmethod
    def _vibe(vibe_id="cozy"):
        from animation.core.presentation_contracts import resolve_vibe

        return resolve_vibe(vibe_id).state.to_dict()

    @staticmethod
    def _native_scene(common_seed=7):
        fallback = {
            "plugin_id": "rainbow",
            "provider": "python",
            "parameter_overrides": {"speed": 0.65},
            "resolved_parameters": {"speed": 0.65},
        }
        return {
            "schema": "ledgrid.scene-state",
            "schema_version": 1,
            "revision": 91,
            "background": {
                "plugin_id": "compiled_rainbow",
                "provider": "receiver_native",
                "parameter_overrides": {"common_seed": common_seed},
                "resolved_parameters": {
                    "preferred_cadence_hz": 30,
                    "common_seed": common_seed,
                },
                "bundle_digest": COMPILED_RAINBOW_BUNDLE_DIGEST,
                "expected_payload_digest": (
                    COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST
                ),
            },
            "overlays": [],
            "known_python_fallback": fallback,
        }

    @classmethod
    def _managed_native_scene(cls):
        scene = cls._native_scene()
        scene["background"].update({
            "plugin_id": "aurora_curtains_native",
            "bundle_digest": "a" * 64,
            "expected_payload_digest": "b" * 64,
            "parameter_overrides": {},
            "resolved_parameters": {"brightness": 0.42},
        })
        return scene

    @classmethod
    def _managed_native_status(cls, scene=None):
        scene = scene or cls._managed_native_scene()
        required = 0x1FF
        parameter_digest = "c" * 64
        context_digest = "d" * 64
        profile_digest = "e" * 64
        topology = ((8, 0, False), (8, 8, False), (8, 24, True),
                    (8, 16, True), (1, 32, False))
        capability_devices = [
            {
                "logical_device": receiver_id,
                "capabilities": required,
                "local_strip_count": width,
                "global_strip_offset": offset,
                "reverse_native_strip_order": reverse,
            }
            for receiver_id, (width, offset, reverse) in enumerate(topology)
        ]
        receiver_devices = [
            {
                "receiver_status_seen": True,
                "receiver_status_version": 6,
                "receiver_logical_device": receiver_id,
                "receiver_capabilities": required,
                "receiver_native_executing": True,
                "receiver_native_cache_integrity_ok": True,
                "receiver_native_active_bundle_digest": "a" * 64,
                "receiver_native_active_payload_digest": "b" * 64,
                "receiver_native_active_parameter_digest": parameter_digest,
                "receiver_active_context_digest": context_digest,
                "receiver_profile_active_global_digest": profile_digest,
                "receiver_vibe_revision": 4,
                "receiver_vibe_digest": "f" * 64,
                "receiver_plant_modifier_revision": 5,
                "receiver_plant_modifier_digest": "1" * 64,
            }
            for receiver_id in range(5)
        ]
        return {
            "is_running": True,
            "current_animation": "aurora_curtains_native",
            "installation_profile_digest": profile_digest,
            "scene_state": scene,
            "scene": {"provider_mode": "receiver_native"},
            "receiver_hybrid": {
                "healthy": True,
                "operational": True,
                "fallback_active": False,
                "error": None,
                "driver": {
                    "state": "active",
                    "bundle_digest": "a" * 64,
                    "payload_digest": "b" * 64,
                    "parameter_digest": parameter_digest,
                    "context_digest": context_digest,
                    "installation_profile_digest": profile_digest,
                    "agreement": {
                        "exact_roster": True,
                        "verified_receiver_ids": [0, 1, 2, 3, 4],
                    },
                    "capability_report": {
                        "required_capabilities": required,
                        "devices": capability_devices,
                    },
                },
            },
            "driver_stats": {"devices": receiver_devices},
            "vibe": {"state": cls._vibe("cozy")},
            "feature_flags": {
                "receiver_local_background": True,
                "receiver_sparse_overlay": True,
                "receiver_native_modules": True,
            },
        }
    @staticmethod
    def _enabled_policy():
        return SceneProviderPolicy(
            receiver_local_background=True,
            receiver_sparse_overlay=True,
        )

    def test_receiver_hybrid_canary_is_explicit_and_disabled_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(receiver_hybrid_canary_enabled())
        for value in (True, "1", "TRUE", "yes", "on"):
            with self.subTest(value=value):
                self.assertTrue(receiver_hybrid_canary_enabled(value))
        for value in (False, "0", "false", "no", "off", ""):
            with self.subTest(value=value):
                self.assertFalse(receiver_hybrid_canary_enabled(value))
        with self.assertRaisesRegex(ValueError, "must be a boolean switch"):
            receiver_hybrid_canary_enabled("maybe")

    def test_record_deploy_updates_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            deployment_path = Path(temporary_dir) / "deployment.json"
            record_deploy(deployment_path, 123.5)
            record_deploy(deployment_path, 456.75)

            self.assertEqual(
                json.loads(deployment_path.read_text())["deploy_timestamp"],
                456.75,
            )

    def test_save_overwrites_preset_and_preserves_authored_speed(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            status_path = root / "status.json"
            presets_dir = root / "presets"
            state_path = root / "state.json"
            preset_path = presets_dir / "sparkle" / "before-deploy.json"
            preset_path.parent.mkdir(parents=True)
            preset_path.write_text(json.dumps({"created_at": 123, "params": {"old": True}}))
            status_path.write_text(json.dumps({
                "is_running": True,
                "current_animation": "sparkle",
                "animation_speed_scale": 0.2,
                "animation_info": {"current_params": {"speed": 0.4, "brightness": 0.7}},
            }))

            preset = save(status_path, presets_dir, state_path)

            self.assertEqual(preset["created_at"], 123)
            self.assertAlmostEqual(preset["params"]["speed"], 0.4)
            self.assertEqual(preset["params"]["brightness"], 0.7)
            self.assertEqual(json.loads(state_path.read_text())["animation"], "sparkle")

    def test_save_status_records_runtime_config_and_loads_restart_default(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            presets_dir = root / "presets"
            state_path = root / "state.json"

            save_status({
                "is_running": True,
                "current_animation": "rainbow",
                "animation_speed_scale": 0.45,
                "target_fps": 144,
                "brightness": 96,
                "plant_aware": False,
                "animation_info": {
                    "current_params": {"speed": 0.9, "brightness": 0.7},
                },
            }, presets_dir, state_path)

            saved = load_saved_state(state_path)
            self.assertEqual(saved["animation"], "rainbow")
            self.assertEqual(saved["params"], {"speed": 0.9, "brightness": 0.7})
            self.assertEqual(saved["animation_speed_scale"], 0.45)
            self.assertEqual(saved["target_fps"], 144)
            self.assertEqual(saved["brightness"], 96)
            self.assertEqual(saved["plant_modifiers"], {
                "version": 1, "active": [], "strengths": {},
            })
            self.assertNotIn("plant_aware", json.loads(state_path.read_text()))

    def test_scene_capture_is_versioned_desired_display_and_round_trips_independent_state(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            vibe = self._vibe("cozy")
            scene = {
                "schema": "ledgrid.scene-state", "schema_version": 1, "revision": 9,
                "background": {
                    "plugin_id": "rainbow", "provider": "python",
                    "parameter_overrides": {"speed": 0.8},
                    "resolved_parameters": {"speed": 0.8},
                },
                "overlays": [{
                    "slot_id": "clock_overlay",
                    "component": {
                        "plugin_id": "clock_overlay", "provider": "python",
                        "parameter_overrides": {"seconds": True},
                        "resolved_parameters": {"seconds": True},
                    },
                    "enabled": True, "opacity": 144,
                    "placement": {
                        "strip_translation": 3, "led_translation": -4,
                        "clip_policy": "clip_to_wall",
                    },
                    "stale_policy": {"policy": "clear_after_lease", "lease_ms": 900},
                }],
                "known_python_fallback": {
                    "plugin_id": "rainbow", "provider": "python",
                    "parameter_overrides": {"speed": 0.8},
                    "resolved_parameters": {"speed": 0.8},
                },
            }
            save_status({
                "is_running": True,
                "current_animation": "rainbow",
                "scene_state": scene,
                "animation_speed_scale": 1.6,
                "target_fps": 120,
                "brightness": 102,
                "plant_modifiers": {
                    "version": 1, "active": ["shadow"],
                    "strengths": {"shadow": 0.5},
                },
                "vibe": {"state": vibe},
                "animation_info": {"current_params": {"speed": 0.8}},
            }, root / "presets", root / "state.json")

            raw = json.loads((root / "state.json").read_text())
            self.assertEqual(raw["schema"], "ledgrid.desired-display-state")
            self.assertEqual(raw["schema_version"], 1)
            self.assertEqual(raw["output"]["master_brightness"], 0.4)
            self.assertEqual(raw["output"]["operator_tempo_scale"], 1.6)
            self.assertNotIn("vibe", raw["scene"])

            loaded = load_saved_state(root / "state.json")
            self.assertEqual(loaded["scene"]["overlays"][0]["opacity"], 144)
            self.assertEqual(loaded["scene"]["overlays"][0]["placement"]["led_translation"], -4)
            self.assertEqual(loaded["brightness"], 102)
            self.assertEqual(loaded["animation_speed_scale"], 1.6)
            self.assertEqual(loaded["target_fps"], 120)
            self.assertEqual(loaded["vibe"], vibe)

    def test_native_scene_round_trips_only_with_canary_and_needs_no_python_preset(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            vibe = self._vibe("cozy")
            scene = self._native_scene(common_seed=37)
            save_status({
                "is_running": True,
                "current_animation": "compiled_rainbow",
                "scene_state": scene,
                "feature_flags": {
                    "receiver_local_background": True,
                    "receiver_sparse_overlay": True,
                },
                "animation_speed_scale": 1.35,
                "target_fps": 144,
                "brightness": 93,
                "plant_modifiers": {
                    "version": 1,
                    "active": ["shadow"],
                    "strengths": {"shadow": 0.6},
                },
                "vibe": {"state": vibe},
            }, root / "presets", root / "state.json")

            raw = json.loads((root / "state.json").read_text())
            self.assertEqual(raw["scene"], scene)
            self.assertNotIn("preset_path", raw)
            self.assertFalse(
                (root / "presets" / "compiled_rainbow" / "before-deploy.json").exists()
            )

            enabled = load_saved_state(
                root / "state.json", provider_policy=self._enabled_policy()
            )
            self.assertEqual(enabled["animation"], "compiled_rainbow")
            self.assertEqual(enabled["params"], {
                "preferred_cadence_hz": 30,
                "common_seed": 37,
            })
            self.assertNotIn("scene_fallback_reason", enabled)
            self.assertEqual(enabled["brightness"], 93)
            self.assertEqual(enabled["animation_speed_scale"], 1.35)
            self.assertEqual(enabled["target_fps"], 144)
            self.assertEqual(enabled["plant_modifiers"]["active"], ["shadow"])
            self.assertEqual(enabled["vibe"], vibe)

            ordinary = load_saved_state(root / "state.json")
            self.assertEqual(ordinary["animation"], "rainbow")
            self.assertEqual(ordinary["params"], {"speed": 0.65})
            self.assertIn("unsupported saved scene", ordinary["scene_fallback_reason"])
            self.assertEqual(ordinary["brightness"], enabled["brightness"])
            self.assertEqual(ordinary["plant_modifiers"], enabled["plant_modifiers"])
            self.assertEqual(ordinary["vibe"], enabled["vibe"])

    def test_idle_canary_update_preserves_native_scene_and_independent_state(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            flags = {
                "receiver_local_background": True,
                "receiver_sparse_overlay": True,
            }
            save_status({
                "is_running": True,
                "current_animation": "compiled_rainbow",
                "scene_state": self._native_scene(common_seed=99),
                "feature_flags": flags,
                "brightness": 80,
                "vibe": {"state": self._vibe("neutral")},
            }, root / "presets", root / "state.json")

            quiet = self._vibe("quiet")
            save_status({
                "is_running": False,
                "current_animation": None,
                "feature_flags": flags,
                "brightness": 41,
                "animation_speed_scale": 0.75,
                "target_fps": 72,
                "plant_modifiers": {
                    "version": 1,
                    "active": ["illuminate"],
                    "strengths": {"illuminate": 0.4},
                },
                "vibe": {"state": quiet},
            }, root / "presets", root / "state.json")

            loaded = load_saved_state(
                root / "state.json", provider_policy=self._enabled_policy()
            )
            self.assertEqual(loaded["animation"], "compiled_rainbow")
            self.assertEqual(loaded["params"]["common_seed"], 99)
            self.assertEqual(loaded["brightness"], 41)
            self.assertEqual(loaded["animation_speed_scale"], 0.75)
            self.assertEqual(loaded["target_fps"], 72)
            self.assertEqual(loaded["plant_modifiers"]["active"], ["illuminate"])
            self.assertEqual(loaded["vibe"], quiet)

    def test_unsupported_desired_scene_falls_back_only_to_recorded_python_component(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            fallback = {
                "plugin_id": "sparkle", "provider": "python",
                "parameter_overrides": {"speed": 0.6},
                "resolved_parameters": {"speed": 0.6},
            }
            state = {
                "schema": "ledgrid.desired-display-state",
                "schema_version": 2,
                "revision": 1,
                "scene": {
                    "schema": "ledgrid.scene-state", "schema_version": 99,
                    "revision": 1,
                    "background": {**fallback, "provider": "receiver_native"},
                    "overlays": [],
                    "known_python_fallback": fallback,
                },
                "vibe": self._vibe("neutral"),
                "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
                "installation_profile_digest": "0" * 64,
                "output": {
                    "master_brightness": 0.5,
                    "operator_tempo_scale": 1.0,
                    "power": True,
                    "target_fps": 200,
                },
            }
            path = root / "state.json"
            path.write_text(json.dumps(state))

            loaded = load_saved_state(path)
            self.assertEqual(loaded["animation"], "sparkle")
            self.assertEqual(loaded["params"], {"speed": 0.6})
            self.assertIn("unsupported desired display", loaded["scene_fallback_reason"])

            state["scene"]["known_python_fallback"]["provider"] = "receiver_native"
            path.write_text(json.dumps(state))
            with self.assertRaisesRegex(RuntimeError, "valid Python fallback"):
                load_saved_state(path)

    def test_malformed_desired_output_fails_before_restore_command(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            save_status({
                "is_running": True, "current_animation": "rainbow",
                "vibe": {"state": self._vibe("neutral")},
                "animation_info": {"current_params": {"speed": 0.9}},
            }, root / "presets", root / "state.json")
            state = json.loads((root / "state.json").read_text())
            state["output"]["master_brightness"] = 1.5
            (root / "state.json").write_text(json.dumps(state))
            with self.assertRaisesRegex(RuntimeError, "master brightness"):
                load_saved_state(root / "state.json")

    def test_save_status_keeps_vibe_independent_from_authored_params_and_preset(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            vibe = self._vibe("cozy")
            save_status({
                "is_running": True,
                "current_animation": "rainbow",
                "current_preset": {
                    "preset_id": "sunset", "name": "Sunset",
                    "animation": "rainbow", "is_dirty": False,
                },
                "animation_speed_scale": 1.7,
                "vibe": {"state": vibe, "profile": {"tempo_scale": 0.9}},
                "animation_info": {"current_params": {"speed": 0.65}},
            }, root / "presets", root / "state.json")

            saved = load_saved_state(root / "state.json")
            self.assertEqual(saved["vibe"], vibe)
            self.assertEqual(saved["params"]["speed"], 0.65)
            self.assertEqual(saved["animation_speed_scale"], 1.7)
            self.assertEqual(saved["current_preset"], {
                "preset_id": "sunset", "name": "Sunset",
                "animation": "rainbow", "is_dirty": False,
            })
            self.assertNotIn("vibe", json.loads(
                (root / "presets" / "rainbow" / "before-deploy.json").read_text()
            )["params"])

    def test_idle_vibe_update_retains_last_playable_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            neutral = self._vibe("neutral")
            quiet = self._vibe("quiet")
            running = {
                "is_running": True,
                "current_animation": "rainbow",
                "animation_info": {"current_params": {"speed": 0.65}},
                "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
                "vibe": {"state": neutral},
            }
            save_status(running, root / "presets", root / "state.json")
            prior = load_saved_state(root / "state.json")

            save_status({
                "is_running": False,
                "current_animation": None,
                "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
                "vibe": {"state": quiet},
            }, root / "presets", root / "state.json")

            saved = load_saved_state(root / "state.json")
            self.assertEqual(saved["animation"], "rainbow")
            self.assertEqual(saved["params"], {"speed": 0.65})
            self.assertEqual(saved["vibe"], quiet)
            self.assertEqual(saved["scene"], prior["scene"])
            self.assertFalse(saved["power"])

    def test_stopped_guarded_scene_persists_new_exact_selection_and_power_off(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            presets = root / "presets"
            state_path = root / "state.json"
            save_status({
                "is_running": True,
                "current_animation": "rainbow",
                "animation_info": {"current_params": {"speed": 0.65}},
                "vibe": {"state": self._vibe("neutral")},
            }, presets, state_path)

            component = {
                "plugin_id": "sparkle",
                "provider": "python",
                "parameter_overrides": {"speed": 0.35},
                "resolved_parameters": {"speed": 0.35},
            }
            selected_scene = {
                "schema": "ledgrid.scene-state",
                "schema_version": 1,
                "revision": 29,
                "background": component,
                "overlays": [],
                "known_python_fallback": component,
            }
            save_status({
                "is_running": False,
                "current_animation": None,
                "scene_state": selected_scene,
                "brightness": 77,
                "animation_speed_scale": 0.8,
                "target_fps": 60,
                "vibe": {"state": self._vibe("quiet")},
                "plant_modifiers": {
                    "version": 1, "active": [], "strengths": {},
                },
                "installation_profile_digest": "a" * 64,
            }, presets, state_path)

            raw = json.loads(state_path.read_text())
            loaded = load_saved_state(state_path)
            self.assertEqual(raw["scene"], selected_scene)
            self.assertFalse(raw["output"]["power"])
            self.assertEqual(loaded["scene"], selected_scene)
            self.assertEqual(loaded["animation"], "sparkle")
            self.assertEqual(loaded["params"], {"speed": 0.35})
            self.assertFalse(loaded["power"])
            self.assertEqual(loaded["installation_profile_digest"], "a" * 64)

    def test_stopped_managed_native_selection_needs_no_live_playback_evidence(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            state_path = root / "state.json"
            scene = self._managed_native_scene()

            save_status({
                "is_running": False,
                "current_animation": None,
                "scene_state": scene,
                "feature_flags": {
                    "receiver_local_background": True,
                    "receiver_sparse_overlay": True,
                    "receiver_native_modules": True,
                },
                "installation_profile_digest": "e" * 64,
                "vibe": {"state": self._vibe("quiet")},
            }, root / "presets", state_path)

            raw = json.loads(state_path.read_text())
            loaded = load_saved_state(
                state_path,
                provider_policy=SceneProviderPolicy(
                    receiver_local_background=True,
                    receiver_sparse_overlay=True,
                    receiver_native_modules=True,
                ),
            )
            self.assertEqual(raw["scene"], scene)
            self.assertFalse(raw["output"]["power"])
            self.assertNotIn("native_expectation", raw)
            self.assertEqual(loaded["scene"], scene)
            self.assertFalse(loaded["power"])

    def test_save_status_ignores_non_finite_optional_runtime_values(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            state_path = root / "state.json"

            save_status({
                "is_running": True,
                "current_animation": "rainbow",
                "animation_speed_scale": float("inf"),
                "target_fps": 0.5,
                "animation_info": {"current_params": {"speed": 0.9}},
            }, root / "presets", state_path)

            state = json.loads(state_path.read_text())
            self.assertNotIn("animation_speed_scale", state)
            self.assertNotIn("target_fps", state)

    def test_load_saved_state_rejects_invalid_optional_runtime_values(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            presets_dir = root / "presets"
            state_path = root / "state.json"
            save_status({
                "is_running": True,
                "current_animation": "rainbow",
                "animation_info": {"current_params": {"speed": 0.9}},
            }, presets_dir, state_path)
            state = json.loads(state_path.read_text())
            state["target_fps"] = "fast"
            state_path.write_text(json.dumps(state))

            with self.assertRaisesRegex(RuntimeError, "invalid target FPS"):
                load_saved_state(state_path)

            state["target_fps"] = 144
            state["brightness"] = 256
            state_path.write_text(json.dumps(state))
            with self.assertRaisesRegex(RuntimeError, "invalid brightness"):
                load_saved_state(state_path)

            state["brightness"] = 96
            state["plant_modifiers"] = {"active": ["attractor", "repulsor"]}
            state_path.write_text(json.dumps(state))
            with self.assertRaisesRegex(RuntimeError, "invalid plant modifiers"):
                load_saved_state(state_path)

    def test_load_migrates_legacy_boolean_without_rewriting_preset(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            preset = root / "before-deploy.json"
            preset.write_text(json.dumps({
                "animation": "sparkle", "params": {"plant_aware": True, "speed": 1.0},
            }))
            original = preset.read_text()
            state = root / "state.json"
            state.write_text(json.dumps({
                "animation": "sparkle", "preset_path": str(preset), "plant_aware": True,
            }))

            loaded = load_saved_state(state)

            self.assertEqual(loaded["plant_modifiers"]["active"], ["illuminate", "obstacle"])
            self.assertNotIn("plant_aware", loaded)
            self.assertEqual(preset.read_text(), original)

    def test_unknown_persisted_vibe_version_is_retained_for_visible_neutral_fallback(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            preset = root / "before-deploy.json"
            preset.write_text(json.dumps({"animation": "sparkle", "params": {}}))
            unknown = self._vibe("cozy")
            unknown["profile_version"] = 999
            state = root / "state.json"
            state.write_text(json.dumps({
                "animation": "sparkle", "preset_path": str(preset),
                "vibe": unknown,
            }))

            loaded = load_saved_state(state)
            fallback, expects_diagnostic = _expected_restored_vibe(loaded["vibe"])

            self.assertEqual(loaded["vibe"]["profile_version"], 999)
            self.assertEqual(fallback["vibe_id"], "neutral")
            self.assertTrue(expects_diagnostic)

    def test_stale_persisted_vibe_digest_requires_visible_neutral_fallback(self):
        stale = self._vibe("cozy")
        stale["resolved_profile_digest"] = "f" * 64

        fallback, expects_diagnostic = _expected_restored_vibe(stale)

        self.assertEqual(fallback["vibe_id"], "neutral")
        self.assertEqual(fallback["revision"], stale["revision"])
        self.assertTrue(expects_diagnostic)

    def test_malformed_fallback_revision_matches_manager_uint64_boundary(self):
        for revision in (True, -1, 2**64):
            with self.subTest(revision=revision):
                stale = self._vibe("cozy")
                stale.update(profile_version=999, revision=revision)
                fallback, expects_diagnostic = _expected_restored_vibe(stale)
                self.assertEqual(fallback["revision"], 0)
                self.assertTrue(expects_diagnostic)

    def test_save_requires_a_running_animation(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            status_path = root / "status.json"
            status_path.write_text(json.dumps({"is_running": False}))

            with self.assertRaisesRegex(RuntimeError, "No running animation"):
                save(status_path, root / "presets", root / "state.json")

    def test_restore_waits_for_restart_and_applies_saved_preset(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            status_path = root / "status.json"
            control_path = root / "control.json"
            preset_path = root / "before-deploy.json"
            state_path = root / "state.json"
            preset_path.write_text(json.dumps({
                "animation": "sparkle",
                "params": {"brightness": 0.7},
            }))
            state_path.write_text(json.dumps({
                "animation": "sparkle",
                "preset_path": str(preset_path),
            }))
            status_path.write_text(json.dumps({"updated_at": 1}))

            def simulate_controller():
                time.sleep(0.05)
                status_path.write_text(json.dumps({"updated_at": time.time()}))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    if control_path.exists():
                        command = json.loads(control_path.read_text())
                        status_path.write_text(json.dumps({
                            "updated_at": time.time(),
                            "last_command_id": command["command_id"],
                            "current_animation": "sparkle",
                            "is_running": True,
                        }))
                        return
                    time.sleep(0.01)

            controller = threading.Thread(target=simulate_controller)
            controller.start()
            preset = restore(status_path, control_path, state_path, 1)
            controller.join()

            self.assertEqual(preset["params"], {"brightness": 0.7})

    def test_canary_restore_sends_versioned_native_scene_not_receiver_commands(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            write_receiver_hybrid_config(root, enabled=True)
            status_path = root / "status.json"
            control_path = root / "control.json"
            state_path = root / "state.json"
            vibe = self._vibe("cozy")
            scene = self._native_scene(common_seed=123)
            save_status({
                "is_running": True,
                "current_animation": "compiled_rainbow",
                "scene_state": scene,
                "feature_flags": {
                    "receiver_local_background": True,
                    "receiver_sparse_overlay": True,
                },
                "vibe": {"state": vibe},
            }, root / "presets", state_path)
            status_path.write_text(json.dumps({"updated_at": 1}))
            observed = []

            def simulate_controller():
                time.sleep(0.03)
                status_path.write_text(json.dumps({"updated_at": time.time()}))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    if not control_path.exists():
                        time.sleep(0.01)
                        continue
                    command = json.loads(control_path.read_text())
                    observed.append(command)
                    status_path.write_text(json.dumps({
                        "updated_at": time.time(),
                        "last_command_id": command["command_id"],
                        "current_animation": "compiled_rainbow",
                        "is_running": True,
                        "installation_profile_digest": "0" * 64,
                        "scene_state": scene,
                        "scene": {"provider_mode": "receiver_hybrid"},
                        "receiver_hybrid": {
                            "healthy": True,
                            "operational": True,
                            "fallback_active": False,
                            "error": None,
                            "transport_policy": "strict_all_readable_v1",
                            "telemetry_complete": True,
                            "readable_devices": [0, 1, 2, 3, 4],
                            "unverified_devices": [],
                        },
                        "vibe": {"state": vibe},
                    }))
                    return

            controller = threading.Thread(target=simulate_controller)
            controller.start()
            restored = restore(
                status_path,
                control_path,
                state_path,
                1,
                root=root,
            )
            controller.join()

            self.assertEqual(restored["animation"], "compiled_rainbow")
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0]["action"], "restore_display_state")
            self.assertEqual(observed[0]["data"]["state"]["scene"], scene)
            self.assertEqual(
                observed[0]["data"]["state"]["installation_profile_digest"],
                "0" * 64,
            )
            self.assertEqual(
                restored["installation_profile_digest"], "0" * 64
            )

    def test_managed_native_restore_uses_durable_native_gate_and_exact_proof(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            write_receiver_hybrid_config(
                root, enabled=True, native_modules_enabled=True
            )
            status_path = root / "status.json"
            control_path = root / "control.json"
            state_path = root / "state.json"
            native_status = self._managed_native_status()
            save_status(native_status, root / "presets", state_path)
            persisted = json.loads(state_path.read_text())
            self.assertEqual(
                persisted["native_expectation"]["parameter_digest"], "c" * 64
            )
            status_path.write_text(json.dumps({"updated_at": 1}))
            observed = []

            def simulate_controller():
                time.sleep(0.03)
                status_path.write_text(json.dumps({"updated_at": time.time()}))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    if not control_path.exists():
                        time.sleep(0.01)
                        continue
                    command = json.loads(control_path.read_text())
                    observed.append(command)
                    restored_status = json.loads(json.dumps(native_status))
                    restored_status.update({
                        "updated_at": time.time(),
                        "last_command_id": command["command_id"],
                        "scene_state": command["data"]["state"]["scene"],
                    })
                    status_path.write_text(json.dumps(restored_status))
                    return

            controller = threading.Thread(target=simulate_controller)
            controller.start()
            restored = restore(
                status_path, control_path, state_path, 1, root=root
            )
            controller.join()

            self.assertEqual(restored["animation"], "aurora_curtains_native")
            self.assertEqual(observed[0]["action"], "restore_display_state")
            self.assertEqual(
                observed[0]["data"]["state"]["scene"]["background"]["provider"],
                "receiver_native",
            )

    def test_feature_off_restore_deterministically_uses_recorded_python_fallback(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            write_receiver_hybrid_config(root, enabled=False)
            status_path = root / "status.json"
            control_path = root / "control.json"
            state_path = root / "state.json"
            save_status(
                self._managed_native_status(), root / "presets", state_path
            )
            status_path.write_text(json.dumps({"updated_at": 1}))
            observed = []

            def simulate_controller():
                time.sleep(0.03)
                status_path.write_text(json.dumps({"updated_at": time.time()}))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    if not control_path.exists():
                        time.sleep(0.01)
                        continue
                    command = json.loads(control_path.read_text())
                    observed.append(command)
                    desired = command["data"]["state"]
                    status_path.write_text(json.dumps({
                        "updated_at": time.time(),
                        "last_command_id": command["command_id"],
                        "current_animation": "rainbow",
                        "is_running": True,
                        "installation_profile_digest": "e" * 64,
                        "scene_state": desired["scene"],
                        "vibe": {"state": desired["vibe"]},
                    }))
                    return

            controller = threading.Thread(target=simulate_controller)
            controller.start()
            restored = restore(
                status_path, control_path, state_path, 1, root=root
            )
            controller.join()

            self.assertEqual(restored["animation"], "rainbow")
            self.assertIn("unsupported saved scene", restored["scene_fallback"])
            self.assertEqual(
                observed[0]["data"]["state"]["scene"]["background"]["provider"],
                "python",
            )

    def test_desired_restore_rejects_matching_status_with_wrong_profile_digest(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            status_path = root / "status.json"
            control_path = root / "control.json"
            state_path = root / "state.json"
            selected_digest = "a" * 64
            wrong_digest = "b" * 64
            save_status({
                "is_running": True,
                "current_animation": "rainbow",
                "animation_info": {"current_params": {"speed": 0.8}},
                "installation_profile_digest": selected_digest,
            }, root / "presets", state_path)
            desired = load_saved_state(state_path)
            status_path.write_text(json.dumps({"updated_at": 1}))

            def simulate_controller():
                time.sleep(0.03)
                status_path.write_text(json.dumps({"updated_at": time.time()}))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    if not control_path.exists():
                        time.sleep(0.01)
                        continue
                    command = json.loads(control_path.read_text())
                    status_path.write_text(json.dumps({
                        "updated_at": time.time(),
                        "last_command_id": command["command_id"],
                        "current_animation": "rainbow",
                        "is_running": True,
                        "scene_state": desired["scene"],
                        "vibe": {"state": desired["vibe"]},
                        "installation_profile_digest": wrong_digest,
                    }))
                    return

            controller = threading.Thread(target=simulate_controller)
            controller.start()
            with self.assertRaisesRegex(
                RuntimeError, "did not restore desired display"
            ):
                restore(status_path, control_path, state_path, 0.2, root=root)
            controller.join()

    def test_native_restore_proof_requires_exact_finalized_roster_evidence(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            write_receiver_hybrid_config(root, enabled=True)
            config = resolve_receiver_hybrid_config(root)
            scene = self._native_scene(common_seed=321)
            status = {
                "scene_state": scene,
                "scene": {"provider_mode": "receiver_hybrid"},
                "receiver_hybrid": {
                    "healthy": True,
                    "operational": True,
                    "fallback_active": False,
                    "error": None,
                    "transport_policy": "strict_all_readable_v1",
                    "telemetry_complete": True,
                    "readable_devices": [0, 1, 2, 3, 4],
                    "unverified_devices": [],
                },
            }
            self.assertTrue(_restored_scene_proof(status, scene, config))

            cases = (
                ("scene_state", {**scene, "revision": 92}),
                ("provider_mode", "python_host"),
                ("operational", False),
                ("fallback_active", True),
                ("error", "receiver failed"),
                ("transport_policy", "degraded_spi1_01_readable"),
                ("telemetry_complete", False),
                ("readable_devices", [0]),
                ("unverified_devices", [4]),
            )
            for field, value in cases:
                with self.subTest(field=field):
                    candidate = json.loads(json.dumps(status))
                    if field == "scene_state":
                        candidate[field] = value
                    elif field == "provider_mode":
                        candidate["scene"][field] = value
                    else:
                        candidate["receiver_hybrid"][field] = value
                    self.assertFalse(
                        _restored_scene_proof(candidate, scene, config)
                    )

    def test_managed_native_restore_requires_exact_bundle_payload_and_topology(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            write_receiver_hybrid_config(
                root, enabled=True, native_modules_enabled=True
            )
            config = resolve_receiver_hybrid_config(root)
            scene = self._managed_native_scene()
            status = self._managed_native_status(scene)
            expectation = {
                "bundle_digest": "a" * 64,
                "payload_digest": "b" * 64,
                "parameter_digest": "c" * 64,
            }
            self.assertTrue(_restored_scene_proof(
                status, scene, config, native_expectation=expectation
            ))
            for field, value in (
                ("bundle_digest", "c" * 64),
                ("payload_digest", "d" * 64),
            ):
                candidate = json.loads(json.dumps(status))
                candidate["receiver_hybrid"]["driver"][field] = value
                self.assertFalse(_restored_scene_proof(
                    candidate, scene, config, native_expectation=expectation
                ))
            candidate = json.loads(json.dumps(status))
            candidate["receiver_hybrid"]["driver"]["capability_report"][
                "devices"
            ][4]["local_strip_count"] = 8
            self.assertFalse(_restored_scene_proof(
                candidate, scene, config, native_expectation=expectation
            ))

            mutations = (
                lambda value: value["receiver_hybrid"]["driver"].pop("agreement"),
                lambda value: value["receiver_hybrid"]["driver"]["agreement"].update(
                    verified_receiver_ids=[0, 1, 2, 3]
                ),
                lambda value: value["receiver_hybrid"]["driver"][
                    "capability_report"
                ].update(required_capabilities=0),
                lambda value: value["driver_stats"]["devices"][4].update(
                    receiver_active_context_digest="0" * 64
                ),
                lambda value: value["driver_stats"]["devices"][3].update(
                    receiver_profile_active_global_digest="0" * 64
                ),
                lambda value: value["driver_stats"]["devices"][2].update(
                    receiver_vibe_digest="0" * 64
                ),
                lambda value: value["driver_stats"]["devices"][1].update(
                    receiver_plant_modifier_digest="0" * 64
                ),
                lambda value: value["driver_stats"]["devices"][0].update(
                    receiver_native_active_parameter_digest="0" * 64
                ),
            )
            for mutate in mutations:
                candidate = json.loads(json.dumps(status))
                mutate(candidate)
                with self.subTest(candidate=candidate):
                    self.assertFalse(_restored_scene_proof(
                        candidate,
                        scene,
                        config,
                        native_expectation=expectation,
                    ))

    def test_restore_acknowledges_vibe_before_start_and_rechecks_final_state(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            status_path = root / "status.json"
            control_path = root / "control.json"
            preset_path = root / "before-deploy.json"
            state_path = root / "state.json"
            vibe = self._vibe("cozy")
            preset_path.write_text(json.dumps({
                "animation": "sparkle", "params": {"speed": 0.65},
            }))
            state_path.write_text(json.dumps({
                "animation": "sparkle", "preset_path": str(preset_path),
                "vibe": vibe,
                "current_preset": {
                    "preset_id": "stars", "name": "Stars",
                    "animation": "sparkle", "is_dirty": False,
                },
            }))
            status_path.write_text(json.dumps({"updated_at": 1}))
            observed_actions = []

            def simulate_controller():
                time.sleep(0.05)
                status_path.write_text(json.dumps({"updated_at": time.time()}))
                first_command_id = None
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if not control_path.exists():
                        time.sleep(0.01)
                        continue
                    command = json.loads(control_path.read_text())
                    if command["command_id"] == first_command_id:
                        time.sleep(0.01)
                        continue
                    observed_actions.append(command["action"])
                    if command["action"] == "set_vibe":
                        first_command_id = command["command_id"]
                        status_path.write_text(json.dumps({
                            "updated_at": time.time(),
                            "last_command_id": first_command_id,
                            "vibe": {"state": vibe},
                        }))
                    elif command["action"] == "start":
                        self.assertEqual(command["data"]["preset"]["preset_id"], "stars")
                        status_path.write_text(json.dumps({
                            "updated_at": time.time(),
                            "last_command_id": command["command_id"],
                            "current_animation": "sparkle",
                            "is_running": True,
                            "vibe": {"state": vibe},
                            "current_preset": command["data"]["preset"],
                        }))
                        return

            controller = threading.Thread(target=simulate_controller)
            controller.start()
            restored = restore(status_path, control_path, state_path, 1)
            controller.join()

            self.assertEqual(observed_actions, ["set_vibe", "start"])
            self.assertEqual(restored["vibe"], vibe)
            self.assertFalse(restored["vibe_fallback"])
            self.assertEqual(restored["params"]["speed"], 0.65)
            self.assertEqual(restored["current_preset"]["preset_id"], "stars")

    def test_restore_rejects_acknowledged_but_mismatched_vibe(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            status_path = root / "status.json"
            control_path = root / "control.json"
            preset_path = root / "before-deploy.json"
            state_path = root / "state.json"
            cozy = self._vibe("cozy")
            neutral = self._vibe("neutral")
            preset_path.write_text(json.dumps({"animation": "sparkle", "params": {}}))
            state_path.write_text(json.dumps({
                "animation": "sparkle", "preset_path": str(preset_path),
                "vibe": cozy,
            }))
            status_path.write_text(json.dumps({"updated_at": 1}))

            def simulate_bad_ack():
                time.sleep(0.02)
                status_path.write_text(json.dumps({"updated_at": time.time()}))
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    if control_path.exists():
                        command = json.loads(control_path.read_text())
                        status_path.write_text(json.dumps({
                            "updated_at": time.time(),
                            "last_command_id": command["command_id"],
                            "vibe": {"state": neutral},
                        }))
                        return
                    time.sleep(0.01)

            controller = threading.Thread(target=simulate_bad_ack)
            controller.start()
            with self.assertRaisesRegex(RuntimeError, "expected vibe"):
                restore(status_path, control_path, state_path, 0.2)
            controller.join()


if __name__ == "__main__":
    unittest.main()
