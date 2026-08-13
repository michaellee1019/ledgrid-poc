#!/usr/bin/env python3
"""Generate the compatibility inventory for shipped animations.

The inventory is deliberately derived from the same manifests and concrete
classes used by the production loader. It records the current Python host-scene
boundary without pretending to be the Phase 2C unified descriptor catalog.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
from dataclasses import dataclass
import inspect
import io
from pathlib import Path
import sys
import textwrap
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from animation.core.base import StatefulAnimationBase
from animation.core.manager import AnimationManager
from animation.core.plugin_loader import AnimationPluginLoader


INVENTORY_VERSION = 2
DEFAULT_OUTPUT = ROOT / "docs" / "animation-plugin-compatibility-inventory.md"

ORDINARY_BACKGROUND = "ordinary_background"
COMPATIBILITY_FULL_SCENE = "compatibility_full_scene"
PYTHON_OVERLAY = "python_overlay"
UNSUPPORTED_DIRECT_HARDWARE_STATEFUL = "unsupported_direct_hardware_stateful"

# These calls bypass the manager-owned frame/presentation path. Attribute reads
# such as ``controller.total_leds`` are part of normal plugin construction and
# are intentionally not considered hardware ownership.
DIRECT_CONTROLLER_MUTATIONS = frozenset({
    "clear",
    "set_all_pixels",
    "set_frame",
    "set_partial_frame",
    "set_pixel",
    "show",
    "write",
})

# Phase 1 freezes the current Clock as a compatibility full scene.  The future
# clock_overlay is a separate package; no class-name heuristic should silently
# apply this exception to other plugins.
FULL_SCENE_COMPATIBILITY = {
    "clock": "Owns both the clock face and its opaque authored background.",
}


@dataclass(frozen=True)
class InventoryEntry:
    plugin_id: str
    class_name: str
    gallery: str
    classification: str
    evidence: str
    direct_controller_calls: tuple[str, ...]


def direct_controller_mutations(animation_class: type) -> tuple[str, ...]:
    """Return direct ``self.controller.<mutation>()`` calls in a class body."""

    try:
        source = textwrap.dedent(inspect.getsource(animation_class))
    except (OSError, TypeError):
        return ()
    tree = ast.parse(source)
    mutations: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = node.func.value
        if (
            isinstance(receiver, ast.Attribute)
            and receiver.attr == "controller"
            and isinstance(receiver.value, ast.Name)
            and receiver.value.id == "self"
            and node.func.attr in DIRECT_CONTROLLER_MUTATIONS
        ):
            mutations.add(node.func.attr)
    return tuple(sorted(mutations))


def classify_plugin(
    plugin_id: str,
    animation_class: type,
    gallery: str,
    *,
    role: str | None = None,
) -> InventoryEntry:
    direct_calls = direct_controller_mutations(animation_class)
    if issubclass(animation_class, StatefulAnimationBase) or direct_calls:
        reasons: list[str] = []
        if issubclass(animation_class, StatefulAnimationBase):
            reasons.append("subclasses StatefulAnimationBase")
        if direct_calls:
            reasons.append(
                "calls self.controller." + ", self.controller.".join(direct_calls)
            )
        classification = UNSUPPORTED_DIRECT_HARDWARE_STATEFUL
        evidence = "; ".join(reasons) + "; excluded from composition"
    elif plugin_id in FULL_SCENE_COMPATIBILITY:
        classification = COMPATIBILITY_FULL_SCENE
        evidence = FULL_SCENE_COMPATIBILITY[plugin_id]
    elif role == "overlay":
        classification = PYTHON_OVERLAY
        evidence = (
            "Explicit Python overlay manifest; returns premultiplied RGBA8 "
            "through the manager-owned composition path."
        )
    else:
        classification = ORDINARY_BACKGROUND
        evidence = "Concrete AnimationBase renderer; no direct controller mutation."
    return InventoryEntry(
        plugin_id=plugin_id,
        class_name=animation_class.__name__,
        gallery=gallery,
        classification=classification,
        evidence=evidence,
        direct_controller_calls=direct_calls,
    )


def build_inventory() -> tuple[InventoryEntry, ...]:
    """Load and classify every shipped package in deterministic plugin-ID order."""

    loader = AnimationPluginLoader(allowed_plugins=AnimationManager.ALLOWED_PLUGINS)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        plugins = loader.load_all_plugins()
    shipped = tuple(loader.scan_plugins())
    if set(shipped) != AnimationManager.ALLOWED_PLUGINS:
        raise RuntimeError("manager allowlist and shipped manifest packages differ")
    if set(plugins) != set(shipped):
        missing = sorted(set(shipped) - set(plugins))
        raise RuntimeError(f"shipped plugins failed to load: {missing}")

    return tuple(
        classify_plugin(
            plugin_id,
            plugins[plugin_id],
            str(loader.plugin_manifests[plugin_id].get("gallery", "show")),
            role=loader.plugin_manifests[plugin_id].get("role"),
        )
        for plugin_id in shipped
    )


def _count(entries: Iterable[InventoryEntry], classification: str) -> int:
    return sum(entry.classification == classification for entry in entries)


def render_markdown(entries: tuple[InventoryEntry, ...]) -> str:
    counts = {
        ORDINARY_BACKGROUND: _count(entries, ORDINARY_BACKGROUND),
        COMPATIBILITY_FULL_SCENE: _count(entries, COMPATIBILITY_FULL_SCENE),
        PYTHON_OVERLAY: _count(entries, PYTHON_OVERLAY),
        UNSUPPORTED_DIRECT_HARDWARE_STATEFUL: _count(
            entries, UNSUPPORTED_DIRECT_HARDWARE_STATEFUL
        ),
    }
    lines = [
        "# Animation Plugin Compatibility Inventory",
        "",
        f"Inventory schema: `{INVENTORY_VERSION}`.",
        "",
        "This compatibility inventory is generated from the shipped `manifest.json` files,",
        "the production plugin loader, and each loaded concrete class. It describes",
        "compatibility with the current fixed host background-plus-overlay stack; it is",
        "not the future unified component descriptor catalog.",
        "",
        "Regenerate with:",
        "",
        "```bash",
        "uv run --with numpy --with pillow tools/generate_animation_compatibility_inventory.py",
        "uv run --with numpy --with pillow tools/generate_animation_compatibility_inventory.py --check",
        "```",
        "",
        "## Classification rules",
        "",
        "- `ordinary_background`: a concrete frame renderer with no direct controller",
        "  mutation. The compatibility adapter treats it as a Python background.",
        "- `compatibility_full_scene`: a deliberate Phase 1 exception that owns a",
        "  complete authored scene. The existing `clock` stays here for preset and",
        "  command compatibility while `clock_overlay` supplies composition.",
        "- `python_overlay`: an explicit Python overlay that returns premultiplied",
        "  RGBA8 and is accepted only by the manager-owned composition path.",
        "- `unsupported_direct_hardware_stateful`: a `StatefulAnimationBase` subclass",
        "  or a class that calls a controller mutation method directly. It cannot join",
        "  composition without conversion to the manager-owned frame contract.",
        "",
        "Reads such as `controller.total_leds` are ordinary geometry access, not",
        "hardware ownership. The scanner conservatively records direct mutations in",
        "the concrete class; inherited manager/base presentation is outside plugin code.",
        "",
        "## Summary",
        "",
        "| Classification | Count |",
        "| --- | ---: |",
        f"| `ordinary_background` | {counts[ORDINARY_BACKGROUND]} |",
        f"| `compatibility_full_scene` | {counts[COMPATIBILITY_FULL_SCENE]} |",
        f"| `python_overlay` | {counts[PYTHON_OVERLAY]} |",
        "| `unsupported_direct_hardware_stateful` | "
        f"{counts[UNSUPPORTED_DIRECT_HARDWARE_STATEFUL]} |",
        f"| **Total shipped packages** | **{len(entries)}** |",
        "",
        "## Shipped packages",
        "",
        "| Plugin ID | Concrete class | Gallery | Compatibility | Evidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in entries:
        lines.append(
            f"| `{entry.plugin_id}` | `{entry.class_name}` | `{entry.gallery}` | "
            f"`{entry.classification}` | {entry.evidence} |"
        )
    lines.extend([
        "",
        "## Current conclusion",
        "",
        "Every shipped package has exactly one classification. The current tree has no",
        "stateful or direct-hardware plugin package. Existing opaque renderers retain the",
        "ordinary Python-background compatibility path, the original Clock remains the",
        "sole compatibility full scene, and explicit overlay packages enter only through",
        "manager-owned composition. This is not permission to make backgrounds transparent",
        "or to infer overlay semantics from black RGB.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the committed inventory differs from current shipped code",
    )
    args = parser.parse_args()

    entries = build_inventory()
    rendered = render_markdown(entries)
    output = args.output.resolve()
    if args.check:
        try:
            existing = output.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"compatibility inventory missing: {output}: {exc}", file=sys.stderr)
            return 1
        if existing != rendered:
            print(
                "compatibility inventory is stale; regenerate with "
                "tools/generate_animation_compatibility_inventory.py",
                file=sys.stderr,
            )
            return 1
        print(f"compatibility inventory is current: {len(entries)} plugins")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(f"wrote {output} with {len(entries)} plugins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
