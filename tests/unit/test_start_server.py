"""Command routing and controller-startup helper tests."""

import unittest

from animation.core.receiver_static_component import (
    COMPILED_RAINBOW_BUNDLE_DIGEST,
    COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST,
)
from scripts.start_server import (
    _restore_display_state,
    device_count_for_strips,
    handle_command,
    receiver_hybrid_feature_flags,
    select_receiver_hybrid_controller,
)


class _Manager:
    def __init__(self):
        self.calls = []
        self.current_animation = None
        self.is_running = False
        self.controller = self

    def start_animation(self, animation, config, preset=None):
        self.calls.append(("start", animation, config, preset))
        self.is_running = animation != "missing"
        return self.is_running

    def stop_animation(self):
        self.calls.append(("stop",))
        self.is_running = False

    def update_animation_parameters(self, params):
        self.calls.append(("update", params))
        return True

    def set_current_preset(self, preset):
        self.calls.append(("preset", preset))
        return True

    def dispatch_interaction(self, kind, x, y, strength):
        self.calls.append(("interaction", kind, x, y, strength))
        return True

    def set_target_fps(self, value):
        if value <= 0:
            raise ValueError("invalid")
        self.calls.append(("fps", value))
        return value

    def set_animation_speed_scale(self, value):
        if value <= 0:
            raise ValueError("invalid")
        self.calls.append(("speed", value))
        return value

    def set_output_brightness(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
            raise ValueError("invalid")
        self.calls.append(("brightness", value))
        return value

    def validate_output_brightness(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
            raise ValueError("invalid")
        return value

    def _validate_tempo_scale(self, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError("invalid")
        return float(value)

    def apply_device_state(self, state):
        self.calls.append(("device", state))
        if state.get("power") is False:
            self.is_running = False
        elif state.get("power") is True or state.get("animation"):
            self.is_running = True
        return True

    def set_plant_aware(self, value):
        if not isinstance(value, bool):
            raise ValueError("invalid")
        self.calls.append(("plant", value))
        return value

    def set_plant_modifiers(self, value):
        if not isinstance(value, dict) or "active" not in value:
            raise ValueError("invalid")
        self.calls.append(("modifiers", value))
        return {"version": 1, "active": value["active"], "strengths": value.get("strengths", {})}

    def set_vibe(self, value):
        self.calls.append(("vibe", value))
        return value if isinstance(value, dict) else {"vibe_id": value}

    def refresh_receiver_status(self, request_id):
        self.calls.append(("refresh_receiver_status", request_id))
        return {"request_id": request_id, "passed": True}

    def clear_painter_frame(self):
        self.calls.append(("clear",))

    def list_components(self):
        return [
            {"plugin_id": "solid", "provider": "python", "role": "background"},
            {"plugin_id": "clock_overlay", "provider": "python", "role": "overlay"},
        ]

    def start_scene(self, scene):
        self.calls.append(("scene", scene))
        self.is_running = True
        return True

    def update_scene_component(self, target, update):
        self.calls.append(("scene_update", target, update))
        return True

    def stop_scene(self):
        self.calls.append(("scene_stop",))
        self.is_running = False


class StartServerTests(unittest.TestCase):
    def test_device_count_uses_ceiling_division(self):
        self.assertEqual(device_count_for_strips(1), 1)
        self.assertEqual(device_count_for_strips(8), 1)
        self.assertEqual(device_count_for_strips(9), 2)
        self.assertEqual(device_count_for_strips(32), 4)

    def test_receiver_hybrid_feature_flags_are_all_off_unless_explicitly_enabled(self):
        ordinary = receiver_hybrid_feature_flags(False)
        self.assertTrue(ordinary.all_disabled)
        canary = receiver_hybrid_feature_flags(True)
        self.assertTrue(canary.receiver_local_background)
        self.assertTrue(canary.receiver_sparse_overlay)
        self.assertFalse(canary.receiver_native_modules)
        with self.assertRaisesRegex(TypeError, "must be boolean"):
            receiver_hybrid_feature_flags(1)

    def test_service_controller_applies_only_explicit_degraded_transport_policy(self):
        class Controller:
            def __init__(self):
                self.policies = []

            def with_receiver_hybrid_transport_policy(
                self, policy, *, physical_lane_order
            ):
                self.policies.append((policy, tuple(physical_lane_order)))
                return ("wrapped", policy, tuple(physical_lane_order))

        controller = Controller()
        self.assertIs(
            select_receiver_hybrid_controller(controller, {
                "enabled": False,
                "transport_policy": "off",
            }),
            controller,
        )
        self.assertEqual(controller.policies, [])
        selected = select_receiver_hybrid_controller(controller, {
            "enabled": True,
            "transport_policy": "degraded_spi1_01_readable",
        })
        self.assertEqual(
            selected,
            ("wrapped", "degraded_spi1_01_readable", (0, 1, 2, 3)),
        )
        self.assertEqual(
            controller.policies,
            [("degraded_spi1_01_readable", (0, 1, 2, 3))],
        )

        mapped = select_receiver_hybrid_controller(controller, {
            "enabled": True,
            "transport_policy": "degraded_spi1_01_readable",
            "physical_lane_order": [0, 1, 3, 2],
        })
        self.assertEqual(
            mapped,
            ("wrapped", "degraded_spi1_01_readable", (0, 1, 3, 2)),
        )

        with self.assertRaisesRegex(RuntimeError, "requires a multi-device"):
            select_receiver_hybrid_controller(object(), {
                "enabled": True,
                "transport_policy": "degraded_spi1_01_readable",
            })

    def test_state_changing_commands_request_persistence(self):
        manager = _Manager()

        self.assertTrue(handle_command(manager, "start", {"animation": "solid", "config": {"red": 4}}))
        self.assertTrue(handle_command(manager, "update_params", {"params": {"brightness": 0.5}}))
        self.assertTrue(handle_command(manager, "set_current_preset", {
            "preset": {"preset_id": "warm", "name": "Warm", "animation": "solid"}
        }))
        self.assertTrue(handle_command(manager, "animation_interaction", {
            "kind": "primary", "x": 5.0, "y": 9.0, "strength": 0.75,
        }))
        self.assertTrue(handle_command(manager, "set_target_fps", {"target_fps": 144}))
        self.assertTrue(handle_command(manager, "set_animation_speed_scale", {"animation_speed_scale": 0.45}))
        self.assertTrue(handle_command(manager, "set_plant_aware", {"plant_aware": False}))
        self.assertTrue(handle_command(manager, "set_plant_modifiers", {
            "plant_modifiers": {"active": ["shadow"], "strengths": {"shadow": 0.5}}
        }))
        self.assertTrue(handle_command(manager, "set_vibe", {"vibe_id": "cozy"}))

        self.assertEqual(manager.calls, [
            ("start", "solid", {"red": 4}, None),
            ("update", {"brightness": 0.5}),
            ("preset", {"preset_id": "warm", "name": "Warm", "animation": "solid"}),
            ("interaction", "primary", 5.0, 9.0, 0.75),
            ("fps", 144),
            ("speed", 0.45),
            ("plant", False),
            ("modifiers", {"active": ["shadow"], "strengths": {"shadow": 0.5}}),
            ("vibe", "cozy"),
        ])

    def test_failed_or_nonpersistent_commands_return_false(self):
        manager = _Manager()

        self.assertFalse(handle_command(manager, "start", {"animation": "missing"}))
        self.assertFalse(handle_command(manager, "set_target_fps", {"target_fps": 0}))
        self.assertFalse(handle_command(manager, "set_animation_speed_scale", {"animation_speed_scale": "bad"}))
        self.assertFalse(handle_command(manager, "set_plant_aware", {"plant_aware": "yes"}))
        self.assertFalse(handle_command(manager, "set_plant_modifiers", {"plant_modifiers": []}))
        self.assertFalse(handle_command(manager, "set_vibe", {"vibe": None}))
        self.assertFalse(handle_command(manager, "stop", {}))
        self.assertFalse(handle_command(manager, "painter_clear", {}))
        self.assertFalse(handle_command(manager, "unknown", {}))

        self.assertEqual(manager.calls[-2:], [("stop",), ("clear",)])

    def test_receiver_status_refresh_is_read_only_and_controller_owned(self):
        manager = _Manager()
        self.assertFalse(handle_command(
            manager, "refresh_receiver_status", {"request_id": "fresh-1"}
        ))
        self.assertEqual(manager.calls, [("refresh_receiver_status", "fresh-1")])

    def test_brightness_and_compound_state_commands_dispatch_once(self):
        manager = _Manager()
        manager.is_running = True

        self.assertTrue(handle_command(
            manager, "set_output_brightness", {"brightness": 96}
        ))
        self.assertTrue(handle_command(manager, "set_device_state", {
            "power": True,
            "brightness": 128,
            "animation": "solid",
        }))
        self.assertFalse(handle_command(
            manager, "set_device_state", {"power": False}
        ))
        self.assertFalse(handle_command(
            manager, "set_output_brightness", {"brightness": 256}
        ))

        self.assertEqual(manager.calls, [
            ("brightness", 96),
            ("device", {
                "power": True, "brightness": 128, "animation": "solid",
            }),
            ("device", {"power": False}),
        ])

    def test_versioned_scene_commands_dispatch_to_manager_product_api(self):
        manager = _Manager()
        component = {
            "plugin_id": "solid", "provider": "python",
            "parameter_overrides": {}, "resolved_parameters": {"red": 4},
        }
        scene = {
            "schema": "ledgrid.scene-state", "schema_version": 1, "revision": 1,
            "background": component, "overlays": [],
            "known_python_fallback": component,
        }

        self.assertTrue(handle_command(manager, "start_scene", {"scene": scene}))
        self.assertTrue(handle_command(manager, "update_scene_component", {
            "target": "clock_overlay", "update": {"enabled": False},
        }))
        self.assertFalse(handle_command(manager, "stop_scene", {}))
        self.assertEqual(manager.calls[0], ("scene", scene))
        self.assertEqual(
            manager.calls[1],
            ("scene_update", "clock_overlay", {"enabled": False}),
        )
        self.assertEqual(manager.calls[2], ("scene_stop",))

    def test_scene_command_rejects_unsupported_provider_before_manager_mutation(self):
        manager = _Manager()
        component = {
            "plugin_id": "solid", "provider": "receiver_native",
            "parameter_overrides": {}, "resolved_parameters": {},
        }
        scene = {
            "schema": "ledgrid.scene-state", "schema_version": 1, "revision": 0,
            "background": component, "overlays": [],
            "known_python_fallback": {**component, "provider": "python"},
        }
        self.assertFalse(handle_command(manager, "start_scene", {"scene": scene}))
        self.assertEqual(manager.calls, [])

    def test_raw_receiver_commands_are_not_part_of_the_ipc_surface(self):
        manager = _Manager()
        self.assertFalse(handle_command(manager, "receiver_start_component", {
            "component_id": 1,
        }))
        self.assertEqual(manager.calls, [])

    def test_feature_off_restore_selects_recorded_fallback_before_mutation(self):
        manager = _Manager()
        fallback = {
            "plugin_id": "solid",
            "provider": "python",
            "parameter_overrides": {"red": 4},
            "resolved_parameters": {"red": 4},
        }
        scene = {
            "schema": "ledgrid.scene-state",
            "schema_version": 1,
            "revision": 19,
            "background": {
                "plugin_id": "compiled_rainbow",
                "provider": "receiver_native",
                "parameter_overrides": {"common_seed": 8},
                "resolved_parameters": {
                    "preferred_cadence_hz": 30,
                    "common_seed": 8,
                },
                "bundle_digest": COMPILED_RAINBOW_BUNDLE_DIGEST,
                "expected_payload_digest": (
                    COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST
                ),
            },
            "overlays": [],
            "known_python_fallback": fallback,
        }
        modifiers = {
            "version": 1,
            "active": ["shadow"],
            "strengths": {"shadow": 0.5},
        }
        vibe = {"schema_version": 1, "vibe_id": "cozy"}

        self.assertTrue(_restore_display_state(manager, {
            "scene": scene,
            "plant_modifiers": modifiers,
            "vibe": vibe,
            "output": {
                "power": True,
                "master_brightness": 0.4,
                "operator_tempo_scale": 1.25,
                "target_fps": 120,
            },
        }))

        self.assertEqual(manager.calls[0][0], "scene")
        self.assertEqual(
            manager.calls[0][1]["background"]["plugin_id"], "solid"
        )
        self.assertEqual(manager.calls[1:], [
            ("modifiers", modifiers),
            ("vibe", vibe),
            ("speed", 1.25),
            ("fps", 120),
            ("brightness", 102),
        ])

    def test_desired_display_is_fully_validated_before_scene_mutation(self):
        manager = _Manager()
        component = {
            "plugin_id": "solid",
            "provider": "python",
            "parameter_overrides": {},
            "resolved_parameters": {"red": 4},
        }
        with self.assertRaisesRegex(ValueError, "target_fps must be an integer"):
            _restore_display_state(manager, {
                "scene": {
                    "schema": "ledgrid.scene-state",
                    "schema_version": 1,
                    "revision": 1,
                    "background": component,
                    "overlays": [],
                    "known_python_fallback": component,
                },
                "plant_modifiers": {
                    "version": 1,
                    "active": [],
                    "strengths": {},
                },
                "output": {"power": True, "target_fps": 120.5},
            })
        self.assertEqual(manager.calls, [])

    def test_desired_display_unknown_vibe_uses_observable_manager_fallback(self):
        from animation.core.manager import AnimationManager, PreviewLEDController
        from animation.core.presentation_contracts import resolve_vibe

        manager = AnimationManager(PreviewLEDController(2, 4), auto_start=False)
        component = {
            "plugin_id": "simple_test", "provider": "python",
            "parameter_overrides": {}, "resolved_parameters": {},
        }
        scene = {
            "schema": "ledgrid.scene-state", "schema_version": 1, "revision": 0,
            "background": component, "overlays": [],
            "known_python_fallback": component,
        }
        stale = resolve_vibe("cozy").state.to_dict()
        stale["profile_version"] = 999
        try:
            self.assertTrue(_restore_display_state(manager, {
                "scene": scene,
                "vibe": stale,
                "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
                "output": {
                    "master_brightness": 0.4,
                    "operator_tempo_scale": 1.25,
                    "target_fps": 120,
                    "power": True,
                },
            }))
            status = manager.get_current_status()
            self.assertEqual(status["vibe"]["state"]["vibe_id"], "neutral")
            self.assertEqual(status["vibe"]["diagnostic"]["code"], "vibe_profile_fallback")
            self.assertEqual(status["brightness"], 102)
            self.assertEqual(status["animation_speed_scale"], 1.25)
            self.assertEqual(status["target_fps"], 120)
        finally:
            manager.stop_animation()


if __name__ == "__main__":
    unittest.main()
