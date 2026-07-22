#!/usr/bin/env python3
"""Discovery and loading for self-contained animation plugins."""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Type

from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT

from .base import AnimationBase


class AnimationPluginLoader:
    """Load animation packages and external flat plugin files.

    Shipped plugins are packages with a validated ``manifest.json``. A package
    owns its implementation, tests, curated presets, and assets. Flat ``.py``
    files remain supported for external plugin directories so existing local
    extensions do not need to migrate in lock-step with the repository.
    """

    MANIFEST_FILENAME = "manifest.json"
    DEFAULT_PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins"

    def __init__(
        self,
        plugins_dir: Optional[str] = None,
        allowed_plugins: Optional[Iterable[str]] = None,
    ):
        self.plugins_dir = Path(plugins_dir or self.DEFAULT_PLUGINS_DIR).resolve()
        self.allowed_plugins = (
            set(allowed_plugins) if allowed_plugins is not None else None
        )

        repo_root = self.plugins_dir.parent.parent
        if (repo_root / "drivers").exists() and str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        if str(self.plugins_dir) not in sys.path:
            sys.path.insert(0, str(self.plugins_dir))

        self.loaded_plugins: Dict[str, Type[AnimationBase]] = {}
        self.plugin_files: Dict[str, Path] = {}
        self.plugin_manifests: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def shipped_plugin_ids(cls) -> List[str]:
        """Return validated shipped package IDs in deterministic order."""
        return cls().scan_plugins()

    @staticmethod
    def _validate_manifest(
        manifest_path: Path, plugin_name: str
    ) -> Dict[str, Any]:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid manifest {manifest_path}: {exc}") from exc

        if not isinstance(payload, dict):
            raise ValueError(f"manifest must contain an object: {manifest_path}")
        if re.fullmatch(r"[a-z][a-z0-9_]*", plugin_name) is None:
            raise ValueError(f"invalid plugin package ID {plugin_name!r}: {manifest_path}")
        if payload.get("plugin_id") != plugin_name:
            raise ValueError(
                f"manifest plugin_id must match package directory {plugin_name!r}: "
                f"{manifest_path}"
            )
        if not isinstance(payload.get("class"), str) or not payload["class"].strip():
            raise ValueError(f"manifest class must be a non-empty string: {manifest_path}")
        if not isinstance(payload.get("icon"), str) or not payload["icon"].strip():
            raise ValueError(f"manifest icon must be a non-empty string: {manifest_path}")
        if payload.get("gallery", "show") not in {"show", "test"}:
            raise ValueError(f"manifest gallery must be 'show' or 'test': {manifest_path}")
        return payload

    def scan_plugins(self) -> List[str]:
        """Scan package and external flat plugins in deterministic ID order."""
        self.plugin_files.clear()
        self.plugin_manifests.clear()
        if not self.plugins_dir.is_dir():
            return []

        candidates: Dict[str, Path] = {}
        manifests: Dict[str, Dict[str, Any]] = {}

        for path in sorted(self.plugins_dir.iterdir(), key=lambda item: item.name):
            if path.name.startswith("__"):
                continue
            if path.is_file() and path.suffix == ".py":
                if path.stem in candidates:
                    raise ValueError(f"duplicate flat and package plugin ID: {path.stem}")
                candidates[path.stem] = path
                continue
            init_path = path / "__init__.py"
            manifest_path = path / self.MANIFEST_FILENAME
            if path.is_dir() and init_path.is_file():
                if not manifest_path.is_file():
                    raise ValueError(f"plugin package is missing manifest: {path}")
                plugin_name = path.name
                if plugin_name in candidates:
                    raise ValueError(f"duplicate flat and package plugin ID: {plugin_name}")
                manifests[plugin_name] = self._validate_manifest(manifest_path, plugin_name)
                candidates[plugin_name] = init_path

        for plugin_name in sorted(candidates):
            if self.allowed_plugins is not None and plugin_name not in self.allowed_plugins:
                continue
            self.plugin_files[plugin_name] = candidates[plugin_name]
            if plugin_name in manifests:
                self.plugin_manifests[plugin_name] = manifests[plugin_name]
        return list(self.plugin_files)

    def _module_name(self, plugin_name: str, file_path: Path) -> str:
        if (
            self.plugins_dir == self.DEFAULT_PLUGINS_DIR.resolve()
            and file_path.name == "__init__.py"
        ):
            return f"animation.plugins.{plugin_name}"
        if file_path.name != "__init__.py":
            return plugin_name
        return f"_ledgrid_animation_plugin_{plugin_name}"

    def load_plugin(self, plugin_name: str) -> Optional[Type[AnimationBase]]:
        """Load one scanned plugin, returning ``None`` after a reported failure."""
        try:
            file_path = self.plugin_files.get(plugin_name)
            if not file_path or not file_path.exists():
                print(f"Plugin file not found: {plugin_name}")
                return None

            module_name = self._module_name(plugin_name, file_path)
            package_locations = [str(file_path.parent)] if file_path.name == "__init__.py" else None
            spec = importlib.util.spec_from_file_location(
                module_name,
                file_path,
                submodule_search_locations=package_locations,
            )
            if spec is None or spec.loader is None:
                print(f"Could not create spec for plugin: {plugin_name}")
                return None

            module = importlib.util.module_from_spec(spec)
            previous_module = sys.modules.get(module_name)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                if previous_module is None:
                    sys.modules.pop(module_name, None)
                else:
                    sys.modules[module_name] = previous_module
                raise

            animation_classes = [
                obj
                for _, obj in inspect.getmembers(module)
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, AnimationBase)
                    and obj is not AnimationBase
                    and obj.__module__ == module.__name__
                    and not inspect.isabstract(obj)
                )
            ]
            if len(animation_classes) != 1:
                raise ValueError(
                    f"expected exactly one concrete animation class in {plugin_name}; "
                    f"found {len(animation_classes)}"
                )
            animation_class = animation_classes[0]

            manifest = self.plugin_manifests.get(plugin_name)
            if manifest and manifest["class"] != animation_class.__name__:
                raise ValueError(
                    f"manifest class {manifest['class']!r} does not match "
                    f"{animation_class.__name__!r} in plugin {plugin_name}"
                )

            self.loaded_plugins[plugin_name] = animation_class
            print(f"✓ Loaded plugin: {plugin_name} -> {animation_class.__name__}")
            return animation_class
        except Exception as exc:
            print(f"✗ Failed to load plugin {plugin_name}: {exc}")
            traceback.print_exc()
            return None

    def load_all_plugins(self) -> Dict[str, Type[AnimationBase]]:
        plugin_names = self.scan_plugins()
        self.loaded_plugins.clear()
        for plugin_name in plugin_names:
            self.load_plugin(plugin_name)
        return self.loaded_plugins.copy()

    def reload_plugin(self, plugin_name: str) -> Optional[Type[AnimationBase]]:
        print(f"🔄 Reloading plugin: {plugin_name}")
        return self.load_plugin(plugin_name)

    def get_plugin(self, plugin_name: str) -> Optional[Type[AnimationBase]]:
        return self.loaded_plugins.get(plugin_name)

    def get_plugin_file(self, plugin_name: str) -> Optional[Path]:
        return self.plugin_files.get(plugin_name)

    def get_plugin_dir(self, plugin_name: str) -> Optional[Path]:
        """Return the owning directory for a scanned plugin."""
        path = self.get_plugin_file(plugin_name)
        if path is None:
            return None
        return path.parent

    def iter_curated_preset_files(self, plugin_name: Optional[str] = None) -> Iterator[Path]:
        """Enumerate shipped, plugin-owned presets in stable path order."""
        names = [plugin_name] if plugin_name is not None else self.scan_plugins()
        for name in names:
            if name not in self.plugin_files:
                self.scan_plugins()
            plugin_file = self.plugin_files.get(name)
            if plugin_file is None or plugin_file.name != "__init__.py":
                continue
            yield from sorted((plugin_file.parent / "presets").glob("*.json"))

    def list_plugins(self) -> List[str]:
        return list(self.loaded_plugins)

    def get_plugin_info(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        plugin_class = self.get_plugin(plugin_name)
        if plugin_class is None:
            return None

        class _InfoController:
            strip_count = DEFAULT_STRIP_COUNT
            leds_per_strip = DEFAULT_LEDS_PER_STRIP
            total_leds = strip_count * leds_per_strip
            debug = False

        manifest = self.plugin_manifests.get(plugin_name, {})
        manifest_info = {
            "emoji": manifest.get("icon", "✨"),
            "is_test": manifest.get("gallery") == "test",
        }
        try:
            info = plugin_class(_InfoController()).get_info()
            info.update(manifest_info)
            info["plugin_name"] = plugin_name
            info["file_path"] = str(self.plugin_files.get(plugin_name, ""))
            return info
        except Exception as exc:
            return {
                **manifest_info,
                "plugin_name": plugin_name,
                "name": plugin_class.__name__,
                "error": str(exc),
                "file_path": str(self.plugin_files.get(plugin_name, "")),
            }
