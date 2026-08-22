"""Focused acceptance for complete PlatformIO flash-installation identity."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.deployment import deploy_target
from tools.deployment.firmware_artifacts import inspect_firmware_installation


ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT = "esp32-s3-devkitc-1"


def _write_installation(firmware: Path) -> Path:
    build = firmware / ".pio" / "build" / ENVIRONMENT
    build.mkdir(parents=True)
    (firmware / "platformio.ini").write_text("board_build.flash_mode = dio\n")
    (firmware / "partitions.csv").write_text(
        "# Name,Type,SubType,Offset,Size,Flags\n"
        "factory,app,factory,0x10000,1M,\n"
    )
    (firmware / "sdkconfig.defaults").write_text("CONFIG_PARTITION_TABLE_OFFSET=0x8000\n")
    (firmware / f"sdkconfig.{ENVIRONMENT}").write_text(
        "CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y\n"
    )
    (build / "firmware.bin").write_bytes(b"application-v1")
    (build / "bootloader.bin").write_bytes(b"bootloader-v1")
    (build / "partitions.bin").write_bytes(b"partitions-v1")
    flash_map = {
        "write_flash_args": ["--flash_mode", "dio", "--flash_size", "16MB"],
        "flash_files": {
            "0x0": "bootloader/bootloader.bin",
            "0x10000": "ledgrid_receiver.bin",
            "0x8000": "partition_table/partition-table.bin",
        },
        "bootloader": {"offset": "0x0", "file": "bootloader/bootloader.bin"},
        "app": {"offset": "0x10000", "file": "ledgrid_receiver.bin"},
        "partition-table": {
            "offset": "0x8000",
            "file": "partition_table/partition-table.bin",
        },
    }
    (build / "flasher_args.json").write_text(
        json.dumps(flash_map, sort_keys=True), encoding="utf-8"
    )
    (build / "flash_args").write_text(
        "--flash_mode dio --flash_size 16MB\n"
        "0x0 bootloader/bootloader.bin\n"
        "0x10000 ledgrid_receiver.bin\n"
        "0x8000 partition_table/partition-table.bin\n",
        encoding="utf-8",
    )
    return build


class FirmwareArtifactIdentityTests(unittest.TestCase):
    def test_repository_pins_esp_idf_reproducible_build_for_every_environment(self) -> None:
        defaults = (
            ROOT / "firmware" / "esp32" / "sdkconfig.defaults"
        ).read_text(encoding="utf-8")
        self.assertIn("CONFIG_APP_REPRODUCIBLE_BUILD=y", defaults)
        self.assertNotIn("# CONFIG_APP_REPRODUCIBLE_BUILD is not set", defaults)

    def test_shared_marker_rejects_symlinks_and_non_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir) / "target"
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            sentinel = Path(temporary_dir) / "external-sentinel"
            sentinel.write_text("do not touch\n", encoding="utf-8")
            marker = root / ".esp32_firmware_hash"
            marker.symlink_to(sentinel)

            with self.assertRaisesRegex(RuntimeError, "non-symlink regular file"):
                deploy_target._prepare_shared_firmware_marker(root, workspace)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not touch\n")
            self.assertTrue(marker.is_symlink())
            self.assertFalse((workspace / ".esp32_firmware_hash").exists())

            marker.unlink()
            marker.mkdir()
            with self.assertRaisesRegex(RuntimeError, "non-symlink regular file"):
                deploy_target._prepare_shared_firmware_marker(root, workspace)

    def test_receipt_binds_every_mapped_artifact_offset_and_layout_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            firmware = Path(temporary_dir) / "firmware"
            build = _write_installation(firmware)
            receipt = inspect_firmware_installation(firmware, ENVIRONMENT)

            self.assertEqual(receipt["schema_version"], 3)
            self.assertEqual(
                [(item["offset"], item["build_path"]) for item in receipt["flash_artifacts"]],
                [
                    ("0x0", "bootloader.bin"),
                    ("0x8000", "partitions.bin"),
                    ("0x10000", "firmware.bin"),
                ],
            )
            self.assertEqual(
                {item["path"] for item in receipt["layout_inputs"]},
                {
                    f".pio/build/{ENVIRONMENT}/flash_args",
                    f".pio/build/{ENVIRONMENT}/flasher_args.json",
                    "platformio.ini",
                    "partitions.csv",
                    "sdkconfig.defaults",
                    f"sdkconfig.{ENVIRONMENT}",
                },
            )
            self.assertEqual(
                receipt["firmware_sha256"],
                next(
                    item["sha256"]
                    for item in receipt["flash_artifacts"]
                    if item["build_path"] == "firmware.bin"
                ),
            )
            self.assertTrue((build / "firmware.bin").is_file())

    def test_bootloader_partition_and_layout_only_changes_each_change_identity(self) -> None:
        mutations = (
            ("bootloader", lambda firmware, build: (build / "bootloader.bin").write_bytes(b"bootloader-v2")),
            ("partition", lambda firmware, build: (build / "partitions.bin").write_bytes(b"partitions-v2")),
            ("generated layout", lambda firmware, build: (build / "flash_args").write_text("changed layout\n")),
            ("source partition", lambda firmware, build: (firmware / "partitions.csv").write_text("changed partition layout\n")),
            ("source layout", lambda firmware, build: (firmware / "sdkconfig.defaults").write_text("changed config\n")),
        )
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary_dir:
                firmware = Path(temporary_dir) / "firmware"
                build = _write_installation(firmware)
                before = inspect_firmware_installation(firmware, ENVIRONMENT)
                mutate(firmware, build)
                after = inspect_firmware_installation(firmware, ENVIRONMENT)
                self.assertEqual(before["firmware_sha256"], after["firmware_sha256"])
                self.assertNotEqual(
                    before["installation_digest"], after["installation_digest"]
                )

    def test_partition_source_is_required_and_must_be_a_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            firmware = Path(temporary_dir) / "firmware"
            _write_installation(firmware)
            partition_source = firmware / "partitions.csv"
            partition_source.unlink()

            with self.assertRaisesRegex(RuntimeError, "layout input"):
                inspect_firmware_installation(firmware, ENVIRONMENT)

            outside = Path(temporary_dir) / "outside-partitions.csv"
            outside.write_text("external\n", encoding="utf-8")
            partition_source.symlink_to(outside)
            with self.assertRaisesRegex(RuntimeError, "missing or unsafe"):
                inspect_firmware_installation(firmware, ENVIRONMENT)

    def test_additional_platformio_flash_file_is_hashed_or_rejected_if_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            firmware = Path(temporary_dir) / "firmware"
            build = _write_installation(firmware)
            path = build / "flasher_args.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["flash_files"]["0xe000"] = "ota_data_initial.bin"
            path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "flash artifact"):
                inspect_firmware_installation(firmware, ENVIRONMENT)

            (build / "ota_data_initial.bin").write_bytes(b"ota-data-v1")
            first = inspect_firmware_installation(firmware, ENVIRONMENT)
            self.assertIn(
                "ota_data_initial.bin",
                {item["build_path"] for item in first["flash_artifacts"]},
            )
            (build / "ota_data_initial.bin").write_bytes(b"ota-data-v2")
            second = inspect_firmware_installation(firmware, ENVIRONMENT)
            self.assertNotEqual(
                first["installation_digest"], second["installation_digest"]
            )

    def test_helper_validates_complete_selection_before_unchanged_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            firmware = root / "firmware" / "esp32"
            build = _write_installation(firmware)
            selected = inspect_firmware_installation(firmware, ENVIRONMENT)
            (root / ".esp32_firmware_hash").write_text(
                selected["installation_digest"] + "\n", encoding="utf-8"
            )
            fake_bin = root / "bin"
            fake_bin.mkdir()
            pio = fake_bin / "pio"
            pio.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"--version\" ]; then\n"
                "  echo 'PlatformIO Core, version 6.1.19'\n"
                "  exit 0\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            pio.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": os.fspath(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
                "DEPLOY_DIR": os.fspath(root),
                "FIRMWARE_PREBUILT": "1",
                "FIRMWARE_ENVIRONMENT": ENVIRONMENT,
                "EXPECTED_FIRMWARE_SHA256": selected["firmware_sha256"],
                "EXPECTED_FIRMWARE_INSTALLATION_DIGEST": selected[
                    "installation_digest"
                ],
                "EXPECTED_FIRMWARE_HASH_FILE": os.fspath(
                    root / ".esp32_firmware_hash"
                ),
            }
            helper = ROOT / "tools" / "deployment" / "flash_esp32.sh"
            unchanged = subprocess.run(
                ("bash", helper), env=environment, text=True, capture_output=True
            )
            self.assertEqual(unchanged.returncode, 0, unchanged.stdout + unchanged.stderr)
            self.assertIn("Firmware unchanged; skipping ESP32 flash", unchanged.stdout)

            (build / "partitions.bin").write_bytes(b"partition-only-change")
            changed = subprocess.run(
                ("bash", helper), env=environment, text=True, capture_output=True
            )
            self.assertNotEqual(changed.returncode, 0)
            self.assertNotIn("Firmware unchanged; skipping", changed.stdout)
            self.assertIn(
                "installation artifacts changed or are incomplete",
                changed.stdout + changed.stderr,
            )

            # A legacy source-only marker remains a valid readable SHA-256,
            # but cannot claim the complete v2 installation. With the exact
            # selected artifacts restored, the helper proceeds toward a
            # migration flash (and stops here only because the test has no
            # serial devices).
            (build / "partitions.bin").write_bytes(b"partitions-v1")
            legacy_digest = "f" * 64
            (root / ".esp32_firmware_hash").write_text(
                legacy_digest + "\n", encoding="utf-8"
            )
            migration = subprocess.run(
                ("bash", helper), env=environment, text=True, capture_output=True
            )
            self.assertNotEqual(migration.returncode, 0)
            self.assertNotIn("Firmware unchanged; skipping", migration.stdout)
            self.assertIn("Discovering ESP32 devices", migration.stdout)
            self.assertEqual(
                (root / ".esp32_firmware_hash").read_text(encoding="utf-8").strip(),
                legacy_digest,
                "a failed migration flash must preserve the prior marker for retry",
            )

            sentinel = root / "external-sentinel"
            sentinel.write_text("unchanged\n", encoding="utf-8")
            mismatched = {
                **environment,
                "EXPECTED_FIRMWARE_HASH_FILE": os.fspath(sentinel),
            }
            rejected = subprocess.run(
                ("bash", helper), env=mismatched, text=True, capture_output=True
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn(
                "does not resolve to the expected shared file",
                rejected.stdout + rejected.stderr,
            )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged\n")

    def test_helper_flashes_only_coordinator_selected_ports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            firmware = root / "firmware" / "esp32"
            _write_installation(firmware)
            selected = inspect_firmware_installation(firmware, ENVIRONMENT)
            marker = root / ".esp32_firmware_hash"
            marker.write_text(
                selected["installation_digest"] + "\n", encoding="utf-8"
            )
            upload_log = root / "pio-uploads.log"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            pio = fake_bin / "pio"
            pio.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"--version\" ]; then\n"
                "  echo 'PlatformIO Core, version 6.1.19'\n"
                "  exit 0\n"
                "fi\n"
                "printf '%s\\n' \"$*\" >> \"$PIO_TEST_LOG\"\n"
                "exit 0\n",
                encoding="utf-8",
            )
            pio.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": os.fspath(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
                "DEPLOY_DIR": os.fspath(root),
                "FIRMWARE_PREBUILT": "1",
                "FIRMWARE_ENVIRONMENT": ENVIRONMENT,
                "EXPECTED_FIRMWARE_SHA256": selected["firmware_sha256"],
                "EXPECTED_FIRMWARE_INSTALLATION_DIGEST": selected[
                    "installation_digest"
                ],
                "EXPECTED_FIRMWARE_HASH_FILE": os.fspath(marker),
                "FORCE_FIRMWARE_FLASH": "1",
                "FIRMWARE_FLASH_PORTS": "/dev/ttyACM7\n/dev/ttyUSB2",
                "EXPECTED_FIRMWARE_PORT_COUNT": "2",
                "PIO_TEST_LOG": os.fspath(upload_log),
            }

            helper = ROOT / "tools" / "deployment" / "flash_esp32.sh"
            completed = subprocess.run(
                ("bash", helper), env=environment, text=True, capture_output=True
            )

            self.assertEqual(
                completed.returncode, 0, completed.stdout + completed.stderr
            )
            uploads = upload_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(uploads), 2)
            self.assertIn("--upload-port /dev/ttyACM7", uploads[0])
            self.assertIn("--upload-port /dev/ttyUSB2", uploads[1])
            self.assertNotIn("Firmware unchanged; skipping", completed.stdout)
            self.assertEqual(
                marker.read_text(encoding="utf-8").strip(),
                selected["installation_digest"],
            )


if __name__ == "__main__":
    unittest.main()
