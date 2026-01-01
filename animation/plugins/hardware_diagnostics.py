#!/usr/bin/env python3
"""
Hardware Diagnostics Animation

Enables on-device diagnostic patterns and streams controller status snapshots to
help isolate flaky wiring, brownouts, and SPI timing issues without reflashing
multiple times.
"""

import time
from typing import Dict, Any, List

from animation import StatefulAnimationBase

DIAG_PATTERNS = {
    'strip_chase': 1,
    'power_pulse': 2,
    'off': 0,
}


class HardwareDiagnosticsAnimation(StatefulAnimationBase):
    """Animation wrapper that delegates diagnostic animations to the ESP32 firmware."""

    ANIMATION_NAME = "Hardware Diagnostics"
    ANIMATION_DESCRIPTION = "Runs firmware-side strip tests and surfaces ESP32 telemetry"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)
        self.pattern = self.config.get('pattern', 'strip_chase')
        self.intensity = int(self.config.get('intensity', 160))
        self.poll_interval = float(self.config.get('poll_interval', 0.25))
        self.log_interval = float(self.config.get('log_interval', 2.0))
        self._last_status: List[Dict[str, Any]] = []
        self._last_log = 0.0

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'pattern': {
                'type': 'str',
                'default': 'strip_chase',
                'description': 'Diagnostic pattern (strip_chase, power_pulse, off)',
            },
            'intensity': {
                'type': 'int',
                'min': 8,
                'max': 255,
                'default': 160,
                'description': 'Brightness for diagnostic pattern',
            },
            'poll_interval': {
                'type': 'float',
                'min': 0.1,
                'max': 2.0,
                'default': 0.25,
                'description': 'How often to refresh controller telemetry (seconds)',
            },
            'log_interval': {
                'type': 'float',
                'min': 0.5,
                'max': 10.0,
                'default': 2.0,
                'description': 'How often to print device summaries (seconds)',
            },
        }

    def start(self):
        self._apply_diagnostic_mode()
        super().start()

    def stop(self):
        self._set_diagnostic_mode('off')
        super().stop()

    def update_parameters(self, params: Dict[str, Any]):
        if 'pattern' in params:
            self.pattern = str(params['pattern'])
            self._apply_diagnostic_mode()
        if 'intensity' in params:
            self.intensity = max(1, min(255, int(params['intensity'])))
            self._apply_diagnostic_mode()
        if 'poll_interval' in params:
            self.poll_interval = max(0.1, float(params['poll_interval']))
        if 'log_interval' in params:
            self.log_interval = max(0.5, float(params['log_interval']))

    def run_animation(self):
        while not self.stop_event.is_set():
            statuses = self._fetch_statuses()
            if statuses:
                self._last_status = statuses
                self._maybe_log(statuses)
            time.sleep(self.poll_interval)

    def get_runtime_stats(self) -> Dict[str, Any]:
        return {
            'pattern': self.pattern,
            'intensity': self.intensity,
            'poll_interval': self.poll_interval,
            'log_interval': self.log_interval,
            'device_status': self._last_status,
        }

    # Internal helpers -----------------------------------------------------
    def _apply_diagnostic_mode(self):
        self._set_diagnostic_mode(self.pattern)

    def _set_diagnostic_mode(self, pattern: str):
        mode = DIAG_PATTERNS.get(pattern, 0)
        setter = getattr(self.controller, 'set_diagnostic_mode', None)
        if callable(setter):
            try:
                setter(mode, self.intensity)
            except Exception as exc:
                print(f"⚠️ Failed to set diagnostic mode ({pattern}): {exc}")
        else:
            print("⚠️ Controller does not support diagnostic commands")

    def _fetch_statuses(self) -> List[Dict[str, Any]]:
        ping = getattr(self.controller, 'ping', None)
        if callable(ping):
            try:
                ping()
            except Exception as exc:
                print(f"⚠️ Diagnostic ping failed: {exc}")
        getter = getattr(self.controller, 'get_device_statuses', None)
        if not callable(getter):
            return []
        try:
            statuses = getter() or []
        except Exception as exc:
            print(f"⚠️ Failed to fetch controller status: {exc}")
            statuses = []
        # ensure JSON-friendly dicts
        normalized = []
        for status in statuses:
            if hasattr(status, 'to_dict'):
                normalized.append(status.to_dict())
            elif isinstance(status, dict):
                normalized.append(status)
        return normalized

    def _maybe_log(self, statuses: List[Dict[str, Any]]):
        now = time.time()
        if now - self._last_log < self.log_interval:
            return
        self._last_log = now
        for status in statuses:
            device = status.get('device_id', '?')
            summary = self._format_summary(status)
            print(f"🩺 Device {device}: {summary}")

    @staticmethod
    def _format_summary(status: Dict[str, Any]) -> str:
        stall = "STALL" if status.get('data_stalled') else ""
        diag = ""
        if status.get('diag_mode'):
            diag = f"diag={status.get('diag_mode')} step={status.get('diag_step')}"
        err = f"spi_err={status.get('last_spi_error')}" if status.get('spi_fault') else ""
        return ("pkts={pkts} frames={frames} show={show}µs ms_since={ms} "
                "heap={heap} last_cmd=0x{cmd:02X} {diag} {stall} {err}").format(
            pkts=status.get('packets_received'),
            frames=status.get('frames_rendered'),
            show=status.get('last_show_us'),
            ms=status.get('ms_since_packet'),
            heap=status.get('free_heap'),
            cmd=status.get('last_command', 0),
            diag=diag,
            stall=stall,
            err=err,
        ).strip()
