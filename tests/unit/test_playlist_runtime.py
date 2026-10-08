from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import uuid

import pytest

from ipc.control_channel import FileControlChannel
from ipc.playlist_runtime import PlaylistRunner
from ipc.runtime_control import ControllerActivationCoordinator
from scripts.start_server import process_playlist_commands
from tests.unit.test_runtime_activation_transaction import (
    _FakeManager, _browser_scene, _catalog, _globals,
)
from ipc.scene_contract import browser_scene_to_host_scene, normalize_browser_scene_document
from animation.core.installation_profile_runtime import EMPTY_INSTALLATION_PROFILE_DIGEST
from web.local_control import LocalControlChannel


class Clock:
    def __init__(self):
        self.value = 100.0
    def __call__(self):
        return self.value
    def advance(self, seconds):
        self.value += seconds


def fixture():
    catalog = _catalog()
    document = normalize_browser_scene_document(
        _browser_scene(catalog, revision=1,
                       profile_digest=EMPTY_INSTALLATION_PROFILE_DIGEST, speed=.5),
        catalog=catalog, purpose="activation",
    )
    initial = browser_scene_to_host_scene(document, catalog=catalog)
    settings = _globals(revision=0, vibe_id="neutral", brightness=200,
                        speed=1.0, target_fps=120)
    manager = _FakeManager(catalog, initial, settings)
    coordinator = ControllerActivationCoordinator(manager, session_id="a" * 32)
    return manager, coordinator, initial


def command(coordinator, scenes, durations, *, request_id=None, run_id=None):
    return {
        "schema": "ledgrid.playlist-command", "schema_version": 1,
        "request_id": request_id or str(uuid.uuid4()),
        "run_id": run_id or str(uuid.uuid4()), "action": "start",
        "requested_at": 100.0, "playlist_id": str(uuid.uuid4()),
        "playlist_name": "Thirty minute demo",
        "expected_controller_session_id": coordinator.session_id,
        "expected_controller_state_revision": coordinator.state_revision,
        "entries": [
            {"entry_id": str(uuid.uuid4()), "label": f"Scene {index + 1}",
             "duration_seconds": duration, "scene": deepcopy(scene)}
            for index, (scene, duration) in enumerate(zip(scenes, durations))
        ],
    }


def test_thirty_entry_schedule_is_finite_and_restores_exact_starting_state():
    manager, coordinator, initial = fixture()
    clock = Clock()
    scenes = []
    for index in range(30):
        scene = deepcopy(initial)
        scene["background"]["parameter_overrides"]["speed"] = .5 + index / 100
        scenes.append(scene)
    runner = PlaylistRunner(manager, coordinator, clock=clock, wall_clock=clock)
    result = runner.start(command(coordinator, scenes, [60] * 30))
    assert result["phase"] == "running"
    assert sum(entry["duration_seconds"] for entry in command(coordinator, scenes, [60] * 30)["entries"]) == 1800
    for expected_index in range(1, 30):
        clock.advance(60)
        result = runner.advance()
        assert result["phase"] == "running"
        assert result["current_index"] == expected_index
    clock.advance(60)
    result = runner.advance()
    assert result["phase"] == "completed"
    assert manager.scene == initial
    assert manager.brightness == 200
    assert manager.target_fps == 120


def test_receiver_three_partial_transition_restores_before_single_retry(monkeypatch):
    import ipc.playlist_runtime as runtime

    manager, coordinator, initial = fixture()
    clock = Clock()
    second = deepcopy(initial)
    second["background"]["parameter_overrides"]["speed"] = .91
    runner = PlaylistRunner(manager, coordinator, clock=clock, wall_clock=clock)
    assert runner.start(command(coordinator, [initial, second], [1, 8]))["phase"] == "running"
    original_start = runtime.start_scene
    attempts = []

    def partial_first_start(manager, scene):
        attempts.append(deepcopy(manager.scene))
        clock.advance(3)
        if len(attempts) == 1:
            manager.scene = deepcopy(scene)
            manager.is_running = False
            raise RuntimeError(runtime.RETRYABLE_RECEIVER_STATUS_ERROR)
        return original_start(manager, scene)

    monkeypatch.setattr(runtime, "start_scene", partial_first_start)
    clock.advance(1)
    result = runner.advance()
    assert result["phase"] == "running"
    assert result["current_index"] == 1
    assert result["retry_count"] == 1
    assert result["last_recovery_error"] == runtime.RETRYABLE_RECEIVER_STATUS_ERROR
    assert len(attempts) == 2
    assert attempts[1] == initial  # The second attempt saw the exact prior Scene.
    assert manager.scene == second
    assert result["remaining_seconds"] == 8
    assert result["entry_deadline_at"] == clock() + 8


