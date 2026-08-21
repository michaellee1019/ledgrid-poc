"""Deployment isolation and source-policy tests for native backgrounds."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tools.deployment import native_background_entrypoint as native_entrypoint
from tools.deployment.deploy_coordinator import CommandResult, DeployContext


class _RecordingRunner:
    def __init__(self, outputs: list[str] | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.outputs = list(outputs or [])

    def run(self, args, **kwargs):
        normalized = tuple(os.fspath(argument) for argument in args)
        self.calls.append((normalized, kwargs))
        stdout = self.outputs.pop(0) if self.outputs else ""
        return CommandResult(normalized, 0, stdout, "", 0.01)


class NativeSourcePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        files = {
            "animation/plugins/aurora_curtains_native/manifest.json": json.dumps(
                {
                    "manifest_version": 1,
                    "plugin_id": "aurora_curtains_native",
                    "provider": "receiver_native",
                    "build": {
                        "source": "native/background.cpp",
                        "abi_version": 2,
                    },
                }
            ).encode(),
            "animation/plugins/aurora_curtains_native/native/background.cpp": b"extern \"C\" {}\n",
            "animation/core/component_catalog.py": b"# catalog normalizer\n",
            "animation/core/plugin_loader.py": b"# loader normalizer\n",
            "animation/native/builder.py": b"# builder\n",
            "animation/native/bundle.py": b"# bundle\n",
            "firmware/esp32/include/ledgrid/native_background_abi_v2.h": b"#pragma once\n",
            "firmware/esp32/platformio.ini": b"platform = pinned\n",
            "pyproject.toml": b"[project]\nname='fixture'\nversion='1'\n",
            "uv.lock": b"version = 1\n",
            "README.md": b"unrelated\n",
        }
        for relative, payload in files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        subprocess.run(("git", "init", "-q", self.root), check=True)
        subprocess.run(("git", "-C", self.root, "config", "user.name", "Test"), check=True)
        subprocess.run(
            ("git", "-C", self.root, "config", "user.email", "test@example.invalid"),
            check=True,
        )
        subprocess.run(("git", "-C", self.root, "add", "."), check=True)
        subprocess.run(
            (
                "git",
                "-C",
                self.root,
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "fixture",
            ),
            check=True,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_plan_is_read_only_scoped_and_explicitly_excludes_deploy_actions(self) -> None:
        before = subprocess.run(
            ("git", "-C", self.root, "status", "--porcelain=v1", "-z"),
            check=True,
            capture_output=True,
        ).stdout
        plan = native_entrypoint.native_source_plan(
            self.root, "aurora_curtains_native"
        )
        after = subprocess.run(
            ("git", "-C", self.root, "status", "--porcelain=v1", "-z"),
            check=True,
            capture_output=True,
        ).stdout
        self.assertEqual(before, after)
        paths = {item["path"] for item in plan["files"]}
        self.assertNotIn("README.md", paths)
        self.assertIn("animation/core/component_catalog.py", paths)
        self.assertIn("animation/core/plugin_loader.py", paths)
        self.assertEqual(
            [step["id"] for step in plan["steps"]],
            ["receiver_background.build", "receiver_background.publish"],
        )
        self.assertEqual(set(plan["ordinary_deploy"].values()), {False})

    def test_digest_changes_only_with_scoped_tracked_inputs(self) -> None:
        first = native_entrypoint.native_source_plan(
            self.root, "aurora_curtains_native"
        )
        (self.root / "README.md").write_bytes(b"unrelated changed\n")
        unrelated = native_entrypoint.native_source_plan(
            self.root, "aurora_curtains_native"
        )
        self.assertEqual(first["source_digest"], unrelated["source_digest"])

        source = (
            self.root
            / "animation/plugins/aurora_curtains_native/native/background.cpp"
        )
        source.write_bytes(b"extern \"C\" { int changed; }\n")
        changed = native_entrypoint.native_source_plan(
            self.root, "aurora_curtains_native"
        )
        self.assertNotEqual(first["source_digest"], changed["source_digest"])
        self.assertEqual(
            changed["modified_tracked_inputs"],
            ["animation/plugins/aurora_curtains_native/native/background.cpp"],
        )

    def test_normalizer_edit_changes_only_native_source_identity(self) -> None:
        first = native_entrypoint.native_source_plan(
            self.root, "aurora_curtains_native"
        )
        normalizer = self.root / "animation/core/component_catalog.py"
        normalizer.write_bytes(b"# catalog normalizer changed\n")
        changed = native_entrypoint.native_source_plan(
            self.root, "aurora_curtains_native"
        )

        self.assertNotEqual(first["source_digest"], changed["source_digest"])
        self.assertEqual(
            changed["modified_tracked_inputs"],
            ["animation/core/component_catalog.py"],
        )
        self.assertEqual(set(changed["ordinary_deploy"].values()), {False})

    def test_untracked_native_input_and_unsafe_plugin_ids_reject(self) -> None:
        untracked = (
            self.root / "animation/plugins/aurora_curtains_native/native/extra.cpp"
        )
        untracked.write_bytes(b"untracked\n")
        with self.assertRaisesRegex(
            native_entrypoint.NativeBackgroundWorkflowError, "untracked"
        ):
            native_entrypoint.native_source_plan(
                self.root, "aurora_curtains_native"
            )
        for invalid in ("../aurora", "Aurora", "aurora-native", ""):
            with self.subTest(invalid=invalid), self.assertRaises(
                native_entrypoint.NativeBackgroundWorkflowError
            ):
                native_entrypoint.native_source_plan(self.root, invalid)

    def test_target_upload_preparation_is_bounded_and_non_overwriting(self) -> None:
        library_root = self.root / "receiver_library/native_backgrounds"
        token = "a" * 32
        prepared = native_entrypoint.library_prepare(library_root, token)
        self.assertEqual(
            Path(prepared["incoming_path"]),
            library_root / ".incoming" / f"{token}.zip",
        )
        incoming = Path(prepared["incoming_path"])
        incoming.write_bytes(b"candidate")
        with self.assertRaisesRegex(
            native_entrypoint.NativeBackgroundWorkflowError, "already exists"
        ):
            native_entrypoint.library_prepare(library_root, token)
        with self.assertRaises(native_entrypoint.NativeBackgroundWorkflowError):
            native_entrypoint.library_prepare(library_root, "../unsafe")

    def test_prebuilt_publish_rejects_stale_repository_provenance(self) -> None:
        bundle_path = self.root / "run_state/native_background_builds/bundle.zip"
        bundle_path.parent.mkdir(parents=True)
        bundle_path.write_bytes(b"validated bundle")
        current = {
            "component_manifest_sha256": "a" * 64,
            "source_sha256": "b" * 64,
            "header_sha256": "c" * 64,
        }
        verified = SimpleNamespace(
            manifest={
                "plugin_id": "aurora_curtains_native",
                "component_manifest_sha256": current["component_manifest_sha256"],
                "build": {"source_sha256": "d" * 64},
                "abi": {"header_sha256": current["header_sha256"]},
            }
        )

        with (
            mock.patch.object(native_entrypoint, "_inspect_bundle", return_value=verified),
            mock.patch.object(
                native_entrypoint,
                "native_source_plan",
                return_value={"source_digest": "e" * 64},
            ),
            mock.patch.object(
                native_entrypoint,
                "_current_native_provenance",
                return_value=current,
            ),
            self.assertRaisesRegex(
                native_entrypoint.NativeBackgroundWorkflowError, "source_sha256"
            ),
        ):
            native_entrypoint._bundle_from_argument(
                self.root, os.fspath(bundle_path)
            )

    def test_prebuilt_publish_runs_only_managed_library_commands(self) -> None:
        bundle_path = (
            self.root
            / "run_state/native_background_builds/aurora_curtains_native/bundle.zip"
        )
        bundle_path.parent.mkdir(parents=True)
        bundle_path.write_bytes(b"validated bundle")
        bundle_digest = "b" * 64
        payload_digest = "c" * 64
        verified = SimpleNamespace(
            bundle_digest=bundle_digest,
            payload_digest=payload_digest,
            manifest={"plugin_id": "aurora_curtains_native"},
        )
        attempt_id = "d" * 32
        deploy_dir = "ledgrid-pod"
        incoming = (
            f"{deploy_dir}/receiver_library/native_backgrounds/"
            f".incoming/{attempt_id}.zip"
        )
        local = _RecordingRunner()
        remote = _RecordingRunner(
            [
                json.dumps({"incoming_path": incoming}),
                json.dumps(
                    {
                        "package_id": "aurora_curtains_native",
                        "bundle_digest": bundle_digest,
                        "payload_digest": payload_digest,
                    }
                ),
            ]
        )
        context = DeployContext(
            target="pi@example.invalid",
            mode="native-publish",
            source_identity={"source_digest": "a" * 64},
            command_runner=local,
            ssh_runner=remote,
            attempt_id=attempt_id,
        )

        with (
            mock.patch.object(
                native_entrypoint,
                "_bundle_from_argument",
                return_value=(context.source_identity, None, verified, bundle_path),
            ),
            mock.patch.object(native_entrypoint, "_context", return_value=context),
            mock.patch.object(
                native_entrypoint, "_inspect_bundle", return_value=verified
            ),
        ):
            result = native_entrypoint.run_publish(
                self.root,
                os.fspath(bundle_path),
                target=context.target,
                deploy_dir=deploy_dir,
                ssh_options=("-o", "BatchMode=yes"),
            )

        self.assertEqual(result["outcome"], "success")
        self.assertEqual(len(local.calls), 1)
        rsync = local.calls[0][0]
        self.assertEqual(rsync[:4], ("rsync", "-az", "-e", "ssh -o BatchMode=yes"))
        self.assertEqual(rsync[-2:], (os.fspath(bundle_path), f"{context.target}:{incoming}"))
        self.assertEqual(
            [call[0][2] for call in remote.calls],
            ["library-prepare", "library-publish"],
        )
        self.assertEqual(
            {call[0][0] for call in remote.calls},
            {"ledgrid-pod/venv/bin/python"},
        )
        forbidden = (
            "systemctl",
            "flash",
            "firmware",
            "reboot",
            "native-start",
            "activate",
        )
        command_text = " ".join(
            argument for call, _kwargs in local.calls + remote.calls for argument in call
        )
        for term in forbidden:
            with self.subTest(term=term):
                self.assertNotIn(term, command_text)


if __name__ == "__main__":
    unittest.main()
