"""Acceptance coverage for immutable application releases and app-only restore."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.deployment.app_releases import (
    AppActivation,
    AppReleaseManager,
    CandidateHealthFailed,
    RELEASE_METADATA,
    ReleaseValidationError,
    build_app_rollback_steps,
    main,
)
from tools.deployment.deploy_coordinator import DeployContext, DeployCoordinator, OperationResult


class AppReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_dir.name)
        self.source = self.base / "source"
        self.target = self.base / "target"
        self.source.mkdir()
        self.manager = AppReleaseManager(self.target)

    def tearDown(self):
        # Releases are intentionally non-writable. Restore owner permissions so
        # TemporaryDirectory can remove the fixture on platforms where unlink
        # requires a writable parent directory (notably macOS).
        if self.target.exists():
            for path in self.target.rglob("*"):
                if not path.is_symlink() and path.is_dir():
                    path.chmod(0o755)
            if self.manager.releases_dir.exists():
                self.manager.releases_dir.chmod(0o755)
        self.temporary_dir.cleanup()

    def write_source(self, relative, content, executable=False):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755 if executable else 0o644)
        return path

    def app_files(self, version):
        return {
            "scripts/start_server.py": self.write_source(
                "scripts/start_server.py", f"print({version!r})\n", executable=True,
            ),
            "web/static/generated/animation-previews/catalog.json": self.write_source(
                "previews/catalog.json", json.dumps({"version": version}),
            ),
        }

    def test_two_releases_have_no_stale_code_and_preserve_all_shared_state(self):
        first_files = self.app_files("one")
        first_files["animation/removed.py"] = self.write_source("animation/removed.py", "old")
        first = self.manager.stage(first_files)
        self.manager.activate(first.id)

        fixtures = {
            "presets/animations/rainbow/user.json": "preset",
            "run_state/control.json": "state",
            "logs/web.log": "log",
            "venv/pyvenv.cfg": "venv",
            "calibration_photos/wall.jpg": "calibration",
            "firmware/esp32.bin": "firmware",
            "receiver_library/bundle.bin": "receiver",
        }
        for relative, content in fixtures.items():
            path = self.target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        second = self.manager.stage(self.app_files("two"))
        previous = self.manager.activate(second.id)

        self.assertEqual(previous, first.id)
        self.assertEqual(self.manager.current_release_id(), second.id)
        self.assertFalse((second.path / "animation/removed.py").exists())
        self.assertEqual(
            json.loads((second.path / "web/static/generated/animation-previews/catalog.json").read_text()),
            {"version": "two"},
        )
        for relative, content in fixtures.items():
            self.assertEqual((self.target / relative).read_text(), content)
        for relative in (
            "presets", "run_state", "logs", "venv", "calibration_photos",
            "firmware", "receiver_library",
        ):
            self.assertTrue((second.path / relative).is_symlink())

    def test_staging_is_deterministic_immutable_and_reuses_valid_digest(self):
        files = self.app_files("same")
        first = self.manager.stage(files)
        second = self.manager.stage(dict(reversed(list(files.items()))))
        self.assertEqual(first.id, second.id)
        self.assertTrue(second.reused)
        self.assertEqual(len(list(self.manager.releases_dir.iterdir())), 1)
        self.assertFalse(first.path.stat().st_mode & 0o222)
        self.assertFalse((first.path / "scripts/start_server.py").stat().st_mode & 0o222)
        self.assertTrue((first.path / "scripts/start_server.py").stat().st_mode & 0o111)

    def test_stage_rejects_state_symlinks_missing_and_unsafe_inputs(self):
        regular = self.write_source("app.py", "ok")
        symlink = self.source / "link.py"
        symlink.symlink_to(regular)
        cases = (
            ({"presets/animations/user.json": regular}, "target-owned"),
            ({"run_state/status.json": regular}, "target-owned"),
            ({"../escape.py": regular}, "unsafe"),
            ({"link.py": symlink}, "regular file"),
            ({"missing.py": self.source / "missing.py"}, "No such file"),
        )
        for files, message in cases:
            with self.subTest(files=files), self.assertRaisesRegex((ValueError, FileNotFoundError), message):
                self.manager.stage(files)

    def test_validate_rejects_corruption_metadata_shape_duplicates_omissions_and_bad_ids(self):
        info = self.manager.stage(self.app_files("one"))
        metadata_path = info.path / RELEASE_METADATA
        original = json.loads(metadata_path.read_text())
        metadata_path.chmod(0o644)

        mutations = []
        malformed_links = dict(original)
        malformed_links["shared_links"] = []
        mutations.append((malformed_links, "shared links"))
        duplicates = json.loads(json.dumps(original))
        duplicates["files"].append(dict(duplicates["files"][0]))
        mutations.append((duplicates, "duplicate"))
        omitted = json.loads(json.dumps(original))
        omitted["files"] = omitted["files"][:-1]
        mutations.append((omitted, "unexpected paths"))
        bad_size = json.loads(json.dumps(original))
        bad_size["files"][0]["size"] = "large"
        mutations.append((bad_size, "size/mode"))

        for payload, message in mutations:
            with self.subTest(message=message):
                metadata_path.write_text(json.dumps(payload))
                with self.assertRaisesRegex(ReleaseValidationError, message):
                    self.manager.validate(info.id)

        metadata_path.write_text(json.dumps(original))
        app_path = info.path / "scripts/start_server.py"
        app_path.chmod(0o755)
        app_path.write_text("corrupt")
        app_path.chmod(0o555)
        with self.assertRaisesRegex(ReleaseValidationError, "digest/size"):
            self.manager.validate(info.id)

        for invalid in ("a", "A" * 64, "g" * 64, "a" * 65, "../" + "a" * 64):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.manager.validate(invalid)

    def test_metadata_tampering_that_describes_existing_files_gets_digest_recomputed(self):
        info = self.manager.stage(self.app_files("one"))
        metadata_path = info.path / RELEASE_METADATA
        payload = json.loads(metadata_path.read_text())
        metadata_path.chmod(0o644)
        payload["files"][0]["executable"] = not payload["files"][0]["executable"]
        # Change the actual mode too: per-file validation passes, release identity must not.
        file_path = info.path / payload["files"][0]["path"]
        file_path.chmod(0o555 if payload["files"][0]["executable"] else 0o444)
        metadata_path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ReleaseValidationError, "content digest mismatch"):
            self.manager.validate(info.id)

    def test_listing_and_default_explicit_rollback_target(self):
        first = self.manager.stage(self.app_files("one"))
        self.manager.activate(first.id)
        second = self.manager.stage(self.app_files("two"))
        self.manager.activate(second.id)
        listed = self.manager.list()
        self.assertEqual({item.id for item in listed}, {first.id, second.id})
        self.assertEqual([item.id for item in listed if item.active], [second.id])
        self.assertEqual(self.manager.rollback_target(), first.id)
        self.assertEqual(self.manager.rollback_target(first.id), first.id)
        with self.assertRaisesRegex(ValueError, "already active"):
            self.manager.rollback_target(second.id)

    def test_unhealthy_activation_restores_previous_healthy_release(self):
        first = self.manager.stage(self.app_files("healthy"))
        self.manager.activate(first.id)
        candidate = self.manager.stage(self.app_files("unhealthy"))
        calls = []
        boundaries = iter((101.0, 102.0))

        def restart(release_id):
            calls.append(("restart", release_id))
            return next(boundaries)

        def restore_settings():
            calls.append(("restore_settings", self.manager.current_release_id()))

        def health(release_id, boundary):
            calls.append(("health", release_id, boundary))
            if release_id == candidate.id:
                raise RuntimeError("candidate API unhealthy")
            return {"release": release_id, "fresh": True}

        activation = AppActivation(
            self.manager,
            candidate.id,
            restart=restart,
            restore_settings=restore_settings,
            health_check=health,
        )
        activation.validate_operation(None)
        activation.activate_operation(None)
        activation.restart_operation(None)
        activation.restore_settings_operation(None)
        with self.assertRaises(CandidateHealthFailed) as caught:
            activation.readiness_operation(None)

        self.assertTrue(caught.exception.failure.restored)
        self.assertEqual(caught.exception.failure.previous_release, first.id)
        self.assertEqual(self.manager.current_release_id(), first.id)
        self.assertIn(("health", first.id, 102.0), calls)

    def test_app_rollback_operations_never_build_provision_reboot_or_flash(self):
        first = self.manager.stage(self.app_files("one"))
        self.manager.activate(first.id)
        second = self.manager.stage(self.app_files("two"))
        self.manager.activate(second.id)
        calls = []
        activation = AppActivation(
            self.manager,
            first.id,
            restart=lambda release: calls.append(("restart", release)) or 100.0,
            restore_settings=lambda: calls.append(("restore",)),
            health_check=lambda release, boundary: {
                "release": release,
                "restart_boundary": boundary,
            },
        )
        operations = activation.operations()
        self.assertEqual(
            set(operations),
            {"app.validate", "app.activate", "host.restart", "state.restore", "health.readiness"},
        )
        for name in ("app.validate", "app.activate", "host.restart", "state.restore", "health.readiness"):
            operations[name](None)
        self.assertEqual(self.manager.current_release_id(), first.id)
        self.assertFalse(any("build" in item or "flash" in item or "provision" in item for item in calls))

    def test_integrated_rollback_records_candidate_failure_and_restoration(self):
        healthy = self.manager.stage(self.app_files("healthy"))
        self.manager.activate(healthy.id)
        unhealthy = self.manager.stage(self.app_files("unhealthy"))
        boundaries = iter((101.0, 102.0))
        activation = AppActivation(
            self.manager,
            unhealthy.id,
            restart=lambda _release: next(boundaries),
            health_check=lambda release, _boundary: (
                (_ for _ in ()).throw(RuntimeError("bad candidate"))
                if release == unhealthy.id
                else {"release": release, "fresh": True}
            ),
        )
        steps = build_app_rollback_steps(
            activation,
            capture_settings=lambda _context: OperationResult(details={"captured": True}),
        )
        receipt = DeployCoordinator().run(
            DeployContext(target="wall", mode="rollback", source_identity={}),
            steps,
        )
        self.assertEqual(receipt.outcome, "failure")
        self.assertEqual(self.manager.current_release_id(), healthy.id)
        failure = receipt.steps[-1].details["activation_failure"]
        self.assertEqual(failure["candidate_release"], unhealthy.id)
        self.assertEqual(failure["previous_release"], healthy.id)
        self.assertTrue(failure["restored"])

    def test_cli_rollback_fails_closed_without_integrated_execution(self):
        with self.assertRaises(SystemExit) as caught:
            main(("--root", os.fspath(self.target), "rollback"))
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
