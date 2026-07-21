"""Regression tests for the fast application's rsync manifest."""

import unittest
from pathlib import Path


class DeployPythonAssetTests(unittest.TestCase):
    def test_fast_deploy_includes_curated_presets_and_web_templates(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "tools" / "deployment" / "deploy_python.sh").read_text(encoding="utf-8")

        preset_rule = "-- 'presets/animations/**/*.json'"
        template_rule = "--include '*.html'"
        final_exclude = "--exclude '*'"

        self.assertIn(preset_rule, script)
        self.assertIn("git -C \"$LOCAL_DIR\" ls-files", script)
        self.assertIn("--files-from=-", script)
        self.assertIn(template_rule, script)
        self.assertLess(script.index(template_rule), script.index(final_exclude))


if __name__ == "__main__":
    unittest.main()
