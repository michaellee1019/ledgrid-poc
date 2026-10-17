"""Read-only controller/receiver diagnostics and bounded existing-frame readback."""
from copy import deepcopy
from io import BytesIO
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from PIL import Image
import pytest

from animation.core.manager import AnimationManager, PreviewLEDController
from ipc.control_channel import FileControlChannel
from ipc.controller_frame_snapshot import ControllerFrameSnapshot, request_controller_frame
from ipc.runtime_control import controller_activation_coordinator
from web.automation_diagnostics import AutomationDiagnostics, register_automation_diagnostic_routes


def receiver(index, stamp):
    return {'device_index': index, 'receiver_connected': True,
            'receiver_status_observed_at': stamp, 'receiver_status_integrity_verified': True,
            'receiver_status_integrity_established': True, 'receiver_status_integrity_errors': 4 if index == 3 else 0,
            'receiver_packets': 1000, 'receiver_frames_displayed': 900,
            'receiver_crc_errors': 17 if index == 3 else 0,
            'receiver_fec_corrected_packets': 22 if index == 3 else 0,
            'receiver_fec_uncorrectable_packets': 2 if index == 3 else 0}


@pytest.fixture
def wall(tmp_path):
    channel = FileControlChannel(tmp_path / 'control.json', tmp_path / 'status.json')
    stamp = time.time()
    raw = {'controller_session_id': 'session-one', 'written_at': stamp, 'is_running': True,
           'controller_playback': {'state': 'running', 'error': None}, 'target_fps': 150,
           'actual_fps': 148.5, 'frame_count': 100, 'frames_presented': 99,
           'performance': {'p95_frame_ms': 6.6, 'blob': ['NEVER_SERIALIZE'] * 10000},
           'receiver_connectivity': [receiver(index, stamp) for index in range(5)],
           'frame_data': ['NEVER_SERIALIZE'] * 10000}
    channel.write_status(raw)
    interface = SimpleNamespace(control_channel=channel, app=Flask(__name__),
                                automation=SimpleNamespace(installation_id='installation', server_session_id='web-session'))
    diagnostic = AutomationDiagnostics(interface)
    return interface, diagnostic, raw, channel


def publish(channel, raw, delta=1):
    next_raw = deepcopy(raw)
    next_raw['written_at'] = time.time()
    for item in next_raw.get('receiver_connectivity', []):
        item['receiver_status_observed_at'] += delta
    channel.write_status(next_raw)
    return next_raw


def test_fixed_receiver_projection_r3_degradation_is_not_controller_failure(wall):
    interface, diagnostic, raw, channel = wall
    result = diagnostic.snapshot()
    assert result['controller']['connected'] and result['controller']['playback'] == 'running'
    assert len(result['receivers']) == 5
    assert result['receivers'][3]['connected']
    assert result['receivers'][3]['protected_status'] == 'verified_with_rejections'
    assert result['receivers'][3]['counters']['crc_errors'] == 17
    assert all(item['output_fps'] is None for item in result['receivers'])
    assert 'NEVER_SERIALIZE' not in json.dumps(result)
    assert len(json.dumps(result)) < 6500
    assert channel.poll_commands() == []


def test_receiver_rates_require_valid_counter_intervals_not_host_cadence(wall):
    interface, diagnostic, raw, channel = wall
    now = time.time()
    raw['written_at'] = now - 1
    for item in raw['receiver_connectivity']:
        item['receiver_status_observed_at'] = now - 1
    channel.write_status(raw)
    diagnostic.snapshot()
    next_raw = publish(channel, raw)
    for item in next_raw['receiver_connectivity']:
        item['receiver_frames_displayed'] += 30 if item['device_index'] == 3 else 140
        item['receiver_packets'] += 150
    channel.write_status(next_raw)
    result = diagnostic.snapshot()
    assert result['controller']['host_actual_fps'] == 148.5
    assert result['receivers'][3]['output_fps'] == 30
    assert result['receivers'][0]['output_fps'] == 140
    assert diagnostic.snapshot() == result  # Same sample does not erase measured intervals.
    # A rejected protected reply makes that entire interval unsuitable for rates.
    changed = deepcopy(next_raw)
    changed['receiver_connectivity'][3]['receiver_status_integrity_errors'] += 1
    channel.write_status(changed)
    assert diagnostic.snapshot()['receivers'][3]['output_fps'] is None
    assert diagnostic.snapshot()['receivers'][3]['protected_status'] == 'rejections_in_interval'
    assert channel.poll_commands() == []


