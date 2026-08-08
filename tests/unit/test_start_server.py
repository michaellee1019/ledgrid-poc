"""Command routing and controller-startup helper tests."""

import unittest

from scripts.start_server import device_count_for_strips, handle_command


class _Manager:
    def __init__(self):
        self.calls = []
        self.current_animation = None

    def start_animation(self, animation, config):
        self.calls.append(("start", animation, config))
        return animation != "missing"

    def stop_animation(self):
        self.calls.append(("stop",))

    def update_animation_parameters(self, params):
        self.calls.append(("update", params))
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


class StartServerTests(unittest.TestCase):
    def test_device_count_uses_ceiling_division(self):
        self.assertEqual(device_count_for_strips(1), 1)
        self.assertEqual(device_count_for_strips(8), 1)
        self.assertEqual(device_count_for_strips(9), 2)
        self.assertEqual(device_count_for_strips(32), 4)

    def test_state_changing_commands_request_persistence(self):
        manager = _Manager()

        self.assertTrue(handle_command(manager, "start", {"animation": "solid", "config": {"red": 4}}))
        self.assertTrue(handle_command(manager, "update_params", {"params": {"brightness": 0.5}}))
        self.assertTrue(handle_command(manager, "set_target_fps", {"target_fps": 144}))
        self.assertTrue(handle_command(manager, "set_animation_speed_scale", {"animation_speed_scale": 0.45}))
        self.assertTrue(handle_command(manager, "set_plant_aware", {"plant_aware": False}))
        self.assertTrue(handle_command(manager, "set_plant_modifiers", {
            "plant_modifiers": {"active": ["shadow"], "strengths": {"shadow": 0.5}}
        }))

        self.assertEqual(manager.calls, [
            ("start", "solid", {"red": 4}),
            ("update", {"brightness": 0.5}),
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


if __name__ == "__main__":
    unittest.main()
