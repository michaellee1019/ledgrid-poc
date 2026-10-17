"""Read-only, bounded automation diagnostics, separate from routine controls."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import base64
import json
import math
import threading
import time
import zlib

from flask import Response, jsonify
from PIL import Image

from ipc.controller_frame_snapshot import request_controller_frame

PREFIX = '/api/v1/automation'
COUNTERS = (
    'receiver_frames_displayed', 'receiver_packets', 'receiver_crc_errors',
    'receiver_fec_corrected_packets', 'receiver_fec_uncorrectable_packets',
    'receiver_fec_semantic_crc_errors', 'receiver_fec_framing_errors',
    'receiver_status_integrity_errors', 'receiver_status_empty_responses',
    'receiver_display_errors', 'receiver_publish_drops', 'receiver_frames_superseded',
)


def number(value):
    return value if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None


def counter(value):
    return value if type(value) is int and value >= 0 else None


def fresh_timestamp(value, now, seconds):
    return number(value) is not None and 0 <= now - value <= seconds


class AutomationDiagnostics:
    """Project existing telemetry; no rendering, probing or playback commands."""

    def __init__(self, interface):
        self.interface = interface
        self._lock = threading.RLock()
        self._samples = {}
        self._results = {}
        self._session = None
        self._image = None
        self._image_captured_at = 0.0
        self._image_session = None
        self._image_attempted_at = float('-inf')
        self._image_error = 'Controller frame has not been requested'

    def snapshot(self):
        raw = self.interface.control_channel.read_status() or {}
        now = time.time()
        stamp = raw.get('updated_at') or raw.get('timestamp') or raw.get('written_at')
        session = raw.get('controller_session_id')
        connected = bool(isinstance(session, str) and session and fresh_timestamp(stamp, now, 5))
        devices = raw.get('receiver_connectivity')
        if not isinstance(devices, list):
            stats = raw.get('driver_stats')
            devices = stats.get('devices', []) if isinstance(stats, dict) else []
        devices = devices if isinstance(devices, list) else []
        by_id = {item['device_index']: item for item in devices if isinstance(item, dict)
                 and type(item.get('device_index')) is int and 0 <= item['device_index'] < 5}
        with self._lock:
            if session != self._session:
                self._samples.clear()
                self._results.clear()
                self._session = session
            receivers = [self._receiver(index, by_id.get(index), connected, now) for index in range(5)]
        performance = raw.get('performance')
        performance = performance if isinstance(performance, dict) else {}
        playback = raw.get('controller_playback')
        playback = playback if isinstance(playback, dict) else {}
        playback_error = playback.get('error') or raw.get('last_error')
        return {
            'schema': 'ledgrid.automation-diagnostics', 'schema_version': 1,
            'installation_id': self.interface.automation.installation_id,
            'server_session_id': self.interface.automation.server_session_id,
            'controller_session_id': session, 'observed_at': stamp,
            'controller': {
                'connected': connected,
                'playback': ('failed' if playback_error else 'running' if raw.get('is_running') else 'stopped') if connected else 'unavailable',
                'error': str(playback_error or '')[:512] if connected else None,
                'host_target_fps': number(raw.get('target_fps')) if connected else None,
                'host_actual_fps': number(raw.get('actual_fps')) if connected else None,
                'host_frames_generated': counter(raw.get('frame_count')) if connected else None,
                'host_frames_presented': counter(raw.get('frames_presented')) if connected else None,
                'unchanged_frames_skipped': counter(raw.get('unchanged_frames_skipped')) if connected else None,
                **{key: number(performance.get(key)) if connected else None
                   for key in ('avg_generate_ms', 'avg_send_ms', 'p95_frame_ms', 'deadline_misses')},
            },
            'receivers': receivers,
            'physical_display_verified': False,
        }

    def _receiver(self, index, raw, controller_connected, now):
        raw = raw or {}
        stamp = raw.get('receiver_status_observed_at')
        fresh = bool(controller_connected and fresh_timestamp(stamp, now, 10))
        trusted = bool(raw.get('receiver_status_integrity_verified') is True
                       and raw.get('receiver_status_integrity_established') is True
                       and raw.get('receiver_status_current_peer_rejected') is not True)
        connected = bool(fresh and raw.get('receiver_connected') is True)
        counts = {key.removeprefix('receiver_'): counter(raw.get(key)) for key in COUNTERS}
        signature = (stamp, fresh, trusted, connected, tuple(counts.items()), bool(raw))
        previous = self._results.get(index)
        if previous and previous[0] == signature:
            return deepcopy(previous[1])
        baseline = self._samples.get(index)
        reason = 'first_sample'
        rate = None
        if not raw:
            reason = 'missing'
        elif not fresh:
            reason = 'stale'
        elif not trusted:
            reason = 'unverified_status'
        elif not connected:
            reason = 'disconnected'
        elif any(counts.get(key) is None for key in ('frames_displayed', 'packets', 'status_integrity_errors')):
            reason = 'missing_counters'
        elif baseline:
            prior_stamp, prior = baseline
            if any(value is not None and prior.get(key) is not None and value < prior[key] for key, value in counts.items()):
                reason = 'counter_reset'
            elif counts['status_integrity_errors'] != prior['status_integrity_errors']:
                reason = 'invalid_protected_interval'
            elif stamp <= prior_stamp:
                reason = 'no_new_sample'
            elif stamp - prior_stamp > 65:
                reason = 'interval_too_long'
            else:
                rate = round((counts['frames_displayed'] - prior['frames_displayed']) / (stamp - prior_stamp), 2)
                reason = 'valid'
        if fresh and trusted and connected and all(counts.get(key) is not None for key in ('frames_displayed', 'packets', 'status_integrity_errors')):
            if reason != 'no_new_sample':
                self._samples[index] = (stamp, counts)
        else:
            self._samples.pop(index, None)
        integrity = ('missing' if not raw else 'stale' if not fresh else 'unverified' if not trusted
                     else 'rejections_in_interval' if reason == 'invalid_protected_interval'
                     else 'verified_with_rejections' if counts['status_integrity_errors'] else 'verified')
        # Historical counters are never relabelled as current receiver evidence.
        result = {'receiver_id': index, 'connected': connected, 'fresh': fresh,
                'protected_status': integrity, 'observed_at': stamp,
                'output_fps': rate, 'output_rate_reason': reason,
                'counters': counts if fresh and trusted else {key: None for key in counts}}
        self._results[index] = (signature, result)
        return deepcopy(result)

    def frame_png(self):
        """Capture only on demand, globally no more than once per five seconds."""
        with self._lock:
            now = time.monotonic()
            if now - self._image_attempted_at >= 5:
                self._image_attempted_at = now
                self._image = None
                try:
                    raw = self.interface.control_channel.read_status() or {}
                    stamp = raw.get('updated_at') or raw.get('timestamp') or raw.get('written_at')
                    if not fresh_timestamp(stamp, time.time(), 5) or not raw.get('controller_session_id'):
                        raise ValueError('Controller observation is unavailable')
                    frame = request_controller_frame(self.interface.control_channel, raw['controller_session_id'])
                    info = frame.get('led_info') or {}
                    if info.get('strip_count') != 33 or info.get('leds_per_strip') != 138:
                        raise ValueError('Controller frame does not match installed 33x138 geometry')
                    pixels = frame.get('frame_data')
                    if pixels is None:
                        encoded = frame.get('frame_data_encoded', '')
                        if not isinstance(encoded, str) or len(encoded) > 200000:
                            raise ValueError('Controller frame encoding is invalid')
                        decoder = zlib.decompressobj()
                        unpacked = decoder.decompress(base64.b64decode(encoded, validate=True), 200001)
                        if len(unpacked) > 200000 or not decoder.eof or decoder.unused_data:
                            raise ValueError('Controller frame encoding exceeds the installed geometry')
                        pixels = json.loads(unpacked)
                    if not isinstance(pixels, list) or len(pixels) != 33 * 138:
                        raise ValueError('Controller frame is missing or incomplete')
                    if any(not isinstance(pixel, (list, tuple)) or len(pixel) != 3
                           or any(type(channel) is not int or not 0 <= channel <= 255 for channel in pixel) for pixel in pixels):
                        raise ValueError('Controller frame contains invalid RGB pixels')
                    image = Image.new('RGB', (33, 138))
                    # Controller pixels are strip-major: x is strip, y is LED.
                    image.putdata([tuple(pixels[x * 138 + y]) for y in range(138) for x in range(33)])
                    buffer = BytesIO()
                    image.save(buffer, format='PNG')
                    self._image = buffer.getvalue()
                    self._image_captured_at = frame['captured_at']
                    self._image_session = raw['controller_session_id']
                except (OSError, ValueError, TimeoutError, zlib.error) as err:
                    self._image_error = str(err)
            if self._image is None:
                raise ValueError(self._image_error)
            # Cached bytes are still bounded by fresh controller and capture evidence.
            current = self.interface.control_channel.read_status() or {}
            stamp = current.get('updated_at') or current.get('timestamp') or current.get('written_at')
            if (not fresh_timestamp(stamp, time.time(), 5) or time.time() - self._image_captured_at > 10
                    or current.get('controller_session_id') != self._image_session):
                raise ValueError('Controller frame is stale')
            return self._image, self._image_captured_at, self._image_session


def register_automation_diagnostic_routes(interface):
    diagnostic = AutomationDiagnostics(interface)
    interface.automation_diagnostics = diagnostic

    def snapshot():
        response = jsonify(diagnostic.snapshot())
        response.headers['Cache-Control'] = 'no-store'
        return response

    def frame():
        try:
            content, captured_at, session = diagnostic.frame_png()
        except (OSError, ValueError, TimeoutError, zlib.error) as err:
            return jsonify({'error': str(err)}), 503
        return Response(content, mimetype='image/png', headers={
            'Cache-Control': 'no-store', 'X-LEDGrid-Captured-At': str(captured_at),
            'X-LEDGrid-Frame-Source': 'controller-output-not-physical-display',
            'X-LEDGrid-Controller-Session': session,
        })

    interface.app.add_url_rule(PREFIX + '/diagnostics', 'automation_diagnostics', snapshot)
    interface.app.add_url_rule(PREFIX + '/frame.png', 'automation_frame', frame)
