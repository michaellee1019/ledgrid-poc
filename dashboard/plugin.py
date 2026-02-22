"""Dashboard animation plugin runtime."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from animation import AnimationBase
from dashboard.renderer import FrameBuffer
from dashboard.scene import DashboardScene


Color = Tuple[int, int, int]


class DashboardAnimationPlugin(AnimationBase):
    """Dashboard runtime focused on deterministic, non-blocking rendering."""

    ANIMATION_NAME = "Dashboard"
    ANIMATION_DESCRIPTION = "Clock-first dashboard overlay"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "0.1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.default_params.update(
            {
                "time_format_24h": True,
                "show_seconds": True,
                "text_red": 255,
                "text_green": 255,
                "text_blue": 255,
                "background_red": 0,
                "background_green": 0,
                "background_blue": 0,
                "safe_margin_x": 0,
                "safe_margin_y": 0,
                "serpentine": False,
            }
        )
        self.params = {**self.default_params, **self.config}

        self.width = int(getattr(controller, "strip_count", 1))
        self.height = int(getattr(controller, "leds_per_strip", 1))
        self.buffer = FrameBuffer(self.width, self.height)
        self.scene = DashboardScene(self.width, self.height)
        self.last_data_second: int | None = None
        self._apply_scene_settings()

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update(
            {
                "time_format_24h": {
                    "type": "bool",
                    "default": True,
                    "description": "Use 24-hour clock format",
                },
                "show_seconds": {
                    "type": "bool",
                    "default": True,
                    "description": "Render seconds in clock output",
                },
                "text_red": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Text red"},
                "text_green": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Text green"},
                "text_blue": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Text blue"},
                "background_red": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "Background red"},
                "background_green": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "Background green"},
                "background_blue": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "Background blue"},
                "safe_margin_x": {
                    "type": "int",
                    "min": 0,
                    "max": 20,
                    "default": 0,
                    "description": "Horizontal safe margin",
                },
                "safe_margin_y": {
                    "type": "int",
                    "min": 0,
                    "max": 20,
                    "default": 0,
                    "description": "Vertical safe margin",
                },
                "serpentine": {
                    "type": "bool",
                    "default": False,
                    "description": "Flip every other strip to match serpentine wiring",
                },
            }
        )
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        super().update_parameters(new_params)
        self._apply_scene_settings()

    def update_data(self, now: datetime) -> None:
        self.scene.consume(now)

    def render_frame(self, now: datetime, frame_count: int) -> List[Color]:
        del now, frame_count
        self.buffer.clear(self._background_color())
        self.scene.render(self.buffer)
        return self.buffer.to_frame(serpentine=bool(self.params.get("serpentine", False)))

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Color]:
        del time_elapsed
        now = datetime.now()
        now_second = int(now.timestamp())
        if self.last_data_second != now_second:
            self.update_data(now)
            self.last_data_second = now_second
        return self.render_frame(now, frame_count)

    def _apply_scene_settings(self) -> None:
        self.scene.update_settings(
            use_24h=bool(self.params.get("time_format_24h", True)),
            show_seconds=bool(self.params.get("show_seconds", True)),
            text_color=self._text_color(),
            safe_margin_x=int(self.params.get("safe_margin_x", 0)),
            safe_margin_y=int(self.params.get("safe_margin_y", 0)),
        )

    def _text_color(self) -> Color:
        return self.apply_brightness(
            (
                int(self.params.get("text_red", 255)),
                int(self.params.get("text_green", 255)),
                int(self.params.get("text_blue", 255)),
            )
        )

    def _background_color(self) -> Color:
        return self.apply_brightness(
            (
                int(self.params.get("background_red", 0)),
                int(self.params.get("background_green", 0)),
                int(self.params.get("background_blue", 0)),
            )
        )
