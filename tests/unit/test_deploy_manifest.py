"""Regression tests for tracked deployment manifests and rsync safety."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from tools.deployment.deploy_manifest import (
    manifest_plan,
    source_identity,
    tracked_paths,
    working_tree_dirty,
)


class DeployManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_dir.name)
        subprocess.run(
            [
                "git",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "tag.gpgsign=false",
                "init",
                "-q",
                self.root,
            ],
            check=True,
        )

    def tearDown(self):
        self.temporary_dir.cleanup()

    def _write(self, relative_path: str, content: bytes = b"test") -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _track(self, *relative_paths: str) -> None:
        subprocess.run(["git", "-C", self.root, "add", "--", *relative_paths], check=True)

    def _commit(self, *relative_paths: str) -> None:
        self._track(*relative_paths)
        subprocess.run(
            [
                "git",
                "-C",
                self.root,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )

    def test_fast_manifest_includes_tracked_code_templates_and_plugin_assets(self):
        tracked = (
            "animation/plugins/rainbow/__init__.py",
            "animation/plugins/rainbow/presets/vivid.json",
            "animation/plugins/gif_animation/assets/party.gif",
            "animation/plugins/gif_animation/assets/contact-sheet.png",
            "web/templates/index.html",
            "web/static/css/dashboard.css",
            "web/static/js/dashboard.js",
            "config/installation_qualification_budget.json",
            "config/plant_globe_map_32x138.json",
            "scripts/start_systemd.sh",
            "web/composer_preset_membership.v1.json",
        )
        for path in tracked:
            self._write(path)
        self._track(*tracked)

        self.assertEqual(
            set(tracked_paths(self.root, "fast")),
            {PurePosixPath(path) for path in tracked},
        )

    def test_fast_manifest_includes_safe_new_code_but_excludes_runtime_presets(self):
        self._write("animation/plugins/rainbow/untracked.py")
        self._write("animation/plugins/rainbow/presets/untracked.json")
        self._write("presets/animations/rainbow/user-saved.json")
        self._write("presets/animations/rainbow/old-curated.json")
        self._track("presets/animations/rainbow/old-curated.json")

        self.assertEqual(
            tracked_paths(self.root, "fast"),
            [
                PurePosixPath("animation/plugins/rainbow/presets/untracked.json"),
                PurePosixPath("animation/plugins/rainbow/untracked.py"),
            ],
        )

    def test_full_manifest_includes_only_tracked_non_runtime_files(self):
        self._write("scripts/start_server.py")
        self._write("docs/README.md")
        self._write("local-only.txt")
        self._write("presets/animations/rainbow/runtime.json")
        self._track(
            "scripts/start_server.py",
            "docs/README.md",
            "presets/animations/rainbow/runtime.json",
        )

        self.assertEqual(
            tracked_paths(self.root, "full"),
            [PurePosixPath("docs/README.md"), PurePosixPath("scripts/start_server.py")],
        )

    def test_full_manifest_includes_new_application_modules_but_not_root_miscellany(self):
        self._write("web/activation_token_store.py")
        self._write("animation/core/plant_awareness.py")
        self._write("pyproject.toml")
        self._write("requirements-pi.lock")
        self._write("scratch-secret.txt")

        self.assertEqual(
            tracked_paths(self.root, "full"),
            [
                PurePosixPath("animation/core/plant_awareness.py"),
                PurePosixPath("pyproject.toml"),
                PurePosixPath("requirements-pi.lock"),
                PurePosixPath("web/activation_token_store.py"),
            ],
        )

    def test_manifest_omits_tracked_files_deleted_from_worktree(self):
        deleted = self._write("docs/removed.md")
        self._track("docs/removed.md")
        deleted.unlink()

        self.assertEqual(tracked_paths(self.root, "full"), [])

    def test_plan_accounts_for_selected_safe_untracked_and_every_exclusion(self):
        self._write("scripts/tracked.py")
        self._write("animation/new.py")
        self._write("notes.txt")
        self._write("presets/animations/rainbow/runtime.json")
        deleted = self._write("docs/deleted.md")
        self._track("scripts/tracked.py", "docs/deleted.md")
        deleted.unlink()

        plan = manifest_plan(self.root, "full")

        self.assertEqual(
            plan.selected,
            (
                PurePosixPath("animation/new.py"),
                PurePosixPath("scripts/tracked.py"),
            ),
        )
        self.assertEqual(plan.safe_untracked, (PurePosixPath("animation/new.py"),))
        self.assertEqual(
            {(item.path.as_posix(), item.reason) for item in plan.excluded},
            {
                ("docs/deleted.md", "deleted from working tree"),
                ("notes.txt", "untracked path is outside safe deployment roots"),
                (
                    "presets/animations/rainbow/runtime.json",
                    "target-owned runtime preset",
                ),
            },
        )

    def test_repository_coordination_metadata_never_enters_a_deployment(self):
        self._write(".beads/interactions.jsonl")
        deleted = self._write(".beads/deleted.jsonl")
        self._write(".agents/skills/demo/SKILL.md")
        self._write(".codex/config.toml")
        self._write("web/app.py")
        self._commit(
            ".beads/interactions.jsonl",
            ".beads/deleted.jsonl",
            ".agents/skills/demo/SKILL.md",
            ".codex/config.toml",
            "web/app.py",
        )
        before = source_identity(self.root, manifest_plan(self.root, "full"))
        deleted.unlink()

        plan = manifest_plan(self.root, "full")

        self.assertEqual(plan.selected, (PurePosixPath("web/app.py"),))
        self.assertEqual(
            {(item.path.as_posix(), item.reason) for item in plan.excluded},
            {
                (".agents/skills/demo/SKILL.md", "repository coordination metadata"),
                (".beads/deleted.jsonl", "repository coordination metadata"),
                (".beads/interactions.jsonl", "repository coordination metadata"),
                (".codex/config.toml", "repository coordination metadata"),
            },
        )
        self.assertEqual(before, source_identity(self.root, plan))

    def test_fast_plan_explains_non_fast_tracked_and_safe_untracked_files(self):
        self._write("docs/README.md")
        self._write("tools/new-config.toml")
        self._write("web/app.py")
        self._track("docs/README.md", "web/app.py")

        plan = manifest_plan(self.root, "fast")

        self.assertEqual(plan.selected, (PurePosixPath("web/app.py"),))
        self.assertEqual(
            {(item.path.as_posix(), item.reason) for item in plan.excluded},
            {
                ("docs/README.md", "outside fast application scope"),
                ("tools/new-config.toml", "outside fast application scope"),
            },
        )

    def test_fast_manifest_keeps_every_generated_composer_runtime_asset(self):
        composer_assets = (
            "web/static/generated/composer/bootstrap.v1.json",
            "web/static/generated/composer/offline_assets.json",
            "web/static/generated/composer/profile.bin",
            "web/static/generated/composer/renderer.wasm",
        )
        other_generated = "web/static/generated/other/catalog.json"
        for path in (*composer_assets, other_generated):
            self._write(path)
        self._track(*composer_assets, other_generated)

        plan = manifest_plan(self.root, "fast")

        self.assertEqual(
            plan.selected,
            tuple(PurePosixPath(path) for path in composer_assets),
        )
        self.assertIn(
            (other_generated, "outside fast application scope"),
            {(item.path.as_posix(), item.reason) for item in plan.excluded},
        )

    def test_dependency_inputs_are_safe_untracked_for_full_but_excluded_from_fast(self):
        for path in ("pyproject.toml", "uv.lock", "requirements-pi.lock"):
            self._write(path)

        full = manifest_plan(self.root, "full")
        fast = manifest_plan(self.root, "fast")

        expected = tuple(
            PurePosixPath(path)
            for path in ("pyproject.toml", "requirements-pi.lock", "uv.lock")
        )
        self.assertEqual(full.selected, expected)
        self.assertEqual(full.safe_untracked, expected)
        self.assertEqual(fast.selected, ())
        self.assertEqual(
            {item.path for item in fast.excluded},
            set(expected),
        )

    def test_dirty_identity_is_stable_and_changes_with_included_source_bytes(self):
        tracked = self._write("scripts/server.py", b"initial")
        self._commit("scripts/server.py")
        tracked.write_bytes(b"edited")
        safe = self._write("web/new.py", b"safe one")
        self._write("notes.txt", b"excluded one")

        plan = manifest_plan(self.root, "full")
        first = source_identity(self.root, plan)
        second = source_identity(self.root, manifest_plan(self.root, "full"))
        self.assertEqual(first, second)
        self.assertEqual(first["safe_untracked"], ["web/new.py"])

        safe.write_bytes(b"safe two")
        changed_safe = source_identity(self.root, manifest_plan(self.root, "full"))
        self.assertNotEqual(first["diff_sha256"], changed_safe["diff_sha256"])

        self._write("notes.txt", b"excluded two")
        changed_excluded = source_identity(self.root, manifest_plan(self.root, "full"))
        self.assertEqual(changed_safe["diff_sha256"], changed_excluded["diff_sha256"])

    def test_fast_identity_ignores_tracked_changes_outside_fast_scope(self):
        self._write("web/app.py", b"web initial")
        docs = self._write("docs/README.md", b"docs initial")
        self._commit("web/app.py", "docs/README.md")

        first = source_identity(self.root, manifest_plan(self.root, "fast"))
        docs.write_bytes(b"docs changed")
        second = source_identity(self.root, manifest_plan(self.root, "fast"))
        self.assertEqual(first["diff_sha256"], second["diff_sha256"])

        self._write("web/app.py", b"web changed")
        third = source_identity(self.root, manifest_plan(self.root, "fast"))
        self.assertNotEqual(second["diff_sha256"], third["diff_sha256"])

    def test_clean_state_includes_non_ignored_untracked_files(self):
        self._write("README.md")
        self._commit("README.md")
        self.assertFalse(working_tree_dirty(self.root))
        self._write("scratch.txt")
        self.assertTrue(working_tree_dirty(self.root))

    def test_clean_state_ignores_repository_coordination_changes_only(self):
        self._write(".beads/interactions.jsonl", b"initial")
        self._write(".agents/skills/demo/SKILL.md", b"initial")
        self._write(".codex/config.toml", b"initial")
        self._write("web/app.py", b"initial")
        self._commit(
            ".beads/interactions.jsonl",
            ".agents/skills/demo/SKILL.md",
            ".codex/config.toml",
            "web/app.py",
        )

        self._write(".beads/interactions.jsonl", b"changed")
        self._write(".agents/skills/demo/LOCAL.md", b"untracked")
        self._write(".codex/local.toml", b"untracked")
        self.assertFalse(working_tree_dirty(self.root))

        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "tools/deployment/deploy_manifest.py"),
            "--root",
            str(self.root),
            "--scope",
            "full",
            "--policy",
            "clean",
            "--json",
        ]
        clean = subprocess.run(command, check=True, capture_output=True, text=True)
        self.assertFalse(json.loads(clean.stdout)["dirty"])

        self._write("web/app.py", b"changed")
        self.assertTrue(working_tree_dirty(self.root))

    def test_clean_cli_rejects_dirty_tree_while_dirty_cli_records_identity(self):
        self._write("README.md")
        self._commit("README.md")
        self._write("web/new.py", b"candidate")
        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "tools/deployment/deploy_manifest.py"),
            "--root",
            str(self.root),
            "--scope",
            "full",
        ]

        clean = subprocess.run(
            [*command, "--policy", "clean", "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(clean.returncode, 2)
        self.assertIn("clean deployment refused", clean.stderr)

        dirty = subprocess.run(
            [*command, "--policy", "dirty", "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(dirty.stdout)
        self.assertTrue(payload["dirty"])
        self.assertEqual(payload["safe_untracked"], ["web/new.py"])
        self.assertEqual(len(payload["diff_sha256"]), 64)

    def test_plan_cli_is_read_only(self):
        self._write("README.md")
        self._commit("README.md")
        self._write("tools/new.py")
        before = subprocess.run(
            ["git", "-C", self.root, "status", "--porcelain=v1", "-z"],
            check=True,
            capture_output=True,
        ).stdout

        plan = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[2] / "tools/deployment/deploy_manifest.py"),
                "--root",
                str(self.root),
                "--scope",
                "full",
                "--policy",
                "plan",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        after = subprocess.run(
            ["git", "-C", self.root, "status", "--porcelain=v1", "-z"],
            check=True,
            capture_output=True,
        ).stdout

        self.assertEqual(before, after)
        self.assertIn("tools/new.py [safe untracked]", plan.stdout)

    def test_sync_contract_deletes_stale_code_but_protects_target_state(self):
        root = Path(__file__).resolve().parents[2]
        sync_script = (root / "tools/deployment/sync_files.sh").read_text(encoding="utf-8")

        self.assertIn("rsync -az --delete --stats", sync_script)
        for protected_path in (
            "--filter 'protect /calibration_photos/***'",
            "--filter 'protect /receiver_library/***'",
            "--filter 'protect /installation_profile_library/***'",
            "--exclude 'venv/'",
            "--exclude 'run_state/'",
            "--filter 'protect /presets/'",
            "--filter 'protect /presets/animations/***'",
            "--exclude '/presets/animations/'",
            "--exclude '.esp32_firmware_hash'",
            "--exclude '*.log'",
        ):
            self.assertIn(protected_path, sync_script)
        self.assertIn("deployment_manifest fast", sync_script)
        self.assertIn("deployment_manifest full", sync_script)
        fast_contract = sync_script[sync_script.index("sync_fast_deployment"):]
        self.assertNotIn("PREVIEW_ARTIFACT_DIR", fast_contract)
        self.assertNotIn("generate_animation_previews.py", fast_contract)
        self.assertNotIn("animation-previews", fast_contract)

    def test_full_sync_filter_preserves_target_owned_bytes_during_delete(self):
        source = self.root / "staged"
        target = self.root / "deployed"
        self._write("staged/web/app.py", b"new deployment")
        self._write("deployed/web/app.py", b"old")
        stale_code = self._write("deployed/web/removed.py", b"stale")
        saved_preset = self._write(
            "deployed/presets/animations/rainbow/my-preset.json",
            b'{"name":"My Preset"}',
        )
        compiled_profile = self._write(
            f"deployed/installation_profile_library/profiles/{'a' * 64}/profile.bin",
            b"compiled-profile-bytes\x00\xff",
        )
        publish_receipt = self._write(
            f"deployed/installation_profile_library/profiles/{'a' * 64}/receipt.json",
            b'{"digest":"published"}\n',
        )
        native_bundle = self._write(
            f"deployed/receiver_library/native_backgrounds/bundles/{'b' * 64}/bundle.zip",
            b"native-bundle-bytes\x00\xff",
        )
        native_payload = self._write(
            f"deployed/receiver_library/native_backgrounds/payloads/{'c' * 64}.so",
            b"native-payload-bytes\x00\xff",
        )

        subprocess.run(
            [
                "rsync",
                "-a",
                "--delete",
                "--filter",
                "protect /presets/",
                "--filter",
                "protect /presets/animations/***",
                "--exclude",
                "/presets/animations/",
                "--filter",
                "protect /installation_profile_library/***",
                "--filter",
                "protect /receiver_library/***",
                f"{source}/",
                f"{target}/",
            ],
            check=True,
        )

        self.assertEqual((target / "web/app.py").read_bytes(), b"new deployment")
        self.assertFalse(stale_code.exists())
        self.assertEqual(saved_preset.read_bytes(), b'{"name":"My Preset"}')
        self.assertEqual(compiled_profile.read_bytes(), b"compiled-profile-bytes\x00\xff")
        self.assertEqual(publish_receipt.read_bytes(), b'{"digest":"published"}\n')
        self.assertEqual(native_bundle.read_bytes(), b"native-bundle-bytes\x00\xff")
        self.assertEqual(native_payload.read_bytes(), b"native-payload-bytes\x00\xff")


if __name__ == "__main__":
    unittest.main()
