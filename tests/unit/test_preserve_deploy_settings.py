import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tools.deployment.preserve_deploy_settings import (
    load_saved_state,
    record_deploy,
    restore,
    save,
    save_status,
)


class PreserveDeploySettingsTests(unittest.TestCase):
    def test_record_deploy_updates_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            deployment_path = Path(temporary_dir) / "deployment.json"
            record_deploy(deployment_path, 123.5)
            record_deploy(deployment_path, 456.75)

            self.assertEqual(
                json.loads(deployment_path.read_text())["deploy_timestamp"],
                456.75,
            )

    def test_save_overwrites_preset_and_unscales_speed(self):
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
            self.assertAlmostEqual(preset["params"]["speed"], 2.0)
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
                "animation_info": {
                    "current_params": {"speed": 0.9, "brightness": 0.7},
                },
            }, presets_dir, state_path)

            saved = load_saved_state(state_path)
            self.assertEqual(saved["animation"], "rainbow")
            self.assertEqual(saved["params"], {"speed": 2.0, "brightness": 0.7})
            self.assertEqual(saved["animation_speed_scale"], 0.45)
            self.assertEqual(saved["target_fps"], 144)

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


if __name__ == "__main__":
    unittest.main()
