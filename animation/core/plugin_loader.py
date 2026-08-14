#!/usr/bin/env python3
"""Discovery and loading for self-contained animation plugins."""

from __future__ import annotations

import importlib.util
import inspect
import json
import math
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Type

from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT

from .base import AnimationBase
from .component_catalog import (
    bind_python_implementation,
    filter_catalog,
    painter_descriptor,
    scanned_descriptor,
    validate_and_normalize_manifest,
    validate_parameter_overrides,
)
from .presentation_contracts import (
    CANONICAL_VIBE_IDS,
    VIBE_CAPABILITIES as CANONICAL_VIBE_CAPABILITIES,
    VIBE_COLOR_POLICIES as CANONICAL_VIBE_COLOR_POLICIES,
    VIBE_PALETTE_ROLES,
    TimingAdapter,
)


class AnimationPluginLoader:
    """Load animation packages and external flat plugin files.

    Shipped plugins are packages with a validated ``manifest.json``. A package
    owns its implementation, tests, curated presets, and assets. Flat ``.py``
    files remain supported for external plugin directories so existing local
    extensions do not need to migrate in lock-step with the repository.
    """

    MANIFEST_FILENAME = "manifest.json"
    DEFAULT_PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins"
    VIBE_IDS = frozenset(CANONICAL_VIBE_IDS)
    VIBE_CAPABILITIES = CANONICAL_VIBE_CAPABILITIES
    VIBE_COLOR_POLICIES = CANONICAL_VIBE_COLOR_POLICIES
    VIBE_SEMANTIC_ROLES = frozenset(VIBE_PALETTE_ROLES)

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
        self.component_descriptors: Dict[str, Dict[str, Any]] = {}
        self._scan_completed = False

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
        payload = validate_and_normalize_manifest(payload, manifest_path, plugin_name)
        preview = payload.get("preview")
        if preview is not None:
            if not isinstance(preview, dict):
                raise ValueError(f"manifest preview must be an object: {manifest_path}")
            unknown = set(preview) - {"capture_seconds", "simulation_fps"}
            if unknown:
                raise ValueError(
                    f"manifest preview has unsupported keys {sorted(unknown)}: {manifest_path}"
                )
            capture_seconds = preview.get("capture_seconds")
            if capture_seconds is not None:
                if (
                    not isinstance(capture_seconds, list)
                    or not 1 <= len(capture_seconds) <= 16
                    or any(
                        not isinstance(value, (int, float))
                        or isinstance(value, bool)
                        or not math.isfinite(float(value))
                        or value < 0
                        for value in capture_seconds
                    )
                    or any(
                        float(left) >= float(right)
                        for left, right in zip(capture_seconds, capture_seconds[1:])
                    )
                ):
                    raise ValueError(
                        "manifest preview.capture_seconds must be 1-16 strictly "
                        f"increasing non-negative numbers: {manifest_path}"
                    )
            simulation_fps = preview.get("simulation_fps")
            if simulation_fps is not None and (
                not isinstance(simulation_fps, int)
                or isinstance(simulation_fps, bool)
                or not 1 <= simulation_fps <= 120
            ):
                raise ValueError(
                    "manifest preview.simulation_fps must be an integer from 1 to 120: "
                    f"{manifest_path}"
                )
        AnimationPluginLoader._normalize_vibe_manifest(payload, manifest_path)
        return payload

    @classmethod
    def _normalize_vibe_manifest(
        cls, payload: Dict[str, Any], manifest_path: Path
    ) -> None:
        """Validate and canonicalize optional Phase 2A presentation metadata."""
        vibe = payload.get("vibe")
        if vibe is None:
            return
        if not isinstance(vibe, dict):
            raise ValueError(f"manifest vibe must be an object: {manifest_path}")
        allowed = {
            "color_policy", "timing_adapter", "capabilities",
            "semantic_roles", "legacy_parameter_mappings",
        }
        unknown = set(vibe) - allowed
        if unknown:
            raise ValueError(
                f"manifest vibe has unsupported keys {sorted(unknown)}: {manifest_path}"
            )

        color_policy = vibe.get("color_policy")
        if color_policy not in cls.VIBE_COLOR_POLICIES:
            raise ValueError(
                "manifest vibe.color_policy must be semantic, grade, or preserve: "
                f"{manifest_path}"
            )
        timing_adapter = vibe.get("timing_adapter")
        try:
            TimingAdapter(timing_adapter)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "manifest vibe.timing_adapter must be legacy_speed_param, "
                f"scaled_context, or wall_clock: {manifest_path}"
            ) from exc

        capabilities = cls._identifier_list(
            vibe.get("capabilities"), "vibe.capabilities", manifest_path
        )
        unsupported = set(capabilities) - cls.VIBE_CAPABILITIES
        if unsupported:
            raise ValueError(
                "manifest vibe.capabilities has unsupported values "
                f"{sorted(unsupported)}: {manifest_path}"
            )
        if timing_adapter == TimingAdapter.WALL_CLOCK.value and "tempo" in capabilities:
            raise ValueError(
                f"wall_clock components cannot claim tempo capability: {manifest_path}"
            )
        if timing_adapter == TimingAdapter.SCALED_CONTEXT.value and "tempo" not in capabilities:
            raise ValueError(
                f"scaled_context components must claim tempo capability: {manifest_path}"
            )

        semantic_roles = cls._identifier_list(
            vibe.get("semantic_roles", []), "vibe.semantic_roles", manifest_path
        )
        unsupported_roles = set(semantic_roles) - cls.VIBE_SEMANTIC_ROLES
        if unsupported_roles:
            raise ValueError(
                "manifest vibe.semantic_roles has unsupported values "
                f"{sorted(unsupported_roles)}: {manifest_path}"
            )
        if semantic_roles and color_policy != "semantic":
            raise ValueError(
                f"only semantic color policy may declare semantic_roles: {manifest_path}"
            )
        if color_policy == "semantic" and (
            "palette_roles" not in capabilities or not semantic_roles
        ):
            raise ValueError(
                "semantic color policy requires palette_roles capability and at least "
                f"one semantic role: {manifest_path}"
            )
        if color_policy == "preserve" and "palette_roles" in capabilities:
            raise ValueError(
                "preserve color policy cannot claim palette_roles capability: "
                f"{manifest_path}"
            )

        mappings = vibe.get("legacy_parameter_mappings", {})
        if not isinstance(mappings, dict):
            raise ValueError(
                f"manifest vibe.legacy_parameter_mappings must be an object: {manifest_path}"
            )
        if mappings and "palette_roles" not in capabilities:
            raise ValueError(
                "manifest vibe legacy parameter mappings require palette_roles "
                f"capability: {manifest_path}"
            )
        normalized_mappings: Dict[str, Dict[str, str]] = {}
        for parameter, values in sorted(mappings.items()):
            if not isinstance(parameter, str) or re.fullmatch(
                r"[a-z][a-z0-9_]*", parameter
            ) is None:
                raise ValueError(
                    "manifest vibe legacy parameter names must be identifiers: "
                    f"{manifest_path}"
                )
            if not isinstance(values, dict) or not values:
                raise ValueError(
                    f"manifest vibe mapping for {parameter!r} must be a non-empty object: "
                    f"{manifest_path}"
                )
            normalized_values: Dict[str, str] = {}
            for vibe_id, target in sorted(values.items()):
                if vibe_id not in cls.VIBE_IDS:
                    raise ValueError(
                        f"manifest vibe mapping has unknown vibe ID {vibe_id!r}: "
                        f"{manifest_path}"
                    )
                if vibe_id == "neutral":
                    raise ValueError(
                        "neutral must preserve the authored parameter and cannot have a "
                        f"legacy mapping: {manifest_path}"
                    )
                if not isinstance(target, str) or not target:
                    raise ValueError(
                        f"manifest vibe mapping target must be a non-empty string: {manifest_path}"
                    )
                normalized_values[vibe_id] = target
            normalized_mappings[parameter] = normalized_values

        vibe["capabilities"] = capabilities
        if semantic_roles or "semantic_roles" in vibe:
            vibe["semantic_roles"] = semantic_roles
        if normalized_mappings or "legacy_parameter_mappings" in vibe:
            vibe["legacy_parameter_mappings"] = normalized_mappings

    @staticmethod
    def _identifier_list(value: Any, label: str, manifest_path: Path) -> List[str]:
        if not isinstance(value, list) or any(
            not isinstance(item, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", item) is None
            for item in value
        ):
            raise ValueError(
                f"manifest {label} must be a list of identifiers: {manifest_path}"
            )
        if len(value) != len(set(value)):
            raise ValueError(
                f"manifest {label} must not contain duplicates: {manifest_path}"
            )
        return sorted(value)

    @staticmethod
    def _bind_vibe_manifest(
        animation_class: Type[AnimationBase], manifest: Dict[str, Any]
    ) -> None:
        """Bind normalized metadata and validate bridge targets against the schema."""
        vibe = manifest.get("vibe")
        if vibe is None:
            return

        mappings = vibe.get("legacy_parameter_mappings", {})
        if mappings:
            class _ManifestController:
                strip_count = DEFAULT_STRIP_COUNT
                leds_per_strip = DEFAULT_LEDS_PER_STRIP
                total_leds = strip_count * leds_per_strip
                debug = False

            schema = animation_class(_ManifestController()).get_parameter_schema()
            for parameter, values in mappings.items():
                definition = schema.get(parameter)
                if not isinstance(definition, dict):
                    raise ValueError(
                        f"manifest vibe maps unknown parameter {parameter!r}"
                    )
                options = definition.get("options")
                if not isinstance(options, (list, tuple)) or not options:
                    raise ValueError(
                        f"manifest vibe mapped parameter {parameter!r} must declare options"
                    )
                invalid = set(values.values()) - set(options)
                if invalid:
                    raise ValueError(
                        f"manifest vibe mapping for {parameter!r} has unsupported targets "
                        f"{sorted(invalid)}"
                    )

        animation_class.TIMING_ADAPTER = TimingAdapter(vibe["timing_adapter"])
        animation_class.VIBE_CAPABILITIES = frozenset(vibe["capabilities"])
        animation_class.VIBE_COLOR_POLICY = vibe["color_policy"]
        animation_class.VIBE_PARAMETER_MAPPINGS = {
            parameter: dict(values) for parameter, values in mappings.items()
        }

    def scan_plugins(self) -> List[str]:
        """Scan package and external flat plugins in deterministic ID order."""
        self.plugin_files.clear()
        self.plugin_manifests.clear()
        self.component_descriptors.clear()
        self._scan_completed = True
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
                self.component_descriptors[plugin_name] = scanned_descriptor(
                    plugin_name, manifests[plugin_name]
                )
            else:
                self.component_descriptors[plugin_name] = scanned_descriptor(
                    plugin_name, None, flat_file=candidates[plugin_name]
                )
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
            if manifest:
                self._bind_vibe_manifest(animation_class, manifest)

            descriptor = self.component_descriptors.get(plugin_name)
            if descriptor is not None:
                self.component_descriptors[plugin_name] = bind_python_implementation(
                    descriptor, animation_class
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

    def component_catalog(
        self, provider: Optional[str] = None, role: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return one normalized provider/role-filterable component catalog."""
        if not self._scan_completed:
            self.scan_plugins()
        descriptors = list(self.component_descriptors.values())
        descriptors.append(painter_descriptor())
        return filter_catalog(descriptors, provider=provider, role=role)

    def get_component_descriptor(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """Return an isolated JSON descriptor for a plugin or painter mode."""
        if not self._scan_completed:
            self.scan_plugins()
        if plugin_id == "painter":
            return painter_descriptor()
        descriptor = self.component_descriptors.get(plugin_id)
        if descriptor is None:
            return None
        matches = filter_catalog((descriptor,))
        return matches[0]

    def validate_component_parameters(
        self, plugin_id: str, values: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate controls against a bound implementation's declared schema."""
        descriptor = self.get_component_descriptor(plugin_id)
        if descriptor is None:
            raise ValueError(f"unknown component {plugin_id!r}")
        return validate_parameter_overrides(descriptor, values)

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
            "vibe": json.loads(json.dumps(manifest.get("vibe", {}))),
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
