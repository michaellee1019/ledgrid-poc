"""In-process control/status channel for the Mac-only software dashboard."""

from __future__ import annotations

import time
from typing import Any, Dict
import uuid

from animation.core.manager import AnimationManager
from drivers.frame_codec import decode_frame_data
from ipc.runtime_control import (
    ControllerActivationError,
    controller_activation_coordinator,
    restore_display_state,
    start_scene,
    update_scene_component,
)


class LocalControlChannel:
    """Expose an AnimationManager through the FileControlChannel interface."""

    def __init__(self, manager: AnimationManager):
        self.manager = manager
        self.activation_coordinator = controller_activation_coordinator(manager)
        self._activation_cancels: Dict[str, Dict[str, Any]] = {}
        self._activation_cancel_results: Dict[str, Dict[str, Any]] = {}
        self._activation_rollbacks: Dict[str, Dict[str, Any]] = {}
        self._activation_rollback_results: Dict[str, Dict[str, Any]] = {}

    def read_status(self) -> Dict[str, Any]:
        payload = self.manager.get_current_frame()
        payload.update(self.manager.get_current_status())
        payload["updated_at"] = time.time()
        payload.update(self.activation_coordinator.controller_status())
        return payload

    def enqueue_activation(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """Run the same guarded transaction without a filesystem hop."""

        self.activation_coordinator.activate(command)
        return dict(command)

    def write_activation_status(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Accept the web layer's initial receipt; execution owns later states."""

        return dict(status)

    def read_activation_status(self, activation_id: str):
        return self.activation_coordinator.get(activation_id)

    def request_activation_cancel(self, activation_id: str) -> Dict[str, Any]:
        existing = self._activation_cancels.get(activation_id)
        if existing is not None:
            return dict(existing)
        request = {
            "schema": "ledgrid.scene-activation-cancel",
            "schema_version": 1,
            "request_id": str(uuid.uuid4()),
            "activation_id": activation_id,
            "requested_at": time.time(),
        }
        self._activation_cancels[activation_id] = request
        try:
            status = self.activation_coordinator.cancel(activation_id)
        except (ControllerActivationError, KeyError, TypeError, ValueError) as exc:
            status = self.activation_coordinator.get(activation_id) or {
                "phase": "unknown"
            }
            self._activation_cancel_results[activation_id] = (
                self._activation_request_result(
                    "cancel", request, "rejected", status, str(exc)
                )
            )
        else:
            if status.get("phase") == "failed" and (
                "cancelled before mutation" in str(status.get("error") or "")
            ):
                self._activation_cancel_results[activation_id] = (
                    self._activation_request_result(
                        "cancel", request, "succeeded", status, None
                    )
                )
        return dict(request)

    def read_activation_cancel(self, activation_id: str):
        request = self._activation_cancels.get(activation_id)
        return dict(request) if request is not None else None

    def read_activation_cancel_result(self, activation_id: str):
        result = self._activation_cancel_results.get(activation_id)
        if result is None and activation_id in self._activation_cancels:
            status = self.activation_coordinator.get(activation_id)
            if status is not None and status.get("phase") in {
                "active", "rolled_back", "failed", "timed_out"
            }:
                cancelled = (
                    status.get("phase") == "failed"
                    and "cancelled before mutation"
                    in str(status.get("error") or "")
                )
                result = self._activation_request_result(
                    "cancel",
                    self._activation_cancels[activation_id],
                    "succeeded" if cancelled else "rejected",
                    status,
                    None if cancelled else "activation completed before cancellation",
                )
                self._activation_cancel_results[activation_id] = result
        return dict(result) if result is not None else None

    def request_activation_rollback(
        self,
        activation_id: str,
        *,
        snapshot_id: str,
        expected_controller_session_id: str,
        expected_controller_state_revision: int,
    ) -> Dict[str, Any]:
        existing = self._activation_rollbacks.get(activation_id)
        if existing is not None:
            comparable = (
                existing["snapshot_id"],
                existing["expected_controller_session_id"],
                existing["expected_controller_state_revision"],
            )
            requested = (
                snapshot_id,
                expected_controller_session_id,
                expected_controller_state_revision,
            )
            if comparable != requested:
                raise FileExistsError(
                    "activation already has a different rollback request"
                )
            return dict(existing)
        request = {
            "schema": "ledgrid.scene-activation-rollback-request",
            "schema_version": 1,
            "request_id": str(uuid.uuid4()),
            "activation_id": activation_id,
            "snapshot_id": snapshot_id,
            "expected_controller_session_id": expected_controller_session_id,
            "expected_controller_state_revision": (
                expected_controller_state_revision
            ),
            "requested_at": time.time(),
        }
        self._activation_rollbacks[activation_id] = request
        try:
            status = self.activation_coordinator.rollback(
                activation_id,
                snapshot_id=snapshot_id,
                expected_session_id=expected_controller_session_id,
                expected_state_revision=expected_controller_state_revision,
            )
        except (ControllerActivationError, KeyError, TypeError, ValueError) as exc:
            status = self.activation_coordinator.get(activation_id) or {
                "phase": "unknown"
            }
            result = self._activation_request_result(
                "rollback", request, "rejected", status, str(exc)
            )
        else:
            succeeded = status.get("phase") == "rolled_back"
            rollback = status.get("rollback") or {}
            result = self._activation_request_result(
                "rollback",
                request,
                "succeeded" if succeeded else "failed",
                status,
                None if succeeded else (
                    rollback.get("error")
                    or status.get("error")
                    or "exact rollback failed"
                ),
            )
        self._activation_rollback_results[activation_id] = result
        return dict(request)

    def read_activation_rollback(self, activation_id: str):
        request = self._activation_rollbacks.get(activation_id)
        return dict(request) if request is not None else None

    def read_activation_rollback_result(self, activation_id: str):
        result = self._activation_rollback_results.get(activation_id)
        return dict(result) if result is not None else None

    @staticmethod
    def _activation_request_result(
        kind: str,
        request: Dict[str, Any],
        outcome: str,
        status: Dict[str, Any],
        error: str | None,
    ) -> Dict[str, Any]:
        return {
            "schema": f"ledgrid.scene-activation-{kind}-result",
            "schema_version": 1,
            "request_id": request["request_id"],
            "activation_id": request["activation_id"],
            "outcome": outcome,
            "status_phase": status.get("phase", "unknown"),
            "error": error,
            "completed_at": time.time(),
        }

    def send_command(self, action: str, **data: Any) -> Dict[str, Any]:
        if action in {"activate_scene", "cancel_activation"}:
            return self._send_command_unlocked(action, **data)
        with self.activation_coordinator.legacy_mutation_guard():
            return self._send_command_unlocked(action, **data)

    def _send_command_unlocked(self, action: str, **data: Any) -> Dict[str, Any]:
        manager = self.manager
        if action == "activate_scene":
            status = self.activation_coordinator.activate(data.get("activation", data))
            return {
                "command_id": status["activation_id"],
                "action": action,
                "data": data,
                "activation_status": status,
            }
        if action == "cancel_activation":
            self.activation_coordinator.cancel(data.get("activation_id"))
            return {"command_id": time.time(), "action": action, "data": data}
        if action == "start":
            manager.start_animation(
                data.get("animation"), data.get("config") or {},
                preset=data.get("preset"),
            )
        elif action == "start_scene":
            start_scene(manager, data.get("scene"))
        elif action == "update_scene_component":
            update_scene_component(
                manager, data.get("target"), data.get("update") or {}
            )
        elif action == "stop_scene":
            stopper = getattr(manager, "stop_scene", manager.stop_animation)
            stopper()
        elif action == "restore_display_state":
            restore_display_state(manager, data.get("state"))
        elif action == "stop":
            manager.stop_animation()
        elif action == "update_params":
            manager.update_animation_parameters(data.get("params") or {})
        elif action == "set_current_preset":
            manager.set_current_preset(data.get("preset") or {})
        elif action == "set_target_fps":
            manager.set_target_fps(int(data.get("target_fps")))
        elif action == "set_animation_speed_scale":
            manager.set_animation_speed_scale(float(data.get("animation_speed_scale")))
        elif action == "set_output_brightness":
            manager.set_output_brightness(data.get("brightness"))
        elif action == "set_device_state":
            manager.apply_device_state(data)
        elif action == "set_plant_aware":
            manager.set_plant_aware(data.get("plant_aware"))
        elif action == "set_plant_modifiers":
            manager.set_plant_modifiers(data.get("plant_modifiers"))
        elif action == "set_vibe":
            requested = data.get("vibe", data.get("vibe_id"))
            if requested is None:
                raise ValueError("vibe is required")
            manager.set_vibe(requested)
        elif action == "refresh_plugins":
            animation = data.get("animation")
            manager.reload_animation(animation) if animation else manager.refresh_plugins()
        elif action == "puncture_hole":
            if "x" in data and "y" in data:
                manager.trigger_hole(float(data["x"]), float(data["y"]), data.get("radius"))
            else:
                manager.trigger_random_hole()
        elif action == "animation_interaction":
            manager.dispatch_interaction(
                data.get("kind", "primary"), data.get("x"), data.get("y"),
                data.get("strength", 1.0),
            )
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
