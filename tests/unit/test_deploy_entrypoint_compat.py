"""Compatibility between entrypoint deploy and the lean main tree."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.deployment import deploy_entrypoint, deploy_target


class DeployEntrypointCompatTests(unittest.TestCase):
    def test_skips_missing_composer_asset_builder(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            with patch.object(deploy_entrypoint.subprocess, "run") as run:
                deploy_entrypoint.maybe_build_browser_assets(stage)
                run.assert_not_called()

    def test_requirements_file_falls_back_to_requirements_txt(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            (app / "requirements.txt").write_text("flask\n", encoding="utf-8")
            self.assertEqual(deploy_target.requirements_file(app).name, "requirements.txt")

    def test_readiness_url_uses_status_endpoint(self):
        self.assertEqual(deploy_target.readiness_url("5000"), "http://127.0.0.1:5000/api/status")

    def test_stop_service_tolerates_missing_unit(self):
        with patch.object(deploy_target.subprocess, "run", return_value=type("R", (), {"returncode": 5})()) as run:
            deploy_target.stop_ledgrid_service()
            run.assert_called_once()

    def test_build_dependencies_install_python3_dev(self):
        with patch.object(deploy_target.subprocess, "run") as run:
            deploy_target.ensure_python_build_dependencies()
            script = run.call_args.kwargs["input"]
            self.assertIn("python3-dev", script)
            self.assertIn("build-essential", script)


if __name__ == "__main__":
    unittest.main()