@pytest.mark.parametrize("failure", [
    "receiver 2 lacks intact protected-v8 display status",
    "receiver 3 reported display error or supersession",
    "complete host-full black barrier failed",
])
def test_other_transition_failures_restore_without_retry(monkeypatch, failure):
    import ipc.playlist_runtime as runtime

    manager, coordinator, initial = fixture()
    second = deepcopy(initial)
    second["background"]["parameter_overrides"]["speed"] = .91
    clock = Clock()
    runner = PlaylistRunner(manager, coordinator, clock=clock)
    runner.start(command(coordinator, [initial, second], [1, 8]))
    attempts = []

    def fail_after_mutation(manager, scene):
        attempts.append(1)
        manager.scene = deepcopy(scene)
        manager.is_running = False
        raise RuntimeError(failure)

    monkeypatch.setattr(runtime, "start_scene", fail_after_mutation)
    clock.advance(1)
    result = runner.advance()
    assert result["phase"] == "failed"
    assert result["error"] == failure
    assert result["retry_count"] == 0
    assert len(attempts) == 1
    assert manager.scene == initial
    assert manager.is_running is True


def test_receiver_three_retry_exhaustion_restores_prior_scene(monkeypatch):
    import ipc.playlist_runtime as runtime

    manager, coordinator, initial = fixture()
    second = deepcopy(initial)
    second["background"]["parameter_overrides"]["speed"] = .91
    clock = Clock()
    runner = PlaylistRunner(manager, coordinator, clock=clock)
    runner.start(command(coordinator, [initial, second], [1, 8]))
    attempts = []

    def fail_after_mutation(manager, scene):
        attempts.append(1)
        manager.scene = deepcopy(scene)
        raise RuntimeError(runtime.RETRYABLE_RECEIVER_STATUS_ERROR)

    monkeypatch.setattr(runtime, "start_scene", fail_after_mutation)
    clock.advance(1)
    result = runner.advance()
    assert result["phase"] == "failed"
    assert result["retry_count"] == 1
    assert len(attempts) == 2
    assert manager.scene == initial
    assert runner.advance()["phase"] == "failed"
    assert len(attempts) == 2


def test_failed_compensation_blocks_receiver_three_retry(monkeypatch):
    import ipc.playlist_runtime as runtime

    manager, coordinator, initial = fixture()
    second = deepcopy(initial)
    second["background"]["parameter_overrides"]["speed"] = .91
    clock = Clock()
    runner = PlaylistRunner(manager, coordinator, clock=clock)
    runner.start(command(coordinator, [initial, second], [1, 8]))
    attempts = []

    def fail_after_mutation(manager, scene):
        attempts.append(1)
        manager.scene = deepcopy(scene)
        raise RuntimeError(runtime.RETRYABLE_RECEIVER_STATUS_ERROR)

    def failed_restore(*_args, **_kwargs):
        raise RuntimeError("restored frame could not be proven")

    monkeypatch.setattr(runtime, "start_scene", fail_after_mutation)
    monkeypatch.setattr(coordinator, "restore_display_snapshot_verified", failed_restore)
    clock.advance(1)
    result = runner.advance()
    assert result["phase"] == "failed"
    assert "exact restoration failed" in result["error"]
    assert result["retry_count"] == 0
    assert len(attempts) == 1


