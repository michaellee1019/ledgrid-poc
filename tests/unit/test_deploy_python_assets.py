"""Regression tests for the fast application's rsync manifest."""

import unittest
from pathlib import Path


class DeployPythonAssetTests(unittest.TestCase):
    def test_fast_deploy_includes_presets_and_web_templates(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "tools" / "deployment" / "deploy_python.sh").read_text(encoding="utf-8")

        preset_rule = "--include '/presets/animations/***'"
        template_rule = "--include '/web/templates/*.html'"
        final_exclude = "--exclude '*'"

        self.assertIn(preset_rule, script)
        self.assertIn(template_rule, script)
        self.assertLess(script.index(preset_rule), script.index(final_exclude))
        self.assertLess(script.index(template_rule), script.index(final_exclude))


if __name__ == "__main__":
    unittest.main()
