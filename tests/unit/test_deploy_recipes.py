"""Regression checks for fast, recoverable deployment recipes."""

import csv
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DeployRecipeTests(unittest.TestCase):
    def test_receiver_profile_partition_layout_is_explicit_and_bounded(self):
        partition_path = ROOT / "firmware/esp32/partitions.csv"
        with partition_path.open(newline="", encoding="utf-8") as stream:
            rows = [
                tuple(field.strip() for field in row)
                for row in csv.reader(stream)
                if row and not row[0].lstrip().startswith("#")
            ]

        self.assertEqual(
            rows,
            [
                ("nvs", "data", "nvs", "0x9000", "0x5000", ""),
                ("otadata", "data", "ota", "0xe000", "0x2000", ""),
                ("ota_0", "app", "ota_0", "0x10000", "0x600000", ""),
                ("ota_1", "app", "ota_1", "0x610000", "0x600000", ""),
                (
                    "profilecache",
                    "data",
                    "spiffs",
                    "0xc10000",
                    "0x3e0000",
                    "",
                ),
            ],
        )
        prior_end = 0x9000
        for name, _type, _subtype, raw_offset, raw_size, _flags in rows:
            offset = int(raw_offset, 0)
            size = int(raw_size, 0)
            self.assertGreaterEqual(offset, prior_end, name)
            prior_end = offset + size
        self.assertLessEqual(prior_end, 16 * 1024 * 1024)
        self.assertGreaterEqual(int(rows[-1][4], 0), 512 * 1024)

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

    def test_native_recipes_use_only_the_separate_native_workflow(self):
        plan = subprocess.run(
            ["just", "--dry-run", "native-plan", "aurora_curtains_native"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        build = subprocess.run(
            ["just", "--dry-run", "native-build", "aurora_curtains_native"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        publish = subprocess.run(
            ["just", "--dry-run", "native-publish", "aurora_curtains_native"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        for output, command in (
            (plan.stdout + plan.stderr, 'plan "aurora_curtains_native"'),
            (build.stdout + build.stderr, 'build "aurora_curtains_native"'),
            (publish.stdout + publish.stderr, 'publish "aurora_curtains_native"'),
        ):
            self.assertIn("native_background_entrypoint.py", output)
            self.assertIn(command, output)
            self.assertNotIn("deploy_entrypoint.py run", output)
            self.assertNotIn("flash_esp32", output)
            self.assertNotIn("systemctl", output)
        self.assertIn("uv run --frozen", plan.stdout + plan.stderr)

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
        self.assertNotIn("native_background_entrypoint.py", full)
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
        self.assertNotIn("native_background_entrypoint.py", fast)
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

    def test_firmware_upload_serializes_supported_upload_target_for_validated_binary(self):
        script = (ROOT / "tools/deployment/flash_esp32.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '$PIO_CMD run -e "$firmware_environment" -t upload \\',
            script,
        )
        self.assertNotIn("-t nobuild", script)
        self.assertIn("Flashing firmware to $port_count ESP32 device(s) sequentially", script)
        self.assertIn("verify_firmware_installation", script)
        self.assertIn('if [ "${FIRMWARE_PREBUILT:-0}" = "1" ]', script)
        self.assertIn('FIRMWARE_ENVIRONMENT:-esp32-s3-devkitc-1', script)
        self.assertIn('--environment "$firmware_environment"', script)
        self.assertIn('current_hash="$actual_installation_digest"', script)
        self.assertIn("EXPECTED_FIRMWARE_INSTALLATION_DIGEST", script)
        self.assertIn("firmware_artifacts.py", script)
        self.assertIn('hash_storage="$(resolve_hash_storage)"', script)
        self.assertIn("EXPECTED_FIRMWARE_HASH_FILE", script)
        self.assertIn('mktemp "${validated_hash_storage}.tmp.XXXXXX"', script)
        self.assertIn('mv "$marker_temporary" "$validated_hash_storage"', script)
        self.assertNotIn('mv "$marker_temporary" "$HASH_FILE"', script)
        self.assertLess(
            script.index("verify_firmware_installation\nelse"),
            script.index('if [ "$current_hash" = "$previous_hash" ]'),
            "the exact selected binary must be verified before early skip",
        )
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
        self.assertIn("board_build.partitions = partitions.csv", platformio)
        self.assertIn('platformio==${EXPECTED_PLATFORMIO_VERSION}', setup)
        self.assertIn("version 6\\.1\\.19", flash)
        artifact_identity = (
            ROOT / "tools/deployment/firmware_artifacts.py"
        ).read_text(encoding="utf-8")
        self.assertIn("platformio.ini", artifact_identity)
        self.assertIn("partitions.csv", artifact_identity)
        self.assertIn("sdkconfig.defaults", artifact_identity)

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
