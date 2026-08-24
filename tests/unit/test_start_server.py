"""Command routing and controller-startup helper tests."""

import unittest

from scripts.start_server import (
    PRODUCTION_STAGGER_PHASES,
    apply_production_stagger,
    device_count_for_strips,
    handle_command,
)


class _Manager:
    def __init__(self):
        self.calls = []
        self.current_animation = None
        self.is_running = False

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

    def clear_painter_frame(self):
        self.calls.append(("clear",))


class _StaggerController:
    def __init__(self):
        self.phases = None

    def set_stagger_phases(self, phases):
        self.phases = phases


class StartServerTests(unittest.TestCase):
    def test_production_stagger_is_three_phases(self):
        self.assertEqual(PRODUCTION_STAGGER_PHASES, 3)
        controller = _StaggerController()
        self.assertTrue(apply_production_stagger(controller))
        self.assertEqual(controller.phases, 3)

    def test_production_stagger_is_a_noop_without_the_method(self):
        self.assertFalse(apply_production_stagger(object()))

    def test_device_count_uses_ceiling_division(self):
        self.assertEqual(device_count_for_strips(1), 1)
        self.assertEqual(device_count_for_strips(8), 1)
        self.assertEqual(device_count_for_strips(9), 2)
        self.assertEqual(device_count_for_strips(32), 4)

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

        self.assertEqual(manager.calls, [
            ("start", "solid", {"red": 4}, None),
            ("update", {"brightness": 0.5}),
            ("preset", {"preset_id": "warm", "name": "Warm", "animation": "solid"}),
            ("interaction", "primary", 5.0, 9.0, 0.75),
            ("fps", 144),
            ("speed", 0.45),
            ("plant", False),
            ("modifiers", {"active": ["shadow"], "strengths": {"shadow": 0.5}}),
        ])

    def test_failed_or_nonpersistent_commands_return_false(self):
        manager = _Manager()

        self.assertFalse(handle_command(manager, "start", {"animation": "missing"}))
        self.assertFalse(handle_command(manager, "set_target_fps", {"target_fps": 0}))
        self.assertFalse(handle_command(manager, "set_animation_speed_scale", {"animation_speed_scale": "bad"}))
        self.assertFalse(handle_command(manager, "set_plant_aware", {"plant_aware": "yes"}))
        self.assertFalse(handle_command(manager, "set_plant_modifiers", {"plant_modifiers": []}))
        self.assertFalse(handle_command(manager, "stop", {}))
        self.assertFalse(handle_command(manager, "painter_clear", {}))
        self.assertFalse(handle_command(manager, "unknown", {}))

        self.assertEqual(manager.calls[-2:], [("stop",), ("clear",)])

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


if __name__ == "__main__":
    unittest.main()
