import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tools.deployment.preserve_deploy_settings import (
    _expected_restored_vibe,
    load_saved_state,
    record_deploy,
    restore,
    save,
    save_status,
)


class PreserveDeploySettingsTests(unittest.TestCase):
    @staticmethod
    def _vibe(vibe_id="cozy"):
        from animation.core.presentation_contracts import resolve_vibe

        return resolve_vibe(vibe_id).state.to_dict()

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
