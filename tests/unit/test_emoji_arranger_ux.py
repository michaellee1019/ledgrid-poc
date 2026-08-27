from pathlib import Path
import re
import unittest


TEMPLATE = Path(__file__).resolve().parents[2] / "web" / "templates" / "emoji_arranger.html"


class EmojiArrangerUxContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = TEMPLATE.read_text(encoding="utf-8")

    def test_page_load_is_a_non_mutating_draft(self):
        self.assertRegex(self.source, r'id="workspaceState"[^>]*>Draft</span>')
        self.assertNotIn("startEmojiArranger();", self.source)
        self.assertNotIn("'/api/parameters'", self.source)
        self.assertIn("setWorkspaceState('draft')", self.source)

    def test_live_activation_hands_off_to_the_guarded_composer(self):
        starts = re.findall(r"/api/start/\$\{ANIMATION_NAME\}", self.source)
        self.assertEqual(starts, [])
        self.assertIn('id="activateInComposerLink" href="/composer"', self.source)
        self.assertIn("Use Composer for the server Check", self.source)
        self.assertIn("fetch('/api/stop', {method: 'POST'})", self.source)
        self.assertIn("Stop / Return to draft", self.source)

    def test_edits_and_presets_target_the_isolated_preview(self):
        self.assertIn("/api/preview/${ANIMATION_NAME}/with_params", self.source)
        self.assertIn("setDraftParameter('text', elements.text.value)", self.source)
        self.assertIn("data-preset=", self.source)
        self.assertIn("Preview only", self.source)
        self.assertIn("Wall unchanged", self.source)

    def test_preview_uses_physical_wall_orientation(self):
        self.assertIn('width="330" height="1380"', self.source)
        self.assertIn("strip * cellWidth", self.source)
        self.assertIn("led * cellHeight", self.source)
        self.assertIn("first 8 of", self.source)
        self.assertIn("active_columns", self.source)

    def test_schema_controls_preserve_falsey_values_and_hide_internal_values(self):
        self.assertIn("Object.prototype.hasOwnProperty.call(currentParams, name)", self.source)
        self.assertIn("definition.step !== undefined", self.source)
        self.assertIn("INTERNAL_PARAMETER_NAMES", self.source)
        self.assertIn("definition.type === 'object'", self.source)
        self.assertNotIn("|| definition.default", self.source)
        self.assertNotIn("[object Object]", self.source)


if __name__ == "__main__":
    unittest.main()
