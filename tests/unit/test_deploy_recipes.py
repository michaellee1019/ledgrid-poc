"""Regression checks for fast, recoverable deployment recipes."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DeployRecipeTests(unittest.TestCase):
    def test_deploy_recipes_default_to_tests_and_allow_explicit_skip(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        self.assertGreaterEqual(justfile.count('${TEST:-true}'), 2)
        self.assertIn('Skipping tests (TEST=$TEST)', justfile)
        self.assertIn('just deploy-precheck', justfile)
        self.assertIn('just test-unit test-rendering test-deployment', justfile)

    def test_full_deploy_caches_packages_and_reports_startup_failures(self):
        script = (ROOT / "tools/deployment/deploy.sh").read_text(encoding="utf-8")
        self.assertIn("venv/.ledgrid_requirements_sha256", script)
        self.assertIn("Python dependencies unchanged; skipping pip install", script)
        self.assertIn("<<'EOF'\nset -euo pipefail\ndeploy_dir=$1", script)
        self.assertIn("for attempt in {1..120}", script)
        self.assertIn("collecting startup logs", script)

    def test_fast_deploy_can_recover_when_old_web_process_is_broken(self):
        script = (ROOT / "tools/deployment/deploy_python.sh").read_text(encoding="utf-8")
        self.assertIn("Existing web service is unhealthy", script)
        self.assertIn('restore_saved=0', script)
        self.assertIn('if [ "$restore_saved" = 1 ]', script)
        self.assertIn("for attempt in {1..120}", script)


if __name__ == "__main__":
    unittest.main()
