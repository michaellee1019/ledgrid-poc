"""Explicit browser-composer validation and save-only action contracts."""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]
COMPOSER_SCRIPT = (ROOT / "web" / "static" / "js" / "composer.js").read_text(
    encoding="utf-8"
)


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
        self.status: dict = {}

    def read_status(self) -> dict:
        self.read_count += 1
        return deepcopy(self.status)

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
        self.assertEqual(
            actions["status_url"], "/api/v1/composer/settings/observed"
        )
        self.assertEqual(
            actions["operations_status_url"],
            "/api/v1/composer/operations/status",
        )
        self.assertEqual(actions["vibe_url"], "/api/v1/vibe")
        self.assertNotIn("masks_url", actions)
        self.assertEqual(
            payload["components"][0]["presentation"]["timing_adapter"],
            "legacy_speed_param",
        )
        self.assert_no_live_effect()

    def test_operations_status_is_uncached_revision_qualified_and_fixture_backed(self) -> None:
        self.channel.status = {
            "updated_at": 1_000.0,
            "is_running": True,
            "controller_session_id": "1" * 32,
            "controller_state_revision": 7,
            "current_identity_digest": "a" * 64,
            "active_identity": {"scene_identity": {"revision": 2, "digest": "b" * 64}},
            "target_fps": 60,
            "actual_fps": 55,
            "receiver_count": 2,
            "receiver_hybrid": {
                "operational": True,
                "degraded": True,
                "telemetry_complete": False,
                "readable_devices": [0],
                "unverified_devices": [1],
            },
        }

        response = self.client.get("/api/v1/composer/operations/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        payload = response.get_json()
        self.assertEqual(payload["schema"], "ledgrid.composer-operations-status")
        self.assertEqual(payload["observation"]["revision"]["state_revision"], 7)
        self.assertEqual(payload["output_power"]["state"], "stale")
        self.assertTrue(payload["output_power"]["observed"])
        self.assertEqual(payload["output_power"]["revision"]["state_revision"], 7)
        self.assertEqual(payload["health"]["receivers"]["missing"], [1])
        self.assertEqual(payload["raw_evidence"]["owner"], "controller_status")

    def test_stop_uses_checked_activation_and_requires_exact_safe_idle_observation(self) -> None:
        self.assertIn("async function stopOutput()", COMPOSER_SCRIPT)
        self.assertIn("await createServerCheck(activationGlobalSettings(safeIdleSettings))", COMPOSER_SCRIPT)
        self.assertIn("expected_controller_state_revision: serverCheck.basis.controller.state_revision", COMPOSER_SCRIPT)
        self.assertIn("headers: {'Idempotency-Key': serverCheck.idempotencyKey}", COMPOSER_SCRIPT)
        self.assertIn("observation.state === 'idle'", COMPOSER_SCRIPT)
        self.assertIn("status?.output_power?.observed === false", COMPOSER_SCRIPT)
        self.assertIn("revision.state_revision === stop.revision", COMPOSER_SCRIPT)
        self.assertNotIn("/api/stop", COMPOSER_SCRIPT)

    def test_interaction_capabilities_are_provider_qualified_and_live_fail_closed(self) -> None:
        gradient = self.interface.preview_manager.components[0]
        gradient["interaction_capabilities"] = {
            "point": {"kind": "primary", "label": "Fixture point input"},
            "directions": ["left", "rotate-right", "unsupported"],
        }

        payload = self.client.get("/api/v1/composer/bootstrap").get_json()
        component = next(item for item in payload["components"] if item["key"] == "python:gradient")
        interactions = component["browser_capabilities"]["interactions"]
        self.assertEqual(interactions["provider"], "python")
        self.assertEqual(interactions["component_id"], "gradient")
        self.assertTrue(interactions["local_preview"]["point"]["supported"])
        self.assertEqual(interactions["local_preview"]["directions"], ["left", "rotate-right"])
        self.assertFalse(interactions["live_wall"]["available"])
        self.assertNotIn("interaction_url", payload["capabilities"]["server_actions"])
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
        path = (
            self.interface.animation_presets_dir / "python" / "gradient"
            / "soft_moss.json"
        )
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

    def test_provider_collision_saves_two_exact_component_presets_and_withholds_legacy(self) -> None:
        interface = AnimationWebInterface(
            _Channel(),
            _Manager([
                _component("compiled_rainbow"),
                _component("compiled_rainbow", provider="receiver_native"),
            ]),
            local_mode=True,
        )
        interface.animation_presets_dir = self.root / "collision-animations"
        interface.scene_presets_dir = self.root / "collision-scenes"
        legacy_dir = interface.animation_presets_dir / "compiled_rainbow"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "old.json").write_text(json.dumps({
            "preset_id": "old",
            "animation": "compiled_rainbow",
            "params": {"speed": 0.3},
        }))
        client = interface.app.test_client()

        for provider, speed in (("python", 0.7), ("receiver_native", 1.3)):
            with self.subTest(provider=provider):
                response = client.post("/api/v1/composer/presets", json={
                    "schema": "ledgrid.browser-composer-save",
                    "schema_version": 1,
                    "component_key": f"{provider}:compiled_rainbow",
                    "name": "Shared Look",
                    "params": {"speed": speed},
                    "overwrite": False,
                })
                self.assertEqual(response.status_code, 201, response.get_json())
                self.assertEqual(response.get_json()["preset"]["provider"], provider)

        bootstrap = client.get("/api/v1/composer/bootstrap?catalog_only=1").get_json()
        collision_components = [
            item for item in bootstrap["components"]
            if item["plugin_id"] == "compiled_rainbow"
        ]
        self.assertEqual(len(collision_components), 2)
        self.assertTrue(all(
            item["browser_capabilities"]["saveable"]
            for item in collision_components
        ))
        self.assertEqual(bootstrap["diagnostics"][0]["code"], "provider_collision")
        self.assertEqual(
            bootstrap["diagnostics"][0]["recovery"]["reimport_url"],
            "/api/v1/composer/presets",
        )

        python_presets = client.get(
            "/api/v1/components/compiled_rainbow/presets?provider=python"
        ).get_json()["presets"]
        native_presets = client.get(
            "/api/v1/components/compiled_rainbow/presets?provider=receiver_native"
        ).get_json()["presets"]
        self.assertEqual(
            [(item["provider"], item["preset_id"]) for item in python_presets],
            [("python", "shared_look")],
        )
        self.assertEqual(
            [(item["provider"], item["preset_id"]) for item in native_presets],
            [("receiver_native", "shared_look")],
        )
        self.assertEqual(
            interface._load_animation_preset(
                "compiled_rainbow", "shared_look", "python"
            )["params"],
            {"speed": 0.7},
        )
        self.assertEqual(
            interface._load_animation_preset(
                "compiled_rainbow", "shared_look", "receiver_native"
            )["params"],
            {"speed": 1.3},
        )
        self.assertIsNone(interface._load_animation_preset(
            "compiled_rainbow", "old", "python"
        ))
        self.assertEqual(
            client.get("/api/v1/presets/legacy/compiled_rainbow/export")
            .status_code,
            404,
        )

    def test_provider_qualified_component_record_routes_are_user_only(self) -> None:
        channel = _Channel()
        interface = AnimationWebInterface(
            channel,
            _Manager([
                _component("compiled_rainbow"),
                _component("compiled_rainbow", provider="receiver_native"),
            ]),
            local_mode=True,
        )
        interface.animation_presets_dir = self.root / "record-animations"
        client = interface.app.test_client()
        for provider, speed in (("python", 0.7), ("receiver_native", 1.3)):
            response = client.post("/api/v1/composer/presets", json={
                "schema": "ledgrid.browser-composer-save",
                "schema_version": 1,
                "component_key": f"{provider}:compiled_rainbow",
                "name": "Shared Look",
                "params": {"speed": speed},
                "overwrite": False,
            })
            self.assertEqual(response.status_code, 201, response.get_json())

        ambiguous = client.get(
            "/api/v1/components/compiled_rainbow/presets/shared_look"
        )
        self.assertEqual(ambiguous.status_code, 409)
        malformed = client.get(
            "/api/v1/components/compiled_rainbow/presets/not%20valid?provider=python"
        )
        self.assertEqual(malformed.status_code, 400)

        fetched = client.get(
            "/api/v1/components/compiled_rainbow/presets/shared_look?provider=python"
        )
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.headers["Cache-Control"], "no-store")
        self.assertEqual(fetched.get_json()["preset"]["params"], {"speed": 0.7})
        self.assertEqual(fetched.get_json()["preset"]["ownership"], "user")

        deleted_native = client.delete(
            "/api/v1/components/compiled_rainbow/presets/shared_look?provider=receiver_native"
        )
        self.assertEqual(deleted_native.status_code, 200)
        self.assertEqual(
            client.get(
                "/api/v1/components/compiled_rainbow/presets/shared_look?provider=python"
            ).get_json()["preset"]["params"],
            {"speed": 0.7},
        )
        deleted_python = client.delete(
            "/api/v1/components/compiled_rainbow/presets/shared_look?provider=python"
        )
        self.assertEqual(deleted_python.status_code, 200)

        curated = self.root / "curated"
        curated.mkdir()
        (curated / "built_in.json").write_text(json.dumps({
            "version": 2,
            "preset_id": "built_in",
            "name": "Built in",
            "animation": "compiled_rainbow",
            "provider": "python",
            "params": {"speed": 0.9},
        }), encoding="utf-8")
        interface._curated_animation_preset_dir = lambda _name: curated
        override = client.post("/api/v1/composer/presets", json={
            "schema": "ledgrid.browser-composer-save",
            "schema_version": 1,
            "component_key": "python:compiled_rainbow",
            "name": "Built in",
            "params": {"speed": 1.1},
            "overwrite": True,
        })
        self.assertEqual(override.status_code, 200, override.get_json())
        self.assertEqual(
            client.get(
                "/api/v1/components/compiled_rainbow/presets/built_in?provider=python"
            ).get_json()["preset"]["ownership"],
            "user",
        )
        restored = client.delete(
            "/api/v1/components/compiled_rainbow/presets/built_in?provider=python"
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(
            client.get(
                "/api/v1/components/compiled_rainbow/presets/built_in?provider=python"
            ).get_json()["preset"]["ownership"],
            "built_in",
        )
        immutable = client.delete(
            "/api/v1/components/compiled_rainbow/presets/built_in?provider=python"
        )
        self.assertEqual(immutable.status_code, 409)
        self.assertEqual(immutable.get_json()["code"], "preset_immutable")
        self.assertFalse((curated / "built_in.json").is_symlink())
        self.assertTrue((curated / "built_in.json").is_file())
        self.assertEqual(channel.commands, [])
        self.assertEqual(channel.read_count, 0)

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

    def test_explicit_live_edit_updates_only_the_expected_active_component(self) -> None:
        self.channel.status = {
            "is_running": True,
            "current_animation": "gradient",
            "scene_state": _scene(),
        }
        response = self.client.patch(
            "/api/v1/scene/components/background",
            json={
                "live_edit": True,
                "expected_component": {
                    "provider": "python", "component_id": "gradient",
                },
                "params": {"speed": 1.4},
            },
        )

        self.assertEqual(response.status_code, 202, response.get_json())
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.channel.commands, [{
            "action": "update_scene_component",
            "data": {
                "target": "background",
                "update": {
                    "params": {"speed": 1.4},
                },
            },
        }])

        mismatched = self.client.patch(
            "/api/v1/scene/components/background",
            json={
                "live_edit": True,
                "expected_component": {
                    "provider": "python", "component_id": "other",
                },
                "params": {"speed": 1.2},
            },
        )
        self.assertEqual(mismatched.status_code, 409)
        self.assertEqual(len(self.channel.commands), 1)

    def test_live_edit_checks_renderer_identity_before_validating_stale_parameters(self) -> None:
        self.channel.status = {
            "is_running": True,
            "current_animation": "gradient",
            "scene_state": _scene(),
        }

        response = self.client.patch(
            "/api/v1/scene/components/background",
            json={
                "live_edit": True,
                "expected_component": {
                    "provider": "python", "component_id": "cloud_canyon",
                },
                # Valid for the stale Cloud Canyon editor, not Gradient.
                "params": {"background": "mist"},
            },
        )

        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()["code"], "live_edit_conflict")
        self.assertIn("no longer running", response.get_json()["error"])
        self.assertEqual(self.channel.commands, [])

    def test_composer_catalog_hides_deployment_recovery_snapshots(self) -> None:
        preset_dir = self.interface.animation_presets_dir / "gradient"
        preset_dir.mkdir(parents=True)
        for preset_id in ("before-deploy", "quiet"):
            (preset_dir / f"{preset_id}.json").write_text(json.dumps({
                "version": 2,
                "preset_id": preset_id,
                "name": preset_id,
                "animation": "gradient",
                "provider": "python",
                "params": {"speed": 1.0},
            }))

        presets = self.interface._list_animation_presets("gradient")

        self.assertEqual([preset["preset_id"] for preset in presets], ["quiet"])

    def test_mobile_layers_surface_keeps_local_actions_and_immediate_apply_status_reachable(self) -> None:
        html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        css = (ROOT / "web/static/css/composer.css").read_text(encoding="utf-8")
        javascript = (ROOT / "web/static/js/composer.js").read_text(encoding="utf-8")

        for element_id in (
            "importPanelButton",
            "exportPanelButton",
            "saveLibraryPanelButton",
            "mobileActivationStatus",
            "immediateApplyStatus",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('data-mobile-target="layers"', html)
        self.assertIn('class="mobile-activate-bar"', html)
        self.assertIn("grid-template-columns: repeat(5, 1fr)", css)
        self.assertIn(".server-action-buttons button, .local-action-buttons button, .mobile-tabs button { min-height: 44px; }", css)
        self.assertIn("return preset?.key || preset?.preset_id", javascript)
        self.assertIn("state.selectedPreset = record.key", javascript)
        self.assertIn("renderClockControls()", javascript)
        self.assertIn("runtime.renderInstance('clock_overlay'", javascript)
        self.assertIn("expected_controller_state_revision", javascript)
        self.assertIn("status.telemetry?.complete", javascript)
        self.assertIn("check_token: serverCheck.token", javascript)
        self.assertIn("IMMEDIATE_APPLY_MIN_INTERVAL_MS = 120", javascript)
        self.assertIn("createLatestStateQueue()", javascript)
        self.assertIn("queueImmediateApply({immediate: true", javascript)
        self.assertIn("await submitCheckedIntent(entry.intent, serverCheck)", javascript)
        self.assertIn("await waitForImmediateActivation(entry, result)", javascript)
        self.assertNotIn("liveEditToggle", javascript)
        self.assertNotIn("reviewActivation", javascript)
        self.assertNotIn('id="activatePanelButton"', html)
        self.assertNotIn('id="mobileActivateButton"', html)

    def test_immediate_apply_is_serial_guarded_and_history_navigation_is_non_mutating(self) -> None:
        html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        javascript = (ROOT / "web/static/js/composer.js").read_text(encoding="utf-8")

        self.assertIn("if (state.urlState.applying) return false", javascript)
        self.assertIn("scheduleApply: false", javascript)
        self.assertIn("if (!fromBrowser) queueImmediateApply", javascript)
        self.assertIn("apply.queue.enqueue(intent)", javascript)
        self.assertIn("if (apply.inFlight) return", javascript)
        self.assertIn("await refreshGlobalSettings({quiet: true, preserveDraft: true})", javascript)
        self.assertIn("await createServerCheck(", javascript)
        self.assertIn("await submitCheckedIntent(entry.intent, serverCheck)", javascript)
        self.assertIn("await waitForImmediateActivation(entry, result)", javascript)
        self.assertIn("controller reconnected. A fresh edit is required", javascript)
        self.assertIn("outcome = {state: 'failed', retryable: true", javascript)
        self.assertNotIn('id="activateDialog"', html)
        self.assertNotIn('id="wallReviewDialog"', html)

        connectivity = javascript[javascript.index("async function checkConnectivity"):]
        connectivity = connectivity[:connectivity.index("function showOfflineReadiness")]
        self.assertIn("await refreshGlobalSettings({", connectivity)
        self.assertLess(
            connectivity.index("await refreshGlobalSettings({"),
            connectivity.index("setServerOnline(payload.online === true"),
        )

    def test_saved_preset_controls_use_provider_qualified_drafts_not_apply_shortcuts(self) -> None:
        html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        javascript = (ROOT / "web/static/js/composer.js").read_text(encoding="utf-8")

        for element_id in (
            "savedRecordSelect", "savedRecordStatus", "reopenSavedRecordButton",
            "updateSavedRecordButton", "deleteSavedRecordButton",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for function_name in (
            "refreshSavedRecords", "reopenSavedRecord", "updateSavedRecord",
            "deleteSavedRecord",
        ):
            self.assertIn(f"function {function_name}", javascript)
        self.assertIn("/api/v1/components/${encodeURIComponent(component.plugin_id)}/presets/", javascript)
        self.assertIn("provider=${encodeURIComponent(component.provider)}", javascript)
        self.assertIn("preset_immutable", (ROOT / "web/app.py").read_text(encoding="utf-8"))
        self.assertIn("queued for guarded immediate apply", javascript)
        self.assertNotIn("/apply", javascript[javascript.index("function reopenSavedRecord"):javascript.index("function updateSavedRecord")])
        update_start = javascript.index("async function updateSavedRecord")
        delete_start = javascript.index("async function deleteSavedRecord")
        update_body = javascript[update_start:delete_start]
        self.assertIn("state.savedRecords.reopened !== state.savedRecords.selected", update_body)
        self.assertIn("presetIdForName", update_body)
        self.assertIn("Keep this record name to update it", update_body)
        delete_body = javascript[delete_start:javascript.index("function defaultParams", delete_start)]
        self.assertIn("state.lastSavedPreset = null", delete_body)
        self.assertIn("preset?.ownership !== 'user'", delete_body)

    def test_wall_workspace_is_complete_and_presets_exclude_global_state(self) -> None:
        html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        javascript = (ROOT / "web/static/js/composer.js").read_text(encoding="utf-8")

        for element_id in (
            "vibeOptions", "globalBrightness", "globalSpeed", "globalTargetFps",
            "plantModifierGroups", "editMasksButton", "maskCanvas",
            "immediateApplyStatus",
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
        self.assertNotIn('id="wallReviewDialog"', html)
        self.assertNotIn('id="confirmWallChangesButton"', html)
        self.assertIn("queueImmediateApply({source: 'wall setting'})", javascript)


if __name__ == "__main__":
    unittest.main()
