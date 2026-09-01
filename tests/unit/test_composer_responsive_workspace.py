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

    def test_phone_operator_surface_is_single_and_precedes_secondary_editing(self) -> None:
        for selector in ('id="sceneIdentity"', 'id="connectionState"', 'id="observedIdentity"',
                         'id="liveAction"', 'id="libraryList"', 'class="inspector look-inspector"'):
            self.assertEqual(self.html.count(selector), 1, selector)
        self.assertIn('id="secondaryOperations" class="secondary-operations" open', self.html)
        self.assertLess(self.html.index('id="liveAction"'), self.html.index('id="secondaryOperations"'))
        self.assertLess(self.html.index('id="secondaryOperations"'), self.html.index('id="checkScene"'))
        self.assertNotIn('provider', self.html.lower())
        self.assertNotIn('staged', self.html.lower())

    def test_phone_layout_orders_operator_actions_and_look_ahead_of_deep_inspectors(self) -> None:
        self.assertIn('@media (max-width: 760px)', self.css)
        for token in (
            '.desktop-workspace { display: flex; flex-direction: column; align-items: stretch; }',
            '.operations-pane { order: 1; }',
            '.library-pane { order: 2; }',
            '.inspectors { order: 3; display: flex; flex-direction: column;',
            '.look-inspector { order: -1; }',
            '.preview-pane { order: 4; }',
            '.button, input:not([type="checkbox"]) { min-height: 44px; }',
            'select { height: 44px; min-height: 44px; padding-right: 2.25rem; -webkit-appearance: none; appearance: none;',
            '.library-filters .button { min-width: 44px; }',
            '.switch, .secondary-operations > summary, .diagnostics > summary { min-height: 44px; padding: .7rem 0; }',
        ):
            self.assertIn(token, self.css)

    def test_secondary_scene_controls_close_only_on_phone_and_remain_keyboard_reachable(self) -> None:
        self.assertIn("window.matchMedia('(max-width: 760px)')", self.script)
        self.assertIn("phoneLayout.addEventListener('change', syncSecondaryOperations);", self.script)
        self.assertIn("$('#secondaryOperations').open = !window.matchMedia('(max-width: 760px)').matches;", self.script)
        self.assertIn('id="checkScene"', self.html)
        self.assertIn('class="diagnostics"', self.html)
        self.assertNotIn('held until Go Live', self.script)

    def test_phone_layout_bounds_preview_identity_and_stretches_each_pane_without_overflow(self) -> None:
        for token in (
            'align-items: stretch;',
            '.library-pane, .preview-pane, .operations-pane { position: static; max-width: 100%;',
            '.preview-pane .pane-heading > * { min-width: 0; }',
            '#previewIdentity { min-width: 0; flex: 0 1 auto; }',
        ):
            self.assertIn(token, self.css)


if __name__ == '__main__':
    unittest.main()