@pytest.mark.parametrize('case,reason', [
    ('missing', 'missing'), ('stale', 'stale'), ('invalid', 'unverified_status'),
    ('disconnected', 'disconnected'), ('missing_counter', 'missing_counters'),
    ('reset', 'counter_reset'), ('session', 'first_sample'),
])
def test_missing_stale_invalid_resets_and_sessions_do_not_invent_receiver_rates(wall, case, reason):
    interface, diagnostic, raw, channel = wall
    diagnostic.snapshot()
    item = raw['receiver_connectivity'][0]
    if case == 'missing':
        raw['receiver_connectivity'].pop(0)
    if case == 'stale':
        item['receiver_status_observed_at'] = time.time() - 11
    if case == 'invalid':
        item['receiver_status_integrity_verified'] = False
    if case == 'disconnected':
        item['receiver_connected'] = False
    if case == 'missing_counter':
        item.pop('receiver_frames_displayed')
    if case == 'reset':
        item['receiver_packets'] = 0
    if case == 'session':
        raw['controller_session_id'] = 'session-two'
    channel.write_status(raw)
    result = diagnostic.snapshot()['receivers'][0]
    assert result['output_fps'] is None and result['output_rate_reason'] == reason
    if case in {'missing', 'stale', 'invalid'}:
        assert all(value is None for value in result['counters'].values())


def test_stopped_failed_missing_and_stale_controller_are_distinct(wall):
    interface, diagnostic, raw, channel = wall
    raw['is_running'] = False
    raw['controller_playback']['state'] = 'stopped'
    channel.write_status(raw)
    assert diagnostic.snapshot()['controller']['playback'] == 'stopped'
    raw['controller_playback']['error'] = 'transport failed'
    channel.write_status(raw)
    assert diagnostic.snapshot()['controller']['playback'] == 'failed'
    raw['controller_playback']['error'] = None
    raw['last_error'] = 'controller failure'
    channel.write_status(raw)
    assert diagnostic.snapshot()['controller']['playback'] == 'failed'
    raw['written_at'] = time.time() - 6
    channel.write_status(raw)
    result = diagnostic.snapshot()
    assert result['controller']['playback'] == 'unavailable'
    assert not result['controller']['connected']
    assert result['controller']['host_actual_fps'] is None
    channel.status_path.unlink()
    assert diagnostic.snapshot()['controller']['playback'] == 'unavailable'


def test_actual_existing_controller_frame_geometry_and_global_cache(wall):
    interface, diagnostic, raw, channel = wall
    manager = AnimationManager(PreviewLEDController(33, 138), auto_start=False)
    session = controller_activation_coordinator(manager).session_id
    raw['controller_session_id'] = session
    channel.write_status(raw)
    manager.current_frame_data = [[x, y, (x + y) % 256] for x in range(33) for y in range(138)]
    capture = ControllerFrameSnapshot(channel, manager)
    stop = threading.Event()
    def poll():
        while not stop.wait(.005):
            capture.poll()
    worker = threading.Thread(target=poll)
    worker.start()
    try:
        with patch.object(manager, 'get_current_frame', wraps=manager.get_current_frame) as readback, \
             patch.object(manager, 'render_composed_scene_frame', side_effect=AssertionError('must not render')):
            content, captured_at, captured_session = diagnostic.frame_png()
            image = Image.open(BytesIO(content))
            assert image.size == (33, 138)
            assert image.getpixel((0, 0)) == (0, 0, 0)
            assert image.getpixel((32, 137)) == (32, 137, 169)
            assert captured_session == session
            assert diagnostic.frame_png() == (content, captured_at, session)
            assert readback.call_count == 1
            assert channel.poll_commands() == []
    finally:
        stop.set()
        worker.join()
        manager.stop_animation(clear_leds=False)


@pytest.mark.parametrize('bad', ['geometry', 'length', 'rgb'])
def test_invalid_existing_frame_is_unavailable_without_substitute(wall, bad):
    interface, diagnostic, raw, channel = wall
    frame = {'led_info': {'strip_count': 33, 'leds_per_strip': 138},
             'frame_data': [[0, 0, 0] for _ in range(33 * 138)], 'captured_at': time.time()}
    if bad == 'geometry':
        frame['led_info']['strip_count'] = 32
    elif bad == 'length':
        frame['frame_data'].pop()
    else:
        frame['frame_data'][0] = [False, 0, 0]
    with patch('web.automation_diagnostics.request_controller_frame', return_value=frame):
        with pytest.raises(ValueError):
            diagnostic.frame_png()
    assert channel.poll_commands() == []