def test_false_return_after_partial_mutation_restores_without_retry(monkeypatch):
    import ipc.playlist_runtime as runtime

    manager, coordinator, initial = fixture()
    second = deepcopy(initial)
    second["background"]["parameter_overrides"]["speed"] = .91
    clock = Clock()
    runner = PlaylistRunner(manager, coordinator, clock=clock)
    runner.start(command(coordinator, [initial, second], [1, 8]))
    attempts = []

    def reject_after_mutation(manager, scene):
        attempts.append(1)
        manager.scene = deepcopy(scene)
        manager.is_running = False
        return False

    monkeypatch.setattr(runtime, "start_scene", reject_after_mutation)
    clock.advance(1)
    result = runner.advance()
    assert result["phase"] == "failed"
    assert result["error"] == "controller rejected playlist entry 2"
    assert result["retry_count"] == 0
    assert len(attempts) == 1
    assert manager.scene == initial
    assert manager.is_running is True


def test_stale_preparation_marker_never_skips_restoration(monkeypatch):
    import ipc.playlist_runtime as runtime
    from ipc.scene_contract import canonical_json_sha256

    manager, coordinator, _initial = fixture()
    scene = {"schema": "ledgrid.scene.v2", "background": {}}
    manager._receiver_last_failure = {
        "operation": "receiver_hybrid_preparation",
        "phase": "before_presentation_takeover",
        "scene_digest": canonical_json_sha256(scene),
        "observed_at": time.time(),
    }
    restores = []
    original_restore = coordinator.restore_display_snapshot_verified

    def recorded_restore(snapshot, *, operation_id):
        restores.append(operation_id)
        return original_restore(snapshot, operation_id=operation_id)

    monkeypatch.setattr(runtime, "start_scene", lambda *_args: False)
    monkeypatch.setattr(coordinator, "restore_display_snapshot_verified", recorded_restore)
    monkeypatch.setattr(
        coordinator, "verify_display_snapshot_unchanged",
        lambda *_args: (_ for _ in ()).throw(AssertionError("stale marker was trusted")),
    )
    runner = PlaylistRunner(manager, coordinator)
    result = runner._start_entry_with_verified_recovery(
        scene, entry_index=0, expected_revision=coordinator.state_revision,
        session_id=coordinator.session_id, operation_id="stale-marker-test",
    )
    assert result["started"] is False
    assert result["retry_count"] == 0
    assert restores == ["stale-marker-test"]


