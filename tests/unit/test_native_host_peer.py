from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import unittest
from unittest import mock

from animation.native.aurora import canonical_palette_roles
from animation.native.builder import build_plugin
from animation.native.constants import TARGET_COMPILER_NAME, TARGET_TOOLCHAIN_PACKAGE
from animation.native.host_peer import build_host_peer, validate_host_peer
from animation.native.managed_preview import ManagedNativeHostPreview
from animation.native.errors import NativeBuildError, NativePreviewError
from animation.core.native_background_library import NativeBackgroundLibrary
from tools.deployment import deploy_target
from tools.deployment.deploy_manifest import manifest_plan


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ID = "native_aurora"
DIGEST = "89b5ae10d2897f0977713b2a575e32b348398a4b86512372d25edd13581b07e4"


@unittest.skipUnless(shutil.which("c++"), "host C++ compiler unavailable")
class NativeHostPeerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = (
            ROOT
            / "run_state/native_background_builds"
            / PLUGIN_ID
            / DIGEST
            / "bundle.zip"
        )
        if not cls.bundle.is_file():
            package = Path.home() / ".platformio/packages" / TARGET_TOOLCHAIN_PACKAGE
            if (
                shutil.which("platformio") is None
                or not (package / "bin" / TARGET_COMPILER_NAME).is_file()
            ):
                raise unittest.SkipTest(
                    "exact managed bundle and pinned target toolchain unavailable"
                )
            result = build_plugin(
                ROOT, PLUGIN_ID, ROOT / "run_state/native_background_builds"
            )
            cls.bundle = Path(result.bundle_path)
        cls.digest = hashlib.sha256(cls.bundle.read_bytes()).hexdigest()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="native-host-peer-")
        self.base = Path(self.temporary.name).resolve()
        self.source = self.base / "source"
        for relative in (
            f"animation/plugins/{PLUGIN_ID}/manifest.json",
            f"animation/plugins/{PLUGIN_ID}/native/background.cpp",
            "firmware/esp32/include/ledgrid/native_background_abi_v2.h",
        ):
            destination = self.source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        self.output = self.base / "run_state/native_background_builds"

    def tearDown(self) -> None:
        for path in self.base.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o755)
            elif path.is_file():
                path.chmod(0o644)
        self.temporary.cleanup()

    def _build(self):
        return build_host_peer(
            self.source,
            self.bundle,
            self.output,
            plugin_id=PLUGIN_ID,
            bundle_digest=self.digest,
        )

    def _seal_source_as_snapshot(self) -> None:
        deploy = self.source / ".deploy"
        deploy.mkdir()
        native_manifest = deploy / "native-build-manifest.json"
        native_manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "files": [f"animation/plugins/{PLUGIN_ID}/native/background.cpp"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        evidence = []
        metadata_path = deploy / "snapshot.json"
        for path in sorted(self.source.rglob("*")):
            if not path.is_file() or path == metadata_path:
                continue
            path.chmod(0o444)
            raw = path.read_bytes()
            evidence.append(
                {
                    "path": path.relative_to(self.source).as_posix(),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size": len(raw),
                    "executable": False,
                }
            )
        metadata = {
            "schema_version": 1,
            "source_identity": {"base_commit": "test", "scope": "fast"},
            "files": evidence,
        }
        metadata["snapshot_id"] = hashlib.sha256(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        metadata_path.write_text(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        metadata_path.chmod(0o444)

    def test_fast_deploy_snapshot_carries_both_exact_compile_inputs(self) -> None:
        selected = set(manifest_plan(ROOT, "fast").selected)
        self.assertIn(
            PurePosixPath(f"animation/plugins/{PLUGIN_ID}/native/background.cpp"),
            selected,
        )
        self.assertIn(
            PurePosixPath("firmware/esp32/include/ledgrid/native_background_abi_v2.h"),
            selected,
        )

    def test_clean_project_build_reuses_without_a_compiler_and_renders_exact_frame(
        self,
    ) -> None:
        first = self._build()
        self.assertFalse(first["reused"])
        with mock.patch(
            "animation.native.host_peer._platform_compiler",
            side_effect=AssertionError("cache reuse must not inspect the compiler"),
        ):
            second = self._build()
        self.assertTrue(second["reused"])

        project = self.base / "deploy/releases" / ("a" * 64)
        manifest = project / f"animation/plugins/{PLUGIN_ID}/manifest.json"
        manifest.parent.mkdir(parents=True)
        shutil.copyfile(
            self.source / f"animation/plugins/{PLUGIN_ID}/manifest.json", manifest
        )
        shared = self.base / "deploy/run_state"
        shared.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.base / "run_state", shared)
        (project / "run_state").symlink_to(Path("../../run_state"))

        preview = ManagedNativeHostPreview(project, PLUGIN_ID, self.digest)
        frame = preview.render(
            parameters={"gain": 0.72, "source_fps": 30.0, "seed": 8012},
            palette=tuple(canonical_palette_roles("mist").values()),
            scaled_scene_time=0.2,
            unscaled_scene_time=0.5,
            frame_index=0,
        )
        self.assertTrue(frame.changed)
        self.assertEqual(
            hashlib.sha256(frame.pixels.tobytes()).hexdigest(),
            "f21d5364471be230809e8cda7357725f2b6457d51aa7d42479d6f29129ccac78",
        )

    def test_source_platform_and_artifact_mismatches_fail_closed(self) -> None:
        self._build()
        directory = self.output / PLUGIN_ID / self.digest
        from animation.core.plugin_loader import AnimationPluginLoader

        loader = AnimationPluginLoader(str(self.source / "animation/plugins"))
        loader.scan_components()
        component = {
            key: value
            for key, value in loader.component_manifests[PLUGIN_ID].items()
            if not key.startswith("_")
        }

        source = self.source / f"animation/plugins/{PLUGIN_ID}/native/background.cpp"
        source.write_text(source.read_text() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(NativeBuildError, "source input is stale"):
            build_host_peer(
                self.source,
                self.bundle,
                self.base / "other",
                plugin_id=PLUGIN_ID,
                bundle_digest=self.digest,
            )

        receipt_path = directory / "receipt.json"
        receipt_path.chmod(0o644)
        receipt = json.loads(receipt_path.read_text())
        receipt["host"]["machine"] = "wrong-machine"
        receipt_path.write_text(
            json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        )
        receipt_path.chmod(0o444)
        with self.assertRaisesRegex(NativePreviewError, "incompatible"):
            validate_host_peer(
                directory,
                plugin_id=PLUGIN_ID,
                bundle_digest=self.digest,
                component_manifest=component,
            )

        receipt_path.chmod(0o644)
        receipt["host"]["machine"] = first_machine = os.uname().machine.lower()
        if first_machine == "arm64":
            receipt["host"]["machine"] = "aarch64"
        receipt_path.write_text(
            json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        )
        receipt_path.chmod(0o444)
        host = directory / "host-preview.so"
        host.chmod(0o644)
        raw = bytearray(host.read_bytes())
        raw[-1] ^= 1
        host.write_bytes(raw)
        host.chmod(0o444)
        with self.assertRaisesRegex(NativePreviewError, "identity"):
            validate_host_peer(
                directory,
                plugin_id=PLUGIN_ID,
                bundle_digest=self.digest,
                component_manifest=component,
            )
        corrupted_host = host.read_bytes()
        shutil.copyfile(
            ROOT / f"animation/plugins/{PLUGIN_ID}/native/background.cpp",
            source,
        )
        with self.assertRaisesRegex(NativePreviewError, "identity"):
            self._build()
        self.assertEqual(host.read_bytes(), corrupted_host)

    def test_target_primitive_publishes_only_the_shared_host_peer(self) -> None:
        target = self.base / "target"
        NativeBackgroundLibrary(target / "receiver_library/native_backgrounds").publish(
            self.bundle
        )
        runtime_python = target / "venv/bin/python"
        runtime_python.parent.mkdir(parents=True)
        invocation_marker = self.base / "runtime-python-used"
        runtime_python.write_text(
            f"#!{os.sys.executable}\n"
            "import os\n"
            "from pathlib import Path\n"
            "import sys\n"
            f"Path({os.fspath(invocation_marker)!r}).touch()\n"
            "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n",
            encoding="utf-8",
        )
        runtime_python.chmod(0o755)
        self._seal_source_as_snapshot()
        result = deploy_target.build_native_host_preview(
            target,
            self.source,
            plugin_id=PLUGIN_ID,
            bundle_digest=self.digest,
        )
        self.assertEqual(result["bundle_digest"], self.digest)
        self.assertFalse(result["reused"])
        self.assertTrue(invocation_marker.is_file())
        self.assertTrue(
            (
                target
                / "run_state/native_background_builds"
                / PLUGIN_ID
                / self.digest
                / "host-preview.so"
            ).is_file()
        )
        self.assertFalse((target / "current").exists())


if __name__ == "__main__":
    unittest.main()
