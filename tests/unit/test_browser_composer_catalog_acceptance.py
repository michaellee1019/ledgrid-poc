"""End-to-end catalog coverage for browser-composable scene backgrounds."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.receiver_static_component import receiver_static_component_catalog
from ipc.scene_contract import SceneProviderPolicy
from tools.build_browser_composer_bootstrap import build_bootstrap
from web.app import AnimationWebInterface
from web.composer_final_preview import current_component_catalog


ROOT = Path(__file__).resolve().parents[2]
PYTHON_BUNDLE = ROOT / "web/static/generated/composer/ledgrid_python_runtime.zip"
SCENE_V2_METADATA_IDS = (
    "gradient", "rainbow", "solid", "sparkle", "wave",
    "ascii_drop", "christmas_tree", "emoji", "night_train_windows",
)
AMBIENT_PRESET_IDS = {"gradient", "rainbow", "solid", "sparkle", "wave"}
CANONICAL_DESCRIPTORS = current_component_catalog().descriptors
CANONICAL_ANIMATIONS = tuple(
    descriptor
    for descriptor in CANONICAL_DESCRIPTORS
    if descriptor.role.value == "animation"
)
CANONICAL_WIDGETS = tuple(
    descriptor
    for descriptor in CANONICAL_DESCRIPTORS
    if descriptor.role.value == "widget"
)


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _CatalogManager:
    controller = _Controller()
    preview_controller = controller

    def __init__(self, flags: AnimationPipelineFeatureFlags) -> None:
        self.feature_flags = flags
        self.plugin_loader = AnimationPluginLoader()
        # Bind the real Python implementations so compatibility/selectability
        # is the same as the running manager, without polluting test output.
        with redirect_stdout(StringIO()):
            self.plugin_loader.load_all_plugins()

    def list_components(self) -> list[dict]:
        catalog = self.plugin_loader.component_catalog()
        catalog.extend(receiver_static_component_catalog(self.feature_flags))
        return catalog

    def list_animations(self) -> list[dict]:
        return []

    def get_animation_info(self, _plugin_id: str) -> None:
        return None

    def scene_provider_policy(self) -> SceneProviderPolicy:
        return SceneProviderPolicy(
            receiver_local_background=self.feature_flags.receiver_local_background,
            receiver_sparse_overlay=self.feature_flags.receiver_sparse_overlay,
            receiver_native_modules=self.feature_flags.receiver_native_modules,
        )


class _Channel:
    def read_status(self) -> dict:
        raise AssertionError("browser composer bootstrap must not observe live status")

    def send_command(self, _action: str, **_data: object) -> dict:
        raise AssertionError("browser composer bootstrap must not mutate live status")


class BrowserComposerCatalogAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with zipfile.ZipFile(PYTHON_BUNDLE, "r") as archive:
            manifest = json.loads(archive.read("ledgrid_browser_manifest.json"))
        cls.python_roles = {
            item["pluginId"]: item["role"] for item in manifest["plugins"]
        }

    def _bootstrap(self, flags: AnimationPipelineFeatureFlags) -> dict:
        manager = _CatalogManager(flags)
        interface = AnimationWebInterface(_Channel(), manager, local_mode=True)
        with tempfile.TemporaryDirectory() as directory:
            interface.animation_presets_dir = Path(directory) / "runtime-presets"
            response = interface.app.test_client().get("/api/v1/composer/bootstrap")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_every_selectable_background_has_a_real_browser_runtime_in_both_modes(self) -> None:
        modes = {
            "default": AnimationPipelineFeatureFlags(),
            "full_hybrid": AnimationPipelineFeatureFlags(
                receiver_local_background=True,
                receiver_sparse_overlay=True,
                receiver_native_modules=True,
            ),
        }
        for mode, flags in modes.items():
            with self.subTest(mode=mode):
                payload = self._bootstrap(flags)
                selectable = [
                    component
                    for component in payload["components"]
                    if component["role"] == "background"
                    and component["scene_compatibility"]["selectable"]
                ]
                self.assertTrue(selectable)
                unsupported = [
                    component["key"]
                    for component in selectable
                    if not component["browser_runtime"]["supported"]
                ]
                self.assertEqual(unsupported, [])

                for component in selectable:
                    with self.subTest(mode=mode, component=component["key"]):
                        runtime = component["browser_runtime"]
                        if component["provider"] == "python":
                            self.assertEqual(runtime["engine"], "python-pyodide-wasm")
                            self.assertEqual(
                                self.python_roles.get(component["plugin_id"]),
                                "background",
                                "bootstrap claimed support for a Python background "
                                "that is absent from the generated browser bundle",
                            )
                        else:
                            self.assertEqual(
                                runtime["engine"], "receiver-native-cpp-wasm"
                            )
                            asset_url = runtime["asset_url"]
                            self.assertTrue(asset_url.startswith("/static/"))
                            asset = ROOT / "web" / asset_url.lstrip("/")
                            self.assertTrue(asset.is_file(), asset)
                            self.assertEqual(asset.read_bytes()[:4], b"\0asm")

    def test_browser_catalog_excludes_the_retired_painter_descriptor(self) -> None:
        payload = self._bootstrap(AnimationPipelineFeatureFlags())
        by_key = {component["key"]: component for component in payload["components"]}

        for plugin_id in self.python_roles:
            if plugin_id == "painter":
                continue
            with self.subTest(component=plugin_id):
                component = by_key[f"python:{plugin_id}"]
                self.assertTrue(component["browser_runtime"]["supported"])
                self.assertEqual(
                    component["browser_runtime"]["engine"],
                    "python-pyodide-wasm",
                )

        self.assertNotIn("python:painter", by_key)

    def test_event_rgba_animations_keep_their_canonical_browser_contract(self) -> None:
        payload = self._bootstrap(AnimationPipelineFeatureFlags())
        by_key = {component["key"]: component for component in payload["components"]}
        for plugin_id in ("fireworks", "flame_burst"):
            with self.subTest(component=plugin_id):
                component = by_key[f"python:{plugin_id}"]
                self.assertEqual(component["role"], "animation")
                self.assertEqual(
                    component["scene_compatibility"],
                    {"selectable": True, "slots": ["animation"], "diagnostic": None},
                )
                self.assertEqual(self.python_roles[plugin_id], "animation")

    def test_qualified_scene_v2_animations_publish_descriptor_metadata(self) -> None:
        payload = self._bootstrap(AnimationPipelineFeatureFlags())
        by_key = {component["key"]: component for component in payload["components"]}
        for plugin_id in SCENE_V2_METADATA_IDS:
            with self.subTest(component=plugin_id):
                component = by_key[f"python:{plugin_id}"]
                self.assertEqual(component["role"], "animation")
                self.assertEqual(
                    component["scene_compatibility"],
                    {"selectable": True, "slots": ["animation"], "diagnostic": None},
                )
                self.assertEqual(component["presentation"], {
                    "timing_adapter": "scaled_context",
                    "vibe_color_policy": "semantic",
                    "vibe_capabilities": ["palette_roles", "tempo"],
                })

                if plugin_id in AMBIENT_PRESET_IDS:
                    self.assertTrue(component["presets"])
                    for preset in component["presets"]:
                        promise = f"{preset['name']} {preset['description']}".lower()
                        self.assertIn("scene palette", promise.replace("-", " "))

    def _assert_canonical_animation_contracts(self, payload: dict) -> None:
        by_key = {component["key"]: component for component in payload["components"]}
        for descriptor in CANONICAL_ANIMATIONS:
            with self.subTest(component=descriptor.component_id):
                component = by_key[
                    f"{descriptor.provider.value}:{descriptor.component_id}"
                ]
                defaults = descriptor.default_parameters()
                self.assertEqual(component["role"], "animation")
                self.assertEqual(component["defaults"], defaults)
                self.assertEqual(set(component["parameter_schema"]), set(defaults))
                self.assertEqual(
                    {
                        name: definition.get("default")
                        for name, definition in component["parameter_schema"].items()
                    },
                    defaults,
                )
                self.assertEqual(component["scene_compatibility"], {
                    "selectable": True,
                    "slots": ["animation"],
                    "diagnostic": None,
                })
                self.assertEqual(component["presentation"], {
                    "timing_adapter": descriptor.timing_policy.value,
                    "vibe_color_policy": descriptor.palette_policy.value,
                    "vibe_capabilities": ["palette_roles", "tempo"],
                })
                capabilities = component["browser_capabilities"]
                self.assertTrue(capabilities["previewable"])
                self.assertTrue(capabilities["saveable"])
                self.assertTrue(capabilities["activation_ready"])
                self.assertIsNone(capabilities["reason"])
                self.assertEqual(capabilities["managed_identity"], {
                    "provider": descriptor.provider.value,
                    "component_id": descriptor.component_id,
                    "component_digest": component["component_digest"],
                    "runtime_digest": component["browser_runtime"]["digest"],
                    "parameter_schema_version": component["parameter_schema_version"],
                })

    def test_server_and_generated_bootstraps_publish_every_canonical_animation(self) -> None:
        self.assertEqual(len(CANONICAL_ANIMATIONS), 39)
        animation_ids = {
            descriptor.component_id for descriptor in CANONICAL_ANIMATIONS
        }
        self.assertTrue({
            "canopy_cup", "aurora_curtains", "tetris", "fireworks",
        }.issubset(animation_ids))
        opaque_ids = {
            descriptor.component_id
            for descriptor in CANONICAL_ANIMATIONS
            if descriptor.alpha_behavior.value == "opaque"
        }
        self.assertTrue(opaque_ids)

        payloads = {
            "server": self._bootstrap(AnimationPipelineFeatureFlags()),
            "generated": build_bootstrap(ROOT),
        }
        for source, payload in payloads.items():
            with self.subTest(source=source):
                self._assert_canonical_animation_contracts(payload)

    def test_browser_native_backgrounds_and_canonical_widgets_keep_browser_roles(self) -> None:
        payload = self._bootstrap(AnimationPipelineFeatureFlags(
            receiver_local_background=True,
            receiver_sparse_overlay=True,
            receiver_native_modules=True,
        ))
        by_key = {component["key"]: component for component in payload["components"]}
        native_backgrounds = [
            component for component in payload["components"]
            if component["provider"] == "receiver_native"
        ]
        self.assertTrue(native_backgrounds)
        self.assertTrue(all(
            component["role"] == "background"
            for component in native_backgrounds
        ))
        for descriptor in CANONICAL_WIDGETS:
            with self.subTest(component=descriptor.component_id):
                component = by_key[f"python:{descriptor.component_id}"]
                self.assertEqual(component["role"], "overlay")
                self.assertEqual(component["defaults"], descriptor.default_parameters())
                self.assertEqual(
                    set(component["parameter_schema"]),
                    set(descriptor.default_parameters()),
                )
                self.assertTrue(component["browser_capabilities"]["activation_ready"])

    def test_actual_browser_lookup_resolves_all_canonical_animations(self) -> None:
        payload = self._bootstrap(AnimationPipelineFeatureFlags())
        script = (ROOT / "web/static/js/composer_slice.js").read_text(
            encoding="utf-8"
        )
        lookup = script[
            script.index("function managedWallComponent"):
            script.index("  function wallComponentReference")
        ]
        javascript = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const bootstrap = JSON.parse(fs.readFileSync(0, 'utf8'));
const animationIds = JSON.parse(process.argv[2]);
const context = {assert, animationIds, state: {wall: {bootstrap}}};
vm.runInNewContext(process.argv[1] + `
  for (const componentId of animationIds) {
    const component = managedWallComponent(componentId, 'animation');
    assert.ok(component, componentId + ' must resolve through managedWallComponent');
    assert.equal(component.browser_capabilities.activation_ready, true);
  }
`, context);
"""
        completed = subprocess.run(
            [
                "node", "-e", javascript, lookup,
                json.dumps([
                    descriptor.component_id
                    for descriptor in CANONICAL_ANIMATIONS
                ]),
            ],
            input=json.dumps(payload),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_every_python_browser_payload_uses_managed_profile_geometry_only(self) -> None:
        payload = self._bootstrap(AnimationPipelineFeatureFlags())
        retired = {"plant_mask_path", "plant_globe_mask_path"}
        for component in payload["components"]:
            if component["provider"] != "python":
                continue
            with self.subTest(component=component["key"]):
                self.assertTrue(retired.isdisjoint(component["parameter_schema"]))
                self.assertTrue(retired.isdisjoint(component["defaults"]))
                for preset in component["presets"]:
                    self.assertTrue(retired.isdisjoint(preset["params"]))

    def test_current_catalog_is_the_only_builtin_source_for_routes_and_bootstrap(self) -> None:
        manager = _CatalogManager(AnimationPipelineFeatureFlags())
        interface = AnimationWebInterface(_Channel(), manager, local_mode=True)
        with tempfile.TemporaryDirectory() as directory:
            interface.animation_presets_dir = Path(directory) / "runtime-presets"
            client = interface.app.test_client()
            bootstrap = client.get(
                "/api/v1/composer/bootstrap?catalog_only=1"
            )
            self.assertEqual(bootstrap.status_code, 200)
            components = bootstrap.get_json()["components"]

            expected = {
                component_id: set(entry["preset_ids"])
                for component_id, entry in interface.composer_presets._membership.items()
            }
            self.assertEqual(len(expected), 40)
            self.assertEqual(sum(map(len, expected.values())), 217)

            builtin_cards = 0
            for component in components:
                component_id = component["plugin_id"]
                provider = component["provider"]
                cards = component["presets"]
                builtin_ids = {
                    card["preset_id"] for card in cards
                    if card["ownership"] == "built_in"
                }
                if provider == "python" and component_id in expected:
                    self.assertEqual(builtin_ids, expected[component_id])
                    route = client.get(
                        f"/api/v1/components/{component_id}/presets?provider=python"
                    )
                    self.assertEqual(route.status_code, 200)
                    self.assertEqual(
                        {item["preset_id"] for item in route.get_json()["presets"]},
                        expected[component_id],
                    )
                    builtin_cards += len(builtin_ids)
                else:
                    self.assertEqual(builtin_ids, set())

            self.assertEqual(builtin_cards, 217)
            clock = next(
                item for item in components
                if item["key"] == "python:clock_overlay"
            )
            self.assertEqual(
                {card["preset_id"] for card in clock["presets"]},
                expected["clock_overlay"],
            )

            retained = client.post("/api/v1/composer/presets", json={
                "schema": "ledgrid.browser-composer-save",
                "schema_version": 1,
                "component_key": "python:gradient",
                "name": "Slow tide",
                "params": {"motion": 0.4},
                "overwrite": False,
            })
            self.assertEqual(retained.status_code, 201, retained.get_json())
            runtime_path = (
                interface.animation_presets_dir / "python" / "gradient"
                / "slow_tide.json"
            )
            rejected_path = runtime_path.with_name("old-global.json")
            rejected_path.write_text(json.dumps({
                "version": 2,
                "preset_id": "old-global",
                "name": "Old global",
                "animation": "gradient",
                "provider": "python",
                "params": {"brightness": 0.5},
            }), encoding="utf-8")
            current_ids = {
                item["preset_id"]
                for item in client.get(
                    "/api/v1/components/gradient/presets?provider=python"
                ).get_json()["presets"]
            }
            self.assertIn("slow_tide", current_ids)
            self.assertNotIn("old-global", current_ids)
            self.assertTrue(rejected_path.is_file())


if __name__ == "__main__":
    unittest.main()
