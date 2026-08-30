"""Current Composer route, documentation, and command-surface contracts."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]



class CurrentComposerContractTests(unittest.TestCase):
    """Keep current product documentation aligned with the served Composer UI."""

    CURRENT_DOCS = (
        ROOT / "README.md",
        ROOT / "web" / "README.md",
        ROOT / "docs" / "CURRENT_UX_ACCEPTANCE.md",
        ROOT / "docs" / "STUDIO_NEXT.md",
        ROOT / "docs" / "browser-composer-portable-evidence-2026-08-27.md",
        ROOT / "docs" / "browser-qualification-rel01.md",
        ROOT / "docs" / "browser-qualification-rel01-supplementary-2026-08-27.md",
    )
    REMOVED_UI_PATTERNS = (
        r"(?<!ipc)/control\\b",
        r"/painter\\b",
        r"/studio-next\\b",
        r"/api/v1/studio-next\\b",
        r"Studio Next",
        r"Check & activate in Composer",
    )

    def test_route_inventory_names_only_composer_as_the_browser_product(self):
        source = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
        route_docs = (ROOT / "web" / "README.md").read_text(encoding="utf-8")

        self.assertIn("return redirect('/composer', code=302)", source)
        self.assertIn("@self.app.route('/composer')", source)
        self.assertIn("sole browser product", route_docs)
        for removed_route in ("'/control'", "'/painter'", "'/studio-next'"):
            self.assertNotIn(f"@self.app.route({removed_route}", source)

    def test_current_documentation_links_resolve_locally(self):
        link_pattern = re.compile(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)")
        for document in self.CURRENT_DOCS:
            contents = document.read_text(encoding="utf-8")
            for target in link_pattern.findall(contents):
                if "://" in target or target.startswith("mailto:"):
                    continue
                candidate = (document.parent / target).resolve()
                with self.subTest(document=document.name, target=target):
                    self.assertTrue(candidate.exists(), f"missing link target: {candidate}")

    def test_current_documentation_has_no_removed_ui_or_api_terms(self):
        for document in self.CURRENT_DOCS:
            contents = document.read_text(encoding="utf-8")
            for removed_pattern in self.REMOVED_UI_PATTERNS:
                with self.subTest(document=document.name, removed_pattern=removed_pattern):
                    self.assertIsNone(re.search(removed_pattern, contents))

    def test_full_matrix_recipes_are_not_current_commands(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        browser_docs = "\n".join(
            document.read_text(encoding="utf-8")
            for document in self.CURRENT_DOCS
            if "browser" in document.name
        )

        self.assertNotRegex(justfile, r"(?m)^browser-qualification(?:-setup)?:")
        self.assertNotIn("just browser-qualification", browser_docs)
        self.assertNotIn("just browser-qualification-setup", browser_docs)

    def test_post_squash_device_safeguards_remain_in_source(self):
        retained_paths = (
            "tools/browser_qualification/external_iphone_evidence.py",
            "tools/browser_qualification/source_identity.py",
            "tools/browser_qualification/evidence.py",
            "tools/browser_qualification/rel01_manifest.json",
        )
        for relative_path in retained_paths:
            with self.subTest(relative_path=relative_path):
                self.assertTrue((ROOT / relative_path).is_file())


if __name__ == "__main__":
    unittest.main()
