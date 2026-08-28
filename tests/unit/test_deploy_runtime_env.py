"""Behavioral tests for digest-addressed runtime environments."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.deployment.runtime_env import (
    IDENTITY_MARKER,
    current_interpreter_identity,
    ensure_runtime_environment,
    environment_path,
)


class RuntimeEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_dir.name)
        self.lock = self.root / "requirements-pi.lock"
        self.lock.write_text("flask==3.1.3 --hash=sha256:abc\n", encoding="utf-8")
        self.install_calls: list[tuple[Path, Path]] = []
        self.smoke_calls: list[tuple[Path, Path]] = []

    def tearDown(self) -> None:
        self.temporary_dir.cleanup()

    @staticmethod
    def _create(_base_python: Path, target: Path) -> None:
        python = target / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("fake python", encoding="utf-8")

    def _install(self, python: Path, lock: Path) -> None:
        self.install_calls.append((python, lock))

    def _smoke(self, python: Path, root: Path) -> None:
        self.smoke_calls.append((python, root))

    def _ensure(self):
        return ensure_runtime_environment(
            self.root,
            self.lock,
            create_venv=self._create,
            install_lock=self._install,
            smoke=self._smoke,
        )

    def test_fresh_environment_is_smoked_before_atomic_activation(self):
        result = self._ensure()

        self.assertTrue(result.installed)
        self.assertEqual(len(self.install_calls), 1)
        self.assertEqual(len(self.smoke_calls), 1)
        self.assertTrue((self.root / "venv").is_symlink())
        self.assertEqual((self.root / "venv").resolve(), result.path)
        self.assertEqual(
            json.loads((result.path / IDENTITY_MARKER).read_text(encoding="utf-8")),
            result.identity.as_dict(),
        )

    def test_unchanged_identity_reuses_environment_without_install_or_smoke(self):
        first = self._ensure()
        self.install_calls.clear()
        self.smoke_calls.clear()

        second = self._ensure()

        self.assertFalse(second.installed)
        self.assertEqual(second.path, first.path)
        self.assertEqual(self.install_calls, [])
        self.assertEqual(self.smoke_calls, [])

    def test_lock_change_selects_a_distinct_environment_and_reinstalls(self):
        first = self._ensure()
        self.lock.write_text("flask==3.1.4 --hash=sha256:def\n", encoding="utf-8")

        second = self._ensure()

        self.assertNotEqual(first.path, second.path)
        self.assertTrue(second.installed)
        self.assertEqual((self.root / "venv").resolve(), second.path)
        self.assertTrue(first.path.is_dir())
        self.assertEqual(len(self.install_calls), 2)

    def test_failed_smoke_leaves_prior_environment_active(self):
        first = self._ensure()
        self.lock.write_text("flask==3.2.0 --hash=sha256:123\n", encoding="utf-8")

        def fail_smoke(_python: Path, _root: Path) -> None:
            raise RuntimeError("entrypoint import failed")

        with self.assertRaisesRegex(RuntimeError, "entrypoint import failed"):
            ensure_runtime_environment(
                self.root,
                self.lock,
                create_venv=self._create,
                install_lock=self._install,
                smoke=fail_smoke,
            )

        self.assertEqual((self.root / "venv").resolve(), first.path)
        self.assertEqual(
            [path.name for path in (self.root / ".venvs").iterdir()],
            [first.path.name],
        )

    def test_corrupt_marker_forces_a_clean_rebuild_of_same_identity(self):
        first = self._ensure()
        (first.path / IDENTITY_MARKER).write_text("not json", encoding="utf-8")

        second = self._ensure()

        self.assertEqual(second.path, first.path)
        self.assertTrue(second.installed)
        self.assertEqual(len(self.install_calls), 2)
        self.assertEqual(len(self.smoke_calls), 2)

    def test_failed_rebuild_of_corrupt_identity_keeps_prior_link_and_files(self):
        first = self._ensure()
        original_python = first.path / "bin" / "python"
        original_python.write_text("prior environment", encoding="utf-8")
        (first.path / IDENTITY_MARKER).write_text("not json", encoding="utf-8")

        def fail_smoke(_python: Path, _root: Path) -> None:
            raise RuntimeError("candidate failed")

        with self.assertRaisesRegex(RuntimeError, "candidate failed"):
            ensure_runtime_environment(
                self.root,
                self.lock,
                create_venv=self._create,
                install_lock=self._install,
                smoke=fail_smoke,
            )

        self.assertEqual((self.root / "venv").resolve(), first.path)
        self.assertEqual(original_python.read_text(encoding="utf-8"), "prior environment")

    def test_existing_mutable_venv_is_preserved_before_link_activation(self):
        legacy = self.root / "venv"
        (legacy / "bin").mkdir(parents=True)
        (legacy / "bin" / "python").write_text("legacy", encoding="utf-8")

        result = self._ensure()

        self.assertIsNotNone(result.migrated_legacy)
        assert result.migrated_legacy is not None
        self.assertTrue((result.migrated_legacy / "bin" / "python").exists())
        self.assertTrue((self.root / "venv").is_symlink())

    def test_empty_link_placeholder_does_not_collide_with_preserved_legacy(self):
        link = self.root / "venv"
        link.mkdir()
        legacy_digest = hashlib.sha256(
            os.fsencode(str(link.resolve()))
        ).hexdigest()[:12]
        preserved = self.root / ".venvs" / f"legacy-venv-{legacy_digest}"
        preserved.mkdir(parents=True)
        (preserved / "retained.txt").write_text("prior migration", encoding="utf-8")

        result = self._ensure()

        self.assertIsNone(result.migrated_legacy)
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), result.path)
        self.assertEqual(
            (preserved / "retained.txt").read_text(encoding="utf-8"),
            "prior migration",
        )

    def test_identity_includes_python_platform_and_lock_bytes(self):
        first = current_interpreter_identity(self.lock)
        expected = environment_path(self.root, first)
        self.lock.write_bytes(self.lock.read_bytes() + b"# changed\n")
        second = current_interpreter_identity(self.lock)

        self.assertTrue(first.implementation)
        self.assertTrue(first.python_version)
        self.assertTrue(first.soabi)
        self.assertIn(first.digest[:24], expected.name)
        self.assertNotEqual(first.digest, second.digest)


if __name__ == "__main__":
    unittest.main()
