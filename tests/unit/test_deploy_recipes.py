"""Regression checks for fast, recoverable deployment recipes."""

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DeployRecipeTests(unittest.TestCase):
    def test_deploy_recipes_default_to_tests_and_allow_explicit_skip(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        self.assertGreaterEqual(justfile.count('${TEST:-true}'), 6)
        self.assertIn('just deploy-precheck', justfile)
        self.assertIn('just test-unit test-rendering test-deployment', justfile)
        self.assertIn("set quiet := true", justfile)
        self.assertNotIn('["bash", "-euxo"', justfile)

    def test_clean_dirty_plan_and_verbose_modes_share_captured_runner(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        for recipe in (
            "deploy:",
            "deploy-dirty:",
            "deploy-plan:",
            "deploy-verbose:",
            "deploy-python:",
            "deploy-python-dirty:",
            "deploy-python-plan:",
            "deploy-python-verbose:",
        ):
            self.assertIn(recipe, justfile)
        self.assertIn("--policy clean", justfile)
        self.assertIn("--policy dirty", justfile)
        self.assertIn("--policy plan", justfile)
        self.assertIn("--verbose --phase", justfile)
        self.assertIn("run_captured.py --log-dir .deploy-logs", justfile)
        self.assertIn("deploy_coordinator.py plan --mode full", justfile)
        self.assertIn("deploy_coordinator.py plan --mode python", justfile)

    def test_deployment_gate_includes_every_phase_zero_policy_suite(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        for suite in (
            "tests/unit/test_deploy_*.py",
            "tests/unit/test_app_releases.py",
            "tests/unit/test_firmware_reconciliation.py",
            "tests/unit/test_gate_policy.py",
            "tests/unit/test_preserve_deploy_settings.py",
        ):
            self.assertIn(suite, justfile)

    def test_just_dry_run_preserves_full_and_python_leaf_order_without_hardware(self):
        full = subprocess.run(
            ["just", "--dry-run", "deploy"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        full = full.stdout + full.stderr
        full_phases = [
            full.index("--phase source.validate"),
            full.index("--phase tests.run"),
            full.index("--phase deploy.full"),
        ]
        self.assertEqual(full_phases, sorted(full_phases))
        self.assertEqual(full.count("./tools/deployment/deploy.sh"), 1)
        self.assertNotIn("ssh ", full)

        fast = subprocess.run(
            ["just", "--dry-run", "deploy-python"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        fast = fast.stdout + fast.stderr
        fast_phases = [
            fast.index("--phase source.validate"),
            fast.index("--phase tests.run"),
            fast.index("--phase deploy.python"),
        ]
        self.assertEqual(fast_phases, sorted(fast_phases))
        self.assertEqual(fast.count("./tools/deployment/deploy_python.sh"), 1)
        self.assertNotIn("ssh ", fast)

    def test_full_deploy_uses_digest_environment_and_reports_startup_failures(self):
        script = (ROOT / "tools/deployment/deploy.sh").read_text(encoding="utf-8")
        self.assertIn("runtime_env.py ensure", script)
        self.assertIn("--lock requirements-pi.lock --link venv", script)
        self.assertNotIn("pip install -r requirements.txt", script)
        self.assertIn("for attempt in {1..120}", script)
        self.assertIn("collecting startup logs", script)
        self.assertIn("journalctl -u ledgrid.service -n 80", script)

    def test_dependencies_and_firmware_toolchains_are_pinned(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        runtime_lock = (ROOT / "requirements-pi.lock").read_text(encoding="utf-8")
        platformio = (ROOT / "firmware/esp32/platformio.ini").read_text(encoding="utf-8")
        setup = (ROOT / "tools/deployment/setup.sh").read_text(encoding="utf-8")
        flash = (ROOT / "tools/deployment/flash_esp32.sh").read_text(encoding="utf-8")

        self.assertIn('requires-python = ">=3.10"', pyproject)
        self.assertIn("spidev>=3.6,<4; sys_platform == 'linux'", pyproject)
        self.assertIn('platformio==6.1.19', pyproject)
        self.assertIn("lock-dependencies:", (ROOT / "Justfile").read_text(encoding="utf-8"))
        self.assertIn("--hash=sha256:", runtime_lock)
        self.assertIn("spidev==", runtime_lock)
        self.assertIn("sys_platform == 'linux'", runtime_lock)
        self.assertIn("releases/download/55.03.39/", platformio)
        self.assertNotIn("releases/download/stable/", platformio)
        self.assertIn('platform = native@1.2.1', platformio)
        self.assertIn('platformio==${EXPECTED_PLATFORMIO_VERSION}', setup)
        self.assertIn("version 6\\.1\\.19", flash)
        self.assertIn("extra_script.py", flash)

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
        self.assertIn("fetch-presets: fetch-wall-data", justfile)
        self.assertIn("plant_pixel_map_32x138.json", script)
        self.assertIn("plant_globe_map_32x138.json", script)
        self.assertIn('remote_presets_dir="~/$DEPLOY_DIR/presets/animations"', script)
        self.assertIn("--ignore-existing", script)
        self.assertIn("--exclude 'before-deploy.json'", script)


if __name__ == "__main__":
    unittest.main()
