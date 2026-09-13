"""History-independent idle work with durable activation integrity."""
from copy import deepcopy
from pathlib import Path
import uuid
from unittest.mock import patch

import pytest

from ipc.control_channel import FileControlChannel
from ipc.runtime_control import ControllerActivationCoordinator
from scripts.start_server import process_activation_commands
from tests.unit import test_runtime_activation_transaction as fixtures


@pytest.fixture
def setup(tmp_path):
    fixture = fixtures.RuntimeActivationTransactionTests()
    fixture.setUp()
    channel = FileControlChannel(str(tmp_path/'control.json'), str(tmp_path/'status.json'))
    manager, coordinator = fixture.coordinator(status_sink=channel.write_activation_status)
    return fixture, channel, manager, coordinator


def failed_history(fixture, channel, coordinator, count=431):
    template = fixture.command(coordinator)
    commands = []
    for index in range(count):
        command = deepcopy(template)
        command['activation_id'] = str(uuid.UUID(int=index+1))
        channel.enqueue_activation(command)
        status = coordinator._new_status(command)
        status.update(phase='failed', error='historical failure before mutation')
        channel.write_activation_status(status)
        commands.append(command)
    return commands


def settle(channel, coordinator):
    for _ in range(3):
        assert process_activation_commands(channel, coordinator) == 0


def test_431_history_idle_and_external_requests_scale_with_changed_ids(setup):
    fixture, channel, manager, coordinator = setup
    commands = failed_history(fixture, channel, coordinator)
    settle(channel, coordinator)
    with patch.object(channel, '_strict_json', wraps=channel._strict_json) as reads, patch.object(coordinator, 'get', wraps=coordinator.get) as gets:
        settle(channel, coordinator)
        assert reads.call_count == gets.call_count == 0
    external = FileControlChannel(str(channel.control_path), str(channel.status_path))
    target = commands[0]['activation_id']
    external.request_activation_cancel(target)
    external.request_activation_rollback(target, snapshot_id=str(uuid.uuid4()), expected_controller_session_id=coordinator.session_id, expected_controller_state_revision=coordinator.state_revision)
    with patch.object(coordinator, 'get', wraps=coordinator.get) as gets:
        assert process_activation_commands(channel, coordinator) == 0
        assert {call.args[0] for call in gets.call_args_list} == {target}
    assert channel.read_activation_cancel_result(target)['outcome'] == 'rejected'
    assert channel.read_activation_rollback_result(target)['outcome'] == 'rejected'
    command = fixture.command(coordinator)
    external.enqueue_activation(command)
    assert process_activation_commands(channel, coordinator) == 1
    mutations = manager.mutation_count
    settle(channel, coordinator)
    assert manager.mutation_count == mutations
    assert len(channel.list_activation_commands()) == 432
    assert len(list(channel.activation_status_path.glob('*.json'))) == 432


def test_evicted_active_history_late_requests_and_live_rollback(setup):
    fixture, channel, manager, coordinator = setup
    commands = []
    first_active = None
    for _ in range(70):
        command = fixture.command(coordinator)
        channel.enqueue_activation(command)
        assert process_activation_commands(channel, coordinator) == 1
        commands.append(command)
        if first_active is None:
            first_active = channel.read_activation_status(command['activation_id'])
    oldest, newest = commands[0]['activation_id'], commands[-1]['activation_id']
    assert coordinator.get(oldest) is None
    settle(channel, coordinator)
    channel.request_activation_cancel(oldest)
    channel.request_activation_rollback(oldest, snapshot_id=first_active['rollback']['snapshot_id'], expected_controller_session_id=coordinator.session_id, expected_controller_state_revision=coordinator.state_revision)
    mutations = manager.mutation_count
    assert process_activation_commands(channel, coordinator) == 0
    assert manager.mutation_count == mutations
    assert channel.read_activation_cancel_result(oldest)['outcome'] == 'rejected'
    assert channel.read_activation_rollback_result(oldest)['outcome'] == 'rejected'
    active = channel.read_activation_status(newest)
    channel.request_activation_rollback(newest, snapshot_id=active['rollback']['snapshot_id'], expected_controller_session_id=coordinator.session_id, expected_controller_state_revision=coordinator.state_revision)
    assert process_activation_commands(channel, coordinator) == 1
    assert channel.read_activation_rollback_result(newest)['outcome'] == 'succeeded'
    mutations = manager.mutation_count
    settle(channel, coordinator)
    assert manager.mutation_count == mutations