def test_cached_frame_is_rejected_after_controller_restart_or_stale_status(wall):
    interface, diagnostic, raw, channel = wall
    frame = {'led_info': {'strip_count': 33, 'leds_per_strip': 138},
             'frame_data': [[0, 0, 0] for _ in range(33 * 138)], 'captured_at': time.time()}
    with patch('web.automation_diagnostics.request_controller_frame', return_value=frame) as readback:
        diagnostic.frame_png()
        raw['controller_session_id'] = 'new-session'
        channel.write_status(raw)
        with pytest.raises(ValueError, match='stale'):
            diagnostic.frame_png()
        assert readback.call_count == 1
        raw['written_at'] = time.time() - 6
        channel.write_status(raw)
        with pytest.raises(ValueError, match='stale'):
            diagnostic.frame_png()


def test_frame_timeout_session_expiry_and_controller_capture_limit(wall):
    interface, diagnostic, raw, channel = wall
    with pytest.raises(TimeoutError):
        request_controller_frame(channel, 'missing-session', timeout=.03)
    request_path = channel.control_path.parent / 'frame-snapshot' / 'request.json'
    assert not request_path.exists()
    manager = SimpleNamespace(_controller_command_coordinator=SimpleNamespace(session_id='session'),
                              get_current_frame=lambda: {'frame_data': [[0, 0, 0]]})
    capture = ControllerFrameSnapshot(channel, manager)
    request = {'schema': 'ledgrid.controller-frame-request', 'schema_version': 1,
               'request_id': '12345678-abcd-4123-8123-123456789abc', 'controller_session_id': 'session',
               'expires_at': time.time() - 1}
    channel._atomic_write(request_path, request)
    assert not capture.poll()
    capture._next_poll = 0
    request.update(request_id='12345678-abcd-4123-8123-123456789abd', expires_at=time.time() + 2)
    channel._atomic_write(request_path, request)
    assert capture.poll()
    capture._next_poll = 0
    request.update(request_id='12345678-abcd-4123-8123-123456789abe', expires_at=time.time() + 2)
    channel._atomic_write(request_path, request)
    assert capture.poll()
    response = json.loads(request_path.with_name('response.json').read_text())
    assert 'rate limited' in response['error']
    capture._next_poll = 0
    request.update(request_id='12345678-abcd-4123-8123-123456789abf', controller_session_id='old')
    channel._atomic_write(request_path, request)
    assert capture.poll()
    response = json.loads(request_path.with_name('response.json').read_text())
    assert 'session changed' in response['error']


def test_routes_do_not_capture_for_diagnostic_reads_and_fail_closed_without_frame(wall):
    interface, diagnostic, raw, channel = wall
    register_automation_diagnostic_routes(interface)
    client = interface.app.test_client()
    with patch('web.automation_diagnostics.request_controller_frame', side_effect=TimeoutError('no readback')) as capture:
        response = client.get('/api/v1/automation/diagnostics')
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'no-store'
        assert capture.call_count == 0
        assert client.get('/api/v1/automation/frame.png').status_code == 503
        assert client.get('/api/v1/automation/frame.png').status_code == 503
        assert capture.call_count == 1
    assert channel.poll_commands() == []


def test_projection_and_idle_poll_have_bounded_overhead(wall, capsys):
    interface, diagnostic, raw, channel = wall
    raw.pop('frame_data')
    raw['performance'].pop('blob')
    channel.write_status(raw)
    started = time.perf_counter()
    for _ in range(500):
        json.dumps(diagnostic.snapshot())
    projection = time.perf_counter() - started
    manager = SimpleNamespace()
    capture = ControllerFrameSnapshot(channel, manager)
    started = time.perf_counter()
    with patch('ipc.controller_frame_snapshot._read', return_value=None) as read:
        for _ in range(10000):
            capture.poll()
    idle = time.perf_counter() - started
    assert read.call_count <= 2
    frame = {'led_info': {'strip_count': 33, 'leds_per_strip': 138},
             'frame_data': [[x, y, (x + y) % 256] for x in range(33) for y in range(138)],
             'captured_at': time.time()}
    started = time.perf_counter()
    with patch('web.automation_diagnostics.request_controller_frame', return_value=frame):
        content, _, _ = diagnostic.frame_png()
    encoding = time.perf_counter() - started
    assert len(content) < 65536
    assert projection < 2 and idle < .5 and encoding < .25, (projection, idle, encoding)
    print(f'500 diagnostic reads+serialization {projection:.4f}s; 10000 idle helper calls {idle:.4f}s; 33x138 PNG projection/encoding {encoding:.4f}s')


