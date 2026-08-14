"""Regression checks for fast, recoverable deployment recipes."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DeployRecipeTests(unittest.TestCase):
    def test_ai_ssh_key_recipe_generates_ignored_ed25519_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            key = Path(temporary_dir) / "agent key"
            generated = subprocess.run(
                ["just", "generate-ai-ssh-key", os.fspath(key)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(key.is_file())
            self.assertTrue(key.with_suffix(".pub").is_file())
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(key.with_suffix(".pub").stat().st_mode & 0o777, 0o644)
            self.assertTrue(
                key.with_suffix(".pub").read_text(encoding="utf-8").startswith(
                    "ssh-ed25519 "
                )
            )
            self.assertIn("ssh-copy-id", generated.stdout)
            self.assertIn("SSH_KEY=", generated.stdout)

            repeated = subprocess.run(
                ["just", "generate-ai-ssh-key", os.fspath(key)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn("Refusing to overwrite", repeated.stderr)

        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/.gpt-key\n", ignore)
        self.assertIn("/.gpt-key.pub\n", ignore)

    def test_deploy_recipes_default_to_tests_and_allow_explicit_skip(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        entrypoint = (ROOT / "tools/deployment/deploy_entrypoint.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('os.environ.get("TEST", "true")', entrypoint)
        self.assertIn('("just", "deploy-precheck")', entrypoint)
        self.assertIn('("just", "test-unit", "test-rendering", "test-deployment")', entrypoint)
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
        self.assertIn("deploy_entrypoint.py plan --mode full", justfile)
        self.assertIn("deploy_entrypoint.py plan --mode python", justfile)
        self.assertIn("deploy-legacy:", justfile)
        self.assertIn("deploy-python-legacy:", justfile)

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

    def test_just_dry_run_uses_authoritative_coordinator_without_hardware(self):
        full = subprocess.run(
            ["just", "--dry-run", "deploy"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        full = full.stdout + full.stderr
        self.assertEqual(full.count("deploy_entrypoint.py run --mode full --policy clean"), 1)
        self.assertNotIn("./tools/deployment/deploy.sh", full)
        self.assertNotIn("ssh ", full)

        fast = subprocess.run(
            ["just", "--dry-run", "deploy-python"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        fast = fast.stdout + fast.stderr
        self.assertEqual(fast.count("deploy_entrypoint.py run --mode python --policy clean"), 1)
        self.assertNotIn("./tools/deployment/deploy_python.sh", fast)
        self.assertNotIn("ssh ", fast)

    def test_full_deploy_uses_digest_environment_and_reports_startup_failures(self):
        script = (ROOT / "tools/deployment/deploy.sh").read_text(encoding="utf-8")
        self.assertIn("runtime_env.py ensure", script)
        self.assertIn("--lock requirements-pi.lock --link venv", script)
        self.assertNotIn("pip install -r requirements.txt", script)
        self.assertIn("for attempt in {1..120}", script)
        self.assertIn("collecting startup logs", script)
        self.assertIn("journalctl -u ledgrid.service -n 80", script)

    def test_legacy_full_sync_preserves_cutover_and_target_owned_state(self):
        script = (ROOT / "tools/deployment/sync_files.sh").read_text(
            encoding="utf-8"
        )
        for protected in (
            "/current",
            "/releases/***",
            "/.incoming/***",
            "/receipts/***",
            "/calibration_photos/***",
            "/receiver_library/***",
            "/presets/animations/***",
        ):
            self.assertIn(f"protect {protected}", script)

    def test_partial_firmware_failure_is_not_reported_as_success(self):
        script = (ROOT / "tools/deployment/flash_esp32.sh").read_text(
            encoding="utf-8"
        )
        failure = 'log_warning "Some devices failed to flash; hash NOT updated (will retry next deploy)"'
        self.assertIn(failure, script)
        self.assertIn(f"{failure}\n  exit 1", script)

    def test_firmware_upload_serializes_nobuild_reuse_of_validated_binary(self):
        script = (ROOT / "tools/deployment/flash_esp32.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("-t nobuild -t upload", script)
        self.assertIn("Flashing firmware to $port_count ESP32 device(s) sequentially", script)
        self.assertIn("verify_firmware_binary", script)
        self.assertIn('if [ "${FIRMWARE_PREBUILT:-0}" = "1" ]', script)
        upload_section = script.split(
            'log_info "Flashing firmware to $port_count ESP32 device(s) sequentially..."',
            1,
        )[1]
        self.assertNotIn("pids=(", upload_section)
        self.assertNotIn(") &", upload_section)
        self.assertNotIn(".platformio-upload-cache", script)

    def test_remote_recovery_helpers_use_the_supervised_service(self):
        stop = (ROOT / "tools/deployment/stop_remote.sh").read_text(encoding="utf-8")
        diagnose = (ROOT / "tools/diagnostics/remote_diagnostics.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("sudo systemctl restart ledgrid.service", stop)
        self.assertNotIn("nohup ./start.sh", stop)
        self.assertIn("sudo systemctl restart ledgrid.service", diagnose)
        self.assertNotIn('standalone web process', diagnose)

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
