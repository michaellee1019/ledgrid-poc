"""On-demand readback of an existing controller buffer, outside playback commands.

The controller's control loop calls poll(); it never captures without a fresh
correlated request. This path has no command ID, controller mutation or renderer.
"""
from __future__ import annotations

import json
import math
import time
import uuid

from ipc.runtime_control import controller_activation_coordinator

REQUEST_SECONDS = 2


def _read(path, max_bytes):
    try:
        if path.stat().st_size > max_bytes:
            return None
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _paths(channel):
    root = channel.control_path.parent / 'frame-snapshot'
    return root / 'request.json', root / 'response.json'


def request_controller_frame(channel, controller_session_id, *, timeout=1.5):
    """Wait for one correlated readback, never retry or replay an expired request."""
    request_path, response_path = _paths(channel)
    request_id = str(uuid.uuid4())
    channel._atomic_write(request_path, {
        'schema': 'ledgrid.controller-frame-request', 'schema_version': 1,
        'request_id': request_id, 'controller_session_id': controller_session_id,
        'expires_at': time.time() + REQUEST_SECONDS,
    })
    deadline = time.monotonic() + min(max(timeout, 0), REQUEST_SECONDS)
    try:
        while time.monotonic() < deadline:
            response = _read(response_path, 1024 * 1024)
            if response and response.get('request_id') == request_id:
                if (response.get('schema') != 'ledgrid.controller-frame-response' or response.get('schema_version') != 1
                        or response.get('controller_session_id') != controller_session_id):
                    raise ValueError('Controller frame session changed')
                if response.get('error'):
                    raise ValueError(response['error'])
                frame = response.get('frame')
                stamp = response.get('captured_at')
                if (not isinstance(frame, dict) or type(stamp) not in {int, float}
                        or not math.isfinite(stamp) or not 0 <= time.time() - stamp <= REQUEST_SECONDS):
                    raise ValueError('Controller frame response is stale or invalid')
                return {**frame, 'captured_at': stamp}
            time.sleep(.025)
        raise TimeoutError('Controller frame readback timed out')
    finally:
        # A controller that reconnects later must never capture this old request.
        current = _read(request_path, 4096)
        if current and current.get('request_id') == request_id:
            request_path.unlink(missing_ok=True)


class ControllerFrameSnapshot:
    def __init__(self, channel, manager):
        self.channel, self.manager = channel, manager
        self._next_poll = 0.0
        self._last_request = None
        self._next_capture = 0.0

    def poll(self):
        now = time.monotonic()
        if now < self._next_poll:
            return False
        self._next_poll = now + .25
        request_path, response_path = _paths(self.channel)
        request = _read(request_path, 4096)
        if not request or request.get('request_id') == self._last_request:
            return False
        request_id = request.get('request_id')
        expiry = request.get('expires_at')
        try:
            if str(uuid.UUID(request_id)) != request_id:
                return False
        except (ValueError, TypeError, AttributeError):
            return False
        self._last_request = request_id
        if (request.get('schema') != 'ledgrid.controller-frame-request' or request.get('schema_version') != 1
                or type(expiry) not in {int, float} or not math.isfinite(expiry)
                or not 0 < expiry - time.time() <= REQUEST_SECONDS):
            return False
        session = controller_activation_coordinator(self.manager).session_id
        response = {'schema': 'ledgrid.controller-frame-response', 'schema_version': 1,
                    'request_id': request_id, 'controller_session_id': session, 'captured_at': time.time()}
        if request.get('controller_session_id') != session:
            response['error'] = 'Controller frame session changed'
        elif now < self._next_capture:
            response['error'] = 'Controller frame capture is rate limited'
        else:
            self._next_capture = now + 5
            try:
                response['frame'] = self.manager.get_current_frame()
            except (OSError, RuntimeError, ValueError) as err:
                response['error'] = str(err)
        try:
            self.channel._atomic_write(response_path, response)
        except OSError:
            # Optional image storage must never unwind the playback loop. The
            # caller times out; this request is not replayed on a later poll.
            return False
        return True
