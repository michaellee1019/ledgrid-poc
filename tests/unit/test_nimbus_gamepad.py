"""Exercise Nimbus input edges and the installed host Tetris path."""

from collections import deque
from datetime import datetime
from time import time
from types import SimpleNamespace

import numpy as np
import pytest

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.scene_runtime import ScenePresentationContext
from drivers import nimbus_gamepad
from ipc.runtime_control import ControllerCommandConflictError, controller_activation_coordinator
from scripts.start_server import handle_command
from web.starter_looks import get_starter


def report(*pressed: int) -> bytes:
    data = bytearray(17)
    for offset in pressed:
        data[offset] = 1 if offset == 12 else 255
    return bytes(data)


@pytest.fixture
def controller(monkeypatch):
    reports = deque()
    clock = [0.0]

    def read(_fd, _size):
        if not reports:
            raise BlockingIOError
        return reports.popleft()

    monkeypatch.setattr(nimbus_gamepad, "os", SimpleNamespace(
        O_RDONLY=0, O_NONBLOCK=1, open=lambda *_: 123, read=read, close=lambda _: None,
    ))
    return nimbus_gamepad.NimbusGamepad(clock=lambda: clock[0]), reports, clock


def test_buttons_emit_press_edges_and_only_movement_repeats(controller):
    reader, reports, clock = controller
    game = object()
    assert reader.poll(game) == ()
    reports.extend((report(12, 3, 4, 5, 6), report(12, 3, 4, 5, 6)))
    assert reader.poll(game) == ("toggle-player", "left", "rotate-right", "drop", "release")
    clock[0] = 0.20
    assert reader.poll(game) == ()
    clock[0] = 0.30
    assert reader.poll(game) == ("left",)
    clock[0] = 5.0
    assert reader.poll(game) == ("left",)

    reports.extend((report(), report(0, 1, 2), report()))
    assert reader.poll(game) == ("rotate-left", "right", "down")
    clock[0] = 6.0
    assert reader.poll(game) == ()


def test_nimbus_controls_the_live_game_and_releases_it_on_disconnect(controller):
    reader, reports, clock = controller
    manager = AnimationManager(PreviewLEDController(33, 138), auto_start=False)
    manager._launch_animation_loop = lambda: setattr(manager, "is_running", True)
    scene = get_starter("human_avalanche_factory")["scene"]
    assert handle_command(manager, "start_scene", {"scene": scene})
    coordinator = controller_activation_coordinator(manager)
    revision = coordinator.state_revision
    canonical = manager._canonical_receiver_scene
    runtime = manager._canonical_receiver_runtime
    game = runtime._runtime._animation.instance
    game._player_time = lambda: clock[0]

    def frame(elapsed):
        clock[0] = elapsed
        return runtime.render(ScenePresentationContext(canonical, elapsed / 10, datetime.now().astimezone()))

    manager.poll_tetris_gamepad(reader)
    reports.extend((report(12), report()))
    manager.poll_tetris_gamepad(reader)
    assert game.player_control
    before = frame(0.1).pixels.copy()
    frame(0.12)
    assert game.player_round_elapsed == pytest.approx(0.02)
    piece, = game.active_pieces
    previous_x = piece.x
    reports.extend((report(3), report()))
    manager.poll_tetris_gamepad(reader)
    assert not np.array_equal(frame(0.14).pixels, before)
    assert piece.x == previous_x - 1
    assert manager._canonical_receiver_scene is canonical
    assert coordinator.state_revision == revision

    reports.extend((report(6), report()))
    manager.poll_tetris_gamepad(reader)
    frame(0.16)
    assert len(game.active_pieces) == 3
    assert all(not inner.meta for inner in game.active_pieces)
    assert all(cell is None for row in game.board for cell in row)
    game.update_parameters({"tetromino_count": 1})
    assert len(game.active_pieces) == 3

    reports.append(b"")
    manager.poll_tetris_gamepad(reader)
    assert not game.player_control


def test_reports_queued_for_another_scene_are_discarded(controller):
    reader, reports, clock = controller
    first, second = object(), object()
    reader.poll(first)
    reports.append(report(3))
    assert reader.poll(first) == ("left",)

    reports.append(report(12, 3))
    clock[0] = 0.30
    assert reader.poll(second) == ()
    clock[0] = 0.60
    assert reader.poll(second) == ()
    reports.extend((report(), report(12)))
    assert reader.poll(second) == ("toggle-player",)


def test_explicit_play_rejects_stale_or_unavailable_input_before_switching(monkeypatch):
    manager = AnimationManager(PreviewLEDController(33, 138), auto_start=False)
    manager._launch_animation_loop = lambda: setattr(manager, "is_running", True)
    assert handle_command(manager, "start_scene", {"scene": get_starter("aurora")["scene"]})
    original = manager._canonical_receiver_runtime
    owner = controller_activation_coordinator(manager)
    guard = {
        "expected_controller_session_id": owner.session_id,
        "expected_controller_state_revision": owner.state_revision,
        "expires_at": time() + 5,
    }

    with pytest.raises(ValueError, match="Nimbus"):
        handle_command(manager, "start_tetris_player", {"_controller_guard": guard})
    assert manager._canonical_receiver_runtime is original
    assert manager.is_running

    connected = SimpleNamespace(connected=True)
    stale = {**guard, "expected_controller_state_revision": 0}
    with pytest.raises(ControllerCommandConflictError):
        handle_command(manager, "start_tetris_player", {"_controller_guard": stale}, gamepad=connected)
    assert manager._canonical_receiver_runtime is original

    with monkeypatch.context() as patch:
        def broken_prepare(_scene):
            raise AttributeError("input dispatch unavailable")

        patch.setattr(manager, "_prepare_canonical_receiver_scene", broken_prepare)
        with pytest.raises(ValueError, match="input dispatch unavailable"):
            handle_command(manager, "start_tetris_player", {"_controller_guard": guard}, gamepad=connected)
    assert manager._canonical_receiver_runtime is original
    assert manager.is_running
    guard["expected_controller_state_revision"] = owner.state_revision

    assert handle_command(manager, "start_tetris_player", {"_controller_guard": guard}, gamepad=connected)
    game = manager._canonical_receiver_runtime._runtime._animation.instance
    assert game.player_control
    assert len(game.active_pieces) == 1
    assert "player_control" not in manager.get_scene_state()["animation"]["parameters"]
