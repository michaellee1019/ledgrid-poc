"""Executable cutover coverage for deployment entrypoint and target adapters.

The workstation entrypoint is intentionally exercised here with a filesystem
target instead of a wall.  Target-leaf tests use the same immutable snapshot
shape so failures cannot hide behind mocked manifest or release semantics.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from types import SimpleNamespace
from typing import Any, Mapping
import unittest
from unittest.mock import MagicMock, patch

from animation.core.plugin_loader import AnimationPluginLoader
from tools.deployment import deploy_entrypoint, deploy_target
from tools.deployment.app_releases import AppReleaseManager
from tools.deployment.deploy_coordinator import (
    AtomicJSONReceiptStore,
    CommandResult,
    DeployContext,
    DeployCoordinator,
    FULL_STEP_ORDER,
    ROLLBACK_STEP_ORDER,
    SSHAtomicJSONReceiptStore,
)
from tools.deployment.receiver_hybrid_config import (
    DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
    NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
    PRODUCTION_FIRMWARE_ENVIRONMENT,
    RECEIVER_HYBRID_CONFIG_RELATIVE_PATH,
    write_receiver_hybrid_config,
)
from tools.deployment.receiver_firmware_inventory import ReceiverUSBDevice


ROOT = Path(__file__).resolve().parents[2]
FIRMWARE_SHA256 = "e" * 64
ROLLOUT_CONFIG_DIGEST = "f" * 64
FIRMWARE_INSTALLATION_DIGEST = "a" * 64


def _receiver_devices() -> tuple[ReceiverUSBDevice, ...]:
    return tuple(
        ReceiverUSBDevice(
            port=f"/dev/ttyACM{index}",
            hardware_serial=f"02:00:00:00:00:{index:02x}",
            physical_location=f"1-1.{index + 1}:1.0",
        )
        for index in range(5)
    )


@contextmanager
def _writable_temporary_directory():
    with tempfile.TemporaryDirectory() as temporary_dir:
        root = Path(temporary_dir)
        try:
            yield root
        finally:
            for path in root.rglob("*"):
                if not path.is_symlink() and path.is_dir():
                    path.chmod(0o755)


def _write_firmware_artifacts(
    firmware: Path,
    environment: str,
    *,
    application: bytes,
) -> Path:
    build = firmware / ".pio" / "build" / environment
    build.mkdir(parents=True, exist_ok=True)
    (firmware / "platformio.ini").write_text("board_build.flash_mode = dio\n")
    (firmware / "partitions.csv").write_text(
        "# Name,Type,SubType,Offset,Size,Flags\n"
        "factory,app,factory,0x10000,1M,\n"
    )
    (firmware / "sdkconfig.defaults").write_text("CONFIG_PARTITION_TABLE_OFFSET=0x8000\n")
    (firmware / f"sdkconfig.{environment}").write_text("CONFIG_FLASH_SIZE=16MB\n")
    binary = build / "firmware.bin"
    binary.write_bytes(application)
    (build / "bootloader.bin").write_bytes(b"bootloader")
    (build / "partitions.bin").write_bytes(b"partitions")
    payload = {
        "flash_files": {
            "0x0": "bootloader/bootloader.bin",
            "0x10000": "ledgrid_receiver.bin",
            "0x8000": "partition_table/partition-table.bin",
        },
        "bootloader": {"file": "bootloader/bootloader.bin"},
        "app": {"file": "ledgrid_receiver.bin"},
        "partition-table": {"file": "partition_table/partition-table.bin"},
    }
    (build / "flasher_args.json").write_text(json.dumps(payload), encoding="utf-8")
    (build / "flash_args").write_text(
        "0x0 bootloader/bootloader.bin\n"
        "0x10000 ledgrid_receiver.bin\n"
        "0x8000 partition_table/partition-table.bin\n",
        encoding="utf-8",
    )
    return binary


class _SnapshotFixture:
    """Build the exact self-describing snapshot accepted by deploy_target."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def build(
        self,
        *,
        app: dict[str, bytes] | None = None,
        support: dict[str, bytes] | None = None,
    ) -> Path:
        app = app or {
            "scripts/start_server.py": b"print('app')\n",
            "web/static/generated/animation-previews/catalog.json": b"{}\n",
        }
        support = support or {
            "firmware/esp32/src/main.cpp": b"void setup() {}\n",
            "requirements-platformio.lock": b"platformio==6.1.19\n",
        }
        files = {**app, **support}
        deploy = self.root / ".deploy"
        deploy.mkdir(parents=True)
        lane_payloads = {
            "app-manifest.json": {
                "schema_version": 1,
                "files": sorted(app),
            },
            "support-manifest.json": {
                "schema_version": 1,
                "files": sorted(support),
            },
        }
        for name, payload in lane_payloads.items():
            files[f".deploy/{name}"] = (
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()

        evidence = []
        for relative, contents in sorted(files.items()):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
            executable = relative.endswith(".sh") or relative.endswith("start_server.py")
            path.chmod(0o555 if executable else 0o444)
            evidence.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(contents).hexdigest(),
                    "size": len(contents),
                    "executable": executable,
                }
            )

        snapshot_metadata = deploy / "snapshot.json"
        snapshot_payload = {
            "schema_version": 1,
            "source_identity": {"base_commit": "abc", "scope": "full"},
            "files": evidence,
        }
        snapshot_payload["snapshot_id"] = hashlib.sha256(
            json.dumps(
                snapshot_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        snapshot_metadata.write_text(
            json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        snapshot_metadata.chmod(0o444)
        return self.root


class _GitFixture:
    """Minimal clean repository that still spans both deployment lanes."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True)
        self.write("scripts/start_server.py", b"print('app')\n", executable=True)
        self.write("tools/deployment/deploy_target.py", b"print('target')\n", executable=True)
        self.write("requirements-pi.lock", b"flask==3.1.3\n")
        self.write("requirements-platformio.lock", b"platformio==6.1.19\n")
        self.write(
            "firmware/esp32/partitions.csv",
            b"profilecache,data,spiffs,0xc10000,0x3e0000,\n",
        )
        self.write("firmware/esp32/src/main.cpp", b"void setup() {}\n")
        self.write("hardware/wiring.txt", b"four receivers\n")
        subprocess.run(("git", "init", "-q", root), check=True)
        subprocess.run(("git", "-C", root, "config", "user.email", "test@example.com"), check=True)
        subprocess.run(("git", "-C", root, "config", "user.name", "Deploy Test"), check=True)
        subprocess.run(("git", "-C", root, "add", "."), check=True)
        subprocess.run(
            (
                "git", "-C", root,
                "-c", "commit.gpgsign=false",
                "-c", "tag.gpgsign=false",
                "commit", "-qm", "fixture",
            ),
            check=True,
        )

    def write(self, relative: str, contents: bytes, *, executable: bool = False) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        path.chmod(0o755 if executable else 0o644)
        return path


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(self, args, **kwargs):
        normalized = tuple(os.fspath(arg) for arg in args)
        self.calls.append((normalized, kwargs))
        return CommandResult(normalized, 0, "", "", 0.01)


class _FakeTarget:
    def __init__(
        self, *, unchanged: bool = False, firmware_changed: bool = False
    ) -> None:
        self.unchanged = unchanged
        self.firmware_changed = firmware_changed
        self.candidate = "c" * 64
        self.previous = self.candidate if unchanged else "b" * 64
        self.support = "d" * 64
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    @property
    def incoming(self) -> str:
        return "fake-root/.incoming/entrypoint-integration"

    def run(self, command: str, *args: str):
        self.calls.append((command, args))
        if command == "stage-support":
            return {"support_release_id": self.support, "reused": self.unchanged}
        if command == "stage-app":
            return {"release_id": self.candidate, "reused": self.unchanged}
        if command == "cleanup-snapshot":
            return {"removed": True}
        if command == "bootstrap-legacy-app":
            return {
                "outcome": "skipped",
                "reason": "an immutable app release is already selected",
                "selected": False,
                "current_release": self.previous,
                "bootstrap_release_id": None,
                "recovery_release": None,
            }
        if command == "migrate-receiver-topology":
            return {
                "outcome": "skipped",
                "migrated": False,
                "strips": 33,
                "receivers": 5,
                "receiver_hybrid_config_digest": ROLLOUT_CONFIG_DIGEST,
                "receiver_hybrid_config": {
                    "receiver_strip_counts": [8, 8, 8, 8, 1],
                    "receiver_global_strip_offsets": [0, 8, 16, 24, 32],
                    "physical_output_lane_masks": [255, 255, 255, 255, 255],
                    "reverse_strips_by_logical_receiver": [
                        False, False, False, False, False,
                    ],
                    "reverse_native_strips_by_logical_receiver": [
                        False, False, True, True, False,
                    ],
                },
            }
        if command == "build-firmware":
            return {
                "outcome": "skipped",
                "reason": "firmware build already exists",
                "firmware_environment": PRODUCTION_FIRMWARE_ENVIRONMENT,
                "firmware_sha256": FIRMWARE_SHA256,
                "firmware_installation_digest": FIRMWARE_INSTALLATION_DIGEST,
                "receiver_hybrid_config_digest": ROLLOUT_CONFIG_DIGEST,
                "receiver_hybrid_config": {
                    "receiver_strip_counts": [8, 8, 8, 8, 1],
                    "receiver_global_strip_offsets": [0, 8, 16, 24, 32],
                    "physical_output_lane_masks": [255, 255, 255, 255, 255],
                    "reverse_strips_by_logical_receiver": [
                        False, False, False, False, False,
                    ],
                    "reverse_native_strips_by_logical_receiver": [
                        False, False, True, True, False,
                    ],
                },
            }
        if command == "provision":
            return {
                "runtime": {"installed": False},
                "unit": {"changed": False},
                "spi": {"status": "ready", "config_changed": False},
            }
        if command == "flash-firmware":
            return {
                "outcome": "executed" if self.firmware_changed else "skipped",
                "reason": (
                    "firmware changed" if self.firmware_changed
                    else "firmware unchanged"
                ),
                "firmware_environment": PRODUCTION_FIRMWARE_ENVIRONMENT,
                "firmware_sha256": FIRMWARE_SHA256,
                "firmware_installation_digest": FIRMWARE_INSTALLATION_DIGEST,
                "receiver_hybrid_config_digest": ROLLOUT_CONFIG_DIGEST,
            }
        if command == "validate-app":
            return {"release_id": self.candidate, "digest": self.candidate}
        if command == "capture-state":
            return {"captured": True}
        if command == "current-release":
            return {"current_release": self.previous}
        if command == "activate":
            return {
                "release_id": args[0],
                "previous_release": self.previous,
                "changed": not self.unchanged,
            }
        if command == "restart":
            return {"restart_started_at": 100.0}
        if command == "restore-state":
            return {
                "restored": True,
                "receiver_hybrid_config_digest": ROLLOUT_CONFIG_DIGEST,
            }
        if command == "health":
            return {
                "desired_release": args[0],
                "observed_release": args[0],
                "stable_samples": 2,
            }
        if command == "complete-legacy-bootstrap":
            return {"outcome": "skipped", "reason": "no legacy bootstrap lifecycle"}
        if command == "record-deploy":
            return {"recorded": True}
        if command == "prune-releases":
            return {"outcome": "skipped", "retain": int(args[1]), "removed_releases": []}
        raise AssertionError(f"unexpected target command: {command} {args}")


class TargetSnapshotIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_dir.name)
        self.snapshot = _SnapshotFixture(self.base / "snapshot").build()
        self.target = self.base / "target"

    def tearDown(self) -> None:
        # Staged releases are deliberately read-only. TemporaryDirectory cleanup
        # needs write/search permission on their parent directories on macOS.
        for path in self.base.rglob("*"):
            if not path.is_symlink() and path.is_dir():
                path.chmod(0o755)
        self.temporary_dir.cleanup()

    def test_verified_snapshot_stages_separate_app_and_support_releases(self) -> None:
        verified = deploy_target.verify_snapshot(self.snapshot)
        app = deploy_target.stage_app(self.target, self.snapshot)
        support = deploy_target.stage_support(self.target, self.snapshot)

        self.assertRegex(verified["snapshot_id"], r"^[0-9a-f]{64}$")
        self.assertRegex(app["release_id"], r"^[0-9a-f]{64}$")
        self.assertRegex(support["support_release_id"], r"^[0-9a-f]{64}$")

        app_root = self.target / "releases" / app["release_id"]
        support_root = self.target / "support_releases" / support["support_release_id"]
        self.assertTrue((app_root / "scripts/start_server.py").is_file())
        # ``firmware`` is target-owned shared state in every app release; the
        # snapshot's firmware source must not be copied into that lane.
        self.assertTrue((app_root / "firmware").is_symlink())
        self.assertFalse((app_root / "firmware/esp32/src/main.cpp").exists())
        self.assertFalse((app_root / "requirements-platformio.lock").exists())
        self.assertTrue((support_root / "firmware/esp32/src/main.cpp").is_file())
        self.assertTrue((support_root / "requirements-platformio.lock").is_file())
        self.assertFalse((support_root / "scripts/start_server.py").exists())
        self.assertFalse(app_root.stat().st_mode & 0o222)
        self.assertFalse(support_root.stat().st_mode & 0o222)

        reused_app = deploy_target.stage_app(self.target, self.snapshot)
        reused_support = deploy_target.stage_support(self.target, self.snapshot)
        self.assertTrue(reused_app["reused"])
        self.assertTrue(reused_support["reused"])

    def test_activation_and_inspection_use_the_exact_staged_release(self) -> None:
        app = deploy_target.stage_app(self.target, self.snapshot)
        support = deploy_target.stage_support(self.target, self.snapshot)

        first = deploy_target.activate(self.target, app["release_id"])
        repeated = deploy_target.activate(self.target, app["release_id"])
        inspected = deploy_target.inspect_target(self.target)

        self.assertTrue(first["changed"])
        self.assertIsNone(first["previous_release"])
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["previous_release"], app["release_id"])
        self.assertEqual(inspected["current_release"], app["release_id"])
        self.assertEqual(inspected["releases"], [app["release_id"]])
        self.assertEqual(inspected["support_releases"], [support["support_release_id"]])
        self.assertEqual(
            inspected["receipt_directory"],
            str(self.target / "run_state/deploy_receipts"),
        )

    def _legacy_bootstrap_fixture(self) -> tuple[AppReleaseManager, str]:
        files = {
            "scripts/start_systemd.sh": "#!/bin/bash\n",
            "scripts/start_server.py": "print('legacy')\n",
            "tools/deployment/preserve_deploy_settings.py": "print('save')\n",
            "drivers/led_layout.py": "DEFAULT_STRIP_COUNT = 33\n",
            "web/app.py": "def create_app(): return None\n",
        }
        for relative, contents in files.items():
            path = self.target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        (self.target / "scripts/.env").write_text("TOKEN=not-a-release-input\n")
        cache = self.target / "tools/build"
        cache.mkdir(parents=True)
        (cache / "cached.bin").write_bytes(b"cache")
        candidate_source = self.base / "candidate.txt"
        candidate_source.write_text("candidate\n", encoding="utf-8")
        manager = AppReleaseManager(self.target)
        candidate = manager.stage({"candidate.txt": candidate_source})
        return manager, candidate.id

    def test_first_cutover_bootstrap_is_immutable_idempotent_and_resume_safe(self) -> None:
        manager, candidate_id = self._legacy_bootstrap_fixture()
        with patch.object(
            deploy_target,
            "_legacy_service_working_directory",
            return_value=self.target.resolve(),
        ):
            first = deploy_target.bootstrap_legacy_app(self.target, candidate_id)

        bootstrap_id = first["bootstrap_release_id"]
        self.assertTrue(first["selected"])
        self.assertNotEqual(bootstrap_id, candidate_id)
        self.assertEqual(manager.current_release_id(), bootstrap_id)
        bootstrap = manager.validate(bootstrap_id)
        self.assertFalse(bootstrap.path.stat().st_mode & stat.S_IWUSR)
        self.assertTrue((bootstrap.path / "scripts/start_systemd.sh").is_file())
        self.assertFalse((bootstrap.path / "scripts/.env").exists())
        self.assertFalse((bootstrap.path / "tools/build/cached.bin").exists())

        repeated = deploy_target.bootstrap_legacy_app(self.target, candidate_id)
        self.assertEqual(repeated["outcome"], "skipped")
        self.assertEqual(repeated["bootstrap_release_id"], bootstrap_id)
        self.assertEqual(manager.current_release_id(), bootstrap_id)

        activated = deploy_target.activate(self.target, candidate_id)
        self.assertEqual(activated["previous_release"], bootstrap_id)
        resumed = deploy_target.bootstrap_legacy_app(self.target, candidate_id)
        self.assertEqual(resumed["recovery_release"], bootstrap_id)
        self.assertEqual(resumed["phase"], "candidate_pending")

        restored = deploy_target.activate(self.target, bootstrap_id)
        self.assertEqual(restored["previous_release"], candidate_id)
        selected = deploy_target.bootstrap_legacy_app(self.target, candidate_id)
        self.assertEqual(selected["phase"], "selected")
        self.assertIsNone(selected["recovery_release"])

        deploy_target.activate(self.target, candidate_id)
        completed = deploy_target.complete_legacy_bootstrap(
            self.target, candidate_id
        )
        self.assertEqual(completed["phase"], "complete")
        final = deploy_target.bootstrap_legacy_app(self.target, candidate_id)
        self.assertEqual(final["phase"], "complete")
        self.assertIsNone(final["recovery_release"])

    def test_blank_slate_bootstrap_skips_without_a_running_mutable_service(self) -> None:
        manager, candidate_id = self._legacy_bootstrap_fixture()
        with patch.object(
            deploy_target,
            "_service_main_pid",
            return_value=0,
        ):
            result = deploy_target.bootstrap_legacy_app(
                self.target, candidate_id
            )

        self.assertEqual(result["outcome"], "skipped")
        self.assertEqual(result["phase"], "blank_slate")
        self.assertFalse(result["selected"])
        self.assertIsNone(result["current_release"])
        self.assertIsNone(result["bootstrap_release_id"])
        self.assertIsNone(result["recovery_release"])
        self.assertIsNone(manager.current_release_id())
        self.assertFalse(
            (self.target / deploy_target.LEGACY_BOOTSTRAP_RECORD).exists()
        )

    def test_prepared_bootstrap_record_recovers_atomic_selection(self) -> None:
        manager, candidate_id = self._legacy_bootstrap_fixture()
        with patch.object(
            deploy_target,
            "_legacy_service_working_directory",
            return_value=self.target.resolve(),
        ):
            first = deploy_target.bootstrap_legacy_app(self.target, candidate_id)
        record_path = self.target / deploy_target.LEGACY_BOOTSTRAP_RECORD.as_posix()
        record = json.loads(record_path.read_text(encoding="utf-8"))
        deploy_target._write_legacy_bootstrap_record(
            self.target, {**record, "phase": "prepared"}
        )
        manager.current_path.unlink()

        resumed = deploy_target.bootstrap_legacy_app(self.target, candidate_id)

        self.assertTrue(resumed["selected"])
        self.assertEqual(manager.current_release_id(), first["bootstrap_release_id"])
        self.assertEqual(
            json.loads(record_path.read_text(encoding="utf-8"))["phase"],
            "selected",
        )

    def test_bootstrap_rejects_symlinked_source_input(self) -> None:
        _manager, candidate_id = self._legacy_bootstrap_fixture()
        (self.target / "drivers/linked.py").symlink_to(
            self.target / "drivers/led_layout.py"
        )
        with (
            patch.object(
                deploy_target,
                "_legacy_service_working_directory",
                return_value=self.target.resolve(),
            ),
            self.assertRaisesRegex(RuntimeError, "regular non-symlink"),
        ):
            deploy_target.bootstrap_legacy_app(self.target, candidate_id)

    def test_snapshot_verification_rejects_mutability_and_byte_tampering(self) -> None:
        selected = self.snapshot / "scripts/start_server.py"
        selected.chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, "writable"):
            deploy_target.verify_snapshot(self.snapshot)

        selected.chmod(0o555)
        selected.chmod(0o755)
        selected.write_bytes(b"changed after freeze\n")
        selected.chmod(0o555)
        with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
            deploy_target.verify_snapshot(self.snapshot)

    def test_snapshot_verification_rejects_unaccounted_files(self) -> None:
        unexpected = self.snapshot / "firmware/extra.bin"
        unexpected.parent.mkdir(parents=True, exist_ok=True)
        unexpected.write_bytes(b"not in snapshot evidence")
        unexpected.chmod(0o444)
        with self.assertRaisesRegex(RuntimeError, "accounting mismatch"):
            deploy_target.verify_snapshot(self.snapshot)

    def test_support_workspace_identity_includes_platformio_lock_and_reuses_exact_input(self) -> None:
        first_snapshot = _SnapshotFixture(self.base / "support-one").build(
            support={
                "firmware/esp32/src/main.cpp": b"void setup() {}\n",
                "requirements-platformio.lock": b"platformio==6.1.19\nfirst\n",
            },
        )
        second_snapshot = _SnapshotFixture(self.base / "support-two").build(
            support={
                "firmware/esp32/src/main.cpp": b"void setup() {}\n",
                "requirements-platformio.lock": b"platformio==6.1.19\nsecond\n",
            },
        )
        first_release = deploy_target.stage_support(self.target, first_snapshot)
        second_release = deploy_target.stage_support(self.target, second_snapshot)

        first_workspace, first_reused = deploy_target._copy_support_workspace(
            self.target, first_release["support_release_id"],
        )
        firmware_root = first_workspace / "firmware" / "esp32"
        source_file = firmware_root / "src" / "main.cpp"
        firmware_root.chmod(0o555)
        source_file.chmod(0o444)
        shared_marker = self.target / ".esp32_firmware_hash"
        shared_marker.write_text("installed\n", encoding="utf-8")
        (first_workspace / ".esp32_firmware_hash").symlink_to(
            os.path.relpath(shared_marker, start=first_workspace),
        )
        repeated_workspace, repeated_reused = deploy_target._copy_support_workspace(
            self.target, first_release["support_release_id"],
        )
        second_workspace, second_reused = deploy_target._copy_support_workspace(
            self.target, second_release["support_release_id"],
        )

        self.assertFalse(first_reused)
        self.assertTrue(repeated_reused)
        self.assertEqual(repeated_workspace, first_workspace)
        self.assertTrue(first_workspace.stat().st_mode & stat.S_IWUSR)
        self.assertTrue(firmware_root.stat().st_mode & stat.S_IWUSR)
        self.assertTrue(source_file.stat().st_mode & stat.S_IWUSR)
        self.assertEqual(
            (first_workspace / ".esp32_firmware_hash").resolve(),
            shared_marker.resolve(),
        )
        self.assertFalse(second_reused)
        self.assertNotEqual(second_workspace, first_workspace)
        self.assertNotEqual(
            (first_workspace / "requirements-platformio.lock").read_bytes(),
            (second_workspace / "requirements-platformio.lock").read_bytes(),
        )

    def test_build_workspace_rejects_symlinks_other_than_shared_firmware_marker(self) -> None:
        workspace = self.target / "build" / "firmware" / ("a" * 64)
        workspace.mkdir(parents=True)
        (workspace / "unexpected").symlink_to(self.target / "outside")

        with self.assertRaisesRegex(RuntimeError, "unsafe symlink"):
            deploy_target._make_build_workspace_writable(workspace)


class TargetHealthIntegrationTests(unittest.TestCase):
    @staticmethod
    def _receiver_contract(environment: str) -> Mapping[str, object]:
        return deploy_entrypoint.receiver_firmware_health_contract(
            environment,
            {
                "receiver_strip_counts": [8, 8, 8, 8, 1],
                "receiver_global_strip_offsets": [0, 8, 16, 24, 32],
                "physical_output_lane_masks": [255, 255, 255, 255, 255],
                "reverse_strips_by_logical_receiver": [
                    False, False, False, False, False,
                ],
                "reverse_native_strips_by_logical_receiver": [
                    False, False, True, True, False,
                ],
            },
            leds_per_strip=138,
            receiver_count=5,
        )

    @staticmethod
    def _receiver_statuses(
        *, version: int, capabilities: int, responses: int = 2,
    ) -> tuple[Mapping[str, object], ...]:
        widths = (8, 8, 8, 8, 1)
        offsets = (0, 8, 16, 24, 32)
        masks = (255, 255, 255, 255, 255)
        transfers = responses * 10
        semantic_bytes = transfers * 100
        crc_bytes = transfers * 2
        status_transfers = transfers // 10
        return tuple(
            {
                "receiver_status_seen": True,
                "receiver_status_responses": responses,
                "receiver_status_version": version,
                "receiver_status_max_version_seen": version,
                "receiver_capabilities": capabilities,
                "transport_envelope_enabled": True,
                "transport_envelope_negotiation_candidate": None,
                "transport_envelope_negotiation_streak": 0,
                "transport_envelope_negotiation_required": 3,
                "fec_transport_requested": logical_id == 3,
                "fec_transport_enabled": logical_id == 3,
                "fec_transport_negotiation_candidate": None,
                "fec_transport_negotiation_streak": 0,
                "fec_transport_negotiation_required": 3,
                "spi_transfers": transfers,
                "semantic_bytes_sent": semantic_bytes,
                "transport_envelope_bytes_sent": (
                    transfers * (16 if logical_id == 3 else 4)
                ),
                "transport_padding_bytes_sent": (
                    transfers * (77 if logical_id == 3 else 1)
                ),
                "crc_bytes_sent": crc_bytes,
                "bytes_sent": (
                    semantic_bytes
                    + transfers * (16 if logical_id == 3 else 4)
                    + transfers * (77 if logical_id == 3 else 1)
                    + crc_bytes
                    + transfers * (680 if logical_id == 3 else 0)
                ),
                "fec_frames_sent": transfers if logical_id == 3 else 0,
                "fec_codewords_sent": 68 * transfers if logical_id == 3 else 0,
                "fec_parity_bytes_sent": 680 * transfers if logical_id == 3 else 0,
                "fec_data_padding_bytes_sent": 76 * transfers if logical_id == 3 else 0,
                "full_frame_transfers": transfers,
                "full_frame_semantic_bytes_sent": (
                    transfers * (1 + widths[logical_id] * 138 * 3)
                ),
                "full_frame_wire_bytes_sent": (
                    transfers * (
                        4088 if logical_id == 3
                        else ((1 + widths[logical_id] * 138 * 3 + 9) // 4) * 4
                    )
                ),
                "full_frame_status_transfers": status_transfers,
                "full_frame_status_samples": status_transfers,
                "full_frame_status_sample_misses": 0,
                "full_frame_write_only_transfers": transfers - status_transfers,
                "full_frame_frames_since_status_sample": transfers % 10,
                "full_frame_max_status_sample_gap": 9,
                "spidev_buffer_size": 4096,
                "full_frame_write_only_supported": True,
                "receiver_logical_device": logical_id,
                "receiver_active_strips": widths[logical_id],
                "receiver_global_strip_offset": offsets[logical_id],
                "receiver_lane_mask": masks[logical_id],
                "receiver_leds_per_strip": 138,
                "receiver_fec_packets_received": (
                    transfers if logical_id == 3 else 0
                ),
                "receiver_fec_packets_accepted": (
                    transfers if logical_id == 3 else 0
                ),
                "receiver_fec_corrected_packets": 0,
                "receiver_fec_corrected_codewords": 0,
                "receiver_fec_uncorrectable_packets": 0,
                "receiver_fec_semantic_crc_errors": 0,
                "receiver_fec_framing_errors": 0,
                "receiver_fec_uncorrectable_packets_process_delta": 0,
                "receiver_fec_semantic_crc_errors_process_delta": 0,
                "receiver_fec_framing_errors_process_delta": 0,
                "receiver_fec_uncorrectable_packets_process_baseline": 0,
                "receiver_fec_semantic_crc_errors_process_baseline": 0,
                "receiver_fec_framing_errors_process_baseline": 0,
                "receiver_fec_terminal_baseline_established": True,
                "receiver_fec_terminal_baseline_invalid": False,
                "receiver_fec_terminal_counter_resets": 0,
                "receiver_fec_last_decode_us": 80 if logical_id == 3 else 0,
                "receiver_fec_max_decode_us": 100 if logical_id == 3 else 0,
            }
            for logical_id in range(5)
        )

    @staticmethod
    def _receiver_aggregate(
        statuses: tuple[Mapping[str, object], ...]
    ) -> Mapping[str, object]:
        fields = (
            "spi_transfers", "bytes_sent", "semantic_bytes_sent",
            "transport_envelope_bytes_sent", "transport_padding_bytes_sent",
            "crc_bytes_sent", "full_frame_transfers",
            "full_frame_semantic_bytes_sent", "full_frame_wire_bytes_sent",
            "full_frame_status_transfers", "full_frame_status_samples",
            "full_frame_status_sample_misses", "full_frame_write_only_transfers",
            "fec_frames_sent", "fec_codewords_sent", "fec_parity_bytes_sent",
            "fec_data_padding_bytes_sent", "receiver_fec_packets_received",
            "receiver_fec_packets_accepted", "receiver_fec_corrected_packets",
            "receiver_fec_corrected_codewords",
            "receiver_fec_uncorrectable_packets",
            "receiver_fec_semantic_crc_errors", "receiver_fec_framing_errors",
            "receiver_fec_uncorrectable_packets_process_delta",
            "receiver_fec_semantic_crc_errors_process_delta",
            "receiver_fec_framing_errors_process_delta",
            "receiver_fec_uncorrectable_packets_process_baseline",
            "receiver_fec_semantic_crc_errors_process_baseline",
            "receiver_fec_framing_errors_process_baseline",
            "receiver_fec_terminal_counter_resets",
        )
        aggregate = {
            field: sum(int(item[field]) for item in statuses)
            for field in fields
        }
        aggregate.update({
            "fec_transport_requested_devices": sum(
                item["fec_transport_requested"] is True for item in statuses
            ),
            "fec_transport_enabled_devices": sum(
                item["fec_transport_enabled"] is True for item in statuses
            ),
            "full_frame_frames_since_status_sample": max(
                int(item["full_frame_frames_since_status_sample"])
                for item in statuses
            ),
            "full_frame_max_status_sample_gap": max(
                int(item["full_frame_max_status_sample_gap"])
                for item in statuses
            ),
            "spidev_buffer_size": min(
                int(item["spidev_buffer_size"]) for item in statuses
            ),
            "full_frame_write_only_supported": all(
                item["full_frame_write_only_supported"] is True
                for item in statuses
            ),
            "receiver_fec_last_decode_us": max(
                int(item["receiver_fec_last_decode_us"]) for item in statuses
            ),
            "receiver_fec_max_decode_us": max(
                int(item["receiver_fec_max_decode_us"]) for item in statuses
            ),
            "receiver_status_version": min(
                int(item["receiver_status_version"]) for item in statuses
            ),
            "receiver_status_max_version_seen": min(
                int(item["receiver_status_max_version_seen"])
                for item in statuses
            ),
        })
        return aggregate

    def _health_sample(
        self, *, responses: int = 2,
        statuses: tuple[Mapping[str, object], ...] | None = None,
        receiver_aggregate: Mapping[str, object] | None = None,
    ) -> deploy_target.TargetHealthSample:
        observed = statuses or self._receiver_statuses(
            version=7, capabilities=0x7C00C, responses=responses,
        )
        return deploy_target.TargetHealthSample(
            sampled_at=100.0 + responses / 10,
            controller_updated_at=99.9 + responses / 10,
            release_id="a" * 64,
            strip_count=33,
            leds_per_strip=138,
            receiver_count=5,
            receiver_logical_ids=(0, 1, 2, 3, 4),
            receiver_device_map=self._receiver_device_map(),
            receiver_statuses=observed,
            transport_envelope_devices=5,
            receiver_aggregate=(
                receiver_aggregate
                if receiver_aggregate is not None
                else self._receiver_aggregate(observed)
            ),
        )

    @staticmethod
    def _receiver_device_map() -> tuple[Mapping[str, object], ...]:
        routes = ((0, 0), (0, 1), (1, 1), (1, 0), (1, 2))
        widths = (8, 8, 8, 8, 1)
        offsets = (0, 8, 16, 24, 32)
        host_reversals = (False, False, False, False, False)
        native_reversals = (False, False, True, True, False)
        masks = (255, 255, 255, 255, 255)
        return tuple(
            {
                "logical_device": logical_id,
                "bus": routes[logical_id][0],
                "chip_select": routes[logical_id][1],
                "local_strip_count": widths[logical_id],
                "global_strip_offset": offsets[logical_id],
                "reverse_host_strip_order": host_reversals[logical_id],
                "reverse_native_strip_order": native_reversals[logical_id],
                "physical_output_lane_mask": masks[logical_id],
                "spi_speed_hz": 8_000_000,
                "spi_mode": 0,
            }
            for logical_id in range(5)
        )

    def test_receiver_status_refresh_uses_the_controller_command_endpoint(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "accepted": True,
            "request_id": "phase4-health-1",
        }).encode()
        with patch.object(deploy_target, "urlopen", return_value=response) as opener:
            request_id = deploy_target._request_receiver_status_refresh(
                "http://127.0.0.1:5000/api/status"
            )

        request = opener.call_args.args[0]
        self.assertEqual(request_id, "phase4-health-1")
        self.assertEqual(request.full_url, (
            "http://127.0.0.1:5000/api/v1/receivers/status/refresh"
        ))
        self.assertEqual(request.get_method(), "POST")

    def test_receiver_status_refresh_rejects_an_unrelated_api_url(self) -> None:
        with (
            patch.object(deploy_target, "urlopen") as opener,
            self.assertRaisesRegex(RuntimeError, "must end with /api/status"),
        ):
            deploy_target._request_receiver_status_refresh(
                "http://127.0.0.1:5000/status"
            )
        opener.assert_not_called()

    def test_sample_health_parses_release_geometry_and_topology(self) -> None:
        active = subprocess.CompletedProcess(("systemctl",), 0, "", "")
        status = {
            "updated_at": 100.25,
            "release_id": "a" * 64,
            "release_consistent": True,
            "is_running": True,
            "led_info": {"strip_count": 32, "leds_per_strip": 138},
            "driver_stats": {"aggregate": {
                "num_devices": 4,
                "transport_envelope_devices": 4,
                "device_map": [
                    {"logical_device": index, "bus": index // 2, "chip_select": index % 2}
                    for index in range(4)
                ],
            }},
        }
        with (
            patch.object(deploy_target, "_command", return_value=active),
            patch.object(deploy_target, "_api_status", return_value=status),
            patch.object(deploy_target, "_service_release", return_value="a" * 64),
            patch.object(deploy_target.time, "time", return_value=100.5),
        ):
            sample = deploy_target._sample_health(
                Path("/target"), unit="ledgrid.service", api_url="http://local/status",
            )

        self.assertEqual(sample.release_id, "a" * 64)
        self.assertEqual(sample.controller_updated_at, 100.25)
        self.assertEqual((sample.strip_count, sample.leds_per_strip), (32, 138))
        self.assertEqual(sample.receiver_count, 4)
        self.assertEqual(sample.receiver_logical_ids, (0, 1, 2, 3))
        self.assertTrue(sample.ready)

    def test_sample_health_requires_controller_ready_state(self) -> None:
        active = subprocess.CompletedProcess(("systemctl",), 0, "", "")
        status = {
            "updated_at": 100.25,
            "release_id": "a" * 64,
            "release_consistent": True,
            "is_running": False,
            "led_info": {"strip_count": 32, "leds_per_strip": 138},
            "driver_stats": {"aggregate": {
                "num_devices": 4,
                "transport_envelope_devices": 4,
                "device_map": [
                    {"logical_device": index} for index in range(4)
                ],
            }},
        }
        with (
            patch.object(deploy_target, "_command", return_value=active),
            patch.object(deploy_target, "_api_status", return_value=status),
            patch.object(deploy_target, "_service_release", return_value="a" * 64),
            patch.object(deploy_target.time, "time", return_value=100.5),
        ):
            sample = deploy_target._sample_health(
                Path("/target"), unit="ledgrid.service", api_url="http://local/status",
            )

        self.assertFalse(sample.ready)

    def test_sample_health_rejects_fractional_transport_device_count(self) -> None:
        active = subprocess.CompletedProcess(("systemctl",), 0, "", "")
        status = {
            "updated_at": 100.25,
            "release_id": "a" * 64,
            "release_consistent": True,
            "is_running": True,
            "led_info": {"strip_count": 33, "leds_per_strip": 138},
            "driver_stats": {"aggregate": {
                "num_devices": 5,
                "transport_envelope_devices": 5.5,
                "device_map": [
                    {"logical_device": index} for index in range(5)
                ],
            }},
        }
        with (
            patch.object(deploy_target, "_command", return_value=active),
            patch.object(deploy_target, "_api_status", return_value=status),
            patch.object(deploy_target, "_service_release", return_value="a" * 64),
            self.assertRaisesRegex(RuntimeError, "timestamp, geometry, or receiver topology"),
        ):
            deploy_target._sample_health(
                Path("/target"), unit="ledgrid.service", api_url="http://local/status",
            )

    def test_sample_health_rejects_web_controller_and_systemd_release_disagreement(self) -> None:
        active = subprocess.CompletedProcess(("systemctl",), 0, "", "")
        status = {
            "updated_at": 100.25,
            "release_id": "b" * 64,
            "release_consistent": False,
            "led_info": {"strip_count": 32, "leds_per_strip": 138},
            "driver_stats": {
                "aggregate": {
                    "num_devices": 4,
                    "transport_envelope_devices": 4,
                    "device_map": [
                        {"logical_device": index} for index in range(4)
                    ],
                },
            },
        }
        with (
            patch.object(deploy_target, "_command", return_value=active),
            patch.object(deploy_target, "_api_status", return_value=status),
            patch.object(deploy_target, "_service_release", return_value="a" * 64),
            self.assertRaisesRegex(RuntimeError, "release identity"),
        ):
            deploy_target._sample_health(
                Path("/target"), unit="ledgrid.service", api_url="http://local/status",
            )

    def test_fresh_health_accepts_only_advancing_exact_release_samples(self) -> None:
        release = "a" * 64
        samples = (
            deploy_target.TargetHealthSample(100.2, 100.1, release, 32, 138, 4, (0, 1, 2, 3)),
            deploy_target.TargetHealthSample(100.4, 100.3, release, 32, 138, 4, (0, 1, 2, 3)),
        )
        with (
            patch.object(deploy_target, "_sample_health", side_effect=samples),
            patch.object(deploy_target.time, "monotonic", side_effect=(0.0, 0.0, 0.0)),
            patch.object(deploy_target.time, "sleep"),
        ):
            result = deploy_target.fresh_health(
                Path("/target"),
                release,
                restart_started_at=100.0,
                strips=32,
                leds_per_strip=138,
                receivers=4,
                stable_samples=2,
                timeout=1.0,
                unit="ledgrid.service",
                api_url="http://local/status",
            )

        self.assertEqual(result["desired_release"], release)
        self.assertEqual(result["observed_release"], release)
        self.assertEqual(result["stable_samples"], 2)

    def test_fresh_health_rejects_wrong_running_release(self) -> None:
        sample = deploy_target.TargetHealthSample(
            100.2, 100.1, "b" * 64, 32, 138, 4, (0, 1, 2, 3),
        )
        with (
            patch.object(deploy_target, "_sample_health", return_value=sample),
            patch.object(deploy_target.time, "monotonic", side_effect=(0.0, 0.0, 2.0)),
            patch.object(deploy_target.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, "service release"),
        ):
            deploy_target.fresh_health(
                Path("/target"),
                "a" * 64,
                restart_started_at=100.0,
                strips=32,
                leds_per_strip=138,
                receivers=4,
                stable_samples=1,
                timeout=1.0,
                unit="ledgrid.service",
                api_url="http://local/status",
            )

    def test_fresh_health_rejects_controller_not_ready(self) -> None:
        sample = deploy_target.TargetHealthSample(
            100.2, 100.1, "a" * 64, 32, 138, 4, (0, 1, 2, 3), ready=False,
        )
        with (
            patch.object(deploy_target, "_sample_health", return_value=sample),
            patch.object(deploy_target.time, "monotonic", side_effect=(0.0, 0.0, 2.0)),
            patch.object(deploy_target.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, "did not report ready"),
        ):
            deploy_target.fresh_health(
                Path("/target"),
                "a" * 64,
                restart_started_at=100.0,
                strips=32,
                leds_per_strip=138,
                receivers=4,
                stable_samples=1,
                timeout=1.0,
                unit="ledgrid.service",
                api_url="http://local/status",
            )

    def test_fresh_health_rejects_duplicate_or_reordered_logical_devices(self) -> None:
        release = "a" * 64
        sample = deploy_target.TargetHealthSample(
            100.2, 100.1, release, 32, 138, 4, (0, 1, 1, 3),
        )
        with (
            patch.object(deploy_target, "_sample_health", return_value=sample),
            patch.object(deploy_target.time, "monotonic", side_effect=(0.0, 0.0, 2.0)),
            patch.object(deploy_target.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, "logical device map"),
        ):
            deploy_target.fresh_health(
                Path("/target"),
                release,
                restart_started_at=100.0,
                strips=32,
                leds_per_strip=138,
                receivers=4,
                stable_samples=1,
                timeout=1.0,
                unit="ledgrid.service",
                api_url="http://local/status",
            )

    def test_invalid_sample_between_valid_samples_resets_stability_window(self) -> None:
        release = "a" * 64
        samples = (
            deploy_target.TargetHealthSample(
                100.2, 100.1, release, 32, 138, 4, (0, 1, 2, 3),
            ),
            deploy_target.TargetHealthSample(
                100.4, 100.3, release, 32, 138, 4, (0, 1, 1, 3),
            ),
            deploy_target.TargetHealthSample(
                100.6, 100.5, release, 32, 138, 4, (0, 1, 2, 3),
            ),
            deploy_target.TargetHealthSample(
                100.8, 100.7, release, 32, 138, 4, (0, 1, 2, 3),
            ),
        )
        with (
            patch.object(deploy_target, "_sample_health", side_effect=samples) as reader,
            patch.object(
                deploy_target.time,
                "monotonic",
                side_effect=(0.0, 0.0, 0.0, 0.0, 0.0),
            ),
            patch.object(deploy_target.time, "sleep"),
        ):
            result = deploy_target.fresh_health(
                Path("/target"),
                release,
                restart_started_at=100.0,
                strips=32,
                leds_per_strip=138,
                receivers=4,
                stable_samples=2,
                timeout=1.0,
                unit="ledgrid.service",
                api_url="http://local/status",
            )

        self.assertEqual(reader.call_count, 4)
        self.assertEqual(result["last_controller_updated_at"], 100.7)

    def test_firmware_health_contracts_accept_exact_environment_capabilities(self) -> None:
        cases = (
            (PRODUCTION_FIRMWARE_ENVIRONMENT, 7, 0x7C00C),
            (DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT, 7, 0xC0FF),
            (NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT, 7, 0xFFFF),
        )
        for environment, version, capabilities in cases:
            with self.subTest(environment=environment):
                contract = self._receiver_contract(environment)
                samples = tuple(
                    deploy_target.TargetHealthSample(
                        sampled_at,
                        updated_at,
                        "a" * 64,
                        33,
                        138,
                        5,
                        (0, 1, 2, 3, 4),
                        receiver_device_map=self._receiver_device_map(),
                        receiver_statuses=self._receiver_statuses(
                            version=version,
                            capabilities=capabilities,
                            responses=responses,
                        ),
                        transport_envelope_devices=5,
                        receiver_aggregate=self._receiver_aggregate(
                            self._receiver_statuses(
                                version=version,
                                capabilities=capabilities,
                                responses=responses,
                            )
                        ),
                    )
                    for sampled_at, updated_at, responses in (
                        (100.2, 100.1, 2), (100.4, 100.3, 3),
                    )
                )
                with (
                    patch.object(
                        deploy_target,
                        "_request_receiver_status_refresh",
                        return_value="health-request",
                    ),
                    patch.object(deploy_target, "_sample_health", side_effect=samples),
                    patch.object(
                        deploy_target.time,
                        "monotonic",
                        side_effect=(0.0, 0.0, 0.0),
                    ),
                    patch.object(deploy_target.time, "sleep"),
                ):
                    result = deploy_target.fresh_health(
                        Path("/target"),
                        "a" * 64,
                        restart_started_at=100.0,
                        strips=33,
                        leds_per_strip=138,
                        receivers=5,
                        stable_samples=1,
                        timeout=1.0,
                        unit="ledgrid.service",
                        api_url="http://local/status",
                        receiver_contract=contract,
                    )

                retained_contract = dict(result["receiver_contract"])
                transport_evidence = retained_contract.pop(
                    "aligned_transport_evidence"
                )
                self.assertEqual(
                    retained_contract,
                    {
                        "schema_version": 2,
                        "minimum_status_version": version,
                        "required_capabilities": capabilities,
                        "fec_receiver_ids": [3],
                        "verified_logical_devices": [0, 1, 2, 3, 4],
                        "status_response_evidence": [
                            {
                                "logical_device": logical_id,
                                "before": 2,
                                "after": 3,
                            }
                            for logical_id in range(5)
                        ],
                    },
                )
                self.assertEqual(transport_evidence["enabled_devices"], 5)
                self.assertTrue(transport_evidence["negotiation_settled"])
                self.assertTrue(transport_evidence["full_frame_traffic_proven"])
                self.assertTrue(transport_evidence["full_frame_sampling_proven"])
                self.assertEqual(
                    transport_evidence["aggregate_sampling_state"]["after"][
                        "spidev_buffer_size"
                    ],
                    4096,
                )
                self.assertEqual(transport_evidence["negotiation_required"], 3)
                self.assertEqual(len(transport_evidence["devices"]), 5)
                self.assertGreater(
                    transport_evidence["aggregate"]["semantic_bytes_sent"]["delta"],
                    0,
                )

    def test_receiver_health_contract_v2_requires_explicit_receiver_3_fec_policy(self) -> None:
        contract = dict(self._receiver_contract(PRODUCTION_FIRMWARE_ENVIRONMENT))
        self.assertEqual(contract["schema_version"], 2)
        self.assertEqual(contract["minimum_status_version"], 7)
        self.assertEqual(contract["required_capabilities"], 0x7C00C)
        self.assertEqual(contract["fec_receiver_ids"], [3])
        validated = deploy_target._validate_receiver_health_contract(
            contract, receivers=5
        )
        self.assertEqual(validated.schema_version, 2)
        self.assertEqual(validated.fec_receiver_ids, (3,))

        for label, mutate, expected in (
            ("v1", lambda item: item.update(schema_version=1), "version"),
            ("missing", lambda item: item.pop("fec_receiver_ids"), "malformed"),
            ("disabled", lambda item: item.update(fec_receiver_ids=[]), "malformed"),
            ("wrong receiver", lambda item: item.update(fec_receiver_ids=[2]), "malformed"),
            ("duplicate", lambda item: item.update(fec_receiver_ids=[3, 3]), "malformed"),
        ):
            with self.subTest(label=label):
                malformed = dict(contract)
                mutate(malformed)
                with self.assertRaisesRegex(ValueError, expected):
                    deploy_target._validate_receiver_health_contract(
                        malformed, receivers=5
                    )

    def test_production_health_fec_selection_negotiation_and_wire_fail_closed(self) -> None:
        contract = self._receiver_contract(PRODUCTION_FIRMWARE_ENVIRONMENT)

        def rejection(
            statuses: tuple[Mapping[str, object], ...],
            aggregate: Mapping[str, object] | None = None,
        ) -> str | None:
            return deploy_target._receiver_health_rejection(
                self._health_sample(
                    statuses=statuses,
                    receiver_aggregate=(
                        aggregate
                        if aggregate is not None
                        else self._receiver_aggregate(statuses)
                    ),
                ),
                minimum_version=int(contract["minimum_status_version"]),
                required_capabilities=int(contract["required_capabilities"]),
                fec_receiver_ids=tuple(contract["fec_receiver_ids"]),
                expected_devices=tuple(contract["devices"]),
            )

        valid = self._receiver_statuses(version=7, capabilities=0x7C00C)
        self.assertIsNone(rejection(valid))

        raced = [dict(item) for item in valid]
        for item in raced:
            item["receiver_status_version"] = 3
            item["receiver_status_max_version_seen"] = 7
        self.assertIsNone(rejection(tuple(raced)))

        downgraded = [dict(item) for item in raced]
        downgraded[0]["receiver_capabilities"] = 0x400C
        self.assertIn(
            "lacks required firmware capabilities",
            rejection(tuple(downgraded)),
        )

        historical_terminal = [dict(item) for item in valid]
        historical_terminal[3]["receiver_fec_packets_received"] += 4
        historical_terminal[3]["receiver_fec_uncorrectable_packets"] = 1
        historical_terminal[3]["receiver_fec_semantic_crc_errors"] = 2
        historical_terminal[3]["receiver_fec_framing_errors"] = 1
        historical_terminal[3][
            "receiver_fec_uncorrectable_packets_process_baseline"
        ] = 1
        historical_terminal[3][
            "receiver_fec_semantic_crc_errors_process_baseline"
        ] = 2
        historical_terminal[3][
            "receiver_fec_framing_errors_process_baseline"
        ] = 1
        self.assertIsNone(rejection(tuple(historical_terminal)))
        bad_terminal_aggregate = dict(
            self._receiver_aggregate(tuple(historical_terminal))
        )
        bad_terminal_aggregate[
            "receiver_fec_uncorrectable_packets_process_baseline"
        ] = 0
        self.assertIn(
            "aggregate receiver_fec_uncorrectable_packets_process_baseline "
            "drifted from per-receiver total",
            rejection(tuple(historical_terminal), bad_terminal_aggregate),
        )

        for terminal_field, process_field in (
            (
                "receiver_fec_uncorrectable_packets",
                "receiver_fec_uncorrectable_packets_process_delta",
            ),
            (
                "receiver_fec_semantic_crc_errors",
                "receiver_fec_semantic_crc_errors_process_delta",
            ),
            (
                "receiver_fec_framing_errors",
                "receiver_fec_framing_errors_process_delta",
            ),
        ):
            with self.subTest(process_field=process_field):
                current_terminal = [dict(item) for item in historical_terminal]
                current_terminal[3][terminal_field] += 1
                current_terminal[3]["receiver_fec_packets_received"] += 1
                current_terminal[3][process_field] = 1
                reason = rejection(tuple(current_terminal))
                self.assertIn("since Host start", reason)
                self.assertIn(f"{process_field}=1", reason)
                self.assertIn("baseline(established=True, invalid=False", reason)

        forged_zero_delta = [dict(item) for item in historical_terminal]
        forged_zero_delta[3]["receiver_fec_uncorrectable_packets"] += 1
        forged_zero_delta[3]["receiver_fec_packets_received"] += 1
        reason = rejection(tuple(forged_zero_delta))
        self.assertIn("terminal baseline accounting is inconsistent", reason)
        self.assertIn("receiver_fec_uncorrectable_packets=2", reason)
        self.assertIn(
            "receiver_fec_uncorrectable_packets_process_baseline=1", reason
        )
        self.assertIn(
            "receiver_fec_uncorrectable_packets_process_delta=0", reason
        )

        baseline_above_lifetime = [dict(item) for item in valid]
        baseline_above_lifetime[3][
            "receiver_fec_uncorrectable_packets_process_baseline"
        ] = 1
        reason = rejection(tuple(baseline_above_lifetime))
        self.assertIn("terminal baseline accounting is inconsistent", reason)
        self.assertIn("receiver_fec_uncorrectable_packets=0", reason)
        self.assertIn(
            "receiver_fec_uncorrectable_packets_process_baseline=1", reason
        )

        reset_terminal = [dict(item) for item in historical_terminal]
        reset_terminal[3]["receiver_fec_terminal_counter_resets"] = 1
        self.assertIn(
            "counter_resets=1",
            rejection(tuple(reset_terminal)),
        )

        missing_baseline = [dict(item) for item in valid]
        missing_baseline[3]["receiver_fec_terminal_baseline_established"] = False
        self.assertIn(
            "baseline(established=False",
            rejection(tuple(missing_baseline)),
        )

        invalid_baseline = [dict(item) for item in valid]
        invalid_baseline[3]["receiver_fec_terminal_baseline_invalid"] = True
        self.assertIn(
            "invalid=True",
            rejection(tuple(invalid_baseline)),
        )

        non_fec_history = [dict(item) for item in valid]
        non_fec_history[0]["receiver_fec_packets_received"] = 1
        non_fec_history[0]["receiver_fec_uncorrectable_packets"] = 1
        non_fec_history[0][
            "receiver_fec_uncorrectable_packets_process_baseline"
        ] = 1
        self.assertIn(
            "FEC receive accounting is inconsistent",
            rejection(tuple(non_fec_history)),
        )

        cases = []
        missing_capability = [dict(item) for item in valid]
        missing_capability[0]["receiver_capabilities"] = 0x400C
        cases.append((missing_capability, "lacks required firmware capabilities"))

        old_status = [dict(item) for item in valid]
        old_status[0]["receiver_status_version"] = 6
        old_status[0]["receiver_status_max_version_seen"] = 6
        cases.append((old_status, "required latest>=v3 and observed>=v7"))

        no_extended_observation = [dict(item) for item in valid]
        no_extended_observation[0]["receiver_status_version"] = 3
        no_extended_observation[0]["receiver_status_max_version_seen"] = 0
        cases.append((no_extended_observation, "max_seen=v0"))

        wrong_selection = [dict(item) for item in valid]
        wrong_selection[2]["fec_transport_requested"] = True
        cases.append((wrong_selection, "FEC selection does not match"))

        disabled = [dict(item) for item in valid]
        disabled[3]["fec_transport_enabled"] = False
        cases.append((disabled, "FEC selection does not match"))

        pending = [dict(item) for item in valid]
        pending[3].update({
            "fec_transport_negotiation_candidate": True,
            "fec_transport_negotiation_streak": 2,
        })
        cases.append((pending, "FEC transport negotiation is not settled"))

        undersized = [dict(item) for item in valid]
        undersized[3]["spidev_buffer_size"] = 3379
        cases.append((undersized, "below 4088 bytes"))

        for statuses, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, rejection(tuple(statuses)))

        bad_aggregate = dict(self._receiver_aggregate(valid))
        bad_aggregate["fec_transport_enabled_devices"] = 0
        self.assertIn(
            "exactly one receiver",
            rejection(valid, bad_aggregate),
        )

        before = self._health_sample(responses=2)
        bad_wire_statuses = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C, responses=3,
        )]
        bad_wire_statuses[3]["full_frame_wire_bytes_sent"] -= 1
        bad_wire = self._health_sample(
            responses=3,
            statuses=tuple(bad_wire_statuses),
            receiver_aggregate=self._receiver_aggregate(tuple(bad_wire_statuses)),
        )
        self.assertIn(
            "expected 4088 bytes per frame",
            deploy_target._transport_accounting_delta_rejection(before, bad_wire),
        )

    def test_production_health_fec_host_receiver_deltas_fail_closed(self) -> None:
        before = self._health_sample(responses=2)

        def sample(
            statuses: tuple[Mapping[str, object], ...]
        ) -> deploy_target.TargetHealthSample:
            return self._health_sample(
                responses=3,
                statuses=statuses,
                receiver_aggregate=self._receiver_aggregate(statuses),
            )

        valid = self._receiver_statuses(
            version=7, capabilities=0x7C00C, responses=3,
        )
        self.assertIsNone(
            deploy_target._transport_accounting_delta_rejection(
                before, sample(valid)
            )
        )

        corrected = [dict(item) for item in valid]
        corrected[3]["receiver_fec_corrected_packets"] = 2
        corrected[3]["receiver_fec_corrected_codewords"] = 3
        self.assertIsNone(
            deploy_target._transport_accounting_delta_rejection(
                before, sample(tuple(corrected))
            )
        )

        dropped = [dict(item) for item in valid]
        dropped[3]["receiver_fec_packets_received"] -= 1
        dropped[3]["receiver_fec_packets_accepted"] -= 1
        self.assertIn(
            "host/receiver FEC frame delta is inconsistent",
            deploy_target._transport_accounting_delta_rejection(
                before, sample(tuple(dropped))
            ),
        )

        terminal = [dict(item) for item in valid]
        terminal[3]["receiver_fec_uncorrectable_packets"] = 1
        self.assertIn(
            "terminal FEC outcome increased",
            deploy_target._transport_accounting_delta_rejection(
                before, sample(tuple(terminal))
            ),
        )

        non_fec = [dict(item) for item in valid]
        non_fec[0]["receiver_fec_packets_received"] = 1
        non_fec[0]["receiver_fec_packets_accepted"] = 1
        self.assertIn(
            "non-FEC transport reported FEC activity",
            deploy_target._transport_accounting_delta_rejection(
                before, sample(tuple(non_fec))
            ),
        )

    def test_fresh_health_accepts_historical_terminal_baseline_with_zero_growth(self) -> None:
        contract = self._receiver_contract(PRODUCTION_FIRMWARE_ENVIRONMENT)

        def historical_statuses(responses: int):
            statuses = [dict(item) for item in self._receiver_statuses(
                version=7, capabilities=0x7C00C, responses=responses,
            )]
            statuses[3]["receiver_fec_packets_received"] += 4
            for field, value in (
                ("receiver_fec_uncorrectable_packets", 1),
                ("receiver_fec_semantic_crc_errors", 2),
                ("receiver_fec_framing_errors", 1),
            ):
                statuses[3][field] = value
                statuses[3][f"{field}_process_baseline"] = value
            return tuple(statuses)

        samples = tuple(
            self._health_sample(
                responses=responses,
                statuses=(statuses := historical_statuses(responses)),
                receiver_aggregate=self._receiver_aggregate(statuses),
            )
            for responses in (2, 3)
        )
        with (
            patch.object(
                deploy_target,
                "_request_receiver_status_refresh",
                return_value="health-request",
            ),
            patch.object(deploy_target, "_sample_health", side_effect=samples),
            patch.object(
                deploy_target.time, "monotonic", side_effect=(0.0, 0.0, 0.0)
            ),
            patch.object(deploy_target.time, "sleep"),
        ):
            result = deploy_target.fresh_health(
                Path("/target"),
                "a" * 64,
                restart_started_at=100.0,
                strips=33,
                leds_per_strip=138,
                receivers=5,
                stable_samples=2,
                timeout=1.0,
                unit="ledgrid.service",
                api_url="http://local/status",
                receiver_contract=contract,
            )

        evidence = result["receiver_contract"]["aligned_transport_evidence"]
        receiver_3 = evidence["devices"][3]
        terminal = receiver_3["counters"][
            "receiver_fec_uncorrectable_packets"
        ]
        self.assertEqual(terminal, {"before": 1, "after": 1, "delta": 0})
        self.assertEqual(
            receiver_3["fec_terminal_state_before"],
            {"baseline_established": True, "baseline_invalid": False},
        )
        for terminal_field, value in (
            ("receiver_fec_uncorrectable_packets", 1),
            ("receiver_fec_semantic_crc_errors", 2),
            ("receiver_fec_framing_errors", 1),
        ):
            for field, expected_value in (
                (terminal_field, value),
                (f"{terminal_field}_process_baseline", value),
                (f"{terminal_field}_process_delta", 0),
            ):
                aggregate = evidence["aggregate"][field]
                self.assertEqual(
                    aggregate,
                    {
                        "before": expected_value,
                        "after": expected_value,
                        "delta": 0,
                    },
                )
                self.assertEqual(
                    aggregate["before"],
                    sum(
                        device["counters"][field]["before"]
                        for device in evidence["devices"]
                    ),
                )
                self.assertEqual(
                    aggregate["after"],
                    sum(
                        device["counters"][field]["after"]
                        for device in evidence["devices"]
                    ),
                )

    def test_production_health_rejects_firmware_without_aligned_transport(self) -> None:
        contract = self._receiver_contract(PRODUCTION_FIRMWARE_ENVIRONMENT)
        sample = deploy_target.TargetHealthSample(
            100.2,
            100.1,
            "a" * 64,
            33,
            138,
            5,
            (0, 1, 2, 3, 4),
            receiver_device_map=self._receiver_device_map(),
            receiver_statuses=self._receiver_statuses(
                version=7, capabilities=0x000C,
            ),
            transport_envelope_devices=5,
        )
        reason = deploy_target._receiver_health_rejection(
            sample,
            minimum_version=int(contract["minimum_status_version"]),
            required_capabilities=int(contract["required_capabilities"]),
            fec_receiver_ids=tuple(contract["fec_receiver_ids"]),
            expected_devices=tuple(contract["devices"]),
        )
        self.assertEqual(
            reason, "receiver 0 lacks required firmware capabilities"
        )

    def test_production_health_requires_host_envelope_on_all_five_receivers(self) -> None:
        contract = self._receiver_contract(PRODUCTION_FIRMWARE_ENVIRONMENT)
        statuses = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C,
        )]
        statuses[3]["transport_envelope_enabled"] = False
        base = dict(
            sampled_at=100.2,
            controller_updated_at=100.1,
            release_id="a" * 64,
            strip_count=33,
            leds_per_strip=138,
            receiver_count=5,
            receiver_logical_ids=(0, 1, 2, 3, 4),
            receiver_device_map=self._receiver_device_map(),
            receiver_statuses=tuple(statuses),
        )
        reason = deploy_target._receiver_health_rejection(
            deploy_target.TargetHealthSample(
                **base, transport_envelope_devices=5,
            ),
            minimum_version=int(contract["minimum_status_version"]),
            required_capabilities=int(contract["required_capabilities"]),
            fec_receiver_ids=tuple(contract["fec_receiver_ids"]),
            expected_devices=tuple(contract["devices"]),
        )
        self.assertEqual(
            reason, "receiver 3 host aligned transport is not enabled"
        )
        reason = deploy_target._receiver_health_rejection(
            deploy_target.TargetHealthSample(
                **{**base, "receiver_statuses": self._receiver_statuses(
                    version=7, capabilities=0x7C00C,
                )},
                transport_envelope_devices=4,
            ),
            minimum_version=int(contract["minimum_status_version"]),
            required_capabilities=int(contract["required_capabilities"]),
            fec_receiver_ids=tuple(contract["fec_receiver_ids"]),
            expected_devices=tuple(contract["devices"]),
        )
        self.assertIn("enabled for 4 receivers; expected 5", reason)

    def test_production_health_requires_settled_three_observation_negotiation(self) -> None:
        contract = self._receiver_contract(PRODUCTION_FIRMWARE_ENVIRONMENT)
        for label, mutate in (
            (
                "missing",
                lambda status: status.pop(
                    "transport_envelope_negotiation_candidate"
                ),
            ),
            (
                "pending",
                lambda status: status.update({
                    "transport_envelope_negotiation_candidate": False,
                    "transport_envelope_negotiation_streak": 1,
                }),
            ),
            (
                "wrong requirement",
                lambda status: status.update({
                    "transport_envelope_negotiation_required": 2,
                }),
            ),
        ):
            with self.subTest(label=label):
                statuses = [dict(item) for item in self._receiver_statuses(
                    version=7, capabilities=0x7C00C,
                )]
                mutate(statuses[0])
                reason = deploy_target._receiver_health_rejection(
                    self._health_sample(statuses=tuple(statuses)),
                    minimum_version=int(contract["minimum_status_version"]),
                    required_capabilities=int(contract["required_capabilities"]),
                    fec_receiver_ids=tuple(contract["fec_receiver_ids"]),
                    expected_devices=tuple(contract["devices"]),
                )
                self.assertIn("negotiation is not settled", reason)

    def test_production_health_rejects_missing_stalled_and_drifted_transport_traffic(self) -> None:
        contract = self._receiver_contract(PRODUCTION_FIRMWARE_ENVIRONMENT)
        expected_devices = tuple(contract["devices"])
        complete_statuses = self._receiver_statuses(
            version=7, capabilities=0x7C00C,
        )
        missing_statuses = [dict(item) for item in complete_statuses]
        missing_statuses[0].pop("semantic_bytes_sent")
        reason = deploy_target._receiver_health_rejection(
            self._health_sample(
                statuses=tuple(missing_statuses),
                receiver_aggregate=self._receiver_aggregate(complete_statuses),
            ),
            minimum_version=int(contract["minimum_status_version"]),
            required_capabilities=int(contract["required_capabilities"]),
            fec_receiver_ids=tuple(contract["fec_receiver_ids"]),
            expected_devices=expected_devices,
        )
        self.assertIn("semantic_bytes_sent is unavailable", reason)

        before = self._health_sample(responses=2)
        stalled_statuses = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C, responses=3,
        )]
        transport_fields = (
            "spi_transfers", "bytes_sent", "semantic_bytes_sent",
            "transport_envelope_bytes_sent", "transport_padding_bytes_sent",
            "crc_bytes_sent",
        )
        for field in transport_fields:
            stalled_statuses[0][field] = before.receiver_statuses[0][field]
        stalled = self._health_sample(
            responses=3, statuses=tuple(stalled_statuses),
        )
        self.assertIn(
            "did not advance",
            deploy_target._transport_accounting_delta_rejection(before, stalled),
        )

        drift_statuses = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C, responses=3,
        )]
        drift_statuses[0]["bytes_sent"] += 1
        drifted = self._health_sample(
            responses=3, statuses=tuple(drift_statuses),
        )
        self.assertIn(
            "wire-byte accounting is inconsistent",
            deploy_target._transport_accounting_delta_rejection(before, drifted),
        )

    def test_production_health_accepts_fec_parity_and_outer_header_accounting(self) -> None:
        def with_fec(
            statuses: tuple[Mapping[str, object], ...]
        ) -> tuple[Mapping[str, object], ...]:
            updated = [dict(item) for item in statuses]
            receiver = updated[3]
            frames = int(receiver["full_frame_transfers"])
            receiver.update({
                "fec_transport_enabled": True,
                "fec_frames_sent": frames,
                "fec_parity_bytes_sent": 680 * frames,
                "transport_envelope_bytes_sent": 16 * frames,
                "full_frame_wire_bytes_sent": 4088 * frames,
            })
            receiver["bytes_sent"] = (
                int(receiver["semantic_bytes_sent"])
                + int(receiver["transport_envelope_bytes_sent"])
                + int(receiver["transport_padding_bytes_sent"])
                + int(receiver["crc_bytes_sent"])
                + int(receiver["fec_parity_bytes_sent"])
            )
            return tuple(updated)

        before_statuses = with_fec(self._receiver_statuses(
            version=3, capabilities=0x7C00C, responses=2,
        ))
        after_statuses = with_fec(self._receiver_statuses(
            version=3, capabilities=0x7C00C, responses=3,
        ))
        before = self._health_sample(
            responses=2,
            statuses=before_statuses,
            receiver_aggregate=self._receiver_aggregate(before_statuses),
        )
        after = self._health_sample(
            responses=3,
            statuses=after_statuses,
            receiver_aggregate=self._receiver_aggregate(after_statuses),
        )
        before.receiver_aggregate.update({
            "fec_frames_sent": int(before_statuses[3]["fec_frames_sent"]),
            "fec_parity_bytes_sent": int(
                before_statuses[3]["fec_parity_bytes_sent"]
            ),
        })
        after.receiver_aggregate.update({
            "fec_frames_sent": int(after_statuses[3]["fec_frames_sent"]),
            "fec_parity_bytes_sent": int(
                after_statuses[3]["fec_parity_bytes_sent"]
            ),
        })

        self.assertIsNone(
            deploy_target._transport_accounting_delta_rejection(before, after)
        )
        evidence = deploy_target._transport_accounting_evidence(before, after)
        self.assertTrue(evidence["full_frame_traffic_proven"])

        broken_statuses = [dict(item) for item in after_statuses]
        broken_statuses[3]["fec_parity_bytes_sent"] += 1
        broken = self._health_sample(
            responses=3,
            statuses=tuple(broken_statuses),
            receiver_aggregate=self._receiver_aggregate(tuple(broken_statuses)),
        )
        broken.receiver_aggregate.update({
            "fec_frames_sent": int(broken_statuses[3]["fec_frames_sent"]),
            "fec_parity_bytes_sent": int(
                broken_statuses[3]["fec_parity_bytes_sent"]
            ),
        })
        self.assertIn(
            "wire-byte accounting is inconsistent",
            deploy_target._transport_accounting_delta_rejection(before, broken),
        )

    def test_production_health_retains_false_full_frame_proofs_when_scene_emits_none(
        self,
    ) -> None:
        before = self._health_sample(responses=2)
        after_statuses = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C, responses=3,
        )]
        full_frame_fields = (
            "full_frame_transfers",
            "full_frame_semantic_bytes_sent",
            "full_frame_wire_bytes_sent",
            *deploy_target.FULL_FRAME_SAMPLING_COUNTERS,
            "full_frame_frames_since_status_sample",
            "full_frame_max_status_sample_gap",
            "fec_frames_sent", "fec_codewords_sent", "fec_parity_bytes_sent",
            "fec_data_padding_bytes_sent",
            "receiver_fec_packets_received", "receiver_fec_packets_accepted",
            "receiver_fec_corrected_packets", "receiver_fec_corrected_codewords",
            "receiver_fec_uncorrectable_packets",
            "receiver_fec_semantic_crc_errors", "receiver_fec_framing_errors",
        )
        for logical_id, status in enumerate(after_statuses):
            for field in full_frame_fields:
                status[field] = before.receiver_statuses[logical_id][field]
        fec_status = after_statuses[3]
        fec_before = before.receiver_statuses[3]
        query_transfers = int(fec_status["spi_transfers"]) - int(
            fec_before["spi_transfers"]
        )
        fec_status["transport_envelope_bytes_sent"] = (
            int(fec_before["transport_envelope_bytes_sent"])
            + 4 * query_transfers
        )
        fec_status["transport_padding_bytes_sent"] = (
            int(fec_before["transport_padding_bytes_sent"])
            + query_transfers
        )
        fec_status["bytes_sent"] = (
            int(fec_before["bytes_sent"])
            + int(fec_status["semantic_bytes_sent"])
            - int(fec_before["semantic_bytes_sent"])
            + 4 * query_transfers
            + query_transfers
            + int(fec_status["crc_bytes_sent"])
            - int(fec_before["crc_bytes_sent"])
        )
        observed = tuple(after_statuses)
        after = self._health_sample(
            responses=3,
            statuses=observed,
            receiver_aggregate=self._receiver_aggregate(observed),
        )

        self.assertIsNone(
            deploy_target._transport_accounting_delta_rejection(before, after)
        )
        evidence = deploy_target._transport_accounting_evidence(before, after)
        self.assertFalse(evidence["full_frame_traffic_proven"])
        self.assertFalse(evidence["full_frame_sampling_proven"])
        self.assertGreater(evidence["aggregate"]["spi_transfers"]["delta"], 0)

        reset_gap_statuses = [dict(item) for item in observed]
        for item in reset_gap_statuses:
            item["full_frame_max_status_sample_gap"] = 8
            item["full_frame_frames_since_status_sample"] = min(
                item["full_frame_frames_since_status_sample"], 8
            )
        reset_gap = tuple(reset_gap_statuses)
        after_reset = self._health_sample(
            responses=3,
            statuses=reset_gap,
            receiver_aggregate=self._receiver_aggregate(reset_gap),
        )
        self.assertIn(
            "maximum status sample gap reset",
            deploy_target._transport_accounting_delta_rejection(
                before, after_reset
            ),
        )

    def test_production_health_rejects_invalid_full_frame_sampling_and_fast_path(self) -> None:
        contract = self._receiver_contract(PRODUCTION_FIRMWARE_ENVIRONMENT)
        expected_devices = tuple(contract["devices"])
        complete = self._receiver_statuses(version=7, capabilities=0x7C00C)

        missing = [dict(item) for item in complete]
        missing[0].pop("full_frame_status_samples")

        broken = [dict(item) for item in complete]
        broken[0]["full_frame_write_only_transfers"] += 1

        unclassified = [dict(item) for item in complete]
        unclassified[0]["full_frame_status_samples"] -= 1

        excessive_gap = [dict(item) for item in complete]
        excessive_gap[0].update({
            "full_frame_frames_since_status_sample": 257,
            "full_frame_max_status_sample_gap": 257,
        })

        unsupported = [dict(item) for item in complete]
        unsupported[0]["full_frame_write_only_supported"] = False

        undersized = [dict(item) for item in complete]
        undersized[0]["spidev_buffer_size"] = 3319

        for label, statuses, expected in (
            ("missing", missing, "sampling counters are unavailable"),
            ("invariant", broken, "transfer invariant is broken"),
            ("unclassified", unclassified, "status transfer classification is broken"),
            ("gap", excessive_gap, "gap is outside 0..256"),
            ("unsupported", unsupported, "fast path is unavailable"),
            ("buffer", undersized, "below 3320 bytes"),
        ):
            with self.subTest(label=label):
                observed = tuple(statuses)
                aggregate = (
                    self._receiver_aggregate(complete)
                    if label == "missing"
                    else self._receiver_aggregate(observed)
                )
                reason = deploy_target._receiver_health_rejection(
                    self._health_sample(
                        statuses=observed, receiver_aggregate=aggregate
                    ),
                    minimum_version=int(contract["minimum_status_version"]),
                    required_capabilities=int(contract["required_capabilities"]),
                    fec_receiver_ids=tuple(contract["fec_receiver_ids"]),
                    expected_devices=expected_devices,
                )
                self.assertIn(expected, reason)

        before = self._health_sample(responses=2)

        miss = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C, responses=3,
        )]
        miss[0]["full_frame_status_sample_misses"] += 1
        miss[0]["full_frame_status_transfers"] += 1
        miss[0]["full_frame_write_only_transfers"] -= 1

        stalled = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C, responses=3,
        )]
        stalled[0]["full_frame_status_samples"] = before.receiver_statuses[0][
            "full_frame_status_samples"
        ]
        stalled[0]["full_frame_status_transfers"] = before.receiver_statuses[0][
            "full_frame_status_transfers"
        ]
        stalled[0]["full_frame_write_only_transfers"] = (
            stalled[0]["full_frame_transfers"]
            - stalled[0]["full_frame_status_transfers"]
        )

        unclassified_delta = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C, responses=3,
        )]
        unclassified_delta[0]["full_frame_status_samples"] = (
            before.receiver_statuses[0]["full_frame_status_samples"]
        )

        reset = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C, responses=3,
        )]
        reset[0]["full_frame_status_samples"] = (
            before.receiver_statuses[0]["full_frame_status_samples"] - 1
        )
        reset[0]["full_frame_status_transfers"] = (
            reset[0]["full_frame_status_samples"]
            + reset[0]["full_frame_status_sample_misses"]
        )
        reset[0]["full_frame_write_only_transfers"] = (
            reset[0]["full_frame_transfers"]
            - reset[0]["full_frame_status_transfers"]
        )

        reset_gap = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C, responses=3,
        )]
        for item in reset_gap:
            item["full_frame_max_status_sample_gap"] = 8
            item["full_frame_frames_since_status_sample"] = min(
                item["full_frame_frames_since_status_sample"], 8
            )

        for label, statuses, expected in (
            ("miss", miss, "sample misses increased"),
            ("stalled", stalled, "status samples did not advance"),
            (
                "unclassified",
                unclassified_delta,
                "status transfer delta classification is broken",
            ),
            ("reset", reset, "sampling counter reset"),
            ("gap reset", reset_gap, "maximum status sample gap reset"),
        ):
            with self.subTest(label=label):
                after = self._health_sample(
                    responses=3,
                    statuses=tuple(statuses),
                    receiver_aggregate=self._receiver_aggregate(tuple(statuses)),
                )
                self.assertIn(
                    expected,
                    deploy_target._transport_accounting_delta_rejection(
                        before, after
                    ),
                )

    def test_receiver_contract_rejects_each_host_device_map_mutation(self) -> None:
        contract = self._receiver_contract(PRODUCTION_FIRMWARE_ENVIRONMENT)
        mutations = (
            ("bus", 1),
            ("chip_select", 2),
            ("local_strip_count", 7),
            ("global_strip_offset", 1),
            ("physical_output_lane_mask", 127),
            ("reverse_host_strip_order", True),
            ("reverse_native_strip_order", True),
            ("spi_mode", 1),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                device_map = [dict(item) for item in self._receiver_device_map()]
                device_map[0][field] = value
                sample = deploy_target.TargetHealthSample(
                    100.2,
                    100.1,
                    "a" * 64,
                    33,
                    138,
                    5,
                    (0, 1, 2, 3, 4),
                    receiver_device_map=tuple(device_map),
                    receiver_statuses=self._receiver_statuses(
                        version=7, capabilities=0x7C00C,
                    ),
                    transport_envelope_devices=5,
                )
                with (
                    patch.object(
                        deploy_target,
                        "_request_receiver_status_refresh",
                        return_value="health-request",
                    ),
                    patch.object(deploy_target, "_sample_health", return_value=sample),
                    patch.object(
                        deploy_target.time,
                        "monotonic",
                        side_effect=(0.0, 0.0, 2.0),
                    ),
                    patch.object(deploy_target.time, "sleep"),
                    self.assertRaisesRegex(RuntimeError, field),
                ):
                    deploy_target.fresh_health(
                        Path("/target"),
                        "a" * 64,
                        restart_started_at=100.0,
                        strips=33,
                        leds_per_strip=138,
                        receivers=5,
                        stable_samples=2,
                        timeout=1.0,
                        unit="ledgrid.service",
                        api_url="http://local/status",
                        receiver_contract=contract,
                    )

    def test_receiver_contract_rejects_one_stale_receiver_response_counter(self) -> None:
        contract = self._receiver_contract(PRODUCTION_FIRMWARE_ENVIRONMENT)
        advanced = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C, responses=3,
        )]
        advanced[4]["receiver_status_responses"] = 2
        samples = (
            deploy_target.TargetHealthSample(
                100.2,
                100.1,
                "a" * 64,
                33,
                138,
                5,
                (0, 1, 2, 3, 4),
                receiver_device_map=self._receiver_device_map(),
                receiver_statuses=self._receiver_statuses(
                    version=7, capabilities=0x7C00C, responses=2,
                ),
                transport_envelope_devices=5,
                receiver_aggregate=self._receiver_aggregate(
                    self._receiver_statuses(
                        version=7, capabilities=0x7C00C, responses=2,
                    )
                ),
            ),
            deploy_target.TargetHealthSample(
                100.4,
                100.3,
                "a" * 64,
                33,
                138,
                5,
                (0, 1, 2, 3, 4),
                receiver_device_map=self._receiver_device_map(),
                receiver_statuses=tuple(advanced),
                transport_envelope_devices=5,
                receiver_aggregate=self._receiver_aggregate(tuple(advanced)),
            ),
        )
        with (
            patch.object(
                deploy_target,
                "_request_receiver_status_refresh",
                return_value="health-request",
            ),
            patch.object(deploy_target, "_sample_health", side_effect=samples),
            patch.object(
                deploy_target.time,
                "monotonic",
                side_effect=(0.0, 0.0, 0.0, 2.0),
            ),
            patch.object(deploy_target.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, r"did not advance.*\[4\]"),
        ):
            deploy_target.fresh_health(
                Path("/target"),
                "a" * 64,
                restart_started_at=100.0,
                strips=33,
                leds_per_strip=138,
                receivers=5,
                stable_samples=2,
                timeout=1.0,
                unit="ledgrid.service",
                api_url="http://local/status",
                receiver_contract=contract,
            )

    def test_production_contract_rejects_live_legacy_status_and_fifth_lane_shape(self) -> None:
        contract = self._receiver_contract(PRODUCTION_FIRMWARE_ENVIRONMENT)
        cases = []
        legacy = list(self._receiver_statuses(version=2, capabilities=0))
        cases.append((tuple(legacy), "required latest>=v3 and observed>=v7"))
        wrong_fifth = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C
        )]
        wrong_fifth[4].update({
            "receiver_active_strips": 8,
            "receiver_lane_mask": 255,
        })
        cases.append((tuple(wrong_fifth), "receiver_active_strips=8"))
        missing_identity = [dict(item) for item in self._receiver_statuses(
            version=7, capabilities=0x7C00C
        )]
        missing_identity[4]["receiver_logical_device"] = None
        cases.append((tuple(missing_identity), "logical identities"))

        for statuses, expected in cases:
            with self.subTest(expected=expected):
                sample = deploy_target.TargetHealthSample(
                    100.2,
                    100.1,
                    "a" * 64,
                    33,
                    138,
                    5,
                    (0, 1, 2, 3, 4),
                    receiver_device_map=self._receiver_device_map(),
                    receiver_statuses=statuses,
                    transport_envelope_devices=5,
                )
                with (
                    patch.object(
                        deploy_target,
                        "_request_receiver_status_refresh",
                        return_value="health-request",
                    ),
                    patch.object(deploy_target, "_sample_health", return_value=sample),
                    patch.object(
                        deploy_target.time,
                        "monotonic",
                        side_effect=(0.0, 0.0, 2.0),
                    ),
                    patch.object(deploy_target.time, "sleep"),
                    self.assertRaisesRegex(RuntimeError, expected),
                ):
                    deploy_target.fresh_health(
                        Path("/target"),
                        "a" * 64,
                        restart_started_at=100.0,
                        strips=33,
                        leds_per_strip=138,
                        receivers=5,
                        stable_samples=1,
                        timeout=1.0,
                        unit="ledgrid.service",
                        api_url="http://local/status",
                        receiver_contract=contract,
                    )


class TargetProvisioningTests(unittest.TestCase):
    def test_systemd_unit_uses_finalized_geometry_and_receiver_roster(self) -> None:
        text = deploy_target._unit_text(
            Path("/target"), "ledgridwall", strips=33, receivers=5
        )
        self.assertIn("Environment=STRIPS=33", text)
        self.assertIn("Environment=EXPECTED_ESP32_DEVICES=5", text)
        self.assertIn("Environment=LEDGRID_FEC_RECEIVER_IDS=3", text)
        self.assertIn("WorkingDirectory=/target/current", text)
        with self.assertRaisesRegex(ValueError, "strips"):
            deploy_target._unit_text(
                Path("/target"), "ledgridwall", strips=0, receivers=5
            )

    def test_unit_install_uses_root_adjacent_atomic_staging_and_replaces_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            systemd_root = Path(temporary_dir) / "systemd"
            systemd_root.mkdir()
            destination = systemd_root / "ledgrid.service"
            hostile = Path(temporary_dir) / "hostile.service"
            hostile.write_text("hostile unit\n", encoding="utf-8")
            destination.symlink_to(hostile)
            written: list[tuple[Path, bytes, str, str]] = []
            commands: list[tuple[str, ...]] = []

            def write_root(path, payload, *, expected_sha256, mode="0444"):
                written.append((path, payload, expected_sha256, mode))
                path.write_bytes(payload)
                # Simulate a same-UID destination swap after staging. mv -T must
                # replace the pathname rather than follow the hostile symlink.
                destination.unlink()
                destination.symlink_to(hostile)

            def command(args, **_kwargs):
                values = tuple(os.fspath(item) for item in args)
                commands.append(values)
                if values[:4] == ("sudo", "mv", "-T", "--"):
                    os.replace(values[4], values[5])
                    return subprocess.CompletedProcess(args, 0, "", "")
                if values[:4] == ("sudo", "rm", "-f", "--"):
                    Path(values[4]).unlink(missing_ok=True)
                    return subprocess.CompletedProcess(args, 0, "", "")
                if values[:3] == ("systemctl", "is-enabled", "--quiet"):
                    return subprocess.CompletedProcess(args, 0, "", "")
                if values == ("sudo", "systemctl", "daemon-reload"):
                    return subprocess.CompletedProcess(args, 0, "", "")
                self.fail(f"unexpected unit command: {values}")

            with (
                patch.object(deploy_target, "SYSTEMD_UNIT_ROOT", systemd_root),
                patch.object(
                    deploy_target, "_write_root_owned_bytes", side_effect=write_root
                ),
                patch.object(
                    deploy_target, "_validate_root_owned_regular_file"
                ) as validate,
                patch.object(deploy_target, "_command", side_effect=command),
            ):
                result = deploy_target.ensure_unit(
                    Path("/target"), user="ledgridwall", strips=33, receivers=5
                )

            self.assertTrue(result["changed"])
            self.assertFalse(result["enabled_changed"])
            self.assertFalse(destination.is_symlink())
            self.assertIn("WorkingDirectory=/target/current", destination.read_text())
            self.assertEqual(hostile.read_text(), "hostile unit\n")
            self.assertEqual(len(written), 1)
            staging, payload, expected_sha256, mode = written[0]
            self.assertEqual(staging.parent, systemd_root)
            self.assertTrue(staging.name.startswith(".ledgrid.service.install-"))
            self.assertEqual(hashlib.sha256(payload).hexdigest(), expected_sha256)
            self.assertEqual(mode, "0644")
            self.assertFalse(any(
                "ledgrid-unit-" in argument
                for command_args in commands
                for argument in command_args
            ))
            command_paths = {
                Path(argument)
                for command_args in commands
                for argument in command_args
                if argument.startswith(temporary_dir)
            }
            self.assertTrue(all(path.parent == systemd_root for path in command_paths))
            self.assertIn(
                ("sudo", "mv", "-T", "--", os.fspath(staging), os.fspath(destination)),
                commands,
            )
            validate.assert_called_once_with(
                destination, expected_sha256=expected_sha256, mode="0644"
            )

    def test_unit_install_failure_cleanup_is_exact_and_cannot_mask_root_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            systemd_root = Path(temporary_dir) / "systemd"
            systemd_root.mkdir()
            cleanup: list[tuple[str, ...]] = []

            def command(args, **_kwargs):
                values = tuple(os.fspath(item) for item in args)
                if values[:4] == ("sudo", "rm", "-f", "--"):
                    cleanup.append(values)
                    raise subprocess.TimeoutExpired(values, 10)
                return subprocess.CompletedProcess(args, 0, "", "")

            with (
                patch.object(deploy_target, "SYSTEMD_UNIT_ROOT", systemd_root),
                patch.object(
                    deploy_target,
                    "_write_root_owned_bytes",
                    side_effect=RuntimeError("injected root writer failure"),
                ),
                patch.object(deploy_target, "_command", side_effect=command),
                self.assertRaisesRegex(RuntimeError, "injected root writer failure"),
            ):
                deploy_target.ensure_unit(Path("/target"), user="ledgridwall")

            self.assertEqual(len(cleanup), 1)
            self.assertEqual(cleanup[0][:4], ("sudo", "rm", "-f", "--"))
            self.assertEqual(Path(cleanup[0][4]).parent, systemd_root)

    def test_target_topology_migration_is_receipted_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            path = root / RECEIVER_HYBRID_CONFIG_RELATIVE_PATH
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "schema": "ledgrid.receiver-hybrid-rollout",
                "schema_version": 1,
                "enabled": True,
                "transport_policy": "degraded_spi1_01_readable",
                "physical_lane_order": [0, 1, 3, 2],
                "reverse_strips_by_logical_receiver": [False, False, True, True],
                "reverse_native_strips_by_logical_receiver": [False, False, True, True],
            }))
            migrated = deploy_target.migrate_receiver_topology(root)
            repeated = deploy_target.migrate_receiver_topology(root)

        self.assertEqual(migrated["outcome"], "executed")
        self.assertTrue(migrated["migrated"])
        self.assertEqual(migrated["strips"], 33)
        self.assertEqual(migrated["receivers"], 5)
        self.assertRegex(
            migrated["receiver_hybrid_config_digest"], r"^[0-9a-f]{64}$"
        )
        self.assertEqual(repeated["outcome"], "skipped")

    def test_runtime_ensure_accepts_first_install_progress_before_final_json(self) -> None:
        root = Path("/target")
        release_id = "a" * 64
        expected = {
            "identity": "b" * 64,
            "installed": True,
            "path": "/target/.venvs/runtime-cpython-3.10-bbbbbbbbbbbbbbbbbbbbbbbb",
            "migrated_legacy": None,
        }
        stdout = (
            "Resolved 37 packages in 211ms\n"
            "Prepared 37 packages in 1.4s\n"
            "Installed 37 packages in 96ms\n"
            + json.dumps(expected, sort_keys=True)
            + "\n"
        )
        completed = subprocess.CompletedProcess(("python3",), 0, stdout, "")

        with patch.object(deploy_target, "_command", return_value=completed) as command:
            result = deploy_target.ensure_runtime(root, release_id)

        self.assertEqual(result, expected)
        args = command.call_args.args[0]
        self.assertEqual(args[0], "python3")
        self.assertIn("runtime_env.py", os.fspath(args[1]))
        self.assertEqual(args[2], "ensure")

    def test_runtime_ensure_rejects_missing_malformed_or_trailing_control_json(self) -> None:
        valid = json.dumps({"installed": True})
        cases = (
            ("", "no JSON result"),
            ("Resolved packages only\n", "did not end with JSON"),
            ("Installed packages\n{\n", "did not end with JSON"),
            (valid + "\ntrailing noise\n", "did not end with JSON"),
            ("Installed packages\n[]\n", "not an object"),
        )
        for stdout, message in cases:
            with self.subTest(stdout=stdout), patch.object(
                deploy_target,
                "_command",
                return_value=subprocess.CompletedProcess(
                    ("python3",), 0, stdout, ""
                ),
            ), self.assertRaisesRegex(RuntimeError, message):
                deploy_target.ensure_runtime(Path("/target"), "a" * 64)

    def test_current_aware_unit_install_waits_until_spi_is_ready_after_reboot(self) -> None:
        root = Path("/target")
        release = root / "releases" / ("a" * 64)
        unit_result = {
            "unit": "ledgrid.service",
            "changed": True,
            "enabled_changed": False,
        }
        with (
            patch.object(deploy_target, "_release", return_value=release),
            patch.object(
                deploy_target,
                "ensure_runtime",
                return_value={"installed": False},
            ),
            patch.object(
                deploy_target,
                "configure_spi",
                side_effect=(
                    {"status": "needs_reboot", "config_changed": True},
                    {"status": "ready", "config_changed": False},
                ),
            ),
            patch.object(deploy_target, "ensure_unit", return_value=unit_result) as ensure_unit,
        ):
            before_reboot = deploy_target.provision(
                root, "a" * 64, user="ledgridwall", hat=False,
            )
            after_reboot = deploy_target.provision(
                root, "a" * 64, user="ledgridwall", hat=False,
            )

        self.assertTrue(before_reboot["unit"]["deferred"])
        self.assertEqual(before_reboot["spi"]["status"], "needs_reboot")
        self.assertEqual(after_reboot["unit"], unit_result)
        self.assertEqual(after_reboot["spi"]["status"], "ready")
        ensure_unit.assert_called_once_with(
            root, user="ledgridwall", strips=33, receivers=5
        )


class TargetFirmwareBuildTests(unittest.TestCase):
    def test_ordinary_target_build_selects_only_feature_off_production_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            firmware = workspace / "firmware/esp32"
            firmware.mkdir(parents=True)
            calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

            def command(args, **kwargs):
                calls.append((tuple(args), kwargs))
                if args[-1] == "--version":
                    return subprocess.CompletedProcess(
                        args, 0, "PlatformIO Core, version 6.1.19\n", "",
                    )
                _write_firmware_artifacts(
                    firmware,
                    PRODUCTION_FIRMWARE_ENVIRONMENT,
                    application=b"production feature-off firmware",
                )
                return subprocess.CompletedProcess(args, 0, "build passed\n", "")

            with (
                patch.object(
                    deploy_target,
                    "_copy_support_workspace",
                    return_value=(workspace, False),
                ),
                patch.object(deploy_target.shutil, "which", return_value="/fake/pio"),
                patch.object(deploy_target, "_command", side_effect=command),
            ):
                result = deploy_target.build_firmware(root, "a" * 64)

            self.assertEqual(result["outcome"], "executed")
            self.assertEqual(
                [args for args, _kwargs in calls],
                [
                    ("/fake/pio", "--version"),
                    ("/fake/pio", "run", "-e", "esp32-s3-devkitc-1"),
                ],
            )
            self.assertFalse(
                (firmware / ".pio/build/esp32-s3-devkitc-1-local-canary").exists(),
            )
            build_kwargs = calls[1][1]
            self.assertEqual(
                build_kwargs["env"]["PLATFORMIO_BUILD_CACHE_DIR"],
                os.fspath(root / "build/firmware/.platformio-build-cache"),
            )
            self.assertEqual(build_kwargs["env"]["IDF_CCACHE_ENABLE"], "1")
            self.assertEqual(
                build_kwargs["env"]["CCACHE_DIR"],
                os.fspath(root / "build/firmware/.ccache"),
            )

    def test_enabled_local_policy_builds_only_allowlisted_canary_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            write_receiver_hybrid_config(root, enabled=True)
            workspace = root / "workspace"
            firmware = workspace / "firmware/esp32"
            firmware.mkdir(parents=True)
            calls = []

            def command(args, **kwargs):
                calls.append(tuple(args))
                if args[-1] == "--version":
                    return subprocess.CompletedProcess(
                        args, 0, "PlatformIO Core, version 6.1.19\n", "",
                    )
                _write_firmware_artifacts(
                    firmware,
                    DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
                    application=b"degraded hybrid firmware",
                )
                return subprocess.CompletedProcess(args, 0, "build passed\n", "")

            with (
                patch.object(
                    deploy_target,
                    "_copy_support_workspace",
                    return_value=(workspace, False),
                ),
                patch.object(deploy_target.shutil, "which", return_value="/fake/pio"),
                patch.object(deploy_target, "_command", side_effect=command),
            ):
                result = deploy_target.build_firmware(root, "a" * 64)

            self.assertEqual(
                calls[1],
                (
                    "/fake/pio", "run", "-e",
                    DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
                ),
            )
            self.assertEqual(
                result["firmware_environment"],
                DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
            )
            self.assertTrue(result["receiver_hybrid_config"]["enabled"])

    def test_managed_native_gate_builds_only_native_canary_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            write_receiver_hybrid_config(
                root, enabled=True, native_modules_enabled=True
            )
            workspace = root / "workspace"
            firmware = workspace / "firmware/esp32"
            firmware.mkdir(parents=True)
            calls = []

            def command(args, **_kwargs):
                calls.append(tuple(args))
                if args[-1] == "--version":
                    return subprocess.CompletedProcess(
                        args, 0, "PlatformIO Core, version 6.1.19\n", "",
                    )
                _write_firmware_artifacts(
                    firmware,
                    NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
                    application=b"managed native firmware",
                )
                return subprocess.CompletedProcess(args, 0, "build passed\n", "")

            with (
                patch.object(
                    deploy_target,
                    "_copy_support_workspace",
                    return_value=(workspace, False),
                ),
                patch.object(deploy_target.shutil, "which", return_value="/fake/pio"),
                patch.object(deploy_target, "_command", side_effect=command),
            ):
                result = deploy_target.build_firmware(root, "a" * 64)

            self.assertEqual(calls[1], (
                "/fake/pio", "run", "-e",
                NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
            ))
            self.assertTrue(result["receiver_hybrid_config"]["native_modules_enabled"])

    def test_flash_rejects_rollout_config_drift_before_port_or_helper_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            with (
                patch.object(deploy_target, "_copy_support_workspace") as workspace,
                patch.object(deploy_target, "_discover_receiver_devices") as discover,
                patch.object(deploy_target, "_command") as command,
                self.assertRaisesRegex(RuntimeError, "selection changed"),
            ):
                deploy_target.flash_firmware(
                    root,
                    "a" * 64,
                    expected_firmware_environment=(
                        DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT
                    ),
                )
            workspace.assert_not_called()
            discover.assert_not_called()
            command.assert_not_called()


class TargetFirmwareFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = patch.object(
            deploy_target,
            "_root_owned_firmware_bundle",
            side_effect=self._immutable_bundle,
        )
        self.validate_bundle = patch.object(
            deploy_target, "_validate_root_owned_firmware_bundle"
        )
        self.bundle.start()
        self.validate_bundle.start()
        self.addCleanup(self.bundle.stop)
        self.addCleanup(self.validate_bundle.stop)

    @staticmethod
    def _production_binary(workspace: Path, content: bytes = b"production") -> Path:
        return _write_firmware_artifacts(
            workspace / "firmware" / "esp32",
            PRODUCTION_FIRMWARE_ENVIRONMENT,
            application=content,
        )

    @staticmethod
    @contextmanager
    def _verified_openocd():
        yield Path("/verified/openocd"), Path("/verified/scripts")

    @staticmethod
    def _program_success(**kwargs):
        device = kwargs["device"]
        artifacts = list(kwargs["artifacts"])
        return {
            **device.to_dict(),
            "operation": "openocd_program_verify",
            "artifacts": artifacts,
            "returncode": 0,
            "verify_count": len(artifacts),
            "expected_verify_count": len(artifacts),
            "output": "** Verify OK **\n" * len(artifacts),
            "outcome": "success",
        }

    @staticmethod
    def _immutable_bundle(firmware: Path, installation: Mapping[str, Any]):
        root = (
            deploy_target.ROOT_OWNED_FIRMWARE_INSTALL_ROOT
            / str(installation["installation_digest"])
        )
        artifacts = [
            {
                **artifact,
                "program_path": os.fspath(root / f"artifact-{index:02d}.bin"),
            }
            for index, artifact in enumerate(installation["flash_artifacts"])
        ]
        return root, artifacts

    def test_leaf_rejects_non_five_topology_before_any_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            malformed = SimpleNamespace(receiver_strip_counts=(8, 8, 8, 9))
            with (
                patch.object(
                    deploy_target,
                    "resolve_receiver_hybrid_config",
                    return_value=malformed,
                ),
                patch.object(deploy_target, "_copy_support_workspace") as copy,
                patch.object(deploy_target, "_discover_receiver_devices") as discover,
                patch.object(deploy_target, "_command") as command,
                self.assertRaisesRegex(RuntimeError, "exactly 5 receivers"),
            ):
                deploy_target.flash_firmware(root, "a" * 64)
            copy.assert_not_called()
            discover.assert_not_called()
            command.assert_not_called()

    def test_removed_receiver_override_is_rejected_by_cli_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                deploy_target._parser().parse_args((
                    "--root",
                    temporary_dir,
                    "flash-firmware",
                    "a" * 64,
                    "--receivers",
                    "4",
                ))

    def test_four_discovered_receivers_are_rejected_before_programming(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            self._production_binary(workspace)
            with (
                patch.object(
                    deploy_target, "_copy_support_workspace",
                    return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices",
                    return_value=_receiver_devices()[:4],
                ),
                patch.object(deploy_target, "_prepare_shared_firmware_marker") as marker,
                patch.object(deploy_target, "_pinned_openocd") as openocd,
                self.assertRaisesRegex(RuntimeError, "exactly 5 discovered receivers"),
            ):
                deploy_target.flash_firmware(root, "a" * 64)
            marker.assert_not_called()
            openocd.assert_not_called()

    def test_openocd_flash_binds_each_exact_device_and_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            binary = self._production_binary(workspace)
            devices = _receiver_devices()

            with (
                patch.object(
                    deploy_target, "_copy_support_workspace", return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices", return_value=devices,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target, "_program_receiver_openocd",
                    side_effect=self._program_success,
                ) as program,
            ):
                result = deploy_target.flash_firmware(
                    root, "a" * 64,
                )

            self.assertEqual(result["outcome"], "executed")
            self.assertEqual(result["firmware_sha256"], deploy_target._sha256_file(binary))
            self.assertEqual(program.call_count, 5)
            self.assertEqual(
                [call.kwargs["device"].hardware_serial for call in program.call_args_list],
                [device.hardware_serial for device in devices],
            )
            for call in program.call_args_list:
                self.assertEqual(
                    call.kwargs["installation_digest"],
                    result["firmware_installation_digest"],
                )
                self.assertEqual(len(call.kwargs["artifacts"]), 3)
            inventory = result["receiver_firmware_inventory"]
            self.assertEqual(len(inventory["observed_devices"]), 5)
            self.assertEqual(
                {item["reason"] for item in inventory["flash_targets"]},
                {"aggregate_marker_mismatch"},
            )
            evidence = json.loads(Path(inventory["flash_evidence_path"]).read_text())
            self.assertEqual(evidence["outcome"], "success")
            self.assertEqual(len(evidence["boards"]), 5)
            self.assertEqual(
                (root / ".esp32_firmware_hash").read_text().strip(),
                result["firmware_installation_digest"],
            )

    def test_openocd_flash_retries_only_pre_attach_usb_serial_race(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            self._production_binary(workspace)
            devices = _receiver_devices()
            calls = 0

            def program(**kwargs):
                nonlocal calls
                calls += 1
                result = self._program_success(**kwargs)
                if calls == 1:
                    return {
                        **result,
                        "returncode": 1,
                        "verify_count": 0,
                        "output": (
                            "Info : No device matches the serial string\n"
                            "Error: esp_usb_jtag: could not find or open device!\n"
                            "** OpenOCD init failed **\n"
                        ),
                        "outcome": "failed",
                    }
                return result

            with (
                patch.object(
                    deploy_target, "_copy_support_workspace",
                    return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices",
                    return_value=devices,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target, "_program_receiver_openocd", side_effect=program,
                ),
            ):
                result = deploy_target.flash_firmware(root, "a" * 64)

            self.assertEqual(result["outcome"], "executed")
            self.assertEqual(calls, 6)
            evidence = json.loads(
                Path(
                    result["receiver_firmware_inventory"]["flash_evidence_path"]
                ).read_text()
            )
            self.assertEqual(len(evidence["boards"]), 5)
            first = evidence["boards"][0]
            self.assertEqual(first["outcome"], "success")
            self.assertEqual(first["transport_attempt_count"], 2)
            self.assertEqual(len(first["transport_attempts"]), 2)
            self.assertEqual(first["transport_attempts"][0]["verify_count"], 0)
            self.assertEqual(first["transport_attempts"][1]["outcome"], "success")

    def test_missing_inventory_migrates_once_then_matching_hardware_skips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            self._production_binary(workspace)
            devices = _receiver_devices()
            installation = deploy_target.inspect_firmware_installation(
                workspace / "firmware/esp32", PRODUCTION_FIRMWARE_ENVIRONMENT
            )
            (root / ".esp32_firmware_hash").write_text(
                installation["installation_digest"] + "\n", encoding="utf-8"
            )

            with (
                patch.object(
                    deploy_target, "_copy_support_workspace", return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices", return_value=devices,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target, "_program_receiver_openocd",
                    side_effect=self._program_success,
                ) as program,
            ):
                migrated = deploy_target.flash_firmware(
                    root, "a" * 64,
                )
                unchanged = deploy_target.flash_firmware(
                    root, "a" * 64,
                )

            self.assertEqual(migrated["outcome"], "executed")
            self.assertEqual(
                {item["reason"] for item in migrated["receiver_firmware_inventory"]["flash_targets"]},
                {"aggregate_marker_mismatch"},
            )
            self.assertEqual(unchanged["outcome"], "skipped")
            self.assertEqual(unchanged["flashed_ports"], [])
            self.assertEqual(program.call_count, 5)

    def test_replaced_hardware_selects_only_its_current_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            self._production_binary(workspace)
            original = _receiver_devices()
            replacement = ReceiverUSBDevice(
                port=original[2].port,
                hardware_serial="0a:0b:0c:0d:0e:0f",
                physical_location=original[2].physical_location,
            )
            replaced = (*original[:2], replacement, *original[3:])
            with (
                patch.object(
                    deploy_target, "_copy_support_workspace", return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices", return_value=original,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target, "_program_receiver_openocd",
                    side_effect=self._program_success,
                ),
            ):
                deploy_target.flash_firmware(
                    root, "a" * 64,
                )

            with (
                patch.object(
                    deploy_target, "_copy_support_workspace", return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices", return_value=replaced,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target, "_program_receiver_openocd",
                    side_effect=self._program_success,
                ) as program,
            ):
                result = deploy_target.flash_firmware(
                    root, "a" * 64,
                )

            self.assertEqual(result["outcome"], "executed")
            self.assertEqual(result["flashed_ports"], [replacement.port])
            self.assertEqual(
                result["receiver_firmware_inventory"]["flash_targets"][0]["reason"],
                "unrecorded_hardware",
            )
            self.assertEqual(
                program.call_args.kwargs["device"], replacement,
            )

    def test_partial_failure_retains_exact_diagnostics_and_never_advances_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            self._production_binary(workspace)
            devices = _receiver_devices()
            calls = 0

            def program(**kwargs):
                nonlocal calls
                calls += 1
                result = self._program_success(**kwargs)
                if calls == 3:
                    return {
                        **result,
                        "returncode": 1,
                        "verify_count": 1,
                        "output": "LIBUSB_ERROR_TIMEOUT on exact board",
                        "outcome": "failed",
                    }
                return result

            with (
                patch.object(
                    deploy_target, "_copy_support_workspace", return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices", return_value=devices,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target, "_program_receiver_openocd", side_effect=program,
                ),
                patch.object(
                    deploy_target,
                    "_command",
                    side_effect=subprocess.TimeoutExpired(("systemctl", "stop"), 10),
                ) as stop,
                self.assertRaisesRegex(
                    RuntimeError,
                    r"serial=02:00:00:00:00:02 usb_path=1-1\.3:1\.0",
                ),
            ):
                deploy_target.flash_firmware(
                    root, "a" * 64,
                )
            self.assertEqual(calls, 3)
            self.assertEqual((root / ".esp32_firmware_hash").read_text(), "")
            self.assertFalse(
                (root / "run_state/receiver_firmware_inventory.json").exists()
            )
            evidence_paths = list(
                (root / "run_state/receiver_flash_attempts").glob("*.json")
            )
            self.assertEqual(len(evidence_paths), 1)
            evidence = json.loads(evidence_paths[0].read_text())
            self.assertEqual(evidence["outcome"], "failed")
            self.assertEqual(len(evidence["boards"]), 3)
            self.assertEqual(evidence["boards"][-1]["hardware_serial"], devices[2].hardware_serial)
            self.assertIn("LIBUSB_ERROR_TIMEOUT", evidence["boards"][-1]["output"])
            stop.assert_called_with(
                ("sudo", "systemctl", "stop", deploy_target.DEFAULT_SYSTEMD_UNIT),
                check=False,
                timeout=10.0,
            )

    def test_forced_partial_flash_revokes_old_authority_and_retry_cannot_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            self._production_binary(workspace)
            devices = _receiver_devices()
            with (
                patch.object(
                    deploy_target, "_copy_support_workspace",
                    return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices", return_value=devices,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target, "_program_receiver_openocd",
                    side_effect=self._program_success,
                ),
            ):
                seeded = deploy_target.flash_firmware(root, "a" * 64)
            self.assertEqual(seeded["outcome"], "executed")

            calls = 0

            def fail_second(**kwargs):
                nonlocal calls
                calls += 1
                result = self._program_success(**kwargs)
                if calls == 2:
                    return {**result, "outcome": "failed", "returncode": 1}
                return result

            with (
                patch.object(
                    deploy_target, "_copy_support_workspace",
                    return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices", return_value=devices,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target, "_program_receiver_openocd", side_effect=fail_second,
                ),
                patch.object(deploy_target, "_command"),
                self.assertRaisesRegex(RuntimeError, "program/readback verification failed"),
            ):
                deploy_target.flash_firmware(root, "a" * 64, force=True)

            commit_path = root / deploy_target.RECEIVER_FIRMWARE_COMMIT.as_posix()
            self.assertEqual(
                json.loads(commit_path.read_text())["status"], "invalidated"
            )

            with (
                patch.object(
                    deploy_target, "_copy_support_workspace",
                    return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices", return_value=devices,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target, "_program_receiver_openocd",
                    side_effect=self._program_success,
                ) as program,
            ):
                repaired = deploy_target.flash_firmware(root, "a" * 64)
            self.assertEqual(repaired["outcome"], "executed")
            self.assertEqual(program.call_count, 5)
            self.assertTrue(
                repaired["receiver_firmware_inventory"]["authority_repair"]
            )

    def test_flash_rejects_missing_build_artifact_before_helper_or_serial_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            with (
                patch.object(
                    deploy_target, "_copy_support_workspace",
                    return_value=(workspace, True),
                ),
                patch.object(deploy_target, "_command") as command,
                self.assertRaisesRegex(RuntimeError, "run build-firmware"),
            ):
                deploy_target.flash_firmware(
                    root, "a" * 64,
                )
            command.assert_not_called()

    def test_source_replacement_cannot_change_immutable_programming_or_evidence(self) -> None:
        for mutation in ("delete", "change"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary_dir:
                root = Path(temporary_dir)
                workspace = root / "workspace"
                workspace.mkdir()
                binary = self._production_binary(workspace)
                devices = _receiver_devices()
                calls = 0

                def program(**kwargs):
                    nonlocal calls
                    calls += 1
                    result = self._program_success(**kwargs)
                    if calls == len(devices):
                        if mutation == "delete":
                            binary.unlink()
                        else:
                            binary.write_bytes(b"different firmware")
                    return result

                with (
                    patch.object(
                        deploy_target, "_copy_support_workspace",
                        return_value=(workspace, True),
                    ),
                    patch.object(
                        deploy_target, "_discover_receiver_devices", return_value=devices,
                    ),
                    patch.object(
                        deploy_target, "_pinned_openocd",
                        return_value=self._verified_openocd(),
                    ),
                    patch.object(
                        deploy_target, "_program_receiver_openocd",
                        side_effect=program,
                    ) as programmer,
                ):
                    result = deploy_target.flash_firmware(
                        root, "a" * 64,
                    )
                self.assertEqual(result["outcome"], "executed")
                source_root = os.fspath(workspace / "firmware/esp32")
                for call in programmer.call_args_list:
                    self.assertTrue(all(
                        not str(artifact["program_path"]).startswith(source_root)
                        for artifact in call.kwargs["artifacts"]
                    ))
                evidence_path = Path(
                    result["receiver_firmware_inventory"]["flash_evidence_path"]
                )
                evidence = json.loads(evidence_path.read_text())
                self.assertTrue(all(
                    str(artifact["program_path"]).startswith(
                        os.fspath(deploy_target.ROOT_OWNED_FIRMWARE_INSTALL_ROOT)
                    )
                    for artifact in evidence["firmware_bundle_artifacts"]
                ))

    def test_openocd_command_uses_exact_serial_offsets_and_readback_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            workspace = Path(temporary_dir)
            self._production_binary(workspace)
            firmware = workspace / "firmware" / "esp32"
            installation = deploy_target.inspect_firmware_installation(
                firmware, PRODUCTION_FIRMWARE_ENVIRONMENT
            )
            device = _receiver_devices()[0]
            bundle_root, artifacts = self._immutable_bundle(firmware, installation)
            verified = "** Verify OK **\n" * len(installation["flash_artifacts"])
            with patch.object(
                deploy_target,
                "_command",
                return_value=subprocess.CompletedProcess(("openocd",), 0, "", verified),
            ) as command:
                result = deploy_target._program_receiver_openocd(
                    executable=Path("/verified/openocd"),
                    scripts=Path("/verified/scripts"),
                    bundle_root=bundle_root,
                    artifacts=artifacts,
                    installation_digest=installation["installation_digest"],
                    device=device,
                )

            args = [os.fspath(item) for item in command.call_args.args[0]]
            self.assertEqual(args[:2], ["sudo", "/verified/openocd"])
            self.assertIn(f"adapter serial {device.hardware_serial.upper()}", args)
            program_commands = [
                args[index + 1]
                for index, item in enumerate(args[:-1])
                if item == "-c" and args[index + 1].startswith("program_esp ")
            ]
            self.assertEqual(len(program_commands), len(installation["flash_artifacts"]))
            for artifact in artifacts:
                self.assertTrue(any(
                    f" {artifact['offset']} verify no_skip_loaded" in item
                    and artifact["program_path"] in item
                    for item in program_commands
                ))
            self.assertEqual(result["outcome"], "success")
            self.assertEqual(result["verify_count"], len(program_commands))

    def test_openocd_timeout_returns_exact_board_failure_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            workspace = Path(temporary_dir)
            self._production_binary(workspace)
            firmware = workspace / "firmware" / "esp32"
            installation = deploy_target.inspect_firmware_installation(
                firmware, PRODUCTION_FIRMWARE_ENVIRONMENT
            )
            bundle_root, artifacts = self._immutable_bundle(firmware, installation)
            device = _receiver_devices()[3]
            with patch.object(
                deploy_target,
                "_command",
                side_effect=subprocess.TimeoutExpired(("openocd",), 180),
            ):
                result = deploy_target._program_receiver_openocd(
                    executable=Path("/verified/openocd"),
                    scripts=Path("/verified/scripts"),
                    bundle_root=bundle_root,
                    artifacts=artifacts,
                    installation_digest=installation["installation_digest"],
                    device=device,
                )

            self.assertEqual(result["outcome"], "failed")
            self.assertEqual(result["hardware_serial"], device.hardware_serial)
            self.assertEqual(result["physical_location"], device.physical_location)
            self.assertIsNone(result["returncode"])
            self.assertIn("TimeoutExpired", result["output"])

    def test_openocd_rejects_adversarial_artifact_path_before_privilege(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            workspace = Path(temporary_dir) / "workspace"
            workspace.mkdir()
            self._production_binary(workspace)
            firmware = workspace / "firmware" / "esp32"
            installation = deploy_target.inspect_firmware_installation(
                firmware, PRODUCTION_FIRMWARE_ENVIRONMENT
            )
            bundle_root, artifacts = self._immutable_bundle(firmware, installation)
            artifacts[0]["program_path"] = "/opt/unsafe;shutdown/artifact.bin"
            with (
                patch.object(deploy_target, "_command") as command,
                self.assertRaisesRegex(RuntimeError, "unsafe Tcl path"),
            ):
                deploy_target._program_receiver_openocd(
                    executable=Path("/verified/openocd"),
                    scripts=Path("/verified/scripts"),
                    bundle_root=bundle_root,
                    artifacts=artifacts,
                    installation_digest=installation["installation_digest"],
                    device=_receiver_devices()[0],
                )
            command.assert_not_called()

    def test_openocd_refuses_deploy_user_owned_install_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            install_root = root / "install"
            install = install_root / deploy_target.PINNED_OPENOCD_SHA256
            install.mkdir(parents=True)
            install_root.chmod(0o777)
            archive = root / "archive.tar.gz"
            archive.write_bytes(b"verified archive placeholder")
            with (
                patch.object(
                    deploy_target, "PINNED_OPENOCD_INSTALL_ROOT", install_root
                ),
                patch.object(
                    deploy_target, "_pinned_openocd_archive", return_value=archive
                ),
                patch.object(deploy_target, "_command") as command,
                self.assertRaisesRegex(RuntimeError, "root-owned and immutable"),
            ):
                with deploy_target._pinned_openocd(root):
                    self.fail("deploy-user-owned OpenOCD tree was accepted")
            command.assert_not_called()

    def test_identity_drift_fails_before_programming_and_keeps_marker_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            self._production_binary(workspace)
            devices = _receiver_devices()
            drifted = (
                ReceiverUSBDevice(
                    port=devices[0].port,
                    hardware_serial=devices[0].hardware_serial,
                    physical_location="9-9.9:1.0",
                ),
                *devices[1:],
            )
            with (
                patch.object(
                    deploy_target, "_copy_support_workspace", return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices", return_value=devices,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target,
                    "_wait_for_receiver_binding",
                    side_effect=RuntimeError(
                        "receiver hardware serial/USB path changed before programming; "
                        f"observed={[_receiver_devices()[0].hardware_serial, drifted[0].physical_location]}"
                    ),
                ),
                patch.object(deploy_target, "_program_receiver_openocd") as program,
                patch.object(deploy_target, "_command"),
                self.assertRaisesRegex(RuntimeError, "serial/USB path changed"),
            ):
                deploy_target.flash_firmware(
                    root, "a" * 64,
                )
            program.assert_not_called()
            self.assertEqual((root / ".esp32_firmware_hash").read_text(), "")
            self.assertFalse(
                (root / "run_state/receiver_firmware_inventory.json").exists()
            )

    def test_usb_identity_stabilization_tolerates_temporary_disappearance(self) -> None:
        devices = _receiver_devices()
        with (
            patch.object(
                deploy_target,
                "_discover_receiver_devices",
                side_effect=(RuntimeError("found 4 receivers"), devices),
            ) as discover,
            patch.object(deploy_target.time, "sleep") as sleep,
        ):
            observed = deploy_target._wait_for_receiver_binding(
                devices,
                receiver_count=5,
                phase="after programming board 0",
                timeout=1.0,
                poll_interval=0.01,
            )
        self.assertEqual(observed, devices)
        self.assertEqual(discover.call_count, 2)
        sleep.assert_called_once()

    def test_usb_identity_stabilization_timeout_is_fail_closed(self) -> None:
        devices = _receiver_devices()
        with (
            patch.object(
                deploy_target,
                "_discover_receiver_devices",
                side_effect=RuntimeError("found 4 receivers"),
            ),
            patch.object(
                deploy_target.time, "monotonic", side_effect=(0.0, 0.0, 1.1)
            ),
            patch.object(deploy_target.time, "sleep"),
            self.assertRaisesRegex(
                RuntimeError,
                r"did not stabilize after programming board 0 within 1\.0s",
            ),
        ):
            deploy_target._wait_for_receiver_binding(
                devices,
                receiver_count=5,
                phase="after programming board 0",
                timeout=1.0,
                poll_interval=0.01,
            )

    def test_inventory_and_marker_boundary_failures_have_no_authoritative_commit(self) -> None:
        receiver_inventory = deploy_target._receiver_inventory_module()
        original_inventory_write = receiver_inventory.write_firmware_inventory
        original_marker_write = deploy_target._write_shared_firmware_marker
        for failure_stage in ("inventory", "marker"):
            with self.subTest(failure_stage=failure_stage), tempfile.TemporaryDirectory() as temporary_dir:
                root = Path(temporary_dir)
                workspace = root / "workspace"
                workspace.mkdir()
                self._production_binary(workspace)
                devices = _receiver_devices()
                installation = deploy_target.inspect_firmware_installation(
                    workspace / "firmware/esp32", PRODUCTION_FIRMWARE_ENVIRONMENT
                )

                def write_inventory(*args, **kwargs):
                    if failure_stage == "inventory":
                        raise OSError("injected inventory boundary failure")
                    return original_inventory_write(*args, **kwargs)

                def write_marker(*args, **kwargs):
                    if failure_stage == "marker":
                        raise OSError("injected marker boundary failure")
                    return original_marker_write(*args, **kwargs)

                with (
                    patch.object(
                        deploy_target, "_copy_support_workspace",
                        return_value=(workspace, True),
                    ),
                    patch.object(
                        deploy_target, "_discover_receiver_devices", return_value=devices,
                    ),
                    patch.object(
                        deploy_target, "_pinned_openocd",
                        return_value=self._verified_openocd(),
                    ),
                    patch.object(
                        deploy_target, "_program_receiver_openocd",
                        side_effect=self._program_success,
                    ),
                    patch.object(
                        receiver_inventory, "write_firmware_inventory",
                        side_effect=write_inventory,
                    ),
                    patch.object(
                        deploy_target, "_write_shared_firmware_marker",
                        side_effect=write_marker,
                    ),
                    patch.object(deploy_target, "_command"),
                    self.assertRaisesRegex(
                        RuntimeError, f"injected {failure_stage} boundary failure"
                    ),
                ):
                    deploy_target.flash_firmware(
                        root, "a" * 64,
                    )

                self.assertEqual((root / ".esp32_firmware_hash").read_text(), "")
                commit_path = root / deploy_target.RECEIVER_FIRMWARE_COMMIT.as_posix()
                self.assertEqual(
                    json.loads(commit_path.read_text())["status"], "invalidated"
                )
                evidence_path = next(
                    (root / "run_state/receiver_flash_attempts").glob("*.json")
                )
                self.assertEqual(
                    json.loads(evidence_path.read_text())["outcome"], "failed"
                )
                self.assertFalse(deploy_target._receiver_firmware_commit_matches(
                    root,
                    devices=devices,
                    installation_digest=installation["installation_digest"],
                    firmware_environment=PRODUCTION_FIRMWARE_ENVIRONMENT,
                    firmware_sha256=installation["firmware_sha256"],
                ))

    def test_final_evidence_and_commit_write_failures_cannot_claim_authority(self) -> None:
        original_atomic_json = deploy_target._atomic_json
        for failure_stage in ("success_evidence", "commit"):
            with self.subTest(failure_stage=failure_stage), tempfile.TemporaryDirectory() as temporary_dir:
                root = Path(temporary_dir)
                workspace = root / "workspace"
                workspace.mkdir()
                self._production_binary(workspace)
                devices = _receiver_devices()
                installation = deploy_target.inspect_firmware_installation(
                    workspace / "firmware/esp32", PRODUCTION_FIRMWARE_ENVIRONMENT
                )

                def atomic_json(path, payload):
                    if (
                        failure_stage == "success_evidence"
                        and payload.get("outcome") == "success"
                    ):
                        raise OSError("injected final evidence failure")
                    if (
                        failure_stage == "commit"
                        and Path(path) == root / deploy_target.RECEIVER_FIRMWARE_COMMIT.as_posix()
                        and payload.get("status") != "invalidated"
                    ):
                        raise OSError("injected authoritative commit failure")
                    return original_atomic_json(Path(path), payload)

                with (
                    patch.object(
                        deploy_target, "_copy_support_workspace",
                        return_value=(workspace, True),
                    ),
                    patch.object(
                        deploy_target, "_discover_receiver_devices", return_value=devices,
                    ),
                    patch.object(
                        deploy_target, "_pinned_openocd",
                        return_value=self._verified_openocd(),
                    ),
                    patch.object(
                        deploy_target, "_program_receiver_openocd",
                        side_effect=self._program_success,
                    ),
                    patch.object(
                        deploy_target, "_atomic_json", side_effect=atomic_json,
                    ),
                    patch.object(deploy_target, "_command"),
                    self.assertRaisesRegex(RuntimeError, "injected"),
                ):
                    deploy_target.flash_firmware(
                        root, "a" * 64,
                    )

                self.assertEqual(
                    (root / ".esp32_firmware_hash").read_text().strip(),
                    installation["installation_digest"],
                )
                commit_path = root / deploy_target.RECEIVER_FIRMWARE_COMMIT.as_posix()
                self.assertEqual(
                    json.loads(commit_path.read_text())["status"], "invalidated"
                )
                evidence_path = next(
                    (root / "run_state/receiver_flash_attempts").glob("*.json")
                )
                self.assertEqual(
                    json.loads(evidence_path.read_text())["outcome"], "failed"
                )
                self.assertFalse(deploy_target._receiver_firmware_commit_matches(
                    root,
                    devices=devices,
                    installation_digest=installation["installation_digest"],
                    firmware_environment=PRODUCTION_FIRMWARE_ENVIRONMENT,
                    firmware_sha256=installation["firmware_sha256"],
                ))

    def test_replacement_commit_failure_cannot_become_skip_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            self._production_binary(workspace)
            original = _receiver_devices()
            replacement = ReceiverUSBDevice(
                port=original[2].port,
                hardware_serial="0a:0b:0c:0d:0e:0f",
                physical_location=original[2].physical_location,
            )
            replaced = (*original[:2], replacement, *original[3:])
            installation = deploy_target.inspect_firmware_installation(
                workspace / "firmware/esp32", PRODUCTION_FIRMWARE_ENVIRONMENT
            )

            with (
                patch.object(
                    deploy_target, "_copy_support_workspace", return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices", return_value=original,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target, "_program_receiver_openocd",
                    side_effect=self._program_success,
                ),
            ):
                deploy_target.flash_firmware(
                    root, "a" * 64,
                )

            original_atomic_json = deploy_target._atomic_json

            def fail_commit(path, payload):
                if (
                    Path(path) == root / deploy_target.RECEIVER_FIRMWARE_COMMIT.as_posix()
                    and payload.get("status") != "invalidated"
                ):
                    raise OSError("injected replacement commit failure")
                return original_atomic_json(Path(path), payload)

            with (
                patch.object(
                    deploy_target, "_copy_support_workspace", return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices", return_value=replaced,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target, "_program_receiver_openocd",
                    side_effect=self._program_success,
                ),
                patch.object(deploy_target, "_atomic_json", side_effect=fail_commit),
                patch.object(deploy_target, "_command"),
                self.assertRaisesRegex(RuntimeError, "replacement commit failure"),
            ):
                deploy_target.flash_firmware(
                    root, "a" * 64,
                )

            self.assertFalse(deploy_target._receiver_firmware_commit_matches(
                root,
                devices=replaced,
                installation_digest=installation["installation_digest"],
                firmware_environment=PRODUCTION_FIRMWARE_ENVIRONMENT,
                firmware_sha256=installation["firmware_sha256"],
                require_current_devices=False,
            ))
            self.assertFalse(deploy_target._receiver_firmware_commit_matches(
                root,
                devices=replaced,
                installation_digest=installation["installation_digest"],
                firmware_environment=PRODUCTION_FIRMWARE_ENVIRONMENT,
                firmware_sha256=installation["firmware_sha256"],
            ))

            with (
                patch.object(
                    deploy_target, "_copy_support_workspace", return_value=(workspace, True),
                ),
                patch.object(
                    deploy_target, "_discover_receiver_devices", return_value=replaced,
                ),
                patch.object(
                    deploy_target, "_pinned_openocd",
                    return_value=self._verified_openocd(),
                ),
                patch.object(
                    deploy_target, "_program_receiver_openocd",
                    side_effect=self._program_success,
                ) as program,
            ):
                repaired = deploy_target.flash_firmware(
                    root, "a" * 64,
                )

            self.assertEqual(repaired["outcome"], "executed")
            self.assertTrue(
                repaired["receiver_firmware_inventory"]["authority_repair"]
            )
            self.assertEqual(program.call_count, 5)
            self.assertTrue(deploy_target._receiver_firmware_commit_matches(
                root,
                devices=replaced,
                installation_digest=installation["installation_digest"],
                firmware_environment=PRODUCTION_FIRMWARE_ENVIRONMENT,
                firmware_sha256=installation["firmware_sha256"],
            ))


class RootOwnedFirmwareBundleTests(unittest.TestCase):
    def test_pinned_reader_rejects_symlink_fifo_device_and_path_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            digest = hashlib.sha256(b"selected").hexdigest()

            symlink = root / "symlink.bin"
            symlink.symlink_to("/dev/null")
            fifo = root / "fifo.bin"
            os.mkfifo(fifo)
            for label, source in (
                ("symlink", symlink),
                ("fifo", fifo),
                ("device", Path("/dev/null")),
            ):
                with self.subTest(label=label), self.assertRaisesRegex(
                    RuntimeError, "safely open|bounded regular file"
                ):
                    deploy_target._read_pinned_regular_source(
                        source,
                        expected_sha256=digest,
                        maximum_bytes=1024,
                    )

            swapped = root / "swapped.bin"
            swapped.write_bytes(b"selected")
            real_open = os.open

            def swap_before_open(path, flags):
                if Path(path) == swapped:
                    swapped.unlink()
                    swapped.symlink_to("/dev/null")
                return real_open(path, flags)

            with (
                patch.object(deploy_target.os, "open", side_effect=swap_before_open),
                self.assertRaisesRegex(RuntimeError, "safely open"),
            ):
                deploy_target._read_pinned_regular_source(
                    swapped,
                    expected_sha256=digest,
                    maximum_bytes=1024,
                )

    def test_copy_is_rehashed_and_source_replacement_cannot_change_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            firmware = root / "firmware" / "esp32"
            application = _write_firmware_artifacts(
                firmware,
                PRODUCTION_FIRMWARE_ENVIRONMENT,
                application=b"selected application",
            )
            installation = deploy_target.inspect_firmware_installation(
                firmware, PRODUCTION_FIRMWARE_ENVIRONMENT
            )
            install_root = root / "root-owned"
            copied_application: bytes | None = None
            commands: list[tuple[str, ...]] = []

            def command(args, **kwargs):
                nonlocal copied_application
                values = tuple(os.fspath(item) for item in args)
                commands.append(values)
                if values[:3] == ("sudo", "mkdir", "-p"):
                    Path(values[3]).mkdir(parents=True, exist_ok=True)
                elif values[:3] == ("sudo", "mkdir", "--"):
                    Path(values[3]).mkdir()
                elif values[:2] == ("sudo", "dd"):
                    destination = Path(values[2].removeprefix("of="))
                    payload = kwargs["input_data"]
                    destination.write_bytes(payload)
                    if payload == b"selected application":
                        copied_application = payload
                        application.write_bytes(b"replaced after descriptor pinning")
                elif values[:2] == ("sudo", "sha256sum"):
                    path = Path(values[2])
                    return subprocess.CompletedProcess(
                        args, 0, deploy_target._sha256_file(path) + "  artifact\n", ""
                    )
                elif values[:3] == ("sudo", "mv", "--"):
                    shutil.move(os.fspath(values[3]), os.fspath(values[4]))
                elif values[:2] in (("sudo", "chown"), ("sudo", "chmod")):
                    pass
                elif values[:3] == ("sudo", "rm", "-rf"):
                    shutil.rmtree(Path(values[-1]), ignore_errors=True)
                else:
                    self.fail(f"unexpected command: {values}")
                return subprocess.CompletedProcess(args, 0, "", "")

            with (
                patch.object(
                    deploy_target, "ROOT_OWNED_FIRMWARE_INSTALL_ROOT", install_root
                ),
                patch.object(deploy_target, "_command", side_effect=command),
                patch.object(
                    deploy_target, "_validate_root_owned_firmware_bundle"
                ) as validate,
                patch.object(
                    deploy_target, "_validate_root_owned_regular_file"
                ),
            ):
                bundle_root, artifacts = deploy_target._root_owned_firmware_bundle(
                    firmware, installation
                )

            self.assertEqual(
                copied_application,
                b"selected application",
                "the root writer must receive only the descriptor-pinned bytes",
            )
            self.assertEqual(
                application.read_bytes(), b"replaced after descriptor pinning"
            )
            self.assertFalse(any(
                os.fspath(application) in argument
                for command_args in commands
                for argument in command_args
            ))
            self.assertEqual(bundle_root, install_root / installation["installation_digest"])
            self.assertTrue(all(
                str(item["program_path"]).startswith(os.fspath(bundle_root))
                for item in artifacts
            ))
            validate.assert_called_once_with(
                bundle_root,
                artifacts,
                installation_digest=installation["installation_digest"],
            )

    def test_validator_rejects_deploy_user_owned_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            install_root = Path(temporary_dir) / "bundles"
            digest = "a" * 64
            bundle_root = install_root / digest
            bundle_root.mkdir(parents=True)
            artifact = bundle_root / "artifact-00.bin"
            artifact.write_bytes(b"firmware")
            artifacts = [{
                "program_path": os.fspath(artifact),
                "sha256": deploy_target._sha256_file(artifact),
            }]
            with (
                patch.object(
                    deploy_target, "ROOT_OWNED_FIRMWARE_INSTALL_ROOT", install_root
                ),
                self.assertRaisesRegex(RuntimeError, "root-owned and immutable"),
            ):
                deploy_target._validate_root_owned_firmware_bundle(
                    bundle_root, artifacts, installation_digest=digest
                )


class FrozenSnapshotEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_dir.name)
        self.repo = _GitFixture(self.base / "repo")

    def tearDown(self) -> None:
        for path in self.base.rglob("*"):
            if not path.is_symlink() and path.is_dir():
                path.chmod(0o755)
        self.temporary_dir.cleanup()

    def test_freeze_snapshot_splits_app_from_support_and_is_target_verifiable(self) -> None:
        snapshot = self.base / "snapshot"
        evidence = deploy_entrypoint.freeze_snapshot(
            self.repo.root,
            "full",
            "clean",
            snapshot,
            generate_previews=False,
        )

        self.assertIn("scripts/start_server.py", evidence.app_files)
        self.assertIn("requirements-pi.lock", evidence.app_files)
        self.assertNotIn("firmware/esp32/src/main.cpp", evidence.app_files)
        self.assertNotIn("requirements-platformio.lock", evidence.app_files)
        self.assertEqual(
            set(evidence.support_files),
            {
                "firmware/esp32/partitions.csv",
                "firmware/esp32/src/main.cpp",
                "hardware/wiring.txt",
                "requirements-platformio.lock",
            },
        )
        verified = deploy_target.verify_snapshot(snapshot)
        self.assertEqual(verified["snapshot_id"], evidence.snapshot_id)
        self.assertFalse(snapshot.stat().st_mode & 0o222)
        self.assertFalse((snapshot / "scripts/start_server.py").stat().st_mode & 0o222)

    def test_staged_runtime_discovers_native_component_without_coupling_source_release(self) -> None:
        plugin_id = "aurora_curtains_native"
        manifest = self.repo.write(
            f"animation/plugins/{plugin_id}/manifest.json",
            (ROOT / f"animation/plugins/{plugin_id}/manifest.json").read_bytes(),
        )
        source = self.repo.write(
            f"animation/plugins/{plugin_id}/native/background.cpp",
            (ROOT / f"animation/plugins/{plugin_id}/native/background.cpp").read_bytes(),
        )
        subprocess.run(
            ("git", "-C", self.repo.root, "add", os.fspath(manifest), os.fspath(source)),
            check=True,
        )
        subprocess.run(
            (
                "git",
                "-C",
                self.repo.root,
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "native fixture",
            ),
            check=True,
        )
        first_snapshot = self.base / "native-snapshot-one"
        first = deploy_entrypoint.freeze_snapshot(
            self.repo.root,
            "full",
            "clean",
            first_snapshot,
            generate_previews=False,
        )
        self.assertIn(
            f"animation/plugins/{plugin_id}/manifest.json", first.app_files
        )
        self.assertEqual(
            first.native_build_files,
            (f"animation/plugins/{plugin_id}/native/background.cpp",),
        )
        self.assertNotIn(first.native_build_files[0], first.app_files)
        self.assertNotIn(first.native_build_files[0], first.support_files)

        target = self.base / "native-lane-target"
        first_app = deploy_target.stage_app(target, first_snapshot)
        first_support = deploy_target.stage_support(target, first_snapshot)
        release = target / "releases" / first_app["release_id"]
        self.assertFalse(
            (release / first.native_build_files[0]).exists(),
            "native source must remain outside the application release",
        )
        loader = AnimationPluginLoader(os.fspath(release / "animation/plugins"))
        self.assertIn(plugin_id, loader.scan_components())
        self.assertEqual(
            loader.get_component_descriptor(plugin_id)["build"]["source"],
            "native/background.cpp",
        )
        self.assertTrue(deploy_target.activate(target, first_app["release_id"])["changed"])

        source.write_bytes(b"// native two\n")
        second_snapshot = self.base / "native-snapshot-two"
        second = deploy_entrypoint.freeze_snapshot(
            self.repo.root,
            "full",
            "dirty",
            second_snapshot,
            generate_previews=False,
        )
        second_app = deploy_target.stage_app(target, second_snapshot)
        second_support = deploy_target.stage_support(target, second_snapshot)
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(first_app["release_id"], second_app["release_id"])
        self.assertEqual(
            first_support["support_release_id"],
            second_support["support_release_id"],
        )
        self.assertTrue(second_app["reused"])
        self.assertFalse(
            deploy_target.activate(target, second_app["release_id"])["changed"],
            "source-only native changes must not select or restart the app release",
        )

    def test_preview_generation_executes_frozen_snapshot_script_without_tracked_filter(self) -> None:
        snapshot = self.base / "preview-snapshot"
        commands: list[tuple[str, ...]] = []

        def generate(command):
            normalized = tuple(os.fspath(item) for item in command)
            commands.append(normalized)
            output = Path(normalized[normalized.index("--output") + 1])
            output.mkdir(parents=True)
            (output / "catalog.json").write_text("{}\n", encoding="utf-8")
            return SimpleNamespace(returncode=0)

        evidence = deploy_entrypoint.freeze_snapshot(
            self.repo.root,
            "full",
            "clean",
            snapshot,
            generate_previews=True,
            command_runner=generate,
        )

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertIn(
            os.fspath(snapshot.resolve() / "tools/generate_animation_previews.py"),
            command,
        )
        self.assertNotIn(
            os.fspath(
                self.repo.root.resolve() / "tools/generate_animation_previews.py"
            ),
            command,
        )
        self.assertNotIn("--tracked-only", command)
        self.assertIn(
            "web/static/generated/animation-previews/catalog.json",
            evidence.app_files,
        )

    def test_freeze_fails_closed_and_removes_snapshot_if_source_changes_mid_build(self) -> None:
        destination = self.base / "changed-snapshot"

        def mutate_source(_command):
            self.repo.write("scripts/start_server.py", b"print('changed')\n", executable=True)
            return SimpleNamespace(returncode=0)

        with self.assertRaisesRegex(RuntimeError, "source changed"):
            deploy_entrypoint.freeze_snapshot(
                self.repo.root,
                "full",
                "dirty",
                destination,
                generate_previews=True,
                command_runner=mutate_source,
            )
        self.assertFalse(destination.exists())

    def test_plan_is_read_only_and_accounts_for_lane_split_and_exact_order(self) -> None:
        before = subprocess.run(
            ("git", "-C", self.repo.root, "status", "--porcelain=v1", "-z"),
            check=True,
            capture_output=True,
        ).stdout
        config = deploy_entrypoint.DeploymentConfig(
            root=self.repo.root,
            mode="full",
            policy="clean",
            run_tests=False,
            generate_previews=False,
            local_receipts=Path("receipts"),
        )
        plan = deploy_entrypoint.deployment_plan(config)
        after = subprocess.run(
            ("git", "-C", self.repo.root, "status", "--porcelain=v1", "-z"),
            check=True,
            capture_output=True,
        ).stdout

        self.assertEqual(before, after)
        self.assertIn("scripts/start_server.py", plan["app_inputs"])
        self.assertNotIn("firmware/esp32/src/main.cpp", plan["app_inputs"])
        self.assertNotIn("firmware/esp32/partitions.csv", plan["app_inputs"])
        self.assertIn("firmware/esp32/src/main.cpp", plan["support_inputs"])
        self.assertIn("firmware/esp32/partitions.csv", plan["support_inputs"])
        self.assertEqual(plan["native_build_inputs"], [])
        self.assertEqual(plan["receiver_background_work"]["outcome"], "skipped")
        self.assertEqual(
            [step["id"] for step in plan["steps"]],
            [item[0] for item in FULL_STEP_ORDER],
        )
        self.assertEqual(
            plan["target_layout"]["current"], "ledgrid-pod/current",
        )
        self.assertEqual(
            plan["target_layout"]["native_background_library"],
            "ledgrid-pod/receiver_library/native_backgrounds",
        )
        self.assertEqual(
            plan["target_layout"]["legacy_app_bootstrap"],
            "ledgrid-pod/run_state/legacy_app_bootstrap.json",
        )
        self.assertFalse((self.repo.root / "receipts").exists())


class CoordinatorEntrypointIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_dir.name)
        self.repo = _GitFixture(self.base / "repo")

    def tearDown(self) -> None:
        for path in self.base.rglob("*"):
            if not path.is_symlink() and path.is_dir():
                path.chmod(0o755)
        self.temporary_dir.cleanup()

    def _deployment(
        self, *, unchanged: bool = False, firmware_changed: bool = False,
        force_firmware: bool = False,
    ):
        runner = _Runner()
        context = DeployContext(
            target="fake@wall",
            mode="full",
            source_identity={},
            source_policy="clean",
            command_runner=runner,
            ssh_runner=runner,
            attempt_id="entrypoint-integration",
        )
        config = deploy_entrypoint.DeploymentConfig(
            root=self.repo.root,
            mode="full",
            policy="clean",
            target="fake@wall",
            deploy_dir="fake-root",
            run_tests=False,
            generate_previews=False,
            health_timeout=1.0,
            force_firmware=force_firmware,
        )
        deployment = deploy_entrypoint.CoordinatorDeployment(config, context)
        target = _FakeTarget(
            unchanged=unchanged, firmware_changed=firmware_changed
        )
        deployment.target = target
        return deployment, context, runner, target

    def test_fake_target_full_deployment_runs_exact_order_and_records_health(self) -> None:
        deployment, context, runner, target = self._deployment()
        try:
            receipt = DeployCoordinator().run(context, deployment.steps())
        finally:
            deployment.close()

        self.assertEqual(receipt.outcome, "success")
        self.assertEqual(
            [step.step_id for step in receipt.steps],
            [item[0] for item in FULL_STEP_ORDER],
        )
        self.assertEqual(receipt.health["observed_release"], target.candidate)
        self.assertEqual(
            target._helper_path,
            f"fake-root/releases/{target.candidate}/tools/deployment/deploy_target.py",
        )
        self.assertEqual(
            [artifact.kind for artifact in receipt.artifacts],
            [
                "app_release",
                "support_release",
                "receiver_firmware_build",
                "receiver_firmware_installation",
            ],
        )
        local_commands = [call[0] for call in runner.calls]
        self.assertTrue(any(command and command[0] == "rsync" for command in local_commands))
        self.assertEqual(
            [command for command, _args in target.calls],
            [
                "stage-support",
                "stage-app",
                "cleanup-snapshot",
                "bootstrap-legacy-app",
                "build-firmware",
                "capture-state",
                "provision",
                "flash-firmware",
                "validate-app",
                "current-release",
                "activate",
                "restart",
                "restore-state",
                "health",
                "complete-legacy-bootstrap",
                "record-deploy",
                "migrate-receiver-topology",
                "prune-releases",
            ],
        )

    def test_unchanged_full_deployment_does_not_activate_restart_restore_or_flash(self) -> None:
        deployment, context, _runner, target = self._deployment(unchanged=True)
        try:
            receipt = DeployCoordinator().run(context, deployment.steps())
        finally:
            deployment.close()

        by_id = {step.step_id: step for step in receipt.steps}
        self.assertEqual(receipt.outcome, "success")
        self.assertEqual(by_id["receiver.firmware_build"].outcome, "skipped")
        self.assertEqual(by_id["receiver.firmware_flash"].outcome, "skipped")
        self.assertEqual(by_id["app.activate"].outcome, "skipped")
        self.assertEqual(by_id["host.restart"].outcome, "skipped")
        self.assertEqual(by_id["state.restore"].outcome, "skipped")
        commands = [command for command, _args in target.calls]
        self.assertNotIn("restart", commands)
        self.assertNotIn("restore-state", commands)
        self.assertEqual(commands.count("activate"), 1)
        self.assertIn("health", commands, "fresh external health must never be cached")

    def test_firmware_mutation_restarts_and_restores_even_when_app_is_unchanged(self) -> None:
        deployment, context, _runner, target = self._deployment(
            unchanged=True, firmware_changed=True
        )
        try:
            receipt = DeployCoordinator().run(context, deployment.steps())
        finally:
            deployment.close()

        by_id = {step.step_id: step for step in receipt.steps}
        self.assertEqual(receipt.outcome, "success")
        self.assertEqual(by_id["receiver.firmware_flash"].outcome, "executed")
        self.assertEqual(by_id["app.activate"].outcome, "skipped")
        self.assertEqual(by_id["host.restart"].outcome, "executed")
        self.assertEqual(by_id["state.restore"].outcome, "executed")
        commands = [command for command, _args in target.calls]
        self.assertIn("restart", commands)
        self.assertIn("restore-state", commands)

    def test_firmware_flash_passes_only_strict_build_selection(self) -> None:
        deployment, context, _runner, target = self._deployment()
        context.state.update(
            {
                "support_id": target.support,
                "release_id": target.candidate,
                "firmware_selection": {
                    "firmware_environment": PRODUCTION_FIRMWARE_ENVIRONMENT,
                    "receiver_hybrid_config_digest": ROLLOUT_CONFIG_DIGEST,
                    "firmware_sha256": FIRMWARE_SHA256,
                    "firmware_installation_digest": FIRMWARE_INSTALLATION_DIGEST,
                },
            },
        )
        try:
            result = deployment._firmware_flash(context)
        finally:
            deployment.close()

        self.assertEqual(result.outcome, "skipped")
        self.assertEqual(
            target.calls[-1],
            (
                "flash-firmware",
                (
                    target.support,
                    "--expected-environment",
                    PRODUCTION_FIRMWARE_ENVIRONMENT,
                    "--expected-config-digest",
                    ROLLOUT_CONFIG_DIGEST,
                    "--expected-installation-digest",
                    FIRMWARE_INSTALLATION_DIGEST,
                ),
            ),
        )

    def test_forced_firmware_flag_reaches_candidate_target_helper(self) -> None:
        deployment, context, _runner, target = self._deployment(force_firmware=True)
        context.state.update(
            {
                "support_id": target.support,
                "release_id": target.candidate,
                "firmware_selection": {
                    "firmware_environment": PRODUCTION_FIRMWARE_ENVIRONMENT,
                    "receiver_hybrid_config_digest": ROLLOUT_CONFIG_DIGEST,
                    "firmware_sha256": FIRMWARE_SHA256,
                    "firmware_installation_digest": FIRMWARE_INSTALLATION_DIGEST,
                },
            },
        )
        try:
            deployment._firmware_flash(context)
        finally:
            deployment.close()

        self.assertEqual(target.calls[-1][0], "flash-firmware")
        self.assertEqual(target.calls[-1][1][-1], "--force")


class _RecordingReceiptSink:
    def __init__(self, location: str) -> None:
        self.location = location
        self.receipts = []

    def persist(self, receipt, _redactor):
        self.receipts.append(receipt)
        return f"{self.location}/{receipt.deployment_id}.json"


class _RollbackTarget:
    def __init__(self) -> None:
        self.requested = "a" * 64
        self.previous = "b" * 64
        self.current = self.previous
        self._helper_path = "fake-root/current/tools/deployment/deploy_target.py"
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    @property
    def incoming(self) -> str:
        return "fake-root/.incoming/rollback-integration"

    def run(self, command: str, *args: str):
        self.calls.append((command, args))
        if command == "inspect":
            return {
                "current_release": self.current,
                "releases": [self.requested, self.previous],
            }
        if command == "validate-app":
            return {"release_id": args[0], "digest": args[0]}
        if command == "capture-state":
            return {"captured": True}
        if command == "current-release":
            return {"current_release": self.current}
        if command == "activate":
            before = self.current
            self.current = args[0]
            return {
                "release_id": args[0],
                "previous_release": before,
                "changed": before != args[0],
                "selected_at": 99.0,
            }
        if command == "restart":
            return {"restart_started_at": 100.0}
        if command == "restore-state":
            return {"restored": True}
        if command == "health":
            return {
                "desired_release": args[0],
                "observed_release": args[0],
                "stable_samples": 2,
                "last_controller_updated_at": 101.0,
            }
        if command == "complete-legacy-bootstrap":
            return {"outcome": "skipped", "reason": "no legacy bootstrap lifecycle"}
        if command == "record-deploy":
            return {"recorded": True}
        if command == "prune-releases":
            return {"outcome": "skipped", "retain": int(args[1]), "removed_releases": []}
        if command == "cleanup-snapshot":
            return {"removed": True}
        raise AssertionError(f"unexpected rollback target command: {command} {args}")


class RollbackEntrypointIntegrationTests(unittest.TestCase):
    def _rollback(self):
        target = _RollbackTarget()
        local_receipts = _RecordingReceiptSink("local")
        remote_receipts = _RecordingReceiptSink("remote")
        runner = _Runner()
        context = DeployContext(
            target="fake@wall",
            mode="rollback",
            source_identity={
                "operation": "app_rollback",
                "requested_release": target.requested,
            },
            source_policy="clean",
            ssh_runner=runner,
            receipt_sinks=(local_receipts, remote_receipts),
            attempt_id="rollback-integration",
        )
        config = deploy_entrypoint.DeploymentConfig(
            root=Path.cwd(),
            mode="python",
            policy="clean",
            target="fake@wall",
            deploy_dir="fake-root",
            run_tests=False,
            generate_previews=False,
            health_timeout=1.0,
        )
        rollback = deploy_entrypoint.CoordinatorRollback(
            config, context, target.requested,
        )
        helper = rollback.target.helper
        rollback.target = target
        return rollback, context, target, (local_receipts, remote_receipts), helper, runner

    def test_rollback_runs_exact_app_only_order_with_fresh_release_health(self) -> None:
        rollback, context, target, sinks, helper, runner = self._rollback()

        try:
            receipt = DeployCoordinator().run(context, rollback.steps())
        finally:
            rollback.close()

        self.assertEqual(receipt.outcome, "success")
        self.assertEqual(receipt.mode, "rollback")
        self.assertEqual(
            [step.step_id for step in receipt.steps],
            [item[0] for item in ROLLBACK_STEP_ORDER],
        )
        self.assertEqual(
            [command for command, _args in target.calls],
            [
                "inspect",
                "validate-app",
                "capture-state",
                "current-release",
                "activate",
                "restart",
                "restore-state",
                "health",
                "complete-legacy-bootstrap",
                "record-deploy",
                "prune-releases",
            ],
        )
        forbidden = {
            "stage-support",
            "stage-app",
            "build-firmware",
            "provision",
            "flash-firmware",
            "reboot",
        }
        self.assertTrue(forbidden.isdisjoint(command for command, _args in target.calls))
        self.assertEqual(helper, "fake-root/current/tools/deployment/deploy_target.py")
        self.assertEqual(runner.calls, [], "rollback has no target-connect/provision commands")
        self.assertFalse(
            any("reboot" in argument for command, _kwargs in runner.calls for argument in command)
        )

        health_call = next(call for call in target.calls if call[0] == "health")
        self.assertEqual(
            health_call[1],
            (
                target.requested,
                "--boundary",
                "100.0",
                "--strips",
                "33",
                "--leds-per-strip",
                "138",
                "--receivers",
                "5",
                "--timeout",
                "1.0",
            ),
        )
        self.assertEqual(receipt.health["observed_release"], target.requested)
        self.assertEqual(receipt.health["desired_release"], target.requested)
        self.assertTrue(receipt.health["deployment_status"]["recorded"])
        for sink in sinks:
            self.assertEqual(sink.receipts, [receipt])

    def test_pinned_rollback_helper_bundle_executes_without_repository_imports(self) -> None:
        with _writable_temporary_directory() as temporary:
            target_root = temporary / "target"
            source_a = temporary / "a.txt"
            source_b = temporary / "b.txt"
            source_a.write_text("release a\n", encoding="utf-8")
            source_b.write_text("release b\n", encoding="utf-8")
            manager = AppReleaseManager(target_root)
            release_a = manager.stage({"payload.txt": source_a})
            release_b = manager.stage({"payload.txt": source_b})
            manager.activate(release_a.id)

            bundle = temporary / "pinned-helper"
            bundle.mkdir()
            for name in deploy_entrypoint.ROLLBACK_HELPER_FILENAMES:
                shutil.copy2(ROOT / "tools" / "deployment" / name, bundle / name)

            helper = bundle / "deploy_target.py"

            def isolated(*args: str) -> dict[str, object]:
                completed = subprocess.run(
                    (
                        sys.executable,
                        os.fspath(helper),
                        "--root",
                        os.fspath(target_root),
                        *args,
                    ),
                    cwd=temporary,
                    env={
                        **os.environ,
                        "PYTHONPATH": os.fspath(temporary / "missing"),
                        "PYTHONNOUSERSITE": "1",
                    },
                    check=False,
                    text=True,
                    capture_output=True,
                )
                if completed.returncode:
                    self.fail(
                        "isolated rollback helper failed:\n"
                        + completed.stdout
                        + completed.stderr
                    )
                return json.loads(completed.stdout)

            inspected = isolated("inspect")
            self.assertEqual(inspected["current_release"], release_a.id)
            self.assertEqual(set(inspected["releases"]), {release_a.id, release_b.id})
            activated = isolated("activate", release_b.id)
            self.assertEqual(activated["previous_release"], release_a.id)
            self.assertEqual(activated["release_id"], release_b.id)
            self.assertEqual(isolated("current-release")["current_release"], release_b.id)

            # Exercise the coordinator's actual pin command construction too;
            # the isolated bundle above is exactly the file set copied here.
            target = _RollbackTarget()
            runner = _Runner()
            context = DeployContext(
                target="fake@wall",
                mode="rollback",
                source_identity={"operation": "app_rollback"},
                ssh_runner=runner,
                attempt_id="pin-bundle",
            )
            config = deploy_entrypoint.DeploymentConfig(
                root=ROOT,
                mode="python",
                policy="clean",
                target="fake@wall",
                deploy_dir="fake-root",
                run_tests=False,
                generate_previews=False,
            )
            rollback = deploy_entrypoint.CoordinatorRollback(
                config, context, target.requested
            )
            with patch.object(
                deploy_entrypoint.CoordinatorDeployment,
                "_capture",
                return_value=deploy_entrypoint.OperationResult(outcome="skipped"),
            ):
                rollback._capture(context)
            copied = [call[0] for call in runner.calls if call[0][0] == "cp"]
            self.assertEqual(
                tuple(Path(path).name for path in copied[0][1:-1]),
                deploy_entrypoint.ROLLBACK_LEGACY_HELPER_FILENAMES,
            )
            self.assertEqual(
                tuple(Path(call[1]).name for call in copied[1:]),
                deploy_entrypoint.ROLLBACK_OPTIONAL_HELPER_FILENAMES,
            )
            rollback._helper_pinned = False

    def test_pinned_rollback_helper_accepts_pre_artifact_inspector_release(self) -> None:
        class _PreChangeRunner(_Runner):
            def run(self, args, **kwargs):
                normalized = tuple(os.fspath(arg) for arg in args)
                self.calls.append((normalized, kwargs))
                missing = (
                    normalized[0] == "test"
                    and Path(normalized[-1]).name == "firmware_artifacts.py"
                )
                return CommandResult(normalized, 1 if missing else 0, "", "", 0.01)

        with _writable_temporary_directory() as temporary:
            target_root = temporary / "target"
            payload = temporary / "payload.txt"
            payload.write_text("legacy release\n", encoding="utf-8")
            release = AppReleaseManager(target_root).stage({"payload.txt": payload})

            bundle = temporary / "legacy-pinned-helper"
            bundle.mkdir()
            current_helper = (
                ROOT / "tools" / "deployment" / "deploy_target.py"
            ).read_text(encoding="utf-8")
            package_import = (
                "    from tools.deployment.firmware_artifacts import "
                "inspect_firmware_installation\n"
            )
            direct_import = (
                "    from firmware_artifacts import inspect_firmware_installation "
                " # type: ignore[no-redef]\n"
            )
            self.assertIn(package_import, current_helper)
            self.assertIn(direct_import, current_helper)
            # This fixture models the immediately preceding target helper: it
            # has the hybrid-config dependency but predates firmware_artifacts.
            (bundle / "deploy_target.py").write_text(
                current_helper.replace(package_import, "").replace(direct_import, ""),
                encoding="utf-8",
            )
            for name in (
                "app_releases.py",
                "deploy_coordinator.py",
                "receiver_hybrid_config.py",
            ):
                shutil.copy2(ROOT / "tools" / "deployment" / name, bundle / name)
            self.assertFalse((bundle / "firmware_artifacts.py").exists())

            completed = subprocess.run(
                (
                    sys.executable,
                    os.fspath(bundle / "deploy_target.py"),
                    "--root",
                    os.fspath(target_root),
                    "inspect",
                ),
                cwd=temporary,
                env={
                    **os.environ,
                    "PYTHONPATH": os.fspath(temporary / "missing"),
                    "PYTHONNOUSERSITE": "1",
                },
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["releases"], [release.id])

            target = _RollbackTarget()
            runner = _PreChangeRunner()
            context = DeployContext(
                target="fake@wall",
                mode="rollback",
                source_identity={"operation": "app_rollback"},
                ssh_runner=runner,
                attempt_id="legacy-pin-bundle",
            )
            config = deploy_entrypoint.DeploymentConfig(
                root=ROOT,
                mode="python",
                policy="clean",
                target="fake@wall",
                deploy_dir="fake-root",
                run_tests=False,
                generate_previews=False,
            )
            rollback = deploy_entrypoint.CoordinatorRollback(
                config, context, target.requested
            )
            with patch.object(
                deploy_entrypoint.CoordinatorDeployment,
                "_capture",
                return_value=deploy_entrypoint.OperationResult(outcome="skipped"),
            ):
                rollback._capture(context)
            copied_names = {
                Path(call[0][1]).name
                for call in runner.calls
                if call[0][0] == "cp" and len(call[0]) == 3
            }
            self.assertIn("receiver_hybrid_config.py", copied_names)
            self.assertNotIn("firmware_artifacts.py", copied_names)
            rollback._helper_pinned = False


class _CompensationTarget:
    def __init__(self, fail_command: str) -> None:
        self.fail_command = fail_command
        self.failed = False
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(self, command: str, *args: str):
        self.calls.append((command, args))
        should_fail = command == self.fail_command and not self.failed
        if should_fail:
            self.failed = True
            raise RuntimeError(f"injected {command} failure")
        if command == "activate":
            return {"changed": True, "release_id": args[0]}
        if command == "restart":
            return {"restart_started_at": 202.0}
        if command == "restore-state":
            return {"restored": True}
        if command == "health":
            return {"desired_release": args[0], "observed_release": args[0]}
        if command == "complete-legacy-bootstrap":
            return {"outcome": "skipped", "reason": "no legacy bootstrap lifecycle"}
        if command == "record-deploy":
            return {"recorded": True}
        raise AssertionError(command)


class _BootstrapCompensationTarget:
    def __init__(self, fail_command: str, *, resumed: bool = False) -> None:
        self.fail_command = fail_command
        self.failed = False
        self.bootstrap = "b" * 64
        self.candidate = "c" * 64
        self.current = self.candidate if resumed else None
        self.phase = "candidate_pending" if resumed else "none"
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(self, command: str, *args: str):
        self.calls.append((command, args))
        if command == "bootstrap-legacy-app":
            if self.phase == "none":
                self.current = self.bootstrap
                self.phase = "selected"
                return {
                    "outcome": "executed",
                    "selected": True,
                    "bootstrap_release_id": self.bootstrap,
                    "bootstrap_digest": self.bootstrap,
                    "recovery_release": None,
                }
            return {
                "outcome": "skipped",
                "selected": self.current == self.bootstrap,
                "bootstrap_release_id": self.bootstrap,
                "bootstrap_digest": self.bootstrap,
                "recovery_release": (
                    self.bootstrap if self.phase == "candidate_pending" else None
                ),
            }
        if command == "current-release":
            return {"current_release": self.current}
        if command == "activate":
            before = self.current
            self.current = args[0]
            self.phase = (
                "candidate_pending"
                if self.current == self.candidate else "selected"
            )
            return {
                "changed": before != self.current,
                "release_id": self.current,
                "previous_release": before,
                "selected_at": 101.0,
            }
        should_fail = command == self.fail_command and not self.failed
        if should_fail:
            self.failed = True
            raise RuntimeError(f"injected {command} failure")
        if command == "restart":
            return {"restart_started_at": 102.0}
        if command == "restore-state":
            return {"restored": True}
        if command == "health":
            return {"desired_release": args[0], "observed_release": args[0]}
        if command == "complete-legacy-bootstrap":
            self.phase = "complete"
            return {"outcome": "executed", "phase": "complete"}
        if command == "record-deploy":
            return {"recorded": True}
        raise AssertionError(command)


class PostActivationCompensationTests(unittest.TestCase):
    def _deployment(self, fail_command: str):
        context = DeployContext(
            target="fake@wall",
            mode="rollback",
            source_identity={},
            attempt_id=f"fail-{fail_command}",
        )
        config = deploy_entrypoint.DeploymentConfig(
            root=Path.cwd(),
            mode="python",
            policy="dirty",
            target="fake@wall",
            health_timeout=1.0,
        )
        deployment = deploy_entrypoint.CoordinatorRollback(
            config, context, "c" * 64,
        )
        context.state.update(
            {
                "previous_release": "b" * 64,
                "activated": True,
                "state_captured": True,
                "acceptance_boundary": 100.0,
                "firmware_selection": {
                    "receiver_health_contract": {"schema_version": 1},
                },
            }
        )
        target = _CompensationTarget(fail_command)
        deployment.target = target
        return deployment, context, target

    def test_every_post_activation_failure_boundary_restores_and_health_checks_prior(self) -> None:
        cases = {
            "restart": lambda deployment, context: deployment._restart(context),
            "restore-state": lambda deployment, context: deployment._restore(context),
            "health": lambda deployment, context: deployment._health(context),
            "record-deploy": lambda deployment, context: deployment._health(context),
        }
        for failed_command, execute in cases.items():
            with self.subTest(failed_command=failed_command):
                deployment, context, target = self._deployment(failed_command)
                with self.assertRaises(deploy_entrypoint.RemoteActivationFailed) as caught:
                    execute(deployment, context)

                failure = caught.exception.failure
                self.assertTrue(failure.restored)
                self.assertEqual(failure.candidate_release, "c" * 64)
                self.assertEqual(failure.previous_release, "b" * 64)
                self.assertIn(f"injected {failed_command} failure", failure.candidate_error)
                self.assertEqual(context.state["compensation"], failure)
                self.assertEqual(
                    [command for command, _args in target.calls[-4:]],
                    ["activate", "restart", "restore-state", "health"],
                )
                self.assertEqual(target.calls[-4][1], ("b" * 64,))
                self.assertEqual(target.calls[-1][1][0], "b" * 64)
                self.assertNotIn(
                    "--receiver-contract-json", target.calls[-1][1]
                )

    def test_first_cutover_restart_restore_and_health_failures_restore_bootstrap(self) -> None:
        cases = {
            "restart": lambda deployment, context: deployment._restart(context),
            "restore-state": lambda deployment, context: deployment._restore(context),
            "health": lambda deployment, context: deployment._health(context),
        }
        for failed_command, execute in cases.items():
            with self.subTest(failed_command=failed_command):
                context = DeployContext(
                    target="fake@wall",
                    mode="full",
                    source_identity={},
                    attempt_id=f"bootstrap-{failed_command}",
                )
                config = deploy_entrypoint.DeploymentConfig(
                    root=Path.cwd(), mode="python", policy="dirty",
                    target="fake@wall", health_timeout=1.0,
                )
                deployment = deploy_entrypoint.CoordinatorRollback(
                    config, context, "c" * 64
                )
                context.state.update({
                    "release_id": "c" * 64,
                    "state_captured": True,
                })
                target = _BootstrapCompensationTarget(failed_command)
                deployment.target = target

                bootstrap = deployment._bootstrap_legacy(context)
                self.assertEqual(
                    bootstrap.artifacts[0].kind, "legacy_app_bootstrap"
                )
                deployment._activate(context)
                context.state["acceptance_boundary"] = 101.0
                with self.assertRaises(
                    deploy_entrypoint.RemoteActivationFailed
                ) as caught:
                    execute(deployment, context)

                self.assertTrue(caught.exception.failure.restored)
                self.assertEqual(
                    caught.exception.failure.previous_release, target.bootstrap
                )
                self.assertEqual(target.current, target.bootstrap)

    def test_interrupted_candidate_resume_retains_bootstrap_compensation(self) -> None:
        context = DeployContext(
            target="fake@wall", mode="full", source_identity={},
            attempt_id="bootstrap-resume",
        )
        config = deploy_entrypoint.DeploymentConfig(
            root=Path.cwd(), mode="python", policy="dirty",
            target="fake@wall", health_timeout=1.0,
        )
        deployment = deploy_entrypoint.CoordinatorRollback(
            config, context, "c" * 64
        )
        context.state.update({
            "release_id": "c" * 64,
            "state_captured": True,
            "acceptance_boundary": 101.0,
        })
        target = _BootstrapCompensationTarget("health", resumed=True)
        deployment.target = target

        deployment._bootstrap_legacy(context)
        activation = deployment._activate(context)
        self.assertTrue(activation.details["recovery_guarded"])
        self.assertTrue(context.state["activated"])
        with self.assertRaises(deploy_entrypoint.RemoteActivationFailed) as caught:
            deployment._health(context)

        self.assertTrue(caught.exception.failure.restored)
        self.assertEqual(target.current, target.bootstrap)


class EntrypointReceiptExitTests(unittest.TestCase):
    @staticmethod
    def _receipt(outcome: str, persistence_errors=()):
        return SimpleNamespace(
            outcome=outcome,
            persistence_errors=tuple(persistence_errors),
            to_dict=lambda _redactor: {
                "schema_version": 1,
                "outcome": outcome,
            },
        )

    def test_run_cli_requires_successful_operations_and_both_receipt_sinks(self) -> None:
        cases = (
            (self._receipt("success"), 0, ""),
            (self._receipt("failure"), 1, ""),
            (self._receipt("success", ("remote receipt unavailable",)), 2, "paired receipt"),
        )
        for receipt, expected_status, expected_error in cases:
            with self.subTest(status=expected_status):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch.object(deploy_entrypoint, "run_deployment", return_value=receipt),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    status = deploy_entrypoint.main(
                        (
                            "run",
                            "--mode",
                            "python",
                            "--policy",
                            "dirty",
                            "--skip-tests",
                            "--skip-previews",
                        )
                    )
                self.assertEqual(status, expected_status)
                self.assertEqual(json.loads(stdout.getvalue())["outcome"], receipt.outcome)
                self.assertIn(expected_error, stderr.getvalue())

    def test_rollback_cli_requires_successful_operations_and_both_receipt_sinks(self) -> None:
        release_id = "a" * 64
        cases = (
            (self._receipt("success"), 0, ""),
            (self._receipt("failure"), 1, ""),
            (self._receipt("success", ("remote receipt unavailable",)), 2, "paired receipt"),
        )
        for receipt, expected_status, expected_error in cases:
            with self.subTest(status=expected_status):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch.object(
                        deploy_entrypoint, "run_rollback", return_value=receipt,
                    ) as run_rollback,
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    status = deploy_entrypoint.main(
                        (
                            "rollback",
                            release_id,
                        )
                    )
                self.assertEqual(status, expected_status)
                self.assertEqual(json.loads(stdout.getvalue())["outcome"], receipt.outcome)
                self.assertIn(expected_error, stderr.getvalue())
                self.assertEqual(run_rollback.call_args.args[1], release_id)

    def test_context_has_local_and_target_append_only_receipt_sinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config = deploy_entrypoint.DeploymentConfig(
                root=root,
                mode="python",
                policy="dirty",
                target="fake@wall",
                deploy_dir="target-root",
                local_receipts=Path("local-receipts"),
            )
            context = deploy_entrypoint._context(config)

            self.assertEqual(len(context.receipt_sinks), 2)
            self.assertIsInstance(context.receipt_sinks[0], AtomicJSONReceiptStore)
            self.assertIsInstance(context.receipt_sinks[1], SSHAtomicJSONReceiptStore)
            self.assertEqual(context.receipt_sinks[0].directory, root / "local-receipts")
            self.assertEqual(
                context.receipt_sinks[1].directory,
                "target-root/run_state/deploy_receipts",
            )


class DedicatedSSHKeyTests(unittest.TestCase):
    def test_ssh_key_env_resolves_from_root_and_disables_agent_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            key = root / "keys" / "agent key"
            key.parent.mkdir()
            key.write_text("private-key-fixture\n", encoding="utf-8")
            key.chmod(0o600)
            with patch.dict(os.environ, {"SSH_KEY": "keys/agent key"}):
                args = deploy_entrypoint._parser().parse_args(
                    ("run", "--mode", "python", "--root", os.fspath(root))
                )
                config = deploy_entrypoint._config(args)

            self.assertEqual(
                config.ssh_options,
                (
                    *deploy_entrypoint.DEFAULT_SSH_OPTIONS,
                    "-i", os.fspath(key.resolve()),
                    "-o", "IdentitiesOnly=yes",
                ),
            )
            context = deploy_entrypoint._context(config)
            self.assertEqual(context.ssh_runner.ssh_options, config.ssh_options)
            self.assertEqual(
                deploy_entrypoint._rsync_ssh_command(config.ssh_options),
                "ssh -o BatchMode=yes -o ConnectTimeout=10 "
                "-o StrictHostKeyChecking=accept-new -i "
                f"'{key.resolve()}' -o IdentitiesOnly=yes",
            )

    def test_unset_ssh_key_preserves_default_openssh_behavior(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            args = deploy_entrypoint._parser().parse_args(
                ("plan", "--mode", "full", "--root", os.fspath(Path.cwd()))
            )
            config = deploy_entrypoint._config(args)
        self.assertEqual(config.ssh_options, deploy_entrypoint.DEFAULT_SSH_OPTIONS)
        self.assertNotIn("IdentitiesOnly=yes", config.ssh_options)

    def test_missing_or_insecure_ssh_key_fails_before_target_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            with self.assertRaisesRegex(ValueError, "does not exist"):
                deploy_entrypoint._ssh_options(root, "missing-key")

            key = root / "open-key"
            key.write_text("private-key-fixture\n", encoding="utf-8")
            key.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "permissions"):
                deploy_entrypoint._ssh_options(root, os.fspath(key))


if __name__ == "__main__":
    unittest.main()
