"""Acceptance coverage for Phase 2C catalog, scene API, presets, and IPC."""

import json
import tempfile
import unittest
from pathlib import Path

from ipc.control_channel import (
    CONTROL_COMMAND_SCHEMA,
    CONTROL_STATUS_SCHEMA,
    FileControlChannel,
)
from ipc.scene_contract import scene_preview_identity
from animation.core.presentation_contracts import component_preset_fingerprint
from tools.deployment.preserve_deploy_settings import _preset_fingerprint
from web.app import AnimationWebInterface


class _Controller:
    strip_count = 2
    leds_per_strip = 3
    total_leds = 6


class _PreviewManager:
    controller = _Controller()
    preview_controller = controller

    def __init__(self):
        self.preview_calls = []
        self.plant_modifier_state = None

    def list_animations(self):
        return []

    def list_components(self, provider=None, role=None):
        items = [
            {
                "plugin_id": "gradient", "name": "Gradient",
                "provider": "python", "role": "background",
                "parameters": {"speed": {"type": "float", "min": 0.1, "max": 3.0}},
            },
            {
                "plugin_id": "unsafe_background", "name": "Unsafe",
                "provider": "python", "role": "background", "parameters": {},
                "compatibility": {
                    "composable": False,
                    "diagnostic": "Implementation is stateful.",
                },
            },
            {
                "plugin_id": "clock_overlay", "name": "Clock overlay",
                "provider": "python", "role": "overlay",
                "parameters": {"seconds": {"type": "bool"}},
            },
            {
                "plugin_id": "clock", "name": "Legacy clock",
                "provider": "python", "role": "full_scene", "parameters": {},
            },
            {
                "plugin_id": "native_canary", "name": "Future native",
                "provider": "receiver_native", "role": "background", "parameters": {},
            },
        ]
        return [
            item for item in items
            if (provider is None or item["provider"] == provider)
            and (role is None or item["role"] == role)
        ]

    def get_animation_info(self, name):
        return next((item for item in self.list_components() if item["plugin_id"] == name), None)

    def get_scene_preview(self, scene, *, vibe=None, plant_modifiers=None, elapsed=0):
        self.preview_calls.append((scene, vibe, plant_modifiers, elapsed))
        return {
            "frame_data": [[1, 2, 3]] * 6,
            "led_info": {"strip_count": 2, "leds_per_strip": 3, "total_leds": 6},
            "preview": True,
        }

    def get_vibe_status(self):
        from animation.core.presentation_contracts import resolve_vibe
        return {"state": resolve_vibe("neutral").state.to_dict()}


class _Channel:
    def __init__(self):
        self.commands = []
        self.status = {
            "is_running": False,
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            "led_info": {"strip_count": 2, "leds_per_strip": 3, "total_leds": 6},
        }

    def read_status(self):
        return dict(self.status)

    def send_command(self, action, **data):
        command = {"command_id": len(self.commands) + 1, "action": action, "data": data}
        self.commands.append(command)
        return command


def _scene(*, overlay=True, provider="python"):
    component = {
        "plugin_id": "gradient", "provider": provider,
        "parameter_overrides": {"speed": 0.5},
        "resolved_parameters": {"speed": 0.5},
    }
    overlays = []
    if overlay:
        overlays.append({
            "slot_id": "clock_overlay",
            "component": {
                "plugin_id": "clock_overlay", "provider": "python",
                "parameter_overrides": {"seconds": True},
                "resolved_parameters": {"seconds": True},
            },
            "enabled": True,
            "opacity": 192,
            "placement": {
                "strip_translation": 1, "led_translation": -2,
                "clip_policy": "clip_to_wall",
            },
            "stale_policy": {"policy": "clear_after_lease", "lease_ms": 1200},
        })
    return {
        "schema": "ledgrid.scene-state", "schema_version": 1, "revision": 7,
        "background": component, "overlays": overlays,
        "known_python_fallback": {**component, "provider": "python"},
    }


