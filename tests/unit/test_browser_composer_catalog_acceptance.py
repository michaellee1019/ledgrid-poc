"""End-to-end catalog coverage for browser-composable scene backgrounds."""

from __future__ import annotations

import json
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
from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]
PYTHON_BUNDLE = ROOT / "web/static/generated/composer/ledgrid_python_runtime.zip"


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


if __name__ == "__main__":
    unittest.main()
