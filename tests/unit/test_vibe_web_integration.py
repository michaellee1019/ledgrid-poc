"""Acceptance coverage for the independent global-vibe product surface."""

import unittest

from animation.core.manager import AnimationManager, PreviewLEDController
from web.app import AnimationWebInterface
from web.local_control import LocalControlChannel


class _Controller:
    strip_count = 1
    leds_per_strip = 2
    total_leds = 2


class _PreviewManager:
    controller = _Controller()
    preview_controller = controller

    def __init__(self):
        self.preview_calls = []
        self.live_vibe = self._state("neutral")

    @staticmethod
    def _state(vibe_id):
        from animation.core.presentation_contracts import resolve_vibe

        return resolve_vibe(vibe_id).state.to_dict()

    def list_animations(self):
        return []

    def get_animation_info(self, _name):
        return {"parameters": {}}

    def get_vibe_status(self):
        return {"state": dict(self.live_vibe)}

    def get_animation_preview(self, animation_name, *, vibe=None):
        self.preview_calls.append(("plain", animation_name, None, vibe))
        return {"frame_data": [[1, 2, 3]], "vibe": {"state": vibe}}

    def get_animation_preview_with_params(self, animation_name, params, *, vibe=None):
        self.preview_calls.append(("params", animation_name, dict(params), vibe))
        return {"frame_data": [[4, 5, 6]], "vibe": {"state": vibe}}


class _Channel:
    def __init__(self, status):
        self.status = status
        self.commands = []

    def read_status(self):
        return dict(self.status)

    def send_command(self, action, **data):
        command = {"action": action, "data": data}
        self.commands.append(command)
        return {**command, "command_id": len(self.commands)}


class VibeWebIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.preview = _PreviewManager()
        vivid = self.preview._state("vivid")
        self.channel = _Channel({
            "is_running": True,
            "current_animation": "sparkle",
            "current_preset": {
                "preset_id": "stars", "name": "Stars",
                "animation": "sparkle", "is_dirty": False,
            },
            "vibe": {"state": vivid},
            "led_info": {"strip_count": 1, "leds_per_strip": 2, "total_leds": 2},
        })
        self.interface = AnimationWebInterface(self.channel, self.preview)
        self.client = self.interface.app.test_client()

    def test_versioned_read_and_update_validate_before_ipc(self):
        status = self.client.get("/api/status").get_json()
        self.assertEqual(status["vibe"]["state"]["vibe_id"], "vivid")

        response = self.client.get("/api/v1/vibe")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["vibe"]["state"]["vibe_id"], "vivid")
        self.assertEqual(
            {profile["vibe_id"] for profile in payload["profiles"]},
            {"neutral", "quiet", "cozy", "vivid", "celebration"},
        )

        rejected = self.client.post("/api/config/vibe", json={"vibe": "unknown"})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.channel.commands, [])

        unsupported_version = self.preview._state("cozy")
        unsupported_version["profile_version"] = 999
        rejected = self.client.post(
            "/api/config/vibe", json={"vibe": unsupported_version}
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.channel.commands, [])

        stale_digest = self.preview._state("cozy")
        stale_digest["resolved_profile_digest"] = "f" * 64
        rejected = self.client.post(
            "/api/config/vibe", json={"vibe": stale_digest}
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.channel.commands, [])

        accepted = self.client.post("/api/config/vibe", json={"vibe": "cozy"})
        self.assertEqual(accepted.status_code, 200)
        accepted_payload = accepted.get_json()
        state = accepted_payload["requested_vibe"]
        self.assertEqual(state["vibe_id"], "cozy")
        self.assertEqual(accepted_payload["profile"]["vibe_id"], "cozy")
        self.assertEqual(accepted_payload["command_id"], 1)
        self.assertEqual(self.channel.commands, [{
            "action": "set_vibe", "data": {"vibe": state},
        }])
        status_after = self.client.get("/api/status").get_json()
        self.assertEqual(status_after["current_preset"]["preset_id"], "stars")
        self.assertFalse(status_after["current_preset"]["is_dirty"])

    def test_plain_and_parameterized_previews_use_explicit_vibe_without_live_mutation(self):
        response = self.client.get("/api/preview/sparkle")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.preview.preview_calls[-1][3]["vibe_id"], "vivid")

        response = self.client.post(
            "/api/preview/sparkle/with_params",
            json={"params": {"speed": 0.7}, "vibe": "quiet"},
        )
        self.assertEqual(response.status_code, 200)
        _, _, params, vibe = self.preview.preview_calls[-1]
        self.assertEqual(params, {"speed": 0.7})
        self.assertEqual(vibe["vibe_id"], "quiet")

        response = self.client.post(
            "/api/preview/sparkle/with_params?vibe=cozy",
            json={"speed": 1.25},
        )
        self.assertEqual(response.status_code, 200)
        _, _, params, vibe = self.preview.preview_calls[-1]
        self.assertEqual(params, {"speed": 1.25})
        self.assertEqual(vibe["vibe_id"], "cozy")
        self.assertEqual(self.preview.live_vibe["vibe_id"], "neutral")

        rejected = self.client.get("/api/preview/sparkle?vibe=missing")
        self.assertEqual(rejected.status_code, 400)

        rejected_shape = self.client.post(
            "/api/preview/sparkle/with_params", json=[]
        )
        self.assertEqual(rejected_shape.status_code, 400)
        self.assertIn("JSON object", rejected_shape.get_json()["error"])

    def test_dashboard_has_one_authoritative_global_vibe_control(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertEqual(html.count('id="globalVibeSelect"'), 1)
        self.assertIn("Wall mood", html)
        self.assertIn("Changes the live wall's palette, energy, and brightness immediately", html)
        self.assertNotIn("Vibe or category", html)
        self.assertLess(html.index('id="globalSpeedRange"'), html.index('id="globalVibeSelect"'))
        self.assertLess(html.index('id="globalVibeSelect"'), html.index('id="plantModifierControls"'))

        response = self.client.get("/static/js/dashboard.js")
        try:
            javascript = response.get_data(as_text=True)
        finally:
            response.close()
        self.assertIn("info.vibe?.legacy_parameter_mappings", javascript)
        self.assertIn("...(currentParams || {})", javascript)
        self.assertIn("setGlobalVibe", javascript)
        self.assertIn("?vibe=${encodeURIComponent(globalVibeId)}", javascript)

    def test_local_dashboard_round_trip_uses_real_manager_and_isolated_preview(self):
        manager = AnimationManager(PreviewLEDController(2, 4), auto_start=False)
        self.assertTrue(manager.start_animation(
            "simple_test", {}, preset={
                "preset_id": "diagnostic", "name": "Diagnostic",
                "animation": "simple_test",
            },
        ))
        interface = AnimationWebInterface(
            LocalControlChannel(manager), manager, local_mode=True
        )
        client = interface.app.test_client()
        try:
            update = client.post("/api/config/vibe", json={"vibe": "cozy"})
            self.assertEqual(update.status_code, 200)
            live = client.get("/api/status").get_json()
            self.assertEqual(live["vibe"]["state"]["vibe_id"], "cozy")
            self.assertEqual(live["vibe"]["state"]["revision"], 1)
            self.assertEqual(live["current_preset"]["preset_id"], "diagnostic")
            self.assertFalse(live["current_preset"]["is_dirty"])

            plain = client.get("/api/preview/simple_test").get_json()
            self.assertEqual(plain["vibe"]["state"]["vibe_id"], "cozy")
            parameterized = client.post(
                "/api/preview/simple_test/with_params?vibe=quiet",
                json={"color_index": 2},
            ).get_json()
            self.assertEqual(parameterized["vibe"]["state"]["vibe_id"], "quiet")
            self.assertEqual(manager.get_vibe_state()["vibe_id"], "cozy")
            self.assertEqual(manager.get_vibe_state()["revision"], 1)
            with self.assertRaisesRegex(ValueError, "vibe is required"):
                interface.control_channel.send_command("set_vibe", vibe=None)
            self.assertEqual(manager.get_vibe_state()["vibe_id"], "cozy")
        finally:
            manager.stop_animation()


if __name__ == "__main__":
    unittest.main()
