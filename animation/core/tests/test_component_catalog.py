"""Phase 2C component descriptor discovery and compatibility acceptance."""

from __future__ import annotations

import builtins
import json
import sys
import tempfile
import unittest
from pathlib import Path

from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.presentation_contracts import (
    COMPONENT_DESCRIPTOR_SCHEMA,
    NEXT_DEADLINE_SEMANTICS,
)


ORDINARY_SOURCE = """
from animation import AnimationBase

class ExampleAnimation(AnimationBase):
    ANIMATION_NAME = "Catalog Example"
    ANIMATION_DESCRIPTION = "A safely bound catalog fixture"

    def __init__(self, controller, config=None):
        super().__init__(controller, config)
        self.default_params.update({"gain": 0.25, "speed": 0.4})
        self.params = {**self.default_params, **(config or {})}

    def get_parameter_schema(self):
        schema = super().get_parameter_schema()
        schema["gain"] = {
            "type": "float", "min": 0.0, "max": 1.0, "default": 0.25
        }
        return schema

    def generate_frame(self, time_elapsed, frame_count):
        return self.next_frame_buffer()
"""

STATEFUL_SOURCE = """
from animation import StatefulAnimationBase

class ExampleAnimation(StatefulAnimationBase):
    ANIMATION_NAME = "Stateful Example"
    ANIMATION_DESCRIPTION = "Owns complete-output timing"

    def run_animation(self):
        return None
"""


