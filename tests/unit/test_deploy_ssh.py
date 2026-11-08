"""Regression tests for optional deployment SSH identity resolution."""

from __future__ import annotations

import os
import stat
import unittest
from pathlib import Path
import tempfile

from tools.deployment.deploy_entrypoint import normalize_pi_host, resolve_deploy_target
from tools.deployment.deploy_ssh import ssh_options


class DeploySSHTests(unittest.TestCase):
    def test_legacy_hostname_maps_to_pi5_target(self):
        self.assertEqual(
            normalize_pi_host("ledgridwall@ledgridwall.local"),
            "ledgridwall5@ledgridwall5.local",
        )
        previous = os.environ.get("PI_HOST")
        os.environ.pop("PI_HOST", None)
        try:
            self.assertEqual(resolve_deploy_target(None), "ledgridwall5@ledgridwall5.local")
        finally:
            if previous is None:
                os.environ.pop("PI_HOST", None)
            else:
                os.environ["PI_HOST"] = previous

    def test_pi_host_env_legacy_value_is_upgraded(self):
        previous = os.environ.get("PI_HOST")
        os.environ["PI_HOST"] = "ledgridwall@ledgridwall.local"
        try:
            self.assertEqual(
                resolve_deploy_target(None),
                "ledgridwall5@ledgridwall5.local",
            )
        finally:
            if previous is None:
                os.environ.pop("PI_HOST", None)
            else:
                os.environ["PI_HOST"] = previous

    def test_falls_back_to_default_ssh_without_dedicated_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            options = ssh_options(root, None)
            self.assertNotIn("-i", options)
            self.assertIn("-o", options)
            self.assertIn("BatchMode=yes", options)

    def test_uses_repository_local_gpt_key_when_present(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / ".gpt-key"
            key.write_text("private-key\n", encoding="utf-8")
            key.chmod(stat.S_IRUSR | stat.S_IWUSR)
            options = ssh_options(root, None)
            self.assertEqual(options[0:4], ["-i", str(key.resolve()), "-o", "IdentitiesOnly=yes"])

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
