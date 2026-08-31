"""DOM and state-owner contracts for the one-page responsive Composer."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class ComposerResponsiveWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        self.script = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.css = (ROOT / "web/static/css/composer_slice.css").read_text(encoding="utf-8")

    def test_one_semantic_workspace_exposes_each_named_section_and_existing_actions(self) -> None:
        self.assertIn('class="composer-workspace"', self.html)
        self.assertIn('class="composer-editor"', self.html)
        self.assertIn('class="composer-rail"', self.html)
        for section in ("build", "preview", "library", "live"):
            self.assertIn(f'href="#{section}"', self.html)
            self.assertIn(f'id="{section}"', self.html)
        self.assertEqual(self.html.count('id="goLive"'), 1)
        self.assertEqual(self.html.count('id="stopScene"'), 1)
        self.assertLess(self.html.index('id="recoveryCard"'), self.html.index('id="build"'))
        self.assertLess(self.html.index('id="previewIdentity"'), self.html.index('id="desiredIdentity"'))

    def test_desktop_rail_and_narrow_touch_workspace_are_declared_without_duplicate_ui(self) -> None:
        self.assertIn('grid-template-columns: minmax(0, 1fr) minmax(290px, 360px)', self.css)
        self.assertIn('.composer-rail { min-width: 0; position: sticky;', self.css)
        self.assertIn('@media (max-width: 760px)', self.css)
        self.assertIn('.composer-workspace { display: flex; flex-direction: column;', self.css)
        self.assertIn('.live-card { background: #102126;', self.css)
        self.assertIn('position: sticky; top: .5rem;', self.css)
        self.assertIn('.preview-canvas { max-height: 42vh;', self.css)
        self.assertIn('#previewIdentity { overflow-wrap: anywhere;', self.css)
        self.assertIn('.button { min-height: 2.75rem;', self.css)

    def test_semantic_navigation_is_an_isolated_focus_only_owner(self) -> None:
        navigation = (ROOT / 'web/static/js/composer_navigation.js').read_text(encoding='utf-8')
        self.assertIn('target.focus({preventScroll:true})', navigation)
        self.assertIn("window.addEventListener('popstate', projectLocation);", navigation)
        self.assertNotIn("window.addEventListener('hashchange'", self.script)
        self.assertNotIn("window.addEventListener('resize'", self.script)


if __name__ == '__main__':
    unittest.main()
