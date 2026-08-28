"""Explicit browser-composer validation and save-only action contracts."""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]


def _component(
    plugin_id: str,
    *,
    provider: str = "python",
    role: str = "background",
) -> dict:
    return {
        "plugin_id": plugin_id,
        "provider": provider,
        "role": role,
        "name": plugin_id.replace("_", " ").title(),
        "description": f"Composer action fixture for {plugin_id}.",
        "icon": "◷" if role == "overlay" else "✦",
        "gallery": "show",
        "entrypoint": (
            f"animation.plugins.{plugin_id}:{plugin_id.title()}Animation"
            if provider == "python"
            else "ledgrid.native-background-abi:2"
        ),
        "parameter_schema": {
            "speed": {
                "type": "float", "min": 0.1, "max": 5.0,
                "default": 1.0,
            },
        },
        "defaults": {"speed": 1.0},
        "vibe": {
            "color_policy": "preserve",
            "timing_adapter": "legacy_speed_param",
            "capabilities": [],
        },
        "build": {},
        "availability": {"state": "ready"},
        "compatibility": {
            "classification": "declared_component",
            "composable": True,
            "implementation_loaded": provider == "python",
            "parameter_metadata": "loaded",
            "diagnostic": "Ready fixture.",
        },
    }


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _Manager:
    controller = _Controller()
    preview_controller = controller

    def __init__(self, components: list[dict]) -> None:
        self.components = deepcopy(components)

    def list_components(self) -> list[dict]:
        return deepcopy(self.components)

    def list_animations(self) -> list[dict]:
        return []

    def get_animation_info(self, plugin_id: str) -> dict | None:
        component = next(
            (
                item for item in self.components
                if item["provider"] == "python" and item["plugin_id"] == plugin_id
            ),
            None,
        )
        return (
            {"parameters": deepcopy(component["parameter_schema"])}
            if component else None
        )


class _Channel:
    def __init__(self) -> None:
        self.read_count = 0
        self.commands: list[dict] = []

    def read_status(self) -> dict:
        self.read_count += 1
        return {}

    def send_command(self, action: str, **data) -> dict:
        self.commands.append({"action": action, "data": deepcopy(data)})
        return {"command_id": "unexpected"}


def _scene() -> dict:
    background = {
        "plugin_id": "gradient",
        "provider": "python",
        "parameter_overrides": {},
        "resolved_parameters": {"speed": 0.7},
    }
    return {
        "schema": "ledgrid.scene-state",
        "schema_version": 1,
        "revision": 9,
        "background": background,
        "overlays": [{
            "slot_id": "clock_overlay",
            "component": {
                "plugin_id": "clock_overlay",
                "provider": "python",
                "parameter_overrides": {},
                "resolved_parameters": {"speed": 1.0},
            },
            "enabled": True,
            "opacity": 220,
            "placement": {
                "strip_translation": 0,
                "led_translation": 0,
                "clip_policy": "clip_to_wall",
            },
            "stale_policy": {"policy": "hold"},
        }],
        "known_python_fallback": deepcopy(background),
    }


class BrowserComposerActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.channel = _Channel()
        self.interface = AnimationWebInterface(
            self.channel,
            _Manager([
                _component("gradient"),
                _component("clock_overlay", role="overlay"),
            ]),
            local_mode=True,
        )
        self.interface.animation_presets_dir = self.root / "animations"
        self.interface.scene_presets_dir = self.root / "scenes"
        self.client = self.interface.app.test_client()

    def assert_no_live_effect(self) -> None:
        self.assertEqual(self.channel.read_count, 0)
        self.assertEqual(self.channel.commands, [])

    def test_connectivity_is_uncached_and_does_not_observe_wall(self) -> None:
        response = self.client.get("/api/v1/composer/connectivity")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertFalse(response.get_json()["actions"]["activate_scene"])
        self.assert_no_live_effect()

    def test_bootstrap_exposes_full_independent_wall_control_contract(self) -> None:
        response = self.client.get("/api/v1/composer/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            [item["vibe_id"] for item in payload["vibe_profiles"]],
            ["neutral", "quiet", "cozy", "vivid", "celebration"],
        )
        contract = payload["global_control_contract"]
        self.assertEqual(len(contract["plant_modifier_ids"]), 14)
        self.assertEqual(
            set(contract["field_modifiers"]),
            {"attractor", "repulsor", "slow_zone"},
        )
        actions = payload["capabilities"]["server_actions"]
        self.assertEqual(actions["status_url"], "/api/status")
        self.assertEqual(actions["vibe_url"], "/api/v1/vibe")
        self.assertEqual(actions["masks_url"], "/api/painter/masks")
        self.assertEqual(
            payload["components"][0]["presentation"]["timing_adapter"],
            "legacy_speed_param",
        )
        self.assert_no_live_effect()

    def test_component_upload_validation_is_read_only_and_provider_qualified(self) -> None:
        response = self.client.post(
            "/api/v1/composer/presets/validate",
            json={
                "version": 2,
                "name": "Soft gradient",
                "animation": "gradient",
                "provider": "python",
                "params": {"speed": 0.7},
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["kind"], "component_preset")
        self.assertEqual(payload["draft"]["component_key"], "python:gradient")
        self.assertFalse(self.interface.animation_presets_dir.exists())
        self.assert_no_live_effect()

    def test_browser_catalog_omits_retired_mask_paths_and_explicit_inputs_fail_closed(self) -> None:
        legacy_names = {"plant_mask_path", "plant_globe_mask_path"}
        gradient = next(
            item
            for item in self.interface.preview_manager.components
            if item["plugin_id"] == "gradient"
        )
        for name in legacy_names:
            gradient["parameter_schema"][name] = {
                "type": "str", "default": f"config/{name}.json"
            }
            gradient["defaults"][name] = f"config/{name}.json"

        preset_dir = self.interface.animation_presets_dir / "gradient"
        preset_dir.mkdir(parents=True)
        (preset_dir / "legacy.json").write_text(json.dumps({
            "version": 2,
            "preset_id": "legacy",
            "name": "Legacy fixture",
            "animation": "gradient",
            "provider": "python",
            "params": {
                "speed": 0.7,
                "plant_mask_path": "config/old-foliage.json",
                "plant_globe_mask_path": "config/old-globes.json",
            },
        }), encoding="utf-8")

        bootstrap = self.client.get("/api/v1/composer/bootstrap").get_json()
        component = next(
            item for item in bootstrap["components"]
            if item["key"] == "python:gradient"
        )
        self.assertTrue(legacy_names.isdisjoint(component["parameter_schema"]))
        self.assertTrue(legacy_names.isdisjoint(component["defaults"]))
        self.assertEqual(component["presets"][0]["params"], {"speed": 0.7})

        component_upload = self.client.post(
            "/api/v1/composer/presets/validate",
            json={
                "version": 2,
                "name": "Injected",
                "animation": "gradient",
                "provider": "python",
                "params": {
                    "speed": 0.7,
                    "plant_mask_path": "config/injected.json",
                },
            },
        )
        self.assertEqual(component_upload.status_code, 400)
        self.assertIn("retired plant-mask path", component_upload.get_json()["error"])

        save = self.client.post("/api/v1/composer/presets", json={
            "schema": "ledgrid.browser-composer-save",
            "schema_version": 1,
            "component_key": "python:gradient",
            "name": "Injected save",
            "params": {
                "speed": 0.7,
                "plant_globe_mask_path": "config/injected.json",
            },
            "overwrite": False,
        })
        self.assertEqual(save.status_code, 400)
        self.assertIn("retired plant-mask path", save.get_json()["error"])

        scene = _scene()
        for reference in (scene["background"], scene["known_python_fallback"]):
            reference["resolved_parameters"]["plant_mask_path"] = (
                "config/injected.json"
            )
        scene_upload = self.client.post(
            "/api/v1/composer/presets/validate",
            json={
                "schema": "ledgrid.scene-preset",
                "schema_version": 1,
                "name": "Injected scene",
                "scene": scene,
            },
        )
        self.assertEqual(scene_upload.status_code, 400)
        self.assertIn("retired plant-mask path", scene_upload.get_json()["error"])
        self.assert_no_live_effect()

    def test_scene_preset_upload_validates_exact_background_clock_and_fallback(self) -> None:
        response = self.client.post(
            "/api/v1/composer/presets/validate",
            json={
                "schema": "ledgrid.scene-preset",
                "schema_version": 1,
                "name": "Evening clock",
                "scene": _scene(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["kind"], "scene_preset")
        self.assertEqual(payload["draft"]["component_key"], "python:gradient")
        self.assertEqual(
            payload["draft"]["scene"]["overlays"][0]["slot_id"],
            "clock_overlay",
        )
        self.assert_no_live_effect()

    def test_save_is_atomic_save_only_and_requires_confirmed_overwrite(self) -> None:
        body = {
            "schema": "ledgrid.browser-composer-save",
            "schema_version": 1,
            "component_key": "python:gradient",
            "name": "Soft Moss",
            "params": {"speed": 0.7},
            "overwrite": False,
        }
        created = self.client.post("/api/v1/composer/presets", json=body)

        self.assertEqual(created.status_code, 201)
        payload = created.get_json()
        self.assertTrue(payload["created"])
        self.assertRegex(payload["preset_fingerprint"], r"^[0-9a-f]{64}$")
        path = self.interface.animation_presets_dir / "gradient" / "soft_moss.json"
        self.assertEqual(json.loads(path.read_text())["params"], {"speed": 0.7})

        conflict = self.client.post("/api/v1/composer/presets", json=body)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["code"], "preset_exists")

        body["overwrite"] = True
        body["params"] = {"speed": 1.4}
        replaced = self.client.post("/api/v1/composer/presets", json=body)
        self.assertEqual(replaced.status_code, 200)
        self.assertFalse(replaced.get_json()["created"])
        self.assertEqual(json.loads(path.read_text())["params"], {"speed": 1.4})
        self.assert_no_live_effect()

    def test_invalid_params_never_create_a_preset(self) -> None:
        response = self.client.post("/api/v1/composer/presets", json={
            "schema": "ledgrid.browser-composer-save",
            "schema_version": 1,
            "component_key": "python:gradient",
            "name": "Broken",
            "params": {"speed": 99},
            "overwrite": False,
        })

        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.interface.animation_presets_dir.exists())
        self.assert_no_live_effect()

    def test_mobile_layers_surface_keeps_local_and_server_actions_reachable(self) -> None:
        html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        css = (ROOT / "web/static/css/composer.css").read_text(encoding="utf-8")
        javascript = (ROOT / "web/static/js/composer.js").read_text(encoding="utf-8")

        for element_id in (
            "importPanelButton",
            "exportPanelButton",
            "saveLibraryPanelButton",
            "activatePanelButton",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('data-mobile-target="layers"', html)
        self.assertIn("grid-template-columns: repeat(6, 1fr)", css)
        self.assertIn(".server-action-buttons button, .local-action-buttons button, .mobile-tabs button { min-height: 44px; }", css)
        self.assertIn("return preset?.key || preset?.preset_id", javascript)
        self.assertIn("state.selectedPreset = record.key", javascript)
        self.assertIn("renderClockControls()", javascript)
        self.assertIn("runtime.renderInstance('clock_overlay'", javascript)
        self.assertIn("expected_controller_state_revision", javascript)
        self.assertIn("status.telemetry?.complete", javascript)
        self.assertIn("check_token: serverCheck.token", javascript)
        self.assertIn("pollActivationStatus()", javascript)

    def test_wall_workspace_is_complete_and_presets_exclude_global_state(self) -> None:
        html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        javascript = (ROOT / "web/static/js/composer.js").read_text(encoding="utf-8")

        for element_id in (
            "vibeOptions", "globalBrightness", "globalSpeed", "globalTargetFps",
            "plantModifierGroups", "editMasksButton", "maskCanvas",
            "wallReviewDialog", "confirmWallChangesButton",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for modifier in (
            "illuminate", "shadow", "refract", "hue_shift", "liquid_glass",
            "attractor", "repulsor", "slow_zone", "obstacle", "portal",
            "bumper", "hazard", "habitat", "emitter",
        ):
            self.assertIn(f"'{modifier}'", javascript)
        self.assertIn("params: authoredParams(state.component, state.params)", javascript)
        self.assertIn("parameters: authoredParams(component, params)", javascript)
        self.assertIn("!isGlobalInstallationParameter(key)", javascript)


if __name__ == "__main__":
    unittest.main()
