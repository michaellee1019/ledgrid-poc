"""Exercise Nimbus input edges and the installed host Tetris path."""

from collections import deque
from datetime import datetime
from functools import partial
from pathlib import Path
import struct
from time import time
from types import SimpleNamespace

import numpy as np
import pytest

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.scene_runtime import ScenePresentationContext
from animation.libraries.scene_palettes import SCENE_PALETTE_IDS
from drivers import nimbus_gamepad
from ipc.playlist_runtime import PlaylistRunner
from ipc.runtime_control import ControllerCommandConflictError, controller_activation_coordinator
from scripts.start_server import handle_command, nimbus_command, poll_wall_composer
from web.starter_looks import get_starter
from web.wall_composer import WallComposer


def report(*pressed: int, axes=(0, 0, 0, 0)) -> bytes:
    data = bytearray(17)
    for offset in pressed:
        data[offset] = 1 if offset == 12 else 255
    struct.pack_into("4b", data, 13, *axes)
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



def test_menu_handoff_preserves_fresh_presses_and_clears_repeats(controller):
    reader, reports, clock = controller
    game = object()
    received = []

    class Menu:
        is_open = False
        momentary = False
        playback_revision = 0

        def open(self):
            self.is_open = True

        def close(self):
            self.is_open = False

        def press(self, button):
            received.append(button)

    menu = Menu()
    manager = SimpleNamespace(tetris_input_target=lambda **_options: game, is_running=True,
                              output_brightness=80, dispatch_tetris_input=lambda _actions: None)
    def poll():
        poll_wall_composer(manager, reader, menu)

    poll()

    # Menu and Left are in one HID report. Left belongs to the outgoing mode.
    # A separate press in the same device backlog belongs to the new menu.
    reports.extend((report(12, 3), report(3), report(3, 4), report(3)))
    poll()
    assert menu.is_open
    assert received == []

    clock[0] = .30
    poll()
    assert received == ["a"]
    clock[0] = .60
    poll()
    assert received == ["a"]

    reports.extend((report(), report(0)))
    poll()
    clock[0] = .90
    poll()
    assert received == ["a", "up", "up"]

    reports.extend((report(), report(12)))
    poll()
    assert not menu.is_open
    reports.extend((report(), report(12), report()))
    poll()
    assert menu.is_open




def test_menu_horizontal_hold_marks_fine_and_fast_repeats(controller):
    reader, reports, clock = controller
    target = object()

    def poll():
        return reader.poll(target, menu_buttons=True, navigating=True)

    assert poll() == ()
    reports.append(report(1))
    assert poll() == ("right",)
    clock[0] = .30
    assert poll() == ("repeat-right",)
    clock[0] = .80
    assert poll() == ("fast-right",)
    reports.extend((report(), report(1)))
    assert poll() == ("right",)


def test_closed_hud_dpad_edges_do_not_repeat(controller):
    reader, reports, clock = controller
    target = object()
    reader.poll(target, menu_buttons=True, browsing=True)
    reports.append(report(0, 1, 2, 3))
    assert reader.poll(target, menu_buttons=True, browsing=True) == ("up", "right", "down", "left")
    clock[0] = 1
    assert reader.poll(target, menu_buttons=True, browsing=True) == ()


