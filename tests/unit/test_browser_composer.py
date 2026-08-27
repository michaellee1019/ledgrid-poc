"""Browser-Wasm composer read-model and private-shell contracts."""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from web.app import AnimationWebInterface


def _component(
    plugin_id: str,
    *,
    provider: str = "python",
    entrypoint: str | None = None,
) -> dict:
    return {
        "plugin_id": plugin_id,
        "name": plugin_id.replace("_", " ").title(),
        "description": f"Browser composer fixture for {plugin_id}.",
        "provider": provider,
        "role": "background",
        "icon": "✦",
        "entrypoint": entrypoint or (
            f"animation.plugins.{plugin_id}:{plugin_id.title()}Animation"
            if provider == "python"
            else "ledgrid.native-background-abi:2"
        ),
        "parameter_schema": {
            "speed": {
                "type": "float", "min": 0.1, "max": 5.0,
                "default": 1.0, "description": "Motion speed.",
            },
        },
        "defaults": {"speed": 1.0},
        "preview": {"framebuffer_readback": False},
        "compatibility": {
            "classification": "declared_component",
            "composable": True,
            "implementation_loaded": provider == "python",
            "parameter_metadata": "loaded" if provider == "python" else "manifest",
            "diagnostic": "Fixture component.",
        },
    }


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _PreviewManager:
    controller = _Controller()
    preview_controller = controller

    def __init__(self, components: list[dict]) -> None:
        self._components = deepcopy(components)

    def list_components(self) -> list[dict]:
        return deepcopy(self._components)

    def list_animations(self) -> list[dict]:
        return []

    def get_animation_info(self, plugin_id: str) -> dict | None:
        component = next(
            (
                item for item in self._components
                if item["plugin_id"] == plugin_id and item["provider"] == "python"
            ),
            None,
        )
        if component is None:
            return None
        return {"parameters": deepcopy(component["parameter_schema"])}


class _Channel:
    def __init__(self) -> None:
        self.read_count = 0
        self.commands: list[dict] = []

    def read_status(self) -> dict:
        self.read_count += 1
        return {}

    def send_command(self, action: str, **data) -> dict:
        self.commands.append({"action": action, "data": deepcopy(data)})
        return {"command_id": f"cmd-{len(self.commands)}"}


class BrowserComposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        components = [
            _component(
                "rainbow",
                entrypoint="animation.plugins.rainbow:RainbowAnimation",
            ),
            _component("aurora_curtains_native", provider="receiver_native"),
            _component("not_ported"),
            _component("malformed", entrypoint="missing-colon"),
        ]
        self.channel = _Channel()
        self.interface = AnimationWebInterface(
            self.channel, _PreviewManager(components), local_mode=True
        )
        self.interface.animation_presets_dir = self.root / "presets"
        self.interface.generated_preview_dir = self.root / "previews"
        self.interface.runtime_preview_dir = self.root / "runtime-previews"
        for plugin_id in (
            "rainbow", "aurora_curtains_native", "not_ported", "malformed",
        ):
            directory = self.interface.animation_presets_dir / plugin_id
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "draft.json").write_text(json.dumps({
                "version": 2,
                "preset_id": "draft",
                "name": f"{plugin_id} Draft",
                "animation": plugin_id,
                "params": {"speed": 0.7},
            }), encoding="utf-8")
        self.client = self.interface.app.test_client()

    def test_shell_is_private_and_registers_installable_assets(self) -> None:
        response = self.client.get("/composer")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("composer.webmanifest", html)
        self.assertIn("composer.js", html)
        self.assertNotIn("/api/preview", html)
        self.assertEqual(self.channel.read_count, 0)
        self.assertEqual(self.channel.commands, [])

    def test_bootstrap_contains_full_presets_and_explicit_runtime_support(self) -> None:
        response = self.client.get("/api/v1/composer/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["schema"], "ledgrid.browser-composer-bootstrap")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["geometry"], {
            "strip_count": 33, "leds_per_strip": 138, "total_leds": 4554,
        })
        self.assertFalse(payload["capabilities"]["live_wall_mutated"])
        by_key = {item["key"]: item for item in payload["components"]}

        rainbow = by_key["python:rainbow"]
        self.assertEqual(rainbow["class_name"], "RainbowAnimation")
        self.assertEqual(rainbow["browser_runtime"]["engine"], "python-pyodide-wasm")
        self.assertEqual(rainbow["presets"][0]["params"], {"speed": 0.7})

        native = by_key["receiver_native:aurora_curtains_native"]
        self.assertEqual(
            native["browser_runtime"]["engine"], "receiver-native-cpp-wasm"
        )
        self.assertFalse(native["preview"]["framebuffer_readback"])

        self.assertTrue(by_key["python:not_ported"]["browser_runtime"]["supported"])

        unsupported = by_key["python:malformed"]
        self.assertFalse(unsupported["browser_runtime"]["supported"])
        self.assertIn("verified browser-Wasm entrypoint", unsupported["browser_runtime"]["reason"])
        self.assertEqual(self.channel.read_count, 0)
        self.assertEqual(self.channel.commands, [])

    def test_service_worker_is_root_scoped_and_not_immutably_cached(self) -> None:
        response = self.client.get("/composer-service-worker.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Service-Worker-Allowed"], "/")
        self.assertEqual(response.headers["Cache-Control"], "no-cache")
        response.close()


if __name__ == "__main__":
    unittest.main()
