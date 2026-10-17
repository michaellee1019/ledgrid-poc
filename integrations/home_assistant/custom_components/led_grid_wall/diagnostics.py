"""Optional shared diagnostic polling and on-demand controller image reads."""
import asyncio
from datetime import timedelta
import logging
import math
import struct
import time

import aiohttp
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import WallError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def normalize_diagnostics(data, installation_id):
    if (not isinstance(data, dict) or data.get('schema') != 'ledgrid.automation-diagnostics'
            or data.get('schema_version') != 1 or data.get('installation_id') != installation_id
            or not isinstance(data.get('controller'), dict) or not isinstance(data.get('receivers'), list)):
        raise WallError('Invalid wall diagnostics')
    def fresh(stamp, seconds):
        return type(stamp) in {int, float} and math.isfinite(stamp) and -2 <= time.time() - stamp <= seconds

    controller = dict(data['controller'])
    controller_fresh = controller.get('connected') is True and fresh(data.get('observed_at'), 5)
    if not controller_fresh:
        controller = {key: None for key in controller}
        controller.update(connected=False, playback='unavailable')
    # Exclude timestamps and packet blobs from HA state. Freshness is evaluated
    # before discarding timestamps so stale responses cannot appear current.
    receivers = {}
    for item in data['receivers']:
        if not isinstance(item, dict) or type(item.get('receiver_id')) is not int or not 0 <= item['receiver_id'] < 5:
            continue
        counts = item.get('counters') if isinstance(item.get('counters'), dict) else {}
        receiver = {key: item.get(key) for key in ('receiver_id', 'connected', 'fresh', 'protected_status', 'output_fps', 'output_rate_reason')}
        receiver['counters'] = {key: value if type(value) is int and value >= 0 else None
                                for key, value in counts.items() if key in {
                                    'frames_displayed', 'packets', 'crc_errors', 'fec_corrected_packets',
                                    'fec_uncorrectable_packets', 'fec_semantic_crc_errors', 'fec_framing_errors',
                                    'status_integrity_errors', 'status_empty_responses', 'display_errors',
                                    'publish_drops', 'frames_superseded'}}
        protected = item.get('protected_status') in {'verified', 'verified_with_rejections', 'rejections_in_interval'}
        if not protected or item.get('fresh') is not True:
            receiver.update(output_fps=None, counters={key: None for key in receiver['counters']})
        fps = receiver.get('output_fps')
        if type(fps) not in {int, float} or not math.isfinite(fps) or fps < 0:
            receiver['output_fps'] = None
        if not controller_fresh or not fresh(item.get('observed_at'), 10):
            receiver.update(connected=False, fresh=False, protected_status='stale',
                            output_fps=None, output_rate_reason='stale',
                            counters={key: None for key in receiver['counters']})
        receivers[item['receiver_id']] = receiver
    return {'controller': controller, 'receivers': receivers}


class WallDiagnosticCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, wall):
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN + ' diagnostics',
                         update_interval=timedelta(seconds=30), always_update=False)
        self.wall = wall
        self.api = wall.api
        self.installation_id = wall.installation_id
        self._initial_task = None
        self._image_lock = asyncio.Lock()
        self._image_attempted_at = float('-inf')
        self._image = None
        self._image_stamp = None
        self._image_session = None

    async def async_ensure_data(self):
        if self._initial_task is None:
            self._initial_task = self.hass.async_create_task(self.async_refresh())
        await self._initial_task

    async def _async_update_data(self):
        try:
            return normalize_diagnostics(await self.api.request('GET', '/diagnostics'), self.installation_id)
        except WallError as err:
            raise UpdateFailed(str(err)) from err

    async def async_frame(self):
        if not self.wall.last_update_success:
            raise WallError('Controller observation is unavailable')
        async with self._image_lock:
            if time.monotonic() - self._image_attempted_at >= 5:
                self._image_attempted_at = time.monotonic()
                self._image = None
                try:
                    async with self.api.session.get(
                        self.api.url + '/api/v1/automation/frame.png',
                        timeout=aiohttp.ClientTimeout(total=3), allow_redirects=False,
                    ) as response:
                        if response.status != 200 or response.content_type != 'image/png':
                            raise WallError('Controller frame is unavailable')
                        chunks = []
                        size = 0
                        async for chunk in response.content.iter_chunked(8192):
                            size += len(chunk)
                            if size > 65536:
                                raise WallError('Controller image exceeds the installed frame size')
                            chunks.append(chunk)
                        content = b''.join(chunks)
                        stamp = float(response.headers['X-LEDGrid-Captured-At'])
                        session = response.headers['X-LEDGrid-Controller-Session']
                        if (len(content) > 65536 or content[:8] != b'\x89PNG\r\n\x1a\n'
                                or not content.endswith(b'\x00\x00\x00\x00IEND\xaeB\x60\x82')
                                or len(content) < 24 or struct.unpack('>II', content[16:24]) != (33, 138)
                                or not 0 <= time.time() - stamp <= 5
                                or session != self.wall.data['controller_session_id']):
                            raise WallError('Invalid or stale controller frame')
                        self._image, self._image_stamp, self._image_session = content, stamp, session
                except (aiohttp.ClientError, TimeoutError, ValueError, KeyError) as err:
                    raise WallError(f'Controller frame request failed: {err}') from err
            if (not self.wall.last_update_success or self._image is None or time.time() - self._image_stamp > 10
                    or self._image_session != self.wall.data['controller_session_id']):
                raise WallError('Controller frame is unavailable')
            return self._image, self._image_stamp


async def async_setup_diagnostics(hass, entry):
    # No optional poll or frame request until an entity is enabled/requested.
    entry.runtime_data.diagnostics = WallDiagnosticCoordinator(hass, entry, entry.runtime_data)


async def async_unload_diagnostics(entry):
    await entry.runtime_data.diagnostics.async_shutdown()