def test_hud_reports_centered_shoulder_changes_without_stick_motion(controller):
    reader, reports, clock = controller
    target = object()

    def poll():
        return reader.poll(target, menu_buttons=True, navigating=True, tuning_sticks=True)

    assert poll() == ()
    reports.append(report(9))
    assert poll() == (nimbus_gamepad.StickBank(2),)
    clock[0] += 1.0
    assert poll() == ()
    reports.append(report(9, 11, axes=(0, 0, 0, 55)))
    assert poll() == (nimbus_gamepad.StickBank(10), nimbus_gamepad.StickMotion(3, 1, 10))
    reports.append(report(9, axes=(0, 0, 0, 55)))
    clock[0] += 1.0
    assert poll() == (nimbus_gamepad.StickBank(2),)
    reader.discard_pending()
    reports.append(report())
    assert poll() == (nimbus_gamepad.StickBank(0),)
    reports.append(report(8))
    assert reader.poll(target, menu_buttons=True, tuning_sticks=True) == ()
    assert reader.poll(target, menu_buttons=True, tuning_sticks=True, track_bank=True) == (
        nimbus_gamepad.StickBank(1),
    )
    reports.append(report())
    assert reader.poll(target, menu_buttons=True, tuning_sticks=True, track_bank=True) == (
        nimbus_gamepad.StickBank(0),
    )


def test_external_discard_cancels_held_repeats_even_with_same_target(controller):
    reader, reports, clock = controller
    menu = object()

    def poll():
        return reader.poll(menu, menu_buttons=True, navigating=True)

    assert poll() == ()
    reports.append(report(2))
    assert poll() == ("down",)
    reader.discard_pending()
    reports.extend((report(2, 4), report(2, 4, 12)))
    clock[0] = .50
    assert poll() == ()
    clock[0] = .80
    assert poll() == ()
    reports.extend((report(), report(4)))
    assert poll() == ("a",)


def test_first_menu_press_after_reconnect(controller):
    reader, reports, clock = controller
    target = object()
    reader.poll(target, menu_buttons=True)
    reports.append(b"")
    assert reader.poll(target, menu_buttons=True) == ("disconnect",)
    clock[0] = 1.01
    reports.append(report())
    assert reader.poll(target, menu_buttons=True) == ()
    reports.extend((report(12), report()))
    assert reader.poll(target, menu_buttons=True) == ("menu",)



@pytest.mark.parametrize("component_id", ("flow_field_silk", "lava_lamp"))
def test_dpad_repaints_each_palette_at_the_same_source_tick(controller, tmp_path, component_id):
    reader, reports, _clock = controller
    wall = PreviewLEDController(33, 138)
    sent = []
    wall.set_all_pixels = lambda pixels: sent.append(pixels.copy())
    manager = AnimationManager(wall, auto_start=False)
    manager.set_output_brightness(80)
    manager._launch_animation_loop = lambda: setattr(manager, "is_running", True)
    owner = controller_activation_coordinator(manager)
    menu = WallComposer(manager, owner, PlaylistRunner(manager, owner),
                        partial(nimbus_command, manager, owner, reader),
                        Path(__file__).resolve().parents[2], tmp_path, tmp_path)
    scene = get_starter("aurora")["scene"]
    scene["animation"].update(component_id=component_id, parameters={})
    scene["look"]["palette_id"] = "neutral"
    scene["background"]["parameters"]["gain"] = 0.0
    assert handle_command(manager, "start_scene", {"scene": scene})
    elapsed = 1.0
    manager._scene_v2_monotonic_elapsed = lambda: elapsed
    wall_time = datetime.now().astimezone()
    runtime = manager._canonical_receiver_runtime
    instance = runtime._runtime._animation.instance

    def frame():
        return manager.render_scene_v2_presentation(runtime, manager._canonical_receiver_scene,
                                                     monotonic_elapsed=elapsed, wall_time=wall_time)

    first = frame().pixels.copy()
    simulation = (instance.get_runtime_stats()["physics_steps"], instance.x.copy()) if component_id == "lava_lamp" else (
        instance._last_sim_tick, instance.filaments.copy())
    appearances = {first.tobytes()}
    poll_wall_composer(manager, reader, menu)
    for offset, order in ((1, SCENE_PALETTE_IDS[1:]), (3, SCENE_PALETTE_IDS[-2::-1])):
        for expected in order:
            reports.extend((report(offset), report(offset), report()))
            poll_wall_composer(manager, reader, menu)
            assert manager.get_scene_state()["look"]["palette_id"] == expected
            updated = frame()
            assert updated.changed
            manager._present_frame(updated.pixels.copy(), None, False, False)
            appearance = sent[-1].tobytes()
            if offset == 1:
                assert appearance not in appearances, expected
                appearances.add(appearance)
            assert not frame().changed
    np.testing.assert_array_equal(frame().pixels, first)
    step, positions = (instance.get_runtime_stats()["physics_steps"], instance.x) if component_id == "lava_lamp" else (
        instance._last_sim_tick, instance.filaments)
    assert step == simulation[0]
    np.testing.assert_array_equal(positions, simulation[1])
    assert manager._canonical_receiver_runtime is runtime
    assert runtime._runtime._animation.instance is instance and menu.card() is None


