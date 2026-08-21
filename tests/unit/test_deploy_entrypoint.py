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
import unittest
from unittest.mock import patch

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
    PRODUCTION_FIRMWARE_ENVIRONMENT,
    write_receiver_hybrid_config,
)


ROOT = Path(__file__).resolve().parents[2]
FIRMWARE_SHA256 = "e" * 64
ROLLOUT_CONFIG_DIGEST = "f" * 64
FIRMWARE_INSTALLATION_DIGEST = "a" * 64


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
        if command == "build-firmware":
            return {
                "outcome": "skipped",
                "reason": "firmware build already exists",
                "firmware_environment": PRODUCTION_FIRMWARE_ENVIRONMENT,
                "firmware_sha256": FIRMWARE_SHA256,
                "firmware_installation_digest": FIRMWARE_INSTALLATION_DIGEST,
                "receiver_hybrid_config_digest": ROLLOUT_CONFIG_DIGEST,
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


class TargetProvisioningTests(unittest.TestCase):
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
        ensure_unit.assert_called_once_with(root, user="ledgridwall")


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

    def test_enabled_degraded_policy_builds_only_allowlisted_canary_environment(self) -> None:
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

    def test_flash_rejects_rollout_config_drift_before_port_or_helper_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            with (
                patch.object(deploy_target, "_copy_support_workspace") as workspace,
                patch.object(deploy_target.glob, "glob") as discover,
                patch.object(deploy_target, "_command") as command,
                self.assertRaisesRegex(RuntimeError, "selection changed"),
            ):
                deploy_target.flash_firmware(
                    root,
                    "a" * 64,
                    receiver_count=4,
                    debug=False,
                    expected_firmware_environment=(
                        DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT
                    ),
                )
            workspace.assert_not_called()
            discover.assert_not_called()
            command.assert_not_called()


class TargetFirmwareFailureTests(unittest.TestCase):
    @staticmethod
    def _production_binary(workspace: Path, content: bytes = b"production") -> Path:
        return _write_firmware_artifacts(
            workspace / "firmware" / "esp32",
            PRODUCTION_FIRMWARE_ENVIRONMENT,
            application=content,
        )

    def test_serial_flash_reuses_build_phase_scons_state_and_exact_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            binary = self._production_binary(workspace)
            helper = root / "current/tools/deployment/flash_esp32.sh"
            helper.parent.mkdir(parents=True)
            helper.write_text("#!/bin/bash\n", encoding="utf-8")
            ports = [f"/dev/ttyACM{index}" for index in range(4)]

            def command(args, **kwargs):
                (root / ".esp32_firmware_hash").write_text(
                    kwargs["env"]["EXPECTED_FIRMWARE_INSTALLATION_DIGEST"] + "\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(
                    args, 0, "Flashed all receivers", "",
                )

            with (
                patch.dict(
                    os.environ,
                    {"PLATFORMIO_BUILD_CACHE_DIR": "/inherited/shared-cache"},
                ),
                patch.object(
                    deploy_target, "_copy_support_workspace", return_value=(workspace, True),
                ),
                patch.object(deploy_target.glob, "glob", side_effect=(ports, [])),
                patch.object(
                    deploy_target,
                    "_command",
                    side_effect=command,
                ) as command,
            ):
                result = deploy_target.flash_firmware(
                    root, "a" * 64, receiver_count=4, debug=False,
                )

            self.assertEqual(result["outcome"], "executed")
            self.assertEqual(result["firmware_sha256"], deploy_target._sha256_file(binary))
            flash_env = command.call_args.kwargs["env"]
            self.assertEqual(
                result["firmware_installation_digest"],
                flash_env["EXPECTED_FIRMWARE_INSTALLATION_DIGEST"],
            )
            self.assertEqual(flash_env["FIRMWARE_PREBUILT"], "1")
            self.assertEqual(
                flash_env["FIRMWARE_ENVIRONMENT"],
                PRODUCTION_FIRMWARE_ENVIRONMENT,
            )
            self.assertEqual(
                flash_env["EXPECTED_FIRMWARE_SHA256"],
                deploy_target._sha256_file(binary),
            )
            self.assertRegex(
                flash_env["EXPECTED_FIRMWARE_INSTALLATION_DIGEST"],
                r"^[0-9a-f]{64}$",
            )
            self.assertEqual(
                flash_env["EXPECTED_FIRMWARE_HASH_FILE"],
                os.fspath(root / ".esp32_firmware_hash"),
            )
            self.assertEqual(
                flash_env["PLATFORMIO_BUILD_CACHE_DIR"],
                os.fspath(root / "build/firmware/.platformio-build-cache"),
            )
            self.assertEqual(flash_env["IDF_CCACHE_ENABLE"], "1")
            self.assertEqual(
                flash_env["CCACHE_DIR"],
                os.fspath(root / "build/firmware/.ccache"),
            )

    def test_flash_failure_exit_or_failure_marker_never_becomes_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            self._production_binary(workspace)
            helper = root / "current/tools/deployment/flash_esp32.sh"
            helper.parent.mkdir(parents=True)
            helper.write_text("#!/bin/bash\n", encoding="utf-8")
            ports = [f"/dev/ttyACM{index}" for index in range(4)]
            failures = (
                subprocess.CompletedProcess(("bash",), 1, "serial failed", ""),
                subprocess.CompletedProcess(
                    ("bash",), 0, "Some devices failed; hash NOT updated", "",
                ),
            )
            for completed in failures:
                with (
                    self.subTest(returncode=completed.returncode),
                    patch.object(
                        deploy_target, "_copy_support_workspace", return_value=(workspace, True),
                    ),
                    patch.object(deploy_target.glob, "glob", side_effect=(ports, [])),
                    patch.object(deploy_target, "_command", return_value=completed),
                    self.assertRaisesRegex(RuntimeError, "flash failed"),
                ):
                    deploy_target.flash_firmware(
                        root, "a" * 64, receiver_count=4, debug=False,
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
                    root, "a" * 64, receiver_count=4, debug=False,
                )
            command.assert_not_called()

    def test_flash_success_cannot_hide_deleted_or_changed_validated_binary(self) -> None:
        for mutation, expected in (
            ("delete", "installation disappeared during flash"),
            ("change", "installation changed during flash"),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary_dir:
                root = Path(temporary_dir)
                workspace = root / "workspace"
                workspace.mkdir()
                binary = self._production_binary(workspace)
                helper = root / "current/tools/deployment/flash_esp32.sh"
                helper.parent.mkdir(parents=True)
                helper.write_text("#!/bin/bash\n", encoding="utf-8")
                ports = [f"/dev/ttyACM{index}" for index in range(4)]

                def command(args, **_kwargs):
                    if tuple(args[:3]) == ("sudo", "systemctl", "stop"):
                        return subprocess.CompletedProcess(args, 0, "", "")
                    if mutation == "delete":
                        binary.unlink()
                    else:
                        binary.write_bytes(b"different firmware")
                    return subprocess.CompletedProcess(args, 0, "All flashed\n", "")

                with (
                    patch.object(
                        deploy_target, "_copy_support_workspace",
                        return_value=(workspace, True),
                    ),
                    patch.object(deploy_target.glob, "glob", side_effect=(ports, [])),
                    patch.object(deploy_target, "_command", side_effect=command),
                    self.assertRaisesRegex(RuntimeError, expected),
                ):
                    deploy_target.flash_firmware(
                        root, "a" * 64, receiver_count=4, debug=False,
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
        self, *, unchanged: bool = False, firmware_changed: bool = False
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
                "record-deploy",
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

    def test_firmware_flash_uses_candidate_app_release_helper(self) -> None:
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
                    "--app-release",
                    target.candidate,
                    "--receivers",
                    "4",
                    "--expected-environment",
                    PRODUCTION_FIRMWARE_ENVIRONMENT,
                    "--expected-config-digest",
                    ROLLOUT_CONFIG_DIGEST,
                    "--expected-installation-digest",
                    FIRMWARE_INSTALLATION_DIGEST,
                ),
            ),
        )


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
                "32",
                "--leds-per-strip",
                "138",
                "--receivers",
                "4",
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