def test_same_channel_new_session_reconciles_without_replay(setup):
    fixture, channel, manager, coordinator = setup
    failed_history(fixture, channel, coordinator)
    command = fixture.command(coordinator)
    channel.enqueue_activation(command)
    assert process_activation_commands(channel, coordinator) == 1
    settle(channel, coordinator)
    restarted = ControllerActivationCoordinator(manager, status_sink=channel.write_activation_status)
    mutations = manager.mutation_count
    with patch.object(restarted, 'reconcile_durable_active', wraps=restarted.reconcile_durable_active) as reconcile:
        assert process_activation_commands(channel, restarted) == 0
        assert reconcile.call_count == 1
    settle(channel, restarted)
    assert manager.mutation_count == mutations
    assert channel.read_activation_status(command['activation_id'])['controller']['session_id'] == restarted.session_id


def test_publication_failure_remains_pending_until_sink_recovers(setup):
    fixture, channel, manager, coordinator = setup
    command = fixture.command(coordinator)
    channel.enqueue_activation(command)
    with patch.object(channel, '_atomic_write', side_effect=OSError('disk temporarily unavailable')):
        assert process_activation_commands(channel, coordinator) == 0
        assert coordinator.has_pending_publications(command['activation_id'])
        assert process_activation_commands(channel, coordinator) == 0
        assert command['activation_id'] in channel._activation_poll_pending
    assert process_activation_commands(channel, coordinator) == 0
    assert not coordinator.has_pending_publications(command['activation_id'])
    assert channel.read_activation_status(command['activation_id'])['phase'] == 'failed'
    assert manager.mutation_count == 0
    settle(channel, coordinator)
    with patch.object(channel, '_strict_json', wraps=channel._strict_json) as reads:
        assert process_activation_commands(channel, coordinator) == 0
        assert reads.call_count == 0


def test_atomic_publication_during_enumeration_is_seen_next_poll(setup):
    fixture, channel, manager, coordinator = setup
    failed_history(fixture, channel, coordinator, count=1)
    original = Path.glob
    injected = False
    command = fixture.command(coordinator)
    def racing_glob(path, pattern):
        nonlocal injected
        paths = list(original(path, pattern))
        if path == channel.activation_queue_path and not injected:
            injected = True
            channel.enqueue_activation(command)
        return iter(paths)
    with patch.object(Path, 'glob', racing_glob):
        assert process_activation_commands(channel, coordinator) == 0
    assert injected
    assert process_activation_commands(channel, coordinator) == 1
    mutations = manager.mutation_count
    settle(channel, coordinator)
    assert manager.mutation_count == mutations


def test_changed_malformed_command_fails_closed_and_is_retried(setup):
    fixture, channel, manager, coordinator = setup
    command = failed_history(fixture, channel, coordinator, count=1)[0]
    settle(channel, coordinator)
    path = channel.activation_command_path(command['activation_id'])
    temporary = path.with_suffix('.tmp')
    temporary.write_text('{bad json')
    temporary.replace(path)
    for _ in range(2):
        with pytest.raises(ValueError, match='malformed'):
            process_activation_commands(channel, coordinator)
    channel._atomic_write(path, command)
    settle(channel, coordinator)
    assert manager.mutation_count == 0


def test_failed_publication_for_older_receipt_is_retried_without_file_change(setup):
    fixture, channel, manager, coordinator = setup
    first = fixture.command(coordinator)
    channel.enqueue_activation(first)
    assert process_activation_commands(channel, coordinator) == 1
    settle(channel, coordinator)
    second = fixture.command(coordinator)
    channel.enqueue_activation(second)
    write = channel._atomic_write
    def fail_old(path, payload):
        if path == channel.activation_status_file(first['activation_id']):
            raise OSError('old receipt temporarily unavailable')
        return write(path, payload)
    with patch.object(channel, '_atomic_write', fail_old):
        assert process_activation_commands(channel, coordinator) == 1
        assert first['activation_id'] in coordinator.pending_publication_ids()
        settle(channel, coordinator)
        assert first['activation_id'] in coordinator.pending_publication_ids()
    assert process_activation_commands(channel, coordinator) == 0
    assert not coordinator.pending_publication_ids()
    assert channel.read_activation_status(first['activation_id'])['rollback']['available'] is False