def test_physical_up_tunes_r1_animation_and_r1_r2_speed_with_momentary_hud(controller, tmp_path):
    reader, reports, _clock = controller
    manager = AnimationManager(PreviewLEDController(33, 138), auto_start=False)
    manager.set_output_brightness(80)
    manager._launch_animation_loop = lambda: setattr(manager, "is_running", True)
    owner = controller_activation_coordinator(manager)
    menu = WallComposer(manager, owner, PlaylistRunner(manager, owner),
                        partial(nimbus_command, manager, owner, reader),
                        Path(__file__).resolve().parents[2], tmp_path, tmp_path)
    assert handle_command(manager, "start_scene", {"scene": get_starter("aurora")["scene"]})
    before = manager.get_scene_state()
    poll_wall_composer(manager, reader, menu)

    reports.append(report(9))
    poll_wall_composer(manager, reader, menu)
    assert menu.momentary and len(menu.hud_sections()) == 5
    assert menu.hud_sections()[0][0].shoulder_bank == 2
    assert manager.get_scene_state() == before

    # The paired Nimbus reports Up as positive on both vertical axes.
    reports.extend((report(9), report(9, axes=(0, 63, 0, 0)), report(9)))
    poll_wall_composer(manager, reader, menu)
    tuned = manager.get_scene_state()
    assert tuned["animation"]["parameters"]["curtain_density"] > before["animation"]["parameters"]["curtain_density"]
    assert tuned["look"] == before["look"]

    # Hold the right bumper (9) and right trigger (11).
    reports.extend((report(9, 11), report(9, 11, axes=(0, 0, 0, 54)), report(9, 11)))
    poll_wall_composer(manager, reader, menu)
    faster = manager.get_scene_state()
    assert faster["look"] == {**before["look"], "pace": .53}
    assert faster["animation"] == tuned["animation"]
    reports.append(report())
    poll_wall_composer(manager, reader, menu)
    assert not menu.is_open and menu.card() is None


def test_sticks_decode_signed_axes_and_limit_repeats(controller):
    reader, reports, clock = controller
    motion = nimbus_gamepad.StickMotion
    target = object()

    def poll():
        return reader.poll(target, menu_buttons=True, tuning_sticks=True)

    assert poll() == ()
    reports.append(report(axes=(18, -14, 13, 19)))
    assert poll() == ()
    reports.append(report(axes=(-110, -52, 30, 70)))
    assert poll() == (motion(0, -2), motion(3, 1))
    clock[0] = .30
    assert poll() == (motion(0, -2, repeated=True), motion(3, 1, repeated=True))
    reports.append(report(axes=(0, 0, 115, 0)))
    clock[0] = .55
    assert poll() == (motion(2, 2),)
    reports.extend((report(), report(axes=(0, 110, 110, 0))))
    assert poll() == (motion(1, 2), motion(2, 2))
    reports.append(report())
    clock[0] = .90
    assert poll() == ()