class SceneProductSurfaceTests(unittest.TestCase):
    def test_receiver_status_refresh_route_issues_versioned_read_only_command(self):
        response = self.client.post("/api/v1/receivers/status/refresh", json={})
        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertTrue(payload["accepted"])
        self.assertTrue(payload["request_id"].startswith("phase3a-"))
        command = self.channel.commands[-1]
        self.assertEqual(command["action"], "refresh_receiver_status")
        self.assertEqual(command["data"]["request_id"], payload["request_id"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.channel = _Channel()
        self.preview = _PreviewManager()
        self.interface = AnimationWebInterface(self.channel, self.preview)
        self.interface.animation_presets_dir = Path(self.temporary.name) / "animations"
        self.interface.animation_presets_dir.mkdir()
        self.interface.scene_presets_dir = Path(self.temporary.name) / "scenes"
        self.interface.scene_presets_dir.mkdir()
        self.client = self.interface.app.test_client()

    def tearDown(self):
        self.temporary.cleanup()

    def test_catalog_filters_and_explains_fixed_editor_compatibility(self):
        payload = self.client.get("/api/v1/components?provider=python&role=overlay").get_json()
        self.assertEqual([item["plugin_id"] for item in payload["components"]], ["clock_overlay"])
        self.assertEqual(
            payload["components"][0]["scene_compatibility"]["slots"],
            ["clock_overlay"],
        )
        all_components = self.client.get("/api/v1/components").get_json()["components"]
        native = next(item for item in all_components if item["plugin_id"] == "native_canary")
        legacy = next(item for item in all_components if item["plugin_id"] == "clock")
        self.assertFalse(native["scene_compatibility"]["selectable"])
        self.assertIn("not executable", native["scene_compatibility"]["diagnostic"])
        self.assertIn("Compatibility full scenes", legacy["scene_compatibility"]["diagnostic"])
        unsafe = next(item for item in all_components if item["plugin_id"] == "unsafe_background")
        self.assertFalse(unsafe["scene_compatibility"]["selectable"])
        rejected_scene = _scene()
        rejected_scene["background"]["plugin_id"] = "unsafe_background"
        rejected_scene["known_python_fallback"]["plugin_id"] = "unsafe_background"
        self.assertEqual(
            self.client.post("/api/v1/scene/validate", json=rejected_scene).status_code,
            400,
        )
        self.assertEqual(self.client.get("/api/v1/components?role=bogus").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/components?provider=javascript").status_code, 400)

    def test_scene_start_requires_guarded_activation_before_any_command(self):
        response = self.client.put("/api/v1/scene", json=_scene())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], "activation_unavailable")
        self.assertEqual(self.channel.commands, [])

        before = len(self.channel.commands)
        unsupported = self.client.put("/api/v1/scene", json=_scene(provider="receiver_native"))
        self.assertEqual(unsupported.status_code, 503)
        self.assertEqual(unsupported.get_json()["code"], "activation_unavailable")
        self.assertEqual(len(self.channel.commands), before)
        bad_role = _scene()
        bad_role["background"]["plugin_id"] = "clock_overlay"
        self.assertEqual(self.client.post("/api/v1/scene/validate", json=bad_role).status_code, 400)
        self.assertEqual(len(self.channel.commands), before)

    def test_targeted_overlay_update_requires_complete_guarded_activation(self):
        self.channel.status.update({
            "is_running": True,
            "current_animation": "gradient",
            "scene_state": _scene(),
        })
        response = self.client.patch(
            "/api/v1/scene/components/clock_overlay",
            json={"enabled": False, "opacity": 64},
        )
        self.assertEqual(response.status_code, 428)
        self.assertEqual(response.get_json()["code"], "guarded_activation_required")
        self.assertEqual(self.channel.commands, [])

    def test_scene_preview_identity_covers_layout_vibe_and_plants_without_live_command(self):
        request = {
            "scene": _scene(),
            "vibe": "cozy",
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            "elapsed": 1.5,
        }
        response = self.client.post("/api/v1/scene/preview", json=request)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertRegex(payload["preview_identity"], r"^[0-9a-f]{64}$")
        self.assertEqual(self.channel.commands, [])
        self.assertEqual(len(self.preview.preview_calls), 1)
        changed = _scene()
        changed["overlays"][0]["placement"]["led_translation"] = 4
        vibe = payload["vibe"] if isinstance(payload.get("vibe"), dict) else self.preview.preview_calls[0][1]
        self.assertNotEqual(
            payload["preview_identity"],
            scene_preview_identity(changed, vibe, request["plant_modifiers"], elapsed=1.5),
        )
        self.assertNotEqual(
            payload["preview_identity"],
            scene_preview_identity(_scene(), vibe, request["plant_modifiers"], elapsed=1.6),
        )

    def test_targeted_background_replacement_requires_atomic_scene_put(self):
        self.channel.status.update({
            "is_running": True, "current_animation": "gradient",
            "scene_state": _scene(),
        })
        before = len(self.channel.commands)
        response = self.client.patch(
            "/api/v1/scene/components/background",
            json={"component": _scene()["background"]},
        )
        self.assertEqual(response.status_code, 428)
        self.assertIn("complete guarded activation", response.get_json()["error"])
        self.assertEqual(len(self.channel.commands), before)

    def test_scene_presets_round_trip_layout_and_never_capture_vibe(self):
        rejected = self.client.post(
            "/api/v1/scene-presets", json={"name": "Bad", "scene": _scene(), "vibe": "cozy"}
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(list(self.interface.scene_presets_dir.iterdir()), [])

        saved = self.client.post(
            "/api/v1/scene-presets", json={"name": "Evening Clock", "scene": _scene()}
        )
        self.assertEqual(saved.status_code, 200)
        preset = saved.get_json()["preset"]
        self.assertEqual(preset["scene"]["overlays"][0]["placement"]["led_translation"], -2)
        self.assertNotIn("vibe", preset)
        preset_id = preset["preset_id"]
        loaded = self.client.get(f"/api/v1/scene-presets/{preset_id}").get_json()
        self.assertEqual(loaded, preset)
        applied = self.client.post(f"/api/v1/scene-presets/{preset_id}/apply")
        self.assertEqual(applied.status_code, 428)
        self.assertEqual(applied.get_json()["code"], "guarded_activation_required")
        self.assertEqual(self.channel.commands, [])

    def test_stale_component_preset_keeps_snapshot_and_reports_dirty(self):
        animation_dir = self.interface.animation_presets_dir / "gradient"
        animation_dir.mkdir(parents=True, exist_ok=True)
        preset = {
            "preset_id": "calm", "name": "Calm", "animation": "gradient",
            "params": {"speed": 0.9},
        }
        (animation_dir / "calm.json").write_text(json.dumps(preset))
        scene = _scene(overlay=False)
        scene["background"].update({
            "preset_id": "calm",
            "preset_fingerprint": "f" * 64,
            "resolved_parameters": {"speed": 0.5},
            "parameter_overrides": {},
        })
        scene["known_python_fallback"] = dict(scene["background"])

        response = self.client.post("/api/v1/scene/validate", json=scene)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["scene"]["background"]["resolved_parameters"], {"speed": 0.5})
        self.assertEqual(payload["preset_diagnostics"][0]["code"], "preset_drift")
        self.assertTrue(payload["preset_diagnostics"][0]["is_dirty"])

    def test_dashboard_has_one_accessible_fixed_scene_editor(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertEqual(html.count('id="sceneEditorHeading"'), 1)
        self.assertIn('id="sceneBackgroundSelect"', html)
        self.assertIn('id="sceneOverlayEnabled"', html)
        self.assertIn('Wall mood and plant behavior remain independent', html)


class SceneIpcEnvelopeTests(unittest.TestCase):
    def test_commands_and_status_have_versioned_envelopes(self):
        with tempfile.TemporaryDirectory() as temporary:
            channel = FileControlChannel(
                str(Path(temporary) / "control.json"),
                str(Path(temporary) / "status.json"),
            )
            command = channel.send_command("start_scene", scene=_scene())
            self.assertEqual(command["schema"], CONTROL_COMMAND_SCHEMA)
            self.assertEqual(command["schema_version"], 1)
            channel.write_status({"is_running": False})
            status = channel.read_status()
            self.assertEqual(status["schema"], CONTROL_STATUS_SCHEMA)
            self.assertEqual(status["schema_version"], 1)
            self.assertEqual(json.loads(Path(channel.control_path).read_text()), command)

    def test_component_preset_fingerprint_is_identical_across_product_layers(self):
        interface = AnimationWebInterface(_Channel(), _PreviewManager())
        preset = {
            "animation": "gradient", "preset_id": "calm",
            "params": {"speed": 0.75},
        }
        canonical = component_preset_fingerprint(
            "gradient", "calm", {"speed": 0.75}
        )
        self.assertEqual(interface._component_preset_fingerprint(preset), canonical)
        self.assertEqual(_preset_fingerprint("gradient", "calm", {"speed": 0.75}), canonical)


if __name__ == "__main__":
    unittest.main()
