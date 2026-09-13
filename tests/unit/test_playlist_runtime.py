from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import time
import uuid

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