@pytest.mark.parametrize("bank", range(16))
def test_shoulder_chords_select_all_banks_on_all_axes(controller, bank):
    reader, reports, _clock = controller
    target = object()
    offsets = tuple(8 + bit for bit in range(4) if bank & (1 << bit))
    reader.poll(target, tuning_sticks=True)
    reports.append(report(*offsets))
    assert reader.poll(target, tuning_sticks=True) == ()
    reports.append(report(*offsets, axes=(120, 120, -120, -120)))
    assert reader.poll(target, tuning_sticks=True) == tuple(
        nimbus_gamepad.StickMotion(axis, step, bank)
        for axis, step in enumerate((2, 2, -2, -2))
    )


def test_bank_change_cancels_repeat_until_axis_recenters(controller):
    reader, reports, clock = controller
    target = object()
    motion = nimbus_gamepad.StickMotion

    def poll():
        return reader.poll(target, tuning_sticks=True)

    assert poll() == ()
    reports.append(report(9, axes=(100, 0, 0, 0)))
    assert poll() == (motion(0, 2, 2),)
    clock[0] = .3
    assert poll() == (motion(0, 2, 2, True),)
    reports.append(report(9, 11, axes=(100, 0, 0, 0)))
    clock[0] = .6
    assert poll() == ()
    reports.extend((report(9, 11), report(9, 11, axes=(100, 0, 0, 0))))
    assert poll() == (motion(0, 2, 10),)
    reports.append(report(axes=(100, 0, 0, 0)))
    clock[0] = .9
    assert poll() == ()


def test_shoulder_pressure_has_hysteresis_and_drops_stale_bank_motion(controller):
    reader, reports, _clock = controller
    target = object()
    motion = nimbus_gamepad.StickMotion
    reader.poll(target, tuning_sticks=True)
    soft_hold = bytearray(report(axes=(0, 100, 0, 0)))
    soft_hold[9] = 20
    reports.extend((report(9), bytes(soft_hold)))
    assert reader.poll(target, tuning_sticks=True) == (motion(1, 2, 2),)
    reports.extend((report(9), report(9, axes=(100, 0, 0, 0)), report()))
    assert reader.poll(target, tuning_sticks=True) == ()


def test_sticks_recenter_after_stale_input_and_reconnect(controller):
    reader, reports, clock = controller
    target = object()
    gesture = report(axes=(0, 110, 0, 0))

    def poll():
        return reader.poll(target, menu_buttons=True, tuning_sticks=True)

    assert poll() == ()
    reports.append(gesture)
    assert poll() == (nimbus_gamepad.StickMotion(1, 2),)
    reader.discard_pending()
    reports.append(gesture)
    clock[0] = .30
    assert poll() == ()
    reports.append(gesture)
    clock[0] = .60
    assert poll() == ()
    reports.append(b"")
    assert poll() == ("disconnect",)
    clock[0] = 1.7
    reports.append(gesture)
    assert poll() == ()
    reports.append(gesture)
    assert poll() == ()
    reports.extend((report(), gesture))
    assert poll() == (nimbus_gamepad.StickMotion(1, 2),)



def test_sticks_held_in_menu_do_not_tune_when_it_closes(controller):
    reader, reports, clock = controller
    target = object()
    reader.poll(target, menu_buttons=True, navigating=True)
    reports.append(report(axes=(0, 100, 0, 0)))
    assert reader.poll(target, menu_buttons=True, navigating=True) == ()
    reader.handoff(target)
    reports.append(report(axes=(0, 100, 0, 0)))
    clock[0] = .5
    assert reader.poll(target, menu_buttons=True, tuning_sticks=True) == ()
    reports.extend((report(), report(axes=(0, 100, 0, 0))))
    assert reader.poll(target, menu_buttons=True, tuning_sticks=True) == (
        nimbus_gamepad.StickMotion(1, 2),
    )


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
    assert game.get_runtime_stats()["effective_render_fps"] == 150
    assert get_starter("human_avalanche_factory")["scene"]["animation"]["parameters"]["render_fps"] == 100
    assert "player_control" not in manager.get_scene_state()["animation"]["parameters"]