class ComponentCatalogTests(unittest.TestCase):
    def _package(
        self,
        root: str,
        *,
        plugin_id: str = "example",
        source: str = ORDINARY_SOURCE,
        manifest: dict | None = None,
    ) -> Path:
        package = Path(root) / plugin_id
        package.mkdir()
        (package / "__init__.py").write_text(source, encoding="utf-8")
        payload = manifest or {
            "plugin_id": plugin_id,
            "class": "ExampleAnimation",
            "icon": "✨",
            "gallery": "show",
        }
        (package / "manifest.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        return package

    @staticmethod
    def _explicit_manifest(**changes):
        return {
            "plugin_id": "example",
            "class": "ExampleAnimation",
            "icon": "✨",
            "gallery": "show",
            "manifest_version": 1,
            "provider": "python",
            "role": "background",
            "entrypoint": "animation.plugins.example:ExampleAnimation",
            "cadence": {"mode": "fixed_fps", "preferred_fps": 30},
            **changes,
        }

    def test_scanning_validates_and_normalizes_without_importing_implementation(self):
        marker = "_ledgrid_component_catalog_import_marker"
        source = (
            f"import builtins\nbuiltins.{marker} = True\n" + ORDINARY_SOURCE
        )
        module_before = sys.modules.get("_ledgrid_animation_plugin_example")
        with tempfile.TemporaryDirectory() as root:
            self._package(root, source=source)
            loader = AnimationPluginLoader(root)

            self.assertEqual(loader.scan_plugins(), ["example"])
            self.assertFalse(hasattr(builtins, marker))
            self.assertIs(
                sys.modules.get("_ledgrid_animation_plugin_example"), module_before
            )
            descriptor = loader.get_component_descriptor("example")

        self.assertEqual(descriptor["schema"], COMPONENT_DESCRIPTOR_SCHEMA)
        self.assertEqual(descriptor["manifest_version"], 1)
        self.assertEqual(descriptor["provider"], "python")
        self.assertEqual(descriptor["role"], "background")
        self.assertEqual(descriptor["parameter_schema"], {})
        self.assertEqual(descriptor["defaults"], {})
        self.assertTrue(descriptor["compatibility"]["legacy_manifest"])
        self.assertFalse(descriptor["compatibility"]["implementation_loaded"])
        self.assertFalse(descriptor["compatibility"]["composable"])

    def test_binding_enriches_schema_defaults_and_accepts_declared_controls_only(self):
        with tempfile.TemporaryDirectory() as root:
            self._package(root)
            loader = AnimationPluginLoader(root)
            loader.scan_plugins()
            self.assertIsNotNone(loader.load_plugin("example"))
            descriptor = loader.get_component_descriptor("example")

            self.assertEqual(descriptor["name"], "Catalog Example")
            self.assertIn("gain", descriptor["parameter_schema"])
            self.assertEqual(descriptor["defaults"]["gain"], 0.25)
            # The inherited schema still says speed=1.0, but a loaded descriptor
            # must preserve this implementation's real no-config behavior.
            self.assertEqual(descriptor["defaults"]["speed"], 0.4)
            self.assertEqual(
                descriptor["parameter_schema"]["speed"]["default"], 0.4
            )
            self.assertTrue(descriptor["compatibility"]["implementation_loaded"])
            self.assertTrue(descriptor["compatibility"]["composable"])
            self.assertEqual(
                loader.validate_component_parameters("example", {"gain": 0.7}),
                {"gain": 0.7},
            )
            with self.assertRaisesRegex(ValueError, "undeclared parameters"):
                loader.validate_component_parameters("example", {"secret": True})

    def test_stateful_and_painter_are_explicit_non_composable_full_scenes(self):
        with tempfile.TemporaryDirectory() as root:
            self._package(root, source=STATEFUL_SOURCE)
            loader = AnimationPluginLoader(root)
            loader.scan_plugins()
            self.assertIsNotNone(loader.load_plugin("example"))
            stateful = loader.get_component_descriptor("example")
            painter = loader.get_component_descriptor("painter")

        for descriptor, classification in (
            (stateful, "stateful_animation"),
            (painter, "painter"),
        ):
            with self.subTest(component=classification):
                self.assertEqual(descriptor["role"], "full_scene")
                self.assertFalse(descriptor["compatibility"]["composable"])
                self.assertEqual(
                    descriptor["compatibility"]["classification"], classification
                )

    def test_catalog_filters_are_deterministic_isolated_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            self._package(root, manifest=self._explicit_manifest(role="overlay"))
            loader = AnimationPluginLoader(root)
            overlays = loader.component_catalog(provider="python", role="overlay")

            self.assertEqual([item["plugin_id"] for item in overlays], ["example"])
            overlays[0]["role"] = "corrupted"
            self.assertEqual(loader.get_component_descriptor("example")["role"], "overlay")
            self.assertEqual(
                [item["plugin_id"] for item in loader.component_catalog(role="full_scene")],
                ["painter"],
            )
            with self.assertRaisesRegex(ValueError, "unsupported component provider"):
                loader.component_catalog(provider="wasm")
            with self.assertRaisesRegex(ValueError, "unsupported component role"):
                loader.component_catalog(role="foreground_graph")

    def test_explicit_v1_fixed_and_event_cadence_are_normalized(self):
        cases = (
            (
                {"mode": "fixed_fps", "preferred_fps": 23.5},
                {"mode": "fixed_fps", "preferred_fps": 23.5},
            ),
            ({"mode": "event_driven"}, {"mode": "event_driven"}),
        )
        for authored, expected in cases:
            with self.subTest(cadence=authored), tempfile.TemporaryDirectory() as root:
                self._package(
                    root, manifest=self._explicit_manifest(cadence=authored)
                )
                descriptor = AnimationPluginLoader(root).component_catalog(role="background")[0]
                self.assertEqual(descriptor["cadence"]["mode"], expected["mode"])
                self.assertEqual(
                    descriptor["cadence"].get("preferred_fps"),
                    expected.get("preferred_fps"),
                )
                self.assertEqual(
                    descriptor["cadence"]["next_deadline_semantics"],
                    NEXT_DEADLINE_SEMANTICS,
                )

    def test_manifest_rejects_version_provider_role_path_entrypoint_and_controls(self):
        cases = (
            ({"manifest_version": 2}, "manifest_version"),
            ({"manifest_version": True}, "manifest_version"),
            ({"provider": "receiver_native"}, "provider"),
            ({"role": "foreground_graph"}, "role"),
            ({"entrypoint": "animation.plugins.other:ExampleAnimation"}, "entrypoint"),
            ({"cadence": {"mode": "fixed_fps"}}, "preferred_fps"),
            ({"cadence": {"mode": "event_driven", "preferred_fps": 1}}, "event_driven"),
            ({"cadence": {"mode": "unbounded"}}, "mode"),
            ({"controls": {"secret": {"type": "bool"}}}, "controls"),
            ({"parameter_schema": {"gain": {"type": "float"}}}, "controls"),
            ({"defaults": {"gain": 1}}, "controls"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as root:
                self._package(root, manifest=self._explicit_manifest(**changes))
                with self.assertRaisesRegex(ValueError, message):
                    AnimationPluginLoader(root).scan_plugins()

        with tempfile.TemporaryDirectory() as root:
            payload = self._explicit_manifest(plugin_id="other")
            self._package(root, manifest=payload)
            with self.assertRaisesRegex(ValueError, "match package directory"):
                AnimationPluginLoader(root).scan_plugins()

    def test_explicit_component_fields_require_complete_versioned_shape(self):
        legacy = self._explicit_manifest()
        legacy.pop("manifest_version")
        with tempfile.TemporaryDirectory() as root:
            self._package(root, manifest=legacy)
            with self.assertRaisesRegex(ValueError, "manifest_version"):
                AnimationPluginLoader(root).scan_plugins()

        partial = {
            "plugin_id": "example",
            "class": "ExampleAnimation",
            "icon": "✨",
            "provider": "python",
        }
        with tempfile.TemporaryDirectory() as root:
            self._package(root, manifest=partial)
            with self.assertRaisesRegex(ValueError, "missing"):
                AnimationPluginLoader(root).scan_plugins()


if __name__ == "__main__":
    unittest.main()
