"""Focused contracts for the local Composer semantic workspace URL."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class ComposerSemanticNavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        self.slice = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.script = (ROOT / "web/static/js/composer_navigation.js").read_text(encoding="utf-8")

    def test_one_versioned_bounded_url_contract_canonicalizes_safe_defaults(self) -> None:
        self.assertIn("const VERSION = 'v1'", self.script)
        self.assertIn("new Set(['build', 'preview', 'library', 'live'])", self.script)
        self.assertIn("new Set(['all', 'starter', 'look', 'favorites', 'recent'])", self.script)
        self.assertIn("const allowed=new Set(['composer', 'section', 'filter', 'q', 'ref'])", self.script)
        self.assertIn("params.get('composer') !== VERSION", self.script)
        self.assertIn("params.getAll(key).length !== 1", self.script)
        self.assertIn("strictQuery(url)", self.script)
        self.assertIn("history.replaceState(null, '', canonicalUrl(fallback))", self.script)

    def test_search_is_case_preserved_and_selected_reference_is_authoritative(self) -> None:
        self.assertIn("value.trim().replace(/\\s+/g, ' ')", self.script)
        self.assertIn("item.name.toLocaleLowerCase().includes(value.query.toLocaleLowerCase())", self.script)
        self.assertIn("snapshot.items.find((item) => sameReference(item, value.reference))", self.script)
        self.assertIn("Selected saved look is local to this installation.", self.script)
        self.assertIn("row.dataset.libraryKind=item.kind", self.slice)
        self.assertIn("composer-library-card-select", self.slice)
        self.assertIn("event.target.closest('button')", self.slice)
        self.assertIn("row.tabIndex=0", self.slice)
        self.assertIn("row.setAttribute('aria-current', 'true')", self.slice)
        self.assertIn("event.key === 'Enter' || event.key === ' '", self.slice)
        self.assertNotIn('role="listbox"', self.html)

    def test_url_projection_is_newest_wins_and_never_owns_scene_mutations(self) -> None:
        self.assertIn("let projection = 0", self.script)
        self.assertIn("if (ticket !== projection) return;", self.script)
        self.assertIn("window.addEventListener('popstate', projectLocation);", self.script)
        self.assertIn("navigator.clipboard.writeText(url)", self.script)
        self.assertIn("target.scrollIntoView({block:'nearest', inline:'nearest'})", self.script)
        self.assertIn("apply(fallback, ticket); message(text)", self.script)
        self.assertIn("id=\"copyComposerLink\"", self.html)
        self.assertIn("window.__composerLibraryNavigation = {apply:applyLibraryNavigation", self.slice)
        for forbidden in ("fetch(", "openLibraryItem(", "recordRecent(", "toggleFavorite(", "edit(", "autosave(", "goLive(", "stopScene("):
            self.assertNotIn(forbidden, self.script)


if __name__ == "__main__":
    unittest.main()