def test_canonical_verified_restore_rejects_missing_receiver_proof(monkeypatch):
    manager, coordinator, initial = fixture()
    prior = coordinator.capture_display_snapshot()
    canonical = {"schema": "ledgrid.scene.v2"}
    snapshot = replace(
        prior, scene=canonical,
        active_identity={"component_identities": [{
            "slot_id": "background", "expected_payload_digest": "f" * 64,
        }]},
    )
    monkeypatch.setattr(coordinator, "restore_display_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(coordinator, "_snapshot", lambda **_kwargs: snapshot)
    monkeypatch.setattr(coordinator, "_receiver_activation_evidence", lambda *_args: None)
    with pytest.raises(RuntimeError, match="canonical rollback lacks exact receiver proof"):
        with coordinator.legacy_mutation_guard():
            coordinator.restore_display_snapshot_verified(snapshot, operation_id="test")


def test_verified_restore_rejects_nonmatching_snapshot(monkeypatch):
    manager, coordinator, initial = fixture()
    snapshot = coordinator.capture_display_snapshot()
    other = deepcopy(initial)
    other["background"]["parameter_overrides"]["speed"] = .91
    monkeypatch.setattr(coordinator, "restore_display_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        coordinator, "_snapshot",
        lambda **_kwargs: replace(snapshot, scene=other),
    )
    with pytest.raises(RuntimeError, match="post-rollback controller snapshot differs"):
        with coordinator.legacy_mutation_guard():
            coordinator.restore_display_snapshot_verified(snapshot, operation_id="test")


def test_first_playlist_entry_retries_after_verified_receiver_three_recovery(monkeypatch):
    import ipc.playlist_runtime as runtime

    manager, coordinator, initial = fixture()
    next_scene = deepcopy(initial)
    next_scene["background"]["parameter_overrides"]["speed"] = .91
    original_start = runtime.start_scene
    attempts = []

    def fail_once(manager, scene):
        attempts.append(deepcopy(manager.scene))
        if len(attempts) == 1:
            manager.scene = deepcopy(scene)
            raise RuntimeError(runtime.RETRYABLE_RECEIVER_STATUS_ERROR)
        return original_start(manager, scene)

    monkeypatch.setattr(runtime, "start_scene", fail_once)
    runner = PlaylistRunner(manager, coordinator)
    result = runner.start(command(coordinator, [next_scene], [8]))
    assert result["phase"] == "running"
    assert result["retry_count"] == 1
    assert attempts == [initial, initial]
    assert manager.scene == next_scene


def test_completion_requires_verified_restoration(monkeypatch):
    manager, coordinator, initial = fixture()
    next_scene = deepcopy(initial)
    next_scene["background"]["parameter_overrides"]["speed"] = .91
    clock = Clock()
    runner = PlaylistRunner(manager, coordinator, clock=clock)
    assert runner.start(command(coordinator, [next_scene], [1]))["phase"] == "running"
    calls = []

    def reject_unverified_completion(*_args, **_kwargs):
        calls.append(1)
        raise RuntimeError("restored display proof is absent")

    monkeypatch.setattr(coordinator, "restore_display_snapshot_verified", reject_unverified_completion)
    clock.advance(1)
    result = runner.advance()
    assert result["phase"] == "failed"
    assert result["error"] == "restored display proof is absent"
    assert calls == [1]


def test_manual_mutation_between_restore_and_retry_wins(monkeypatch):
    import contextlib
    import ipc.playlist_runtime as runtime

    manager, coordinator, initial = fixture()
    second = deepcopy(initial)
    second["background"]["parameter_overrides"]["speed"] = .91
    clock = Clock()
    runner = PlaylistRunner(manager, coordinator, clock=clock)
    runner.start(command(coordinator, [initial, second], [1, 8]))
    original_start = runtime.start_scene
    attempts = []

    def fail_once(manager, scene):
        attempts.append(1)
        if len(attempts) == 1:
            manager.scene = deepcopy(scene)
            raise RuntimeError(runtime.RETRYABLE_RECEIVER_STATUS_ERROR)
        return original_start(manager, scene)

    original_guard = coordinator.legacy_mutation_guard
    inject = True

    @contextlib.contextmanager
    def manual_after_guard(*args, **kwargs):
        nonlocal inject
        with original_guard(*args, **kwargs) as mutation:
            yield mutation
        if inject:
            inject = False
            with original_guard():
                manager.set_output_brightness(17)

    monkeypatch.setattr(runtime, "start_scene", fail_once)
    monkeypatch.setattr(coordinator, "legacy_mutation_guard", manual_after_guard)
    clock.advance(1)
    result = runner.advance()
    assert result["phase"] == "overridden"
    assert len(attempts) == 1
    assert manager.brightness == 17
    assert manager.scene == initial


def test_slow_transition_does_not_consume_entry_dwell(monkeypatch):
    import ipc.playlist_runtime as runtime

    original_start = runtime.start_scene
    for lateness in (0, .25):
        manager, coordinator, initial = fixture()
        clock = Clock()
        runner = PlaylistRunner(manager, coordinator, clock=clock,
                                wall_clock=lambda: clock() + 1000)
        monkeypatch.setattr(runtime, "start_scene", original_start)
        runner.start(command(coordinator, [initial, initial], [5, 5]))

        def slow_start(manager, scene):
            clock.advance(12)
            return original_start(manager, scene)

        monkeypatch.setattr(runtime, "start_scene", slow_start)
        clock.advance(5 + lateness)
        result = runner.advance()
        assert result["current_index"] == 1
        assert result["entry_started_at"] == clock() + 1000
        assert result["entry_deadline_at"] == 1122
        assert result["remaining_seconds"] == 5 - lateness
        assert runner.status()["remaining_seconds"] == 5 - lateness
        assert runner.advance()["phase"] == "running"
        clock.advance(5 - lateness)
        assert runner.advance()["phase"] == "completed"
        assert manager.scene == initial


def test_manual_mutation_wins_and_prevents_completion_restore():
    manager, coordinator, initial = fixture()
    clock = Clock()
    scene = deepcopy(initial)
    scene["background"]["parameter_overrides"]["speed"] = .9
    runner = PlaylistRunner(manager, coordinator, clock=clock, wall_clock=clock)
    assert runner.start(command(coordinator, [scene], [60]))["phase"] == "running"
    with coordinator.legacy_mutation_guard():
        manager.set_output_brightness(17)
    clock.advance(60)
    status = runner.advance()
    assert status["phase"] == "overridden"
    assert manager.brightness == 17
    assert manager.scene == scene


def test_explicit_stop_ends_ownership_without_restoring_the_starting_scene():
    manager, coordinator, initial = fixture()
    scene = deepcopy(initial)
    scene["background"]["parameter_overrides"]["speed"] = .88
    runner = PlaylistRunner(manager, coordinator)
    started = command(coordinator, [scene], [60])
    assert runner.start(started)["phase"] == "running"
    stopped = runner.stop({
        "schema": "ledgrid.playlist-command", "schema_version": 1,
        "request_id": str(uuid.uuid4()), "action": "stop",
        "requested_at": time.time(), "run_id": started["run_id"],
    })
    assert stopped["phase"] == "stopped"
    assert manager.scene == scene


def test_competing_start_cannot_create_second_runner():
    manager, coordinator, initial = fixture()
    runner = PlaylistRunner(manager, coordinator)
    first = command(coordinator, [initial], [60])
    second = command(coordinator, [initial], [60])
    assert runner.start(first)["phase"] == "running"
    rejected = runner.start(second)
    assert rejected["phase"] == "rejected"
    assert rejected["run_id"] == second["run_id"]
    assert runner.status()["run_id"] == first["run_id"]


def test_file_queue_rejects_stale_session_and_does_not_reparse_idle_history():
    manager, coordinator, initial = fixture()
    with tempfile.TemporaryDirectory() as directory:
        channel = FileControlChannel(str(Path(directory) / "control.json"),
                                     str(Path(directory) / "status.json"))
        stale = command(coordinator, [initial], [60])
        channel.enqueue_playlist_command(stale)
        restarted = ControllerActivationCoordinator(manager, session_id="b" * 32)
        runner = PlaylistRunner(manager, restarted,
                                status_sink=channel.write_playlist_current_status)
        channel.write_playlist_current_status(runner.status())
        assert process_playlist_commands(channel, runner) == 1
        assert channel.read_playlist_request_status(stale["request_id"])["phase"] == "rejected"
        original = channel.read_playlist_command
        channel.read_playlist_command = lambda _request_id: (_ for _ in ()).throw(
            AssertionError("idle poll reparsed playlist history")
        )
        assert process_playlist_commands(channel, runner) == 0
        channel.read_playlist_command = original
        assert channel.read_playlist_current_status()["phase"] == "idle"


def test_local_channel_progresses_without_status_reads_or_browser_polling():
    manager, _coordinator, initial = fixture()
    channel = LocalControlChannel(manager)
    scene = deepcopy(initial)
    scene["background"]["parameter_overrides"]["speed"] = .91
    payload = command(channel.activation_coordinator, [scene, initial], [.05, .05])
    channel.enqueue_playlist_command(payload)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and channel.playlist_runner.status()["phase"] == "running":
        time.sleep(.02)
    assert channel.playlist_runner.status()["phase"] == "completed"
    assert manager.scene == initial


def test_playlist_definitions_save_reopen_and_edit_separately_from_execution():
    from web.composer_playlist_store import ComposerPlaylistStore
    _manager, _coordinator, scene = fixture()
    with tempfile.TemporaryDirectory() as directory:
        store = ComposerPlaylistStore(Path(directory) / "definitions.json")
        definition = {
            "name": "Thirty minute demo",
            "entries": [
                {"entry_id": str(uuid.uuid4()), "label": f"Scene {index + 1}",
                 "duration_seconds": 60, "scene": deepcopy(scene)}
                for index in range(30)
            ],
        }
        saved = store.save(definition)
        assert store.list() == [{"id": saved["id"], "name": "Thirty minute demo",
                                 "entry_count": 30, "total_duration_seconds": 1800.0}]
        reopened = ComposerPlaylistStore(Path(directory) / "definitions.json").get(saved["id"])
        reopened["entries"][0]["duration_seconds"] = 90
        updated = store.save(reopened, playlist_id=saved["id"])
        assert updated["entries"][0]["duration_seconds"] == 90.0
        assert store.list()[0]["total_duration_seconds"] == 1830.0


def test_malformed_and_unavailable_entries_fail_before_any_playlist_mutation():
    manager, coordinator, initial = fixture()
    runner = PlaylistRunner(manager, coordinator)
    malformed = command(coordinator, [initial], [60])
    malformed["entries"][0]["duration_seconds"] = 0
    try:
        runner.start(malformed)
    except ValueError as exc:
        assert "duration" in str(exc)
    else:
        raise AssertionError("malformed playlist was accepted")
    unavailable = command(coordinator, [initial], [60])
    unavailable["entries"][0]["scene"]["background"]["plugin_id"] = "missing"
    unavailable["entries"][0]["scene"]["background"]["component_id"] = "missing"
    status = runner.start(unavailable)
    assert status["phase"] == "rejected"
    assert manager.scene == initial
    assert coordinator.state_revision == 0


def test_composer_api_saves_reopens_and_enqueues_an_immutable_snapshot():
    from tests.unit.test_composer_slice import _PreviewManager
    from web.app import AnimationWebInterface
    from web.composer_playlist_store import ComposerPlaylistStore

    class Channel:
        def __init__(self):
            self.commands = []
            self.request_status = {}
        def send_command(self, action, **data):
            return {"action": action, "data": data}
        def read_status(self):
            return {"controller_session_id": "c" * 32,
                    "controller_state_revision": 4}
        def enqueue_playlist_command(self, value):
            self.commands.append(deepcopy(value)); return value
        def read_playlist_request_status(self, request_id):
            return self.request_status.get(request_id)
        def read_playlist_current_status(self):
            return None

    channel = Channel()
    with tempfile.TemporaryDirectory() as directory:
        interface = AnimationWebInterface(channel, _PreviewManager(), local_mode=True)
        interface.composer_playlists = ComposerPlaylistStore(Path(directory) / "playlists.json")
        interface._validated_browser_activation_scene = lambda scene: ({"document": True}, {"host": deepcopy(scene)})
        client = interface.app.test_client()
        definition = {"name": "Demo", "entries": [{
            "entry_id": str(uuid.uuid4()), "label": "Aurora", "duration_seconds": 60,
            "scene": {"schema": "browser-fixture", "value": 1},
        }]}
        saved_response = client.post("/api/composer/playlists", json=definition)
        assert saved_response.status_code == 200
        saved = saved_response.get_json()["playlist"]
        assert client.get(f"/api/composer/playlists/{saved['id']}").get_json()["playlist"] == saved
        request_id = str(uuid.uuid4())
        started = client.post("/api/composer/playlists/run", json={"playlist_id": saved["id"]},
                              headers={"Idempotency-Key": request_id})
        assert started.status_code == 202
        assert len(channel.commands) == 1
        queued = channel.commands[0]
        assert queued["request_id"] == request_id
        assert queued["expected_controller_state_revision"] == 4
        assert queued["entries"][0]["scene"] == {"host": {"schema": "browser-fixture", "value": 1}}
        # Editing the durable definition after Start cannot mutate the queued run.
        changed = deepcopy(saved); changed["entries"][0]["duration_seconds"] = 90
        assert client.put(f"/api/composer/playlists/{saved['id']}", json=changed).status_code == 200
        assert queued["entries"][0]["duration_seconds"] == 60.0


def test_file_controller_path_transitions_and_completes_without_web_status_reads():
    manager, coordinator, initial = fixture()
    clock = Clock()
    second = deepcopy(initial)
    second["background"]["parameter_overrides"]["speed"] = .77
    with tempfile.TemporaryDirectory() as directory:
        channel = FileControlChannel(str(Path(directory) / "control.json"),
                                     str(Path(directory) / "status.json"))
        runner = PlaylistRunner(manager, coordinator, clock=clock, wall_clock=clock,
                                status_sink=channel.write_playlist_current_status)
        queued = command(coordinator, [initial, second], [1, 1])
        channel.enqueue_playlist_command(queued)
        assert process_playlist_commands(channel, runner) == 1
        assert channel.read_playlist_current_status()["current_index"] == 0
        clock.advance(1)
        assert runner.advance()["current_index"] == 1
        assert manager.scene == second
        clock.advance(1)
        assert runner.advance()["phase"] == "completed"
        assert manager.scene == initial


def test_manual_mutation_immediately_after_start_guard_is_not_adopted_as_playlist_ownership():
    import contextlib

    manager, coordinator, initial = fixture()
    clock = Clock()
    scene = deepcopy(initial)
    scene["background"]["parameter_overrides"]["speed"] = .87
    original_guard = coordinator.legacy_mutation_guard
    inject = True

    @contextlib.contextmanager
    def injecting_guard(*args, **kwargs):
        nonlocal inject
        with original_guard(*args, **kwargs) as mutation:
            yield mutation
        if inject:
            inject = False
            with original_guard():
                manager.set_output_brightness(17)

    coordinator.legacy_mutation_guard = injecting_guard
    runner = PlaylistRunner(manager, coordinator, clock=clock, wall_clock=clock)
    assert runner.start(command(coordinator, [scene], [60]))["phase"] == "running"
    clock.advance(60)
    status = runner.advance()
    assert status["phase"] == "overridden"
    assert manager.brightness == 17


def test_playlist_definition_transactions_serialize_between_store_instances():
    import threading

    from web.composer_playlist_store import ComposerPlaylistStore

    _manager, _coordinator, scene = fixture()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "definitions.json"
        active = 0
        maximum = 0
        counter_lock = threading.Lock()
        start = threading.Barrier(2)

        class SlowReadStore(ComposerPlaylistStore):
            def _records(self):
                nonlocal active, maximum
                with counter_lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(.05)
                try:
                    return super()._records()
                finally:
                    with counter_lock:
                        active -= 1

        stores = [SlowReadStore(path), SlowReadStore(path)]
        errors = []

        def save(index):
            try:
                start.wait()
                stores[index].save({"name": f"Playlist {index}", "entries": [{
                    "entry_id": str(uuid.uuid4()), "label": "Aurora",
                    "duration_seconds": 60, "scene": deepcopy(scene),
                }]})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=save, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        assert not errors
        assert not any(thread.is_alive() for thread in threads)
        assert maximum == 1
        assert {item["name"] for item in ComposerPlaylistStore(path).list()} == {
            "Playlist 0", "Playlist 1",
        }


def test_file_request_status_retry_reuses_successful_start_result_without_replay():
    manager, coordinator, initial = fixture()
    with tempfile.TemporaryDirectory() as directory:
        channel = FileControlChannel(str(Path(directory) / "control.json"),
                                     str(Path(directory) / "status.json"))
        queued = command(coordinator, [initial], [60])
        channel.enqueue_playlist_command(queued)
        runner = PlaylistRunner(manager, coordinator,
                                status_sink=channel.write_playlist_current_status)
        original_write = channel.write_playlist_request_status
        attempts = 0

        def fail_first(status):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("injected status publication failure")
            return original_write(status)

        channel.write_playlist_request_status = fail_first
        try:
            process_playlist_commands(channel, runner)
        except OSError:
            pass
        else:
            raise AssertionError("injected request status failure did not escape")
        assert runner.status()["phase"] == "running"
        assert process_playlist_commands(channel, runner) == 1
        status = channel.read_playlist_request_status(queued["request_id"])
        assert status["phase"] == "running"
        assert status["run_id"] == queued["run_id"]


def test_terminal_current_status_publication_retries_after_sink_recovers():
    manager, coordinator, initial = fixture()
    clock = Clock()
    published = []
    failing = False

    def sink(status):
        if failing:
            raise OSError("injected current publication failure")
        published.append(deepcopy(status))

    runner = PlaylistRunner(manager, coordinator, clock=clock, wall_clock=clock,
                            status_sink=sink)
    assert runner.start(command(coordinator, [initial], [1]))["phase"] == "running"
    failing = True
    clock.advance(1)
    assert runner.advance()["phase"] == "completed"
    assert published[-1]["phase"] == "running"
    failing = False
    assert runner.advance()["phase"] == "completed"
    assert published[-1]["phase"] == "completed"


def test_composer_stop_targets_the_just_accepted_run_instead_of_completed_status():
    from tests.unit.test_composer_slice import _PreviewManager
    from web.app import AnimationWebInterface
    from web.composer_playlist_store import ComposerPlaylistStore

    completed_run = str(uuid.uuid4())

    class Channel:
        def __init__(self):
            self.commands = []
        def send_command(self, action, **data):
            return {"action": action, "data": data}
        def read_status(self):
            return {"controller_session_id": "c" * 32,
                    "controller_state_revision": 4}
        def enqueue_playlist_command(self, value):
            self.commands.append(deepcopy(value)); return value
        def read_playlist_request_status(self, request_id):
            return None
        def read_playlist_current_status(self):
            return {"phase": "completed", "run_id": completed_run}

    channel = Channel()
    with tempfile.TemporaryDirectory() as directory:
        interface = AnimationWebInterface(channel, _PreviewManager(), local_mode=True)
        interface.composer_playlists = ComposerPlaylistStore(Path(directory) / "playlists.json")
        interface._validated_browser_activation_scene = lambda scene: ({"document": True}, deepcopy(scene))
        client = interface.app.test_client()
        saved = client.post("/api/composer/playlists", json={"name": "Next", "entries": [{
            "entry_id": str(uuid.uuid4()), "label": "Aurora", "duration_seconds": 60,
            "scene": {"schema": "fixture"},
        }]}).get_json()["playlist"]
        accepted = client.post("/api/composer/playlists/run", json={"playlist_id": saved["id"]}).get_json()["accepted"]
        response = client.post("/api/composer/playlists/stop", json={"run_id": accepted["run_id"]})
        assert response.status_code == 202
        assert channel.commands[-1]["action"] == "stop"
        assert channel.commands[-1]["run_id"] == accepted["run_id"]
        assert channel.commands[-1]["run_id"] != completed_run


def test_manual_mutation_immediately_after_transition_guard_stops_playlist_before_next_restore():
    import contextlib

    manager, coordinator, initial = fixture()
    clock = Clock()
    first = deepcopy(initial)
    first["background"]["parameter_overrides"]["speed"] = .83
    second = deepcopy(initial)
    second["background"]["parameter_overrides"]["speed"] = .93
    runner = PlaylistRunner(manager, coordinator, clock=clock, wall_clock=clock)
    assert runner.start(command(coordinator, [first, second], [1, 1]))["phase"] == "running"
    original_guard = coordinator.legacy_mutation_guard
    inject = True

    @contextlib.contextmanager
    def injecting_guard(*args, **kwargs):
        nonlocal inject
        with original_guard(*args, **kwargs) as mutation:
            yield mutation
        if inject:
            inject = False
            with original_guard():
                manager.set_output_brightness(17)

    coordinator.legacy_mutation_guard = injecting_guard
    clock.advance(1)
    assert runner.advance()["phase"] == "running"
    clock.advance(1)
    status = runner.advance()
    assert status["phase"] == "overridden"
    assert manager.brightness == 17
    assert manager.scene == second


def test_next_entry_comes_from_active_snapshot_and_clears_on_completion():
    manager, coordinator, initial = fixture()
    clock = Clock()
    published = []
    runner = PlaylistRunner(manager, coordinator, clock=clock, wall_clock=clock,
                            status_sink=published.append)
    requested = command(coordinator, [initial, initial], [1, 1])
    result = runner.start(requested)
    assert result["next_entry"]["label"] == "Scene 2"
    requested["entries"][1]["label"] = "Edited after start"
    assert runner.status()["next_entry"]["label"] == "Scene 2"
    assert published[-1]["next_entry"]["entry_id"] == result["next_entry"]["entry_id"]
    clock.advance(1)
    assert runner.advance()["next_entry"] is None
    clock.advance(1)
    assert runner.advance()["phase"] == "completed"
    assert runner.status()["next_entry"] is None
    assert manager.scene == initial
