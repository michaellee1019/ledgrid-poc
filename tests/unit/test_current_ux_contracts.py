"""Check local links in the current Composer contract and runbooks."""

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
DOCS = (
    "README.md",
    "web/README.md",
    "docs/CURRENT_COMPOSER_VALIDATION.md",
    "docs/CURRENT_UX_ACCEPTANCE.md",
    "docs/DEPLOYMENT.md",
)
LINK = re.compile(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)")


@pytest.mark.parametrize("name", DOCS)
def test_current_documentation_local_links_resolve(name):
    document = ROOT / name
    for target in LINK.findall(document.read_text(encoding="utf-8")):
        if "://" not in target and not target.startswith("mailto:"):
            assert (document.parent / target).exists(), f"Broken link in {name}: {target}"
