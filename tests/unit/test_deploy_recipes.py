"""Regression checks for fast, recoverable deployment recipes."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DeployRecipeTests(unittest.TestCase):
    def test_deploy_recipes_default_to_tests_and_allow_explicit_skip(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        self.assertGreaterEqual(justfile.count('${TEST:-true}'), 2)
        self.assertIn('Skipping tests (TEST=$TEST)', justfile)
        self.assertIn('just deploy-precheck', justfile)
        self.assertIn('just test-unit test-rendering test-deployment', justfile)

    def test_full_deploy_caches_packages_and_reports_startup_failures(self):
        script = (ROOT / "tools/deployment/deploy.sh").read_text(encoding="utf-8")
        self.assertIn("venv/.ledgrid_requirements_sha256", script)
        self.assertIn("Python dependencies unchanged; skipping pip install", script)
        self.assertIn("<<'EOF'\nset -euo pipefail\ndeploy_dir=$1", script)
        self.assertIn("for attempt in {1..120}", script)
        self.assertIn("collecting startup logs", script)
        self.assertIn("prepare_native_animation_packages", script)
        self.assertIn("sync_firmware_provisioning", script)
        self.assertIn("install_native_animation_packages", script)
        self.assertIn("check_receiver_animation_readiness", script)
        self.assertIn("--wait-seconds 30", script)
        self.assertIn("--min-updated-at", script)
        self.assertIn("RECEIVER_READINESS_MIN_UPDATED_AT", script)
        self.assertIn('date +%s.%N', script)
        self.assertIn("EnvironmentFile=/home/$PI_USER/$DEPLOY_DIR/run_state/firmware/deploy.env", script)

    def test_receiver_flash_is_explicit_signed_and_device_specific(self):
        script = (ROOT / "tools/deployment/flash_esp32.sh").read_text(encoding="utf-8")
        self.assertIn("LEDGRID_RECEIVER_0_PORT", script)
        self.assertIn("for logical_device in 0 1 2 3", script)
        self.assertIn("platformio-config", script)
        self.assertIn("CONFIG_LEDGRID_LOGICAL_DEVICE=$logical_device", script)
        self.assertIn("CONFIG_LEDGRID_ALLOW_UNSIGNED_DEVELOPMENT is not set", script)
        self.assertNotIn("/dev/ttyACM*", script)
        self.assertNotIn("Flashing firmware to $port_count", script)

    def test_fast_deploy_can_recover_when_old_web_process_is_broken(self):
        script = (ROOT / "tools/deployment/deploy_python.sh").read_text(encoding="utf-8")
        self.assertIn("Existing web service is unhealthy", script)
        self.assertIn('restore_saved=0', script)
        self.assertIn('if [ "$restore_saved" = 1 ]', script)
        self.assertIn("for attempt in {1..120}", script)

    def test_wall_data_recipe_fetches_masks_and_presets_together(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        script = (ROOT / "tools" / "deployment" / "fetch_wall_data.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("fetch-wall-data:", justfile)
        self.assertIn("fetch-wall-data:", justfile)
        self.assertNotIn("fetch-presets:", justfile)
        self.assertNotIn("deploy-no-firmware:", justfile)
        self.assertIn("plant_pixel_map_32x138.json", script)
        self.assertIn("plant_globe_map_32x138.json", script)
        self.assertIn('remote_presets_dir="~/$DEPLOY_DIR/presets/animations"', script)
        self.assertIn("--ignore-existing", script)
        self.assertIn("--exclude 'before-deploy.json'", script)


if __name__ == "__main__":
    unittest.main()
