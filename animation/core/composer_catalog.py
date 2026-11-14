"""Discover the Scene roles explicitly opted in by shipped plugin manifests."""

from functools import lru_cache
from importlib import import_module
from pathlib import Path
import json

from animation.core.component_catalog import ComponentCatalog, component_definition


def current_component_catalog() -> ComponentCatalog:
    return discover_composer_components(Path(__file__).resolve().parents[1] / "plugins")


@lru_cache(maxsize=4)
def discover_composer_components(plugins: Path, package: str = "animation.plugins") -> ComponentCatalog:
    """Load declarations from shipped packages without constructing renderers."""
    definitions = []
    for path in sorted(plugins.glob("*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        membership = manifest.get("composer")
        if not membership:
            continue
        plugin_id = path.parent.name
        implementation = getattr(import_module(f"{package}.{plugin_id}"), manifest["class"])
        descriptor_methods = ("component_descriptor",) if membership is True else membership
        if not isinstance(descriptor_methods, (list, tuple)) or any(
            not isinstance(method, str) or not method.endswith("_descriptor")
            for method in descriptor_methods
        ):
            raise ValueError(f"invalid Composer membership for {plugin_id}")
        for method in descriptor_methods:
            descriptor = getattr(implementation, method)()
            definitions.append(component_definition(plugin_id, implementation, descriptor))
    return ComponentCatalog(definitions)
