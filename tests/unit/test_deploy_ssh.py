"""Regression tests for dedicated wall SSH identity resolution."""

from __future__ import annotations

import os
import stat
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from tools.deployment.deploy_entrypoint import resolve_deploy_target
from tools.deployment.deploy_ssh import ssh_options


class DeploySSHTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"PI_HOST": "", "SSH_KEY": "", "LEDGRID_SSH_KEY": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def test_target_defaults_to_pi5(self):
        self.assertEqual(resolve_deploy_target(None), "ledgridwall5@ledgridwall5.local")

    def test_explicit_target_overrides_environment(self):
        os.environ["PI_HOST"] = "user@wall.local"
        self.assertEqual(resolve_deploy_target(None), "user@wall.local")
        self.assertEqual(resolve_deploy_target("user@192.0.2.1"), "user@192.0.2.1")

    def test_missing_key_fails_before_ssh(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "Dedicated SSH key"):
                ssh_options(root)

    def test_unreadable_explicit_path_does_not_fall_back_to_repo_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / ".gpt-key"
            key.write_text("private-key\n", encoding="utf-8")
            key.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "Dedicated SSH key"):
                ssh_options(root, "missing-key")

    def test_uses_repository_local_gpt_key_when_present(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / ".gpt-key"
            key.write_text("private-key\n", encoding="utf-8")
            key.chmod(stat.S_IRUSR | stat.S_IWUSR)
            options = ssh_options(root, None)
            self.assertEqual(options[0:4], ["-i", str(key.resolve()), "-o", "IdentitiesOnly=yes"])
            self.assertIn("IdentityAgent=none", options)
            self.assertIn("BatchMode=yes", options)

    def test_explicit_ssh_key_overrides_default_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            explicit = root / "custom-key"
            explicit.write_text("private-key\n", encoding="utf-8")
            explicit.chmod(stat.S_IRUSR | stat.S_IWUSR)
            (root / ".gpt-key").write_text("ignored\n", encoding="utf-8")
            (root / ".gpt-key").chmod(stat.S_IRUSR | stat.S_IWUSR)
            options = ssh_options(root, str(explicit))
            self.assertEqual(options[1], str(explicit.resolve()))

    def test_rejects_world_readable_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / ".gpt-key"
            key.write_text("private-key\n", encoding="utf-8")
            key.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)
            with self.assertRaisesRegex(ValueError, "permissions must not grant group/other"):
                ssh_options(root, None)

    def test_env_ssh_key_is_used_when_set(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / "from-env"
            key.write_text("private-key\n", encoding="utf-8")
            key.chmod(stat.S_IRUSR | stat.S_IWUSR)
            previous = os.environ.get("SSH_KEY")
            os.environ["SSH_KEY"] = str(key)
            try:
                options = ssh_options(root, None)
            finally:
                if previous is None:
                    os.environ.pop("SSH_KEY", None)
                else:
                    os.environ["SSH_KEY"] = previous
            self.assertEqual(options[1], str(key.resolve()))


if __name__ == "__main__":
    unittest.main()
