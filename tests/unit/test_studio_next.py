"""Focused Studio Next backend adapter and command-boundary tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from animation.core.presentation_contracts import resolve_vibe
from web.app import AnimationWebInterface


class _Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


def _component(
    plugin_id: str,
    *,
    provider: str = "python",
    role: str = "background",
    gallery: str = "show",
    composable: bool = True,
    implementation_loaded: bool = True,
    status: str | None = None,
) -> dict:
    result = {
        "plugin_id": plugin_id,
        "name": plugin_id.replace("_", " ").title(),
        "description": f"Complete description for {plugin_id}.",
        "provider": provider,
        "role": role,
        "gallery": gallery,
        "parameter_schema": {
            "speed": {"type": "float", "min": 0.1, "max": 5.0, "default": 1.0},
        },
        "defaults": {"speed": 1.0},
        "preview": {
            "kind": "host_contract_renderer" if provider == "receiver_native" else "generated",
            "framebuffer_readback": False,
        },
        "compatibility": {
            "classification": "declared_component",
            "composable": composable,
            "implementation_loaded": implementation_loaded,
            "parameter_metadata": "loaded" if implementation_loaded else "manifest",
            "diagnostic": "Test descriptor diagnostic.",
        },
    }
    if status is not None:
        result["status"] = status
    return result


DEFAULT_COMPONENTS = [
    _component("meadow"),
    _component("clock_overlay", role="overlay"),
    _component("plant_calibration", gallery="test"),
    _component("build_only", implementation_loaded=False),
    _component("native_glow", provider="receiver_native"),
]


class _PreviewManager:
    controller = _Controller()
    preview_controller = controller

    def __init__(self, components: list[dict]) -> None:
        self._components = deepcopy(components)
        self._loaded = {
            item["plugin_id"]
            for item in components
            if (
                item.get("provider") == "python"
                and item.get("compatibility", {}).get("implementation_loaded") is True
            )
        }

    def list_components(self) -> list[dict]:
        return deepcopy(self._components)

    def list_animations(self) -> list[dict]:
        return []

    def get_animation_info(self, plugin_id: str) -> dict | None:
        if plugin_id not in self._loaded:
            return None
        component = next(
            item for item in self._components
            if item["provider"] == "python" and item["plugin_id"] == plugin_id
        )
        return {"parameters": deepcopy(component["parameter_schema"])}

    @staticmethod
    def get_vibe_status() -> dict:
        return {"state": resolve_vibe("neutral").state.to_dict()}


class _Channel:
    def __init__(self) -> None:
        self.commands: list[dict] = []
        self.read_count = 0
        self.status = {
            "is_running": True,
            "mode": "animation",
            "current_animation": "meadow",
            "current_preset": {
                "preset_id": "evening",
                "name": "Evening Meadow",
                "animation": "meadow",
            },
            "brightness": 96,
            "target_fps": 60,
            "animation_speed_scale": 0.05,
            "vibe": {"state": resolve_vibe("neutral").state.to_dict()},
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            "led_info": {
                "strip_count": 32,
                "leds_per_strip": 138,
                "total_leds": 4416,
            },
            "updated_at": 1787410000.0,
        }

    def read_status(self) -> dict:
        self.read_count += 1
        return deepcopy(self.status)

    def send_command(self, action: str, **data) -> dict:
        command = {
            "command_id": f"cmd-{len(self.commands) + 1}",
            "action": action,
            "data": deepcopy(data),
        }
        self.commands.append(command)
        return deepcopy(command)


class StudioNextBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.interface, self.client, self.channel = self._surface(DEFAULT_COMPONENTS)
        for plugin_id in (
            "meadow", "clock_overlay", "plant_calibration", "build_only", "native_glow"
        ):
            self._write_preset(self.interface, plugin_id, "evening", {"speed": 0.7})

    def _surface(
        self, components: list[dict]
    ) -> tuple[AnimationWebInterface, object, _Channel]:
        channel = _Channel()
        interface = AnimationWebInterface(
            channel, _PreviewManager(components), local_mode=True
        )
        surface_root = self.root / f"surface-{id(interface)}"
        interface.animation_presets_dir = surface_root / "presets"
        interface.scene_presets_dir = surface_root / "scenes"
        interface.generated_preview_dir = surface_root / "previews"
        interface.runtime_preview_dir = surface_root / "runtime-previews"
        interface.animation_presets_dir.mkdir(parents=True)
        interface.scene_presets_dir.mkdir(parents=True)
        return interface, interface.app.test_client(), channel

    @staticmethod
    def _write_preset(
        interface: AnimationWebInterface,
        plugin_id: str,
        preset_id: str,
        params: dict,
    ) -> None:
        directory = interface.animation_presets_dir / plugin_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{preset_id}.json").write_text(json.dumps({
            "version": 2,
            "preset_id": preset_id,
            "name": f"{plugin_id.title()} {preset_id.title()}",
            "animation": plugin_id,
            "params": params,
        }), encoding="utf-8")

    def test_shell_starts_unknown_without_reading_or_commanding_the_wall(self) -> None:
        response = self.client.get("/studio-next")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="liveState" data-state="unknown"', html)
        self.assertIn("No fixture content is presented as live", html)
        self.assertEqual(self.channel.read_count, 0)
        self.assertEqual(self.channel.commands, [])

    def test_bootstrap_is_provider_qualified_honest_and_non_mutating(self) -> None:
        response = self.client.get("/api/v1/studio-next/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["schema"], "ledgrid.studio-next-bootstrap")
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["local_mode"])
        self.assertIsInstance(payload["generated_at"], float)
        self.assertEqual(payload["status"]["current_animation"], "meadow")
        self.assertEqual(payload["scene"]["schema"], "ledgrid.scene-api")
        self.assertTrue(payload["scene"]["active"])
        self.assertEqual(payload["scene"]["scene"]["background"]["plugin_id"], "meadow")
        self.assertEqual(len(payload["vibe_profiles"]), 5)
        self.assertEqual(payload["scene_presets"], [])

        catalog = payload["catalog"]
        self.assertEqual(catalog["schema"], "ledgrid.studio-next-catalog")
        self.assertEqual(catalog["totals"], {
            "components": 5,
            "presets": 5,
            "presets_withheld": 0,
            "components_by_provider": {"python": 4, "receiver_native": 1},
            "provider_collisions": 0,
        })
        self.assertEqual(
            {component["key"] for component in catalog["components"]},
            {
                "python:meadow",
                "python:clock_overlay",
                "python:plant_calibration",
                "python:build_only",
                "receiver_native:native_glow",
            },
        )
        self.assertEqual(
            {preset["key"] for preset in catalog["presets"]},
            {
                "python:meadow:evening",
                "python:clock_overlay:evening",
                "python:plant_calibration:evening",
                "python:build_only:evening",
                "receiver_native:native_glow:evening",
            },
        )
        meadow = next(
            item for item in catalog["components"] if item["key"] == "python:meadow"
        )
        native = next(
            item for item in catalog["components"]
            if item["key"] == "receiver_native:native_glow"
        )
        self.assertTrue(meadow["action"]["take_look_enabled"])
        self.assertFalse(native["action"]["take_look_enabled"])
        self.assertFalse(meadow["preview"]["live_state_mutated"])
        self.assertEqual(native["preview"]["provenance"], "receiver_host_simulation")
        self.assertFalse(native["preview"]["framebuffer_readback"])
        self.assertEqual(self.channel.commands, [])

    def test_provider_collision_withholds_legacy_assets_and_fails_closed(self) -> None:
        components = [
            _component("meadow"),
            _component("meadow", provider="receiver_native"),
        ]
        interface, client, channel = self._surface(components)
        self._write_preset(interface, "meadow", "evening", {"speed": 0.7})

        bootstrap = client.get("/api/v1/studio-next/bootstrap").get_json()
        catalog = bootstrap["catalog"]
        self.assertEqual(catalog["totals"]["presets"], 0)
        self.assertEqual(catalog["totals"]["presets_withheld"], 1)
        self.assertEqual(catalog["totals"]["provider_collisions"], 1)
        self.assertEqual(catalog["diagnostics"], [{
            "code": "provider_collision",
            "plugin_id": "meadow",
            "providers": ["python", "receiver_native"],
            "withheld_legacy_presets": 1,
            "message": (
                "Legacy preset and preview records are withheld because their "
                "provider cannot be determined safely."
            ),
        }])
        for component in catalog["components"]:
            self.assertTrue(component["provider_collision"])
            self.assertIsNone(component["preview"])
            self.assertEqual(component["preset_keys"], [])
            self.assertEqual(component["action"]["code"], "provider_collision")

        response = client.post("/api/v1/studio-next/take-look", json={
            "provider": "python", "plugin_id": "meadow", "preset_id": "evening",
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "provider_collision")
        self.assertEqual(channel.commands, [])

    def test_take_look_starts_exact_ready_host_preset_with_command_id(self) -> None:
        response = self.client.post("/api/v1/studio-next/take-look", json={
            "provider": "python", "plugin_id": "meadow", "preset_id": "evening",
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["command_id"], "cmd-1")
        self.assertEqual(payload["identity"], {
            "key": "python:meadow:evening",
            "component_key": "python:meadow",
            "provider": "python",
            "plugin_id": "meadow",
            "preset_id": "evening",
        })
        self.assertEqual(self.channel.commands, [{
            "command_id": "cmd-1",
            "action": "start",
            "data": {
                "animation": "meadow",
                "config": {"speed": 0.7},
                "preset": {
                    "preset_id": "evening",
                    "name": "Meadow Evening",
                    "animation": "meadow",
                },
            },
        }])

    def test_scene_rejects_unloaded_python_component_before_command_write(self) -> None:
        def scene_for(plugin_id: str) -> dict:
            return {
                "schema": "ledgrid.scene-state",
                "schema_version": 1,
                "revision": 1,
                "background": {
                    "provider": "python",
                    "plugin_id": plugin_id,
                    "parameter_overrides": {},
                    "resolved_parameters": {},
                },
                "overlays": [],
                "known_python_fallback": {
                    "provider": "python",
                    "plugin_id": plugin_id,
                    "parameter_overrides": {},
                    "resolved_parameters": {},
                },
            }

        response = self.client.post(
            "/api/v1/scene", json={"scene": scene_for("build_only")}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not loaded", response.get_json()["error"])
        self.assertEqual(self.channel.commands, [])

        response = self.client.post(
            "/api/v1/studio-next/take-scene",
            json={"scene": scene_for("plant_calibration")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("unavailable", response.get_json()["error"])
        self.assertEqual(self.channel.commands, [])

        response = self.client.post(
            "/api/v1/studio-next/take-scene",
            json={"scene": scene_for("meadow")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["command_id"], "cmd-1")
        self.assertEqual(self.channel.commands[0]["action"], "start_scene")

    def test_studio_scene_rejects_quarantined_clock_before_command_write(self) -> None:
        components = [
            _component("meadow"),
            _component("clock_overlay", role="overlay", status="quarantined"),
        ]
        _interface, client, channel = self._surface(components)
        scene = {
            "schema": "ledgrid.scene-state",
            "schema_version": 1,
            "revision": 2,
            "background": {
                "provider": "python", "plugin_id": "meadow",
                "parameter_overrides": {}, "resolved_parameters": {},
            },
            "overlays": [{
                "slot_id": "clock_overlay",
                "component": {
                    "provider": "python", "plugin_id": "clock_overlay",
                    "parameter_overrides": {}, "resolved_parameters": {},
                },
                "enabled": True,
                "opacity": 255,
                "placement": {
                    "strip_translation": 0, "led_translation": 0,
                    "clip_policy": "clip_to_wall",
                },
                "stale_policy": {"policy": "hold"},
            }],
            "known_python_fallback": {
                "provider": "python", "plugin_id": "meadow",
                "parameter_overrides": {}, "resolved_parameters": {},
            },
        }

        response = client.post(
            "/api/v1/studio-next/take-scene", json={"scene": scene}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("ready Host Python clock_overlay", response.get_json()["error"])
        self.assertEqual(channel.commands, [])

    def test_take_look_rejects_every_forbidden_case_before_command_write(self) -> None:
        cases = (
            {"provider": "receiver_native", "plugin_id": "native_glow", "preset_id": "evening"},
            {"provider": "python", "plugin_id": "clock_overlay", "preset_id": "evening"},
            {"provider": "python", "plugin_id": "plant_calibration", "preset_id": "evening"},
            {"provider": "python", "plugin_id": "build_only", "preset_id": "evening"},
            {"provider": "python", "plugin_id": "meadow", "preset_id": "missing"},
            {"provider": "python", "plugin_id": "missing", "preset_id": "evening"},
            {
                "provider": "python", "plugin_id": "meadow",
                "preset_id": "evening", "unexpected": True,
            },
        )
        for body in cases:
            with self.subTest(body=body):
                response = self.client.post(
                    "/api/v1/studio-next/take-look", json=body
                )
                self.assertIn(response.status_code, {400, 404, 409})
        self.assertEqual(self.channel.commands, [])

    def test_existing_control_routes_return_supplied_command_ids(self) -> None:
        calls = (
            ("/api/stop", {}),
            ("/api/device/state", {"power": True}),
            ("/api/animations/meadow/presets/evening/apply", {}),
            ("/api/v1/vibe", {"id": "quiet"}),
            (
                "/api/config/plant-modifiers",
                {"plant_modifiers": {"version": 1, "active": [], "strengths": {}}},
            ),
            ("/api/config/brightness", {"brightness": 128}),
            ("/api/config/target-fps", {"target_fps": 90}),
            ("/api/config/animation-speed", {"multiplier": 1.25}),
        )
        for index, (path, body) in enumerate(calls, start=1):
            with self.subTest(path=path):
                response = self.client.post(path, json=body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["command_id"], f"cmd-{index}")
        self.assertEqual(len(self.channel.commands), len(calls))


if __name__ == "__main__":
    unittest.main()
