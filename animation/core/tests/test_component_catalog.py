"""Phase 2C component descriptor discovery and compatibility acceptance."""

from __future__ import annotations

import builtins
import copy
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


def native_manifest(**changes):
    payload = {
        "manifest_version": 1,
        "plugin_id": "native_example",
        "name": "Native Example",
        "description": "A repository-built analytic receiver background",
        "icon": "🌌",
        "gallery": "show",
        "provider": "receiver_native",
        "role": "background",
        "entrypoint": "ledgrid.native-background-abi:2",
        "cadence": {"mode": "fixed_fps", "preferred_fps": 60},
        "parameter_schema": {
            "speed": {
                "type": "float", "min": 0.1, "max": 4.0, "default": 1.0,
                "description": "Motion multiplier",
            },
            "palette": {
                "type": "str", "options": ["aurora", "sunset"],
                "default": "aurora", "description": "Color treatment",
            },
            "shimmer": {
                "type": "bool", "default": True,
                "description": "Enable bounded highlight texture",
            },
            "bands": {
                "type": "int", "min": 1, "max": 8, "default": 3,
                "description": "Number of analytic ribbons",
            },
        },
        "vibe": {
            "color_policy": "semantic",
            "timing_adapter": "scaled_context",
            "capabilities": ["palette_roles", "tempo", "luminance"],
            "semantic_roles": [
                "background_low", "background_mid", "background_high",
            ],
        },
        "installation_profile_requirements": [],
        "geometry": {
            "global_strips": 33,
            "leds_per_strip": 138,
            "receiver_views": [
                {"logical_receiver_id": 0, "global_strip_offset": 0, "local_strips": 8, "reverse_local_strip_order": False},
                {"logical_receiver_id": 1, "global_strip_offset": 8, "local_strips": 8, "reverse_local_strip_order": False},
                {"logical_receiver_id": 3, "global_strip_offset": 16, "local_strips": 8, "reverse_local_strip_order": True},
                {"logical_receiver_id": 2, "global_strip_offset": 24, "local_strips": 8, "reverse_local_strip_order": True},
                {"logical_receiver_id": 4, "global_strip_offset": 32, "local_strips": 1, "reverse_local_strip_order": False},
            ],
        },
        "preview": {
            "kind": "native_host_build",
            "capture_seconds": [0, 0.5, 1.0, 2.0],
            "simulation_fps": 60,
            "framebuffer_readback": False,
        },
        "build": {
            "artifact_kind": "receiver_native_module",
            "bundle_schema": "ledgrid.native-background-bundle",
            "bundle_version": 1,
            "abi_schema": "ledgrid.native-background-abi",
            "abi_version": 2,
            "target": "esp32-s3",
            "source": "native/background.cpp",
        },
    }
    payload.update(changes)
    return payload


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

    def _native_package(
        self, root: str, *, manifest: dict | None = None, preset: bool = False
    ) -> Path:
        package = Path(root) / "native_example"
        source_dir = package / "native"
        source_dir.mkdir(parents=True)
        (source_dir / "background.cpp").write_text(
            'extern "C" int ledgrid_native_test_symbol = 1;\n', encoding="utf-8"
        )
        (package / "manifest.json").write_text(
            json.dumps(manifest or native_manifest()), encoding="utf-8"
        )
        if preset:
            preset_dir = package / "presets"
            preset_dir.mkdir()
            (preset_dir / "quiet.json").write_text(json.dumps({
                "version": 1,
                "preset_id": "quiet",
                "name": "Quiet",
                "animation": "native_example",
                "params": {
                    "speed": 0.5, "palette": "aurora",
                    "shimmer": False, "bands": 2,
                },
            }), encoding="utf-8")
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

    def test_manifest_only_native_peer_is_catalog_and_preset_visible_not_executable(self):
        with tempfile.TemporaryDirectory() as root:
            self._package(root)
            native_dir = self._native_package(root, preset=True)
            loader = AnimationPluginLoader(root, allowed_plugins={"example"})

            self.assertEqual(
                loader.scan_components(), ["example", "native_example"]
            )
            self.assertEqual(loader.scan_plugins(), ["example"])
            self.assertEqual(
                loader.get_component_dir("native_example"), native_dir.resolve()
            )
            self.assertIsNone(loader.get_plugin_dir("native_example"))
            self.assertIsNone(loader.get_plugin_file("native_example"))
            self.assertNotIn("native_example", loader.plugin_manifests)
            self.assertIn("native_example", loader.component_manifests)

            descriptor = loader.get_component_descriptor("native_example")
            self.assertEqual(descriptor["provider"], "receiver_native")
            self.assertEqual(descriptor["role"], "background")
            self.assertEqual(
                descriptor["entrypoint"], "ledgrid.native-background-abi:2"
            )
            self.assertEqual(descriptor["defaults"], {
                "speed": 1.0, "palette": "aurora", "shimmer": True, "bands": 3,
            })
            self.assertEqual(
                descriptor["build"]["source"], "native/background.cpp"
            )
            self.assertFalse(descriptor["preview"]["framebuffer_readback"])
            self.assertEqual(
                descriptor["compatibility"]["classification"],
                "receiver_native_source",
            )
            self.assertTrue(descriptor["compatibility"]["composable"])
            self.assertFalse(descriptor["compatibility"]["implementation_loaded"])
            self.assertEqual(
                loader.validate_component_parameters(
                    "native_example", {"speed": 2.0, "palette": "sunset"}
                ),
                {"speed": 2.0, "palette": "sunset"},
            )
            for invalid in (
                {"speed": float("inf")}, {"speed": 5.0}, {"bands": True},
                {"palette": "missing"}, {"unknown": 1},
            ):
                with self.subTest(parameters=invalid), self.assertRaises(ValueError):
                    loader.validate_component_parameters("native_example", invalid)

            self.assertIsNone(loader.load_plugin("native_example"))
            self.assertEqual(
                [path.name for path in loader.iter_component_preset_files(
                    provider="receiver_native"
                )],
                ["quiet.json"],
            )
            self.assertEqual(list(loader.iter_curated_preset_files("native_example")), [])
            self.assertEqual(set(loader.load_all_plugins()), {"example"})

    def test_native_package_rejects_python_package_and_class_without_importing(self):
        marker = "_ledgrid_native_component_import_marker"
        with tempfile.TemporaryDirectory() as root:
            package = self._native_package(root)
            (package / "__init__.py").write_text(
                f"import builtins\nbuiltins.{marker} = True\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "must not contain __init__"):
                AnimationPluginLoader(root).scan_components()
            self.assertFalse(hasattr(builtins, marker))

        with tempfile.TemporaryDirectory() as root:
            payload = native_manifest()
            payload["class"] = "NativeAnimation"
            self._native_package(root, manifest=payload)
            with self.assertRaisesRegex(ValueError, "unknown=.*class"):
                AnimationPluginLoader(root).scan_components()

    def test_native_manifest_fails_closed_on_contract_and_schema_drift(self):
        cases = []

        def case(label, mutate, message):
            payload = copy.deepcopy(native_manifest())
            mutate(payload)
            cases.append((label, payload, message))

        case("version", lambda p: p.update(manifest_version=2), "manifest_version")
        case("boolean version", lambda p: p.update(manifest_version=True), "manifest_version")
        case("float version", lambda p: p.update(manifest_version=1.0), "manifest_version")
        case("role", lambda p: p.update(role="overlay"), "role")
        case("entrypoint", lambda p: p.update(entrypoint="native:wrong"), "entrypoint")
        case("cadence", lambda p: p.update(cadence={"mode": "event_driven"}), "fixed_fps")
        case(
            "cadence bound",
            lambda p: p.update(cadence={"mode": "fixed_fps", "preferred_fps": 201}),
            "1 to 200",
        )
        case(
            "cadence lower bound",
            lambda p: p.update(cadence={"mode": "fixed_fps", "preferred_fps": 0.5}),
            "1 to 200",
        )
        case("unknown", lambda p: p.update(secret=True), "unknown=.*secret")
        case(
            "preview kind",
            lambda p: p["preview"].update(kind="python_renderer"),
            "preview kind",
        )
        case(
            "preview readback",
            lambda p: p["preview"].update(framebuffer_readback=True),
            "framebuffer_readback",
        )
        case(
            "preview cadence",
            lambda p: p["preview"].update(simulation_fps=121),
            "simulation_fps",
        )
        case(
            "one preview frame",
            lambda p: p["preview"].update(capture_seconds=[0.0]),
            "2-16",
        )
        case(
            "preview timestamp overflow",
            lambda p: p["preview"].update(
                capture_seconds=[0.0, (2**64 + 1) / 1_000_000]
            ),
            "uint64",
        )
        case(
            "build target", lambda p: p["build"].update(target="host"),
            "build contract",
        )
        case(
            "boolean bundle version",
            lambda p: p["build"].update(bundle_version=True),
            "versions must be integers",
        )
        case(
            "boolean ABI version",
            lambda p: p["build"].update(abi_version=True),
            "versions must be integers",
        )
        case(
            "build source", lambda p: p["build"].update(source="../background.cpp"),
            "escapes package",
        )
        case(
            "reserved parameter",
            lambda p: p["parameter_schema"].update(
                plant_modifiers={
                    "type": "bool", "default": False, "description": "Reserved",
                }
            ),
            "parameter name",
        )
        case(
            "default bound",
            lambda p: p["parameter_schema"]["speed"].update(default=9.0),
            "outside its bounds",
        )
        case(
            "float32 overflow",
            lambda p: p["parameter_schema"]["speed"].update(max=1e100),
            "float32",
        )
        case(
            "float32 underflow",
            lambda p: p["parameter_schema"]["speed"].update(min=1e-100),
            "float32",
        )
        case(
            "enum default",
            lambda p: p["parameter_schema"]["palette"].update(default="missing"),
            "must be one of",
        )
        case(
            "legacy mapping",
            lambda p: p["vibe"].update(
                legacy_parameter_mappings={"palette": {"quiet": "aurora"}}
            ),
            "legacy_parameter_mappings",
        )
        case(
            "empty legacy mapping",
            lambda p: p["vibe"].update(legacy_parameter_mappings={}),
            "legacy_parameter_mappings",
        )
        case(
            "missing vibe capability field",
            lambda p: p["vibe"].pop("capabilities"),
            "vibe.capabilities|vibe fields",
        )
        case(
            "semantic vibe without roles",
            lambda p: p["vibe"].pop("semantic_roles"),
            "semantic.*role",
        )
        case(
            "non-semantic vibe with roles",
            lambda p: p["vibe"].update(color_policy="grade"),
            "only .*semantic",
        )
        case(
            "long installation requirement",
            lambda p: p.update(installation_profile_requirements=["a" * 49]),
            "48 characters",
        )

        for label, payload, message in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as root:
                self._native_package(root, manifest=payload)
                with self.assertRaisesRegex(ValueError, message):
                    AnimationPluginLoader(root).scan_components()

    def test_native_catalog_is_descriptor_only_when_build_source_is_not_deployed(self):
        with tempfile.TemporaryDirectory() as root:
            package = self._native_package(root)
            (package / "native" / "background.cpp").unlink()
            loader = AnimationPluginLoader(root)
            self.assertEqual(loader.scan_components(), ["native_example"])
            descriptor = loader.get_component_descriptor("native_example")
            self.assertEqual(descriptor["build"]["source"], "native/background.cpp")
            self.assertEqual(
                descriptor["compatibility"]["classification"],
                "receiver_native_source",
            )

    def test_native_nonsemantic_vibe_normalizes_bundle_compatible_empty_roles(self):
        with tempfile.TemporaryDirectory() as root:
            payload = native_manifest()
            payload["vibe"] = {
                "color_policy": "grade",
                "timing_adapter": "legacy_speed_param",
                "capabilities": ["luminance"],
            }
            self._native_package(root, manifest=payload)
            loader = AnimationPluginLoader(root)
            loader.scan_components()
            self.assertEqual(
                loader.component_manifests["native_example"]["vibe"],
                {
                    "color_policy": "grade",
                    "timing_adapter": "legacy_speed_param",
                    "capabilities": ["luminance"],
                    "semantic_roles": [],
                },
            )

    def test_native_package_directory_must_not_be_a_symlink(self):
        with (
            tempfile.TemporaryDirectory() as source_root,
            tempfile.TemporaryDirectory() as plugins_root,
        ):
            package = self._native_package(source_root)
            (Path(plugins_root) / "native_example").symlink_to(
                package, target_is_directory=True
            )
            with self.assertRaisesRegex(ValueError, "package directory.*symlink"):
                AnimationPluginLoader(plugins_root).scan_components()

    def test_manifest_only_directory_with_malformed_json_fails_closed(self):
        cases = ("{", "[]")
        for content in cases:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as root:
                package = Path(root) / "broken_component"
                package.mkdir()
                (package / "manifest.json").write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "invalid manifest|object"):
                    AnimationPluginLoader(root).scan_components()


if __name__ == "__main__":
    unittest.main()
