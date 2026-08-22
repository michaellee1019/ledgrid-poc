#!/usr/bin/env python3
"""Static acceptance checks for the dependency-free Lumen Path prototype."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class PrototypeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.local_refs: list[str] = []
        self.href_targets: list[str] = []
        self.external_refs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"] or "")
        for attribute in ("src", "href"):
            value = values.get(attribute)
            if not value:
                continue
            if value.startswith("#"):
                self.href_targets.append(value[1:])
            elif re.match(r"(?:https?:)?//", value):
                self.external_refs.append(value)
            elif not value.startswith(("data:", "mailto:", "tel:")):
                self.local_refs.append(value.split("#", 1)[0])


def require(condition: bool, message: str, failures: list[str]) -> None:
    if condition:
        print(f"PASS  {message}")
    else:
        print(f"FAIL  {message}")
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    files = {name: ROOT / name for name in ("index.html", "styles.css", "app.js", "PARADIGM.md", "README.md")}
    require(all(path.is_file() for path in files.values()), "all prototype deliverables exist", failures)
    if failures:
        return 1

    html = files["index.html"].read_text(encoding="utf-8")
    css = files["styles.css"].read_text(encoding="utf-8")
    js = files["app.js"].read_text(encoding="utf-8")
    paradigm = files["PARADIGM.md"].read_text(encoding="utf-8")

    parser = PrototypeParser()
    parser.feed(html)
    require(len(parser.ids) == len(set(parser.ids)), "HTML IDs are unique", failures)
    require(all((ROOT / ref).is_file() for ref in parser.local_refs), "all local HTML assets resolve", failures)
    require(all(target in parser.ids for target in parser.href_targets), "all internal fragment links resolve", failures)
    require(not parser.external_refs, "HTML has no external assets", failures)

    combined_runtime = html + css + js
    network_patterns = [r"https?://", r"//cdn\.", r"\bfetch\s*\(", r"XMLHttpRequest", r"new\s+WebSocket"]
    require(not any(re.search(pattern, combined_runtime, re.I) for pattern in network_patterns), "runtime has no network or CDN dependency", failures)
    require("web/templates" not in combined_runtime and "web/static" not in combined_runtime, "prototype does not reference existing frontend paths", failures)

    match = re.search(r"const componentNames = \[(.*?)\n\s*\];", js, re.S)
    names: list[str] = []
    if match:
        names = json.loads("[" + match.group(1) + "]")
    require(len(names) == 52, "field guide defines exactly 52 components", failures)
    total = sum(6 if index < 32 else 5 for index in range(len(names)))
    require(total == 292 and "292" in html, "field guide generates and declares exactly 292 possibilities", failures)

    safety_phrases = [
        "PRIVATE REHEARSAL", "Wall unaffected", "physical wall is untouched",
        "Press and hold to take live", "not receiver framebuffer readback",
        "Expected degraded", "Save layout only", "known Python fallback",
    ]
    require(all(phrase.lower() in combined_runtime.lower() for phrase in safety_phrases), "critical preview/live/provider safety copy is present", failures)
    required_surfaces = [
        "Calm the room", "Host guests", "Play together", "Paint with light", "Author an arc",
        "Neutral", "Quiet", "Cozy", "Vivid", "Celebration", "Target FPS", "Punch a hole",
        "D-pad", "Pixel painter", "Plant masks", "Scene Studio", "Wall Care",
    ]
    require(all(term.lower() in combined_runtime.lower() for term in required_surfaces), "required activity and control surfaces are represented", failures)
    require("@media (max-width: 720px)" in css and ".mobile-nav" in css, "phone-specific layout is defined", failures)
    require("prefers-reduced-motion" in css, "reduced-motion preference is respected", failures)
    require("52 components" in paradigm and "292" in paradigm, "paradigm documents the scale contract", failures)

    require(css.count("{") == css.count("}"), "CSS braces are balanced", failures)
    node = shutil.which("node")
    if node:
        result = subprocess.run([node, "--check", str(files["app.js"])], capture_output=True, text=True, check=False)
        require(result.returncode == 0, "JavaScript passes node --check", failures)
        if result.returncode != 0:
            print(result.stderr)
    else:
        print("SKIP  node is unavailable; JavaScript syntax check skipped")

    if failures:
        print(f"\n{len(failures)} check(s) failed.")
        return 1
    print("\nAll static checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
