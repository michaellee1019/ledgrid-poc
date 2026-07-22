"""In-process control/status channel for the Mac-only software dashboard."""

from __future__ import annotations

import time
from typing import Any, Dict

from animation.core.manager import AnimationManager
from drivers.frame_codec import decode_frame_data


class LocalControlChannel:
    """Expose an AnimationManager through the FileControlChannel interface."""

    def __init__(self, manager: AnimationManager):
        self.manager = manager

    def read_status(self) -> Dict[str, Any]:
        payload = self.manager.get_current_frame()
        payload.update(self.manager.get_current_status())
        payload["updated_at"] = time.time()
        return payload

    def send_command(self, action: str, **data: Any) -> Dict[str, Any]:
        manager = self.manager
        if action == "start":
            manager.start_animation(data.get("animation"), data.get("config") or {})
        elif action == "stop":
            manager.stop_animation()
        elif action == "update_params":
            manager.update_animation_parameters(data.get("params") or {})
        elif action == "set_target_fps":
            manager.set_target_fps(int(data.get("target_fps")))
        elif action == "set_animation_speed_scale":
            manager.set_animation_speed_scale(float(data.get("animation_speed_scale")))
        elif action == "set_plant_aware":
            manager.set_plant_aware(data.get("plant_aware"))
        elif action == "set_plant_modifiers":
            manager.set_plant_modifiers(data.get("plant_modifiers"))
        elif action == "refresh_plugins":
            animation = data.get("animation")
            manager.reload_animation(animation) if animation else manager.refresh_plugins()
        elif action == "puncture_hole":
            if "x" in data and "y" in data:
                manager.trigger_hole(float(data["x"]), float(data["y"]), data.get("radius"))
            else:
                manager.trigger_random_hole()
        elif action == "dpad":
            current = manager.current_animation
            if current is not None and hasattr(current, "handle_input"):
                current.handle_input(data.get("direction"))
        elif action == "painter_set_frame":
            frame = data.get("frame_data")
            if frame is None:
                frame = decode_frame_data(data.get("frame_data_encoded") or "")
            manager.set_painter_frame(frame)
        elif action == "painter_apply_updates":
            manager.apply_painter_updates(data.get("updates") or [])
        elif action == "painter_clear":
            manager.clear_painter_frame()
        else:
            raise ValueError(f"unknown local dashboard action: {action}")
        return {"command_id": time.time(), "action": action, "data": data}
