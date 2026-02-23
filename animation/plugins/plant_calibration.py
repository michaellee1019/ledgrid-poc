#!/usr/bin/env python3
"""
Plant Calibration Animation Plugin

Cycles through static calibration patterns with transition gaps so a camera can
capture multiple reference images for pixel-to-wall mapping.
"""

from typing import Any, Dict, List, Tuple

from animation import AnimationBase


Color = Tuple[int, int, int]


class PlantCalibrationAnimation(AnimationBase):
    """Photo-friendly calibration patterns for mapping plant-covered pixels."""

    ANIMATION_NAME = "Plant Calibration"
    ANIMATION_DESCRIPTION = "Static reference patterns for wall photo calibration"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.1"
    PATTERN_SEQUENCE_LABELS: List[str] = [
        "orientation_markers",
        "major_grid_lines",
        "checkerboard",
        "coordinate_gradient",
        "full_white",
    ]

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.strip_count, self.leds_per_strip = self.get_strip_info()
        self.total_leds = self.strip_count * self.leds_per_strip

        self._pattern_names: List[str] = list(self.PATTERN_SEQUENCE_LABELS)
        self._pattern_frames: List[List[Color]] = []
        self._black_frame = [(0, 0, 0)] * self.total_leds
        self._last_stage_key = ""

        self._rebuild_pattern_frames()

    def start(self):
        super().start()
        self._last_stage_key = ""
        manual = self._manual_pattern_index()
        print("Plant Calibration started")
        if manual >= 0:
            print(f"   Manual pattern lock enabled: index={manual} ({self._pattern_names[manual]})")
            return
        print(
            f"   Pattern hold: {self._pattern_hold_seconds():.1f}s, "
            f"transition gap: {self._transition_seconds():.1f}s"
        )
        print(f"   Total cycle length: {self._cycle_duration():.1f}s")

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        return {
            "pattern_hold_seconds": {
                "type": "float",
                "min": 2.0,
                "max": 20.0,
                "default": 6.0,
                "description": "How long each pattern is shown (seconds)",
            },
            "transition_seconds": {
                "type": "float",
                "min": 0.0,
                "max": 8.0,
                "default": 2.0,
                "description": "Black gap between patterns (seconds)",
            },
            "manual_pattern_index": {
                "type": "int",
                "min": -1,
                "max": len(self._pattern_names) - 1,
                "default": -1,
                "description": "Lock output to one pattern index; -1 cycles automatically",
            },
            "brightness": {
                "type": "float",
                "min": 0.05,
                "max": 1.0,
                "default": 0.55,
                "description": "Overall pattern brightness",
            },
            "major_col_step": {
                "type": "int",
                "min": 4,
                "max": 28,
                "default": 10,
                "description": "Column interval for major grid lines",
            },
            "major_row_step": {
                "type": "int",
                "min": 1,
                "max": 8,
                "default": 4,
                "description": "Row interval for major grid lines",
            },
            "checker_tile_width": {
                "type": "int",
                "min": 2,
                "max": 16,
                "default": 4,
                "description": "Checkerboard tile width in pixels",
            },
            "checker_tile_height": {
                "type": "int",
                "min": 1,
                "max": 8,
                "default": 2,
                "description": "Checkerboard tile height in pixels",
            },
        }

    def update_parameters(self, new_params: Dict[str, Any]):
        super().update_parameters(new_params)
        # All patterns are static and parameterized; rebuild if values change.
        self._rebuild_pattern_frames()

    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Color]:
        if not self._pattern_frames:
            self._rebuild_pattern_frames()

        manual = self._manual_pattern_index()
        if manual >= 0:
            stage_key = f"{manual}:manual"
            if stage_key != self._last_stage_key:
                print(
                    f"Manual pattern {manual + 1}/{len(self._pattern_names)}: "
                    f"{self._pattern_names[manual]}"
                )
                self._last_stage_key = stage_key
            return self._pattern_frames[manual]

        pattern_index, in_transition = self._stage_for_time(time_elapsed)

        stage_key = f"{pattern_index}:{'gap' if in_transition else 'pattern'}"
        if stage_key != self._last_stage_key:
            if in_transition:
                print("Transition gap")
            else:
                print(
                    f"Pattern {pattern_index + 1}/{len(self._pattern_names)}: "
                    f"{self._pattern_names[pattern_index]}"
                )
            self._last_stage_key = stage_key

        if in_transition:
            return self._black_frame

        return self._pattern_frames[pattern_index]

    def get_runtime_stats(self) -> Dict[str, Any]:
        time_elapsed = 0.0
        if self.is_running:
            # start_time is set by AnimationBase.start()
            import time as _time
            time_elapsed = max(0.0, _time.time() - self.start_time)

        manual = self._manual_pattern_index()
        if manual >= 0:
            pattern_index = manual
            in_transition = False
        else:
            pattern_index, in_transition = self._stage_for_time(time_elapsed)
        return {
            "current_pattern_index": int(pattern_index),
            "current_pattern_name": self._pattern_names[pattern_index],
            "in_transition_gap": bool(in_transition),
            "manual_pattern_index": int(manual),
            "pattern_hold_seconds": self._pattern_hold_seconds(),
            "transition_seconds": self._transition_seconds(),
            "cycle_duration_seconds": self._cycle_duration(),
            "pattern_names": self._pattern_names,
        }

    # Pattern timing -------------------------------------------------------
    def _pattern_hold_seconds(self) -> float:
        return max(2.0, float(self.params.get("pattern_hold_seconds", 6.0)))

    def _transition_seconds(self) -> float:
        return max(0.0, float(self.params.get("transition_seconds", 2.0)))

    def _stage_duration(self) -> float:
        return self._pattern_hold_seconds() + self._transition_seconds()

    def _cycle_duration(self) -> float:
        return self._stage_duration() * len(self._pattern_names)

    def _manual_pattern_index(self) -> int:
        raw = int(self.params.get("manual_pattern_index", -1))
        if raw < 0:
            return -1
        return min(raw, len(self._pattern_names) - 1)

    def _stage_for_time(self, time_elapsed: float) -> Tuple[int, bool]:
        stage_duration = self._stage_duration()
        if stage_duration <= 0:
            return 0, False

        cycle_pos = time_elapsed % self._cycle_duration()
        pattern_index = int(cycle_pos // stage_duration)
        if pattern_index >= len(self._pattern_names):
            pattern_index = len(self._pattern_names) - 1

        in_stage_pos = cycle_pos % stage_duration
        in_transition = in_stage_pos >= self._pattern_hold_seconds() and self._transition_seconds() > 0
        return pattern_index, in_transition

    # Pattern builders -----------------------------------------------------
    def _rebuild_pattern_frames(self):
        self._pattern_frames = [
            self._build_orientation_markers(),
            self._build_major_grid_lines(),
            self._build_checkerboard(),
            self._build_coordinate_gradient(),
            self._build_full_white(),
        ]

    def _brightness(self) -> float:
        return max(0.05, min(1.0, float(self.params.get("brightness", 0.55))))

    def _scale(self, color: Color) -> Color:
        brightness = self._brightness()
        return (
            int(color[0] * brightness),
            int(color[1] * brightness),
            int(color[2] * brightness),
        )

    def _pixel_index(self, strip: int, led: int) -> int:
        return strip * self.leds_per_strip + led

    def _set_pixel(self, frame: List[Color], strip: int, led: int, color: Color):
        if 0 <= strip < self.strip_count and 0 <= led < self.leds_per_strip:
            frame[self._pixel_index(strip, led)] = color

    def _build_orientation_markers(self) -> List[Color]:
        frame = [(0, 0, 0)] * self.total_leds
        border = self._scale((70, 70, 70))
        width = self.leds_per_strip
        height = self.strip_count

        for led in range(width):
            self._set_pixel(frame, 0, led, border)
            self._set_pixel(frame, height - 1, led, border)
        for strip in range(height):
            self._set_pixel(frame, strip, 0, border)
            self._set_pixel(frame, strip, width - 1, border)

        marker_w = min(6, max(2, width // 20))
        marker_h = min(4, max(1, height // 10))

        self._fill_rect(frame, 0, 0, marker_h, marker_w, self._scale((255, 40, 40)))  # top-left
        self._fill_rect(frame, 0, width - marker_w, marker_h, marker_w, self._scale((40, 255, 40)))  # top-right
        self._fill_rect(frame, height - marker_h, 0, marker_h, marker_w, self._scale((40, 40, 255)))  # bottom-left
        self._fill_rect(
            frame,
            height - marker_h,
            width - marker_w,
            marker_h,
            marker_w,
            self._scale((255, 220, 40)),
        )  # bottom-right

        # Center cross improves perspective alignment in photos.
        center_strip = height // 2
        center_led = width // 2
        cross_color = self._scale((220, 220, 220))
        for led in range(max(0, center_led - 8), min(width, center_led + 9)):
            self._set_pixel(frame, center_strip, led, cross_color)
        for strip in range(max(0, center_strip - 4), min(height, center_strip + 5)):
            self._set_pixel(frame, strip, center_led, cross_color)

        return frame

    def _build_major_grid_lines(self) -> List[Color]:
        frame = [(0, 0, 0)] * self.total_leds
        width = self.leds_per_strip
        height = self.strip_count

        col_step = max(4, int(self.params.get("major_col_step", 10)))
        row_step = max(1, int(self.params.get("major_row_step", 4)))
        minor_step = max(2, col_step // 2)

        col_palette = [
            self._scale((255, 60, 60)),
            self._scale((60, 255, 60)),
            self._scale((60, 120, 255)),
            self._scale((255, 210, 60)),
            self._scale((220, 80, 255)),
            self._scale((60, 255, 220)),
        ]
        minor_color = self._scale((30, 30, 30))
        row_color = self._scale((220, 220, 220))

        for led in range(width):
            if led % col_step == 0:
                color = col_palette[(led // col_step) % len(col_palette)]
                for strip in range(height):
                    self._set_pixel(frame, strip, led, color)
            elif led % minor_step == 0:
                for strip in range(height):
                    self._set_pixel(frame, strip, led, minor_color)

        for strip in range(height):
            if strip % row_step == 0:
                for led in range(width):
                    self._set_pixel(frame, strip, led, row_color)

        return frame

    def _build_checkerboard(self) -> List[Color]:
        frame = [(0, 0, 0)] * self.total_leds
        width = self.leds_per_strip
        height = self.strip_count

        tile_w = max(2, int(self.params.get("checker_tile_width", 4)))
        tile_h = max(1, int(self.params.get("checker_tile_height", 2)))

        color_a = self._scale((255, 255, 255))
        color_b = self._scale((30, 30, 30))

        for strip in range(height):
            tile_row = strip // tile_h
            for led in range(width):
                tile_col = led // tile_w
                color = color_a if ((tile_row + tile_col) % 2 == 0) else color_b
                self._set_pixel(frame, strip, led, color)

        return frame

    def _build_coordinate_gradient(self) -> List[Color]:
        frame = [(0, 0, 0)] * self.total_leds
        width = self.leds_per_strip
        height = self.strip_count

        max_x = max(1, width - 1)
        max_y = max(1, height - 1)

        for strip in range(height):
            green = int(255 * strip / max_y)
            for led in range(width):
                red = int(255 * led / max_x)
                blue = (red ^ green) & 0xFF
                self._set_pixel(frame, strip, led, self._scale((red, green, blue)))

        return frame

    def _build_full_white(self) -> List[Color]:
        return [self._scale((255, 255, 255))] * self.total_leds

    def _fill_rect(
        self,
        frame: List[Color],
        start_strip: int,
        start_led: int,
        strip_count: int,
        led_count: int,
        color: Color,
    ):
        end_strip = start_strip + strip_count
        end_led = start_led + led_count
        for strip in range(start_strip, end_strip):
            for led in range(start_led, end_led):
                self._set_pixel(frame, strip, led, color)