@pytest.mark.parametrize('fail_publication', [False, True])
def test_production_controller_loop_serves_requested_existing_frame(tmp_path, monkeypatch, fail_publication):
    """The shipped loop calls readback; helper-only tests cannot establish that."""
    import sys
    if 'spidev' not in sys.modules:
        monkeypatch.setitem(sys.modules, 'spidev', SimpleNamespace(SpiDev=object))
    import drivers.multi_device
    import scripts.start_server as server
    from drivers.frame_codec import decode_frame_data

    controller = PreviewLEDController(33, 138)
    controller.close = lambda: None
    manager = AnimationManager(controller, auto_start=False)
    manager.set_output_brightness(0)
    manager._launch_animation_loop = lambda: None
    from web.starter_looks import get_starter
    assert manager.start_scene(get_starter('aurora')['scene'])
    manager.current_frame_data = [[8, 16, 32] for _ in range(33 * 138)]
    monkeypatch.setattr(drivers.multi_device, 'MultiDeviceLEDController', lambda **kwargs: controller)
    monkeypatch.setattr(server, 'AnimationManager', lambda *args, **kwargs: manager)
    channel = FileControlChannel(tmp_path / 'control.json', tmp_path / 'status.json')
    request_id = '12345678-abcd-4123-8123-123456789abc'
    request_path = channel.control_path.parent / 'frame-snapshot' / 'request.json'
    writes, failures = [], []
    original_write = FileControlChannel._atomic_write
    def write(channel, path, payload):
        if fail_publication and path.name == 'response.json' and path.parent.name == 'frame-snapshot':
            failures.append(path)
            raise OSError('optional snapshot storage unavailable')
        writes.append(path)
        return original_write(channel, path, payload)
    monkeypatch.setattr(FileControlChannel, '_atomic_write', write)
    virtual_time = [0.]
    def monotonic():
        virtual_time[0] += .3
        return virtual_time[0]
    monkeypatch.setattr(server.time, 'monotonic', monotonic)
    iterations = []
    def next_iteration(_seconds):
        iterations.append(True)
        if len(iterations) == 1:
            status = channel.read_status()
            channel._atomic_write(request_path, {
                'schema': 'ledgrid.controller-frame-request', 'schema_version': 1,
                'request_id': request_id, 'controller_session_id': status['controller_session_id'],
                'expires_at': time.time() + 2,
            })
        elif fail_publication:
            assert failures and not request_path.with_name('response.json').exists()
            assert manager.is_running  # Publication failure did not stop playback.
            assert channel.read_status()['is_running'] is True
            assert len([path for path in writes if path == channel.status_path]) >= len(iterations)
            if len(iterations) >= 3:
                raise KeyboardInterrupt
        else:
            response = json.loads(request_path.with_name('response.json').read_text())
            assert response['request_id'] == request_id
            assert decode_frame_data(response['frame']['frame_data_encoded']) == manager.current_frame_data
            assert response['frame']['led_info'] == {'strip_count': 33, 'leds_per_strip': 138, 'total_leds': 4554}
            raise KeyboardInterrupt
    monkeypatch.setattr(server.time, 'sleep', next_iteration)
    args = SimpleNamespace(strips=33, leds_per_strip=138, saved_state_file=str(tmp_path / 'settings.json'),
                           presets_dir=str(tmp_path / 'presets'), controller_debug=False, spi_speed=20000000,
                           brightness=0, animation_speed_scale=.3, animations_dir='animation/plugins',
                           target_fps=150, control_file=str(channel.control_path), status_file=str(channel.status_path),
                           status_interval=0, poll_interval=.05)
    with patch.object(manager, 'get_current_frame', wraps=manager.get_current_frame) as readback:
        with pytest.raises(KeyboardInterrupt):
            server.run_controller_mode(args)
        assert readback.call_count == 1
    assert channel.poll_commands() == []
    assert not manager.is_running and manager.output_brightness == 0
