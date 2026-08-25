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

    def test_prebuilt_bundle_rejects_direct_and_parent_symlinks(self) -> None:
        build_root = self.root / "run_state/native_background_builds"
        real_parent = build_root / "real"
        real_parent.mkdir(parents=True)
        real_bundle = real_parent / "bundle.zip"
        real_bundle.write_bytes(b"bundle")

        direct = build_root / "direct.zip"
        direct.symlink_to(real_bundle)
        with self.assertRaisesRegex(
            native_entrypoint.NativeBackgroundWorkflowError, "symbolic link"
        ):
            native_entrypoint._bundle_from_argument(
                self.root, os.fspath(direct)
            )

        linked_parent = build_root / "linked"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaisesRegex(
            native_entrypoint.NativeBackgroundWorkflowError, "symbolic link"
        ):
            native_entrypoint._bundle_from_argument(
                self.root, os.fspath(linked_parent / "bundle.zip")
            )

        provenance = {
            "component_manifest_sha256": "a" * 64,
            "source_sha256": "b" * 64,
            "header_sha256": "c" * 64,
        }
        verified = SimpleNamespace(
            manifest={
                "plugin_id": "aurora_curtains_native",
                "component_manifest_sha256": provenance[
                    "component_manifest_sha256"
                ],
                "build": {"source_sha256": provenance["source_sha256"]},
                "abi": {"header_sha256": provenance["header_sha256"]},
            }
        )
        with (
            mock.patch.object(
                native_entrypoint, "_inspect_bundle", return_value=verified
            ),
            mock.patch.object(
                native_entrypoint,
                "native_source_plan",
                return_value={"source_digest": "d" * 64},
            ),
            mock.patch.object(
                native_entrypoint,
                "_current_native_provenance",
                return_value=provenance,
            ),
        ):
            _plan, _result, resolved, path = (
                native_entrypoint._bundle_from_argument(
                    self.root, os.fspath(real_bundle)
                )
            )
        self.assertIs(resolved, verified)
        self.assertEqual(path, real_bundle)

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

    def test_native_run_persists_all_four_exact_command_bound_steps(self) -> None:
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
        attempt_id = "e" * 32
        deploy_dir = "ledgrid-pod"
        incoming = (
            f"{deploy_dir}/receiver_library/native_backgrounds/"
            f".incoming/{attempt_id}.zip"
        )
        local = _RecordingRunner()
        remote = _RecordingRunner([
            json.dumps({"incoming_path": incoming}),
            json.dumps({
                "package_id": "aurora_curtains_native",
                "bundle_digest": bundle_digest,
                "payload_digest": payload_digest,
            }),
        ])
        receipt_dir = self.root / "native-receipts"
        context = DeployContext(
            target="pi@example.invalid",
            mode="native-run",
            source_identity={"source_digest": "a" * 64},
            command_runner=local,
            ssh_runner=remote,
            receipt_sinks=(
                native_entrypoint.AtomicJSONReceiptStore(receipt_dir),
            ),
            attempt_id=attempt_id,
        )

        def command_result(operation, state, command_id):
            return {
                "operation": "start" if operation == "activate" else operation,
                "plugin_id": "aurora_curtains_native",
                "bundle_digest": bundle_digest,
                "payload_digest": payload_digest,
                "command_id": command_id,
                "native_background": {
                    "state": state,
                    "operation": operation,
                    "bundle_digest": bundle_digest,
                    "payload_digest": payload_digest,
                    "error": None,
                },
            }

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
            mock.patch.object(
                native_entrypoint,
                "run_install",
                return_value=command_result("install", "ready", 7),
            ),
            mock.patch.object(
                native_entrypoint,
                "run_start",
                return_value=command_result("activate", "active", 8),
            ),
        ):
            result = native_entrypoint.run_native_run(
                self.root,
                os.fspath(bundle_path),
                target=context.target,
                deploy_dir=deploy_dir,
                ssh_options=("-o", "BatchMode=yes"),
                timeout=1,
            )

        self.assertEqual(result["deployment_id"], attempt_id)
        self.assertEqual(result["installation"]["command_id"], 7)
        self.assertEqual(result["activation"]["command_id"], 8)
        receipt = json.loads((receipt_dir / f"{attempt_id}.json").read_text())
        self.assertEqual(
            [step["id"] for step in receipt["steps"]],
            [
                "receiver_background.build",
                "receiver_background.publish",
                "receiver_background.install",
                "receiver_background.activate",
            ],
        )
        self.assertEqual(
            [step["details"].get("command_id") for step in receipt["steps"]],
            [None, None, 7, 8],
        )
        self.assertEqual(
            [artifact["kind"] for artifact in receipt["artifacts"]],
            [
                "receiver_background_bundle",
                "receiver_background_library_bundle",
                "receiver_background_install",
                "receiver_background_activate",
            ],
        )
        self.assertTrue(all(
            artifact["digest"] == bundle_digest
            and artifact["target_id"] == payload_digest
            for artifact in receipt["artifacts"]
        ))

    def test_native_run_context_persists_receipt_locally_and_on_target(self) -> None:
        context = native_entrypoint._context(
            root=self.root,
            target="pi@example.invalid",
            mode="native-run",
            source_identity={"source_digest": "a" * 64},
            deploy_dir="ledgrid-pod",
            ssh_options=(),
        )
        self.assertEqual(len(context.receipt_sinks), 2)
        self.assertIsInstance(
            context.receipt_sinks[0], native_entrypoint.AtomicJSONReceiptStore
        )
        self.assertIsInstance(
            context.receipt_sinks[1], native_entrypoint.SSHAtomicJSONReceiptStore
        )

    def test_command_receipt_rejects_missing_or_mismatched_identity_proof(self) -> None:
        candidate = SimpleNamespace(
            bundle_digest="b" * 64,
            payload_digest="c" * 64,
            manifest={"plugin_id": "aurora_curtains_native"},
        )
        proof = {
            "plugin_id": "aurora_curtains_native",
            "bundle_digest": candidate.bundle_digest,
            "payload_digest": candidate.payload_digest,
            "command_id": 7,
            "native_background": {
                "state": "ready",
                "operation": "install",
                "bundle_digest": candidate.bundle_digest,
                "payload_digest": candidate.payload_digest,
                "error": None,
            },
        }
        mutations = (
            lambda value: value.pop("command_id"),
            lambda value: value.update(payload_digest="d" * 64),
            lambda value: value["native_background"].update(
                bundle_digest="e" * 64
            ),
            lambda value: value["native_background"].update(
                operation="activate"
            ),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                altered = json.loads(json.dumps(proof))
                mutate(altered)
                with self.assertRaisesRegex(
                    native_entrypoint.NativeBackgroundWorkflowError,
                    "exact command-bound proof",
                ):
                    native_entrypoint._native_command_operation_result(
                        altered,
                        candidate,
                        expected_operation="install",
                        expected_state="ready",
                    )


class NativeRuntimeCommandTests(unittest.TestCase):
    bundle = "a" * 64
    payload = "b" * 64
    defaults = {"brightness": 0.42}
    schema = {
        "brightness": {
            "type": "float", "default": 0.42, "min": 0.0, "max": 1.0,
        },
    }
    parameter_digest = "c" * 64
    context_digest = "d" * 64
    profile_digest = "e" * 64

    @classmethod
    def native_descriptor(cls):
        return {
            "plugin_id": "aurora_curtains_native",
            "provider": "receiver_native",
            "role": "background",
            "defaults": cls.defaults,
            "parameter_schema": cls.schema,
            "build": {
                "bundle_digest": cls.bundle,
                "expected_payload_digest": cls.payload,
            },
        }

    @classmethod
    def native_status(cls, *, state):
        required = 0x1FF
        topology = native_entrypoint.FINALIZED_NATIVE_TOPOLOGY
        driver = {
            "state": state,
            "operation": "install" if state == "ready" else "activate",
            "bundle_digest": cls.bundle,
            "payload_digest": cls.payload,
            "error": None,
            "capability_report": {
                "required_capabilities": required,
                "devices": [
                    {
                        "logical_device": receiver_id,
                        "capabilities": required,
                        "local_strip_count": width,
                        "global_strip_offset": offset,
                        "reverse_native_strip_order": reverse,
                    }
                    for receiver_id, (width, offset, reverse)
                    in topology.items()
                ],
            },
        }
        result = {
            "last_command_id": 7,
            "receiver_hybrid": {"driver": driver},
        }
        if state == "active":
            from animation.core.native_background_operation import (
                encode_native_parameters,
            )

            cls.parameter_digest = encode_native_parameters(
                cls.schema, cls.defaults
            ).digest
            driver.update({
                "parameter_digest": cls.parameter_digest,
                "context_digest": cls.context_digest,
                "installation_profile_digest": cls.profile_digest,
                "agreement": {
                    "exact_roster": True,
                    "verified_receiver_ids": [0, 1, 2, 3, 4],
                },
            })
            result.update({
                "installation_profile_digest": cls.profile_digest,
                "scene": {"provider_mode": "receiver_native"},
                "receiver_hybrid": {
                    "healthy": True,
                    "operational": True,
                    "fallback_active": False,
                    "error": None,
                    "driver": driver,
                },
                "driver_stats": {"devices": [
                    {
                        "receiver_status_seen": True,
                        "receiver_status_version": 6,
                        "receiver_logical_device": receiver_id,
                        "receiver_native_executing": True,
                        "receiver_native_cache_integrity_ok": True,
                        "receiver_native_active_bundle_digest": cls.bundle,
                        "receiver_native_active_payload_digest": cls.payload,
                        "receiver_native_active_parameter_digest": (
                            cls.parameter_digest
                        ),
                        "receiver_active_context_digest": cls.context_digest,
                        "receiver_profile_active_global_digest": cls.profile_digest,
                        "receiver_vibe_revision": 4,
                        "receiver_vibe_digest": "f" * 64,
                        "receiver_plant_modifier_revision": 5,
                        "receiver_plant_modifier_digest": "1" * 64,
                    }
                    for receiver_id in range(5)
                ]},
            })
        return result

    def test_install_resolves_catalog_binding_and_waits_for_exact_ready_proof(self):
        observed = []

        def api(_target, path, **kwargs):
            observed.append((path, kwargs))
            if path.startswith("/api/v1/components?"):
                return {"components": [self.native_descriptor()]}
            if path.endswith("/install"):
                return {"command_id": 7}
            return self.native_status(state="ready")

        with mock.patch.object(native_entrypoint, "_api_json", side_effect=api):
            result = native_entrypoint.run_install(
                "wall@example.invalid", "aurora_curtains_native", timeout=1
            )
        self.assertEqual(result["bundle_digest"], self.bundle)
        self.assertEqual(result["native_background"]["state"], "ready")
        self.assertEqual(observed[1][1]["method"], "POST")
        self.assertIn(self.bundle, observed[1][0])

    def test_start_builds_digest_bound_scene_with_known_python_fallback(self):
        observed_scene = None
        fallback = {
            "plugin_id": "aurora_curtains",
            "provider": "python",
            "role": "background",
            "defaults": {"motion": 0.2},
        }

        def api(_target, path, **kwargs):
            nonlocal observed_scene
            if path.startswith("/api/v1/components?"):
                return {"components": [self.native_descriptor()]}
            if path == "/api/v1/components":
                return {"components": [self.native_descriptor(), fallback]}
            if path == "/api/v1/scene":
                observed_scene = kwargs["payload"]
                return {"command_id": 7}
            status = self.native_status(state="active")
            status["scene_state"] = observed_scene
            return status

        with mock.patch.object(native_entrypoint, "_api_json", side_effect=api):
            result = native_entrypoint.run_start(
                "wall@example.invalid", self.bundle, timeout=1
            )
        self.assertEqual(observed_scene["background"]["bundle_digest"], self.bundle)
        self.assertEqual(
            observed_scene["known_python_fallback"]["plugin_id"],
            "aurora_curtains",
        )
        self.assertEqual(result["native_background"]["state"], "active")

    def test_install_and_start_status_proofs_reject_stale_or_partial_state(self):
        ready = self.native_status(state="ready")
        self.assertIsNone(native_entrypoint._native_command_status_error(
            ready,
            bundle_digest=self.bundle,
            payload_digest=self.payload,
            expected_state="ready",
            expected_operation="install",
        ))
        ready["receiver_hybrid"]["driver"]["payload_digest"] = "0" * 64
        self.assertIn("outcome", native_entrypoint._native_command_status_error(
            ready,
            bundle_digest=self.bundle,
            payload_digest=self.payload,
            expected_state="ready",
            expected_operation="install",
        ))

        active = self.native_status(state="active")
        active["scene_state"] = {"revision": 1}
        arguments = {
            "bundle_digest": self.bundle,
            "payload_digest": self.payload,
            "expected_state": "active",
            "expected_operation": "activate",
            "expected_parameter_digest": self.parameter_digest,
            "expected_scene": active["scene_state"],
        }
        self.assertIsNone(
            native_entrypoint._native_command_status_error(active, **arguments)
        )
        mutations = (
            lambda value: value["receiver_hybrid"]["driver"]["agreement"].update(
                verified_receiver_ids=[0, 1, 2, 3]
            ),
            lambda value: value["receiver_hybrid"]["driver"].update(
                parameter_digest="0" * 64
            ),
            lambda value: value["driver_stats"]["devices"][4].update(
                receiver_active_context_digest="0" * 64
            ),
            lambda value: value["receiver_hybrid"]["driver"][
                "capability_report"
            ]["devices"][4].update(local_strip_count=8),
        )
        for mutate in mutations:
            candidate = json.loads(json.dumps(active))
            mutate(candidate)
            with self.subTest(candidate=candidate):
                self.assertIsNotNone(
                    native_entrypoint._native_command_status_error(
                        candidate, **arguments
                    )
                )


if __name__ == "__main__":
    unittest.main()
