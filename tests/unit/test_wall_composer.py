"""Exercise the shared Nimbus menu, real Scene output, and local ownership."""

from copy import deepcopy
from datetime import datetime
from functools import partial
import multiprocessing
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from animation.core.wall_menu_frame import (
    ACCENT, CARD_HEIGHT, DISABLED_EDGE, DISABLED_TEXT, HEIGHT, MUTED, SHOULDER_HEIGHT, STICK_CARD_HEIGHT, TITLE_HEIGHT,
    WHITE, MenuCard, WallMenuFrame,
)
from drivers.nimbus_gamepad import ChordPress, StickBank, StickMotion
from ipc.playlist_runtime import PlaylistRunner
from ipc.runtime_control import controller_activation_coordinator
from ipc.scene_contract import normalize_composer_scene
from scripts.simulate_wall_composer import SimulatedWall
from scripts.start_server import handle_command, nimbus_command, poll_wall_composer, reconcile_wall_menu
from animation.core.manager import AnimationManager
from animation.core.scene_runtime import ScenePresentationContext
from web.composer_content import gallery_entries
from web.composer_final_preview import current_component_catalog, installed_plant_geometry
from web.numbered_preset_store import PRESET_COUNT, NumberedPresetStore
from web.scene_look_store import SceneLookStore
from web.starter_looks import get_starter
from web.wall_composer import STICK_BANK_COLORS, STICK_BANKS, STICK_FEEDBACK_SECONDS, WallComposer


ROOT = Path(__file__).resolve().parents[2]


def starter(name="aurora"):
    return get_starter(name)["scene"]


@pytest.fixture
def instrument(tmp_path):
    manager = AnimationManager(SimulatedWall(), auto_start=False)
    manager.set_output_brightness(80)
    manager._launch_animation_loop = lambda: setattr(manager, "is_running", True)
    owner = controller_activation_coordinator(manager)
    runner = PlaylistRunner(manager, owner)
    gamepad = SimpleNamespace(connected=True, shoulder_bank=0, poll=lambda *_args, **_kw: (),
                              discard_pending=lambda: None, handoff=lambda _target: None)
    menu = WallComposer(manager, owner, runner, partial(nimbus_command, manager, owner, gamepad),
                        ROOT, tmp_path, tmp_path)
    yield manager, owner, runner, gamepad, menu
    manager.stop_animation()


def inputs(manager, gamepad, menu, *buttons):
    gamepad.poll = lambda *_args, **_kw: buttons
    poll_wall_composer(manager, gamepad, menu)


def stick_sections(menu):
    return tuple(section for section in menu.hud_sections() if section[0].stick_axis is not None)


def stick_panels(renderer, menu):
    return renderer.render_sections(menu.hud_sections()).panels[1:]


def focus(menu, name):
    page, rows = menu._row()
    page.index = next(index for index, row in enumerate(rows) if row.name == name)


def test_shoulder_tiles_follow_each_button_while_a_picker_owns_input(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    menu.open()
    focus(menu, "Presets")
    inputs(manager, gamepad, menu, "a", "a")
    renderer = WallMenuFrame()
    revision = owner.state_revision
    for bank, lit in ((0, ()), (2, (2,)), (5, (0, 1)), (8, (3,)), (15, (0, 1, 2, 3)), (0, ())):
        inputs(manager, gamepad, menu, StickBank(bank))
        sections = menu.hud_sections()
        assert sections[0][0].shoulder_bank == bank and sections[-1][0].preset_selection == 0
        top = renderer.render_sections(sections).panels[0]
        for tile in range(4):
            color = STICK_BANK_COLORS[bank] if tile in lit else (27, 40, 55)
            assert tuple(top.panel[1 + tile * 8, -2]) == color
    assert owner.state_revision == revision


def test_numbered_preset_board_saves_overwrites_and_loads_complete_scenes(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_clock")})
    initial = manager.get_scene_state()
    menu.open()
    focus(menu, "Presets")
    inputs(manager, gamepad, menu, "a", "down", "a", "a")
    assert menu.error == "Preset 01 is empty"
    inputs(manager, gamepad, menu, "b", "b", "up", "a", "repeat-right")
    assert menu.card().preset_selection == 0
    inputs(manager, gamepad, menu, "right", "repeat-right", "left", "left", "up", "left")
    card = menu.card()
    assert card.preset_selection == 19 and len(card.preset_slots) == PRESET_COUNT and not any(card.preset_slots)
    board = WallMenuFrame().render_sections(menu.hud_sections()).panels[-1]
    tile = board.panel[25:32, ::-1][:, 48:57]
    assert tuple(tile[0, 0]) == ACCENT and np.any(np.all(tile == (5, 10, 19), axis=2))
    revision = owner.state_revision
    inputs(manager, gamepad, menu, "a")
    assert menu.card().value == "Slot 20" and owner.state_revision == revision
    assert menu.numbered_presets.load(20).scene == initial

    changed = manager.get_scene_state()
    changed["look"].update(palette_id="xmas", pace=.4)
    assert handle_command(manager, "tune_scene", {"scene": changed})
    inputs(manager, gamepad, menu, "a", "a")
    assert menu.card().preset_selection == 19 and menu.card().hint == "A WRITE"
    inputs(manager, gamepad, menu, "a")
    assert menu.numbered_presets.load(20).scene == changed

    assert handle_command(manager, "start_scene", {"scene": starter("aurora_conway")})
    inputs(manager, gamepad, menu, "a", "down", "a")
    assert menu.card().preset_selection == 19 and menu.card().preset_slots[-1]
    inputs(manager, gamepad, menu, "a")
    assert menu.card().value == "Slot 20" and manager.get_scene_state() == changed
    inputs(manager, gamepad, menu, "a", "a")
    assert menu.card().preset_selection == 19 and menu.card().hint == "A LOAD"


def test_failed_numbered_preset_replace_preserves_saved_slot(tmp_path, monkeypatch):
    catalog = current_component_catalog()
    store = NumberedPresetStore(tmp_path, catalog)
    opening = normalize_composer_scene({"origin": "composer", "scene": starter()}, catalog)
    changed = normalize_composer_scene({"origin": "composer", "scene": starter("aurora_clock")}, catalog)
    store.save(1, opening)
    store.save(20, changed)

    def fail_replace(self, target):
        raise OSError("write failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="write failed"):
        store.save(1, changed)
    reopened = NumberedPresetStore(tmp_path, catalog)
    assert reopened.load(1) == opening and reopened.load(20) == changed
    assert sorted(path.name for path in tmp_path.iterdir()) == ["01.json", "20.json"]
    with pytest.raises(ValueError, match="Choose preset"):
        reopened.load(21)


def test_menu_live_palette_keeps_runtime_and_parameter_changes_use_canonical_scene(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    runtime = manager._canonical_receiver_runtime
    instance = runtime._runtime._animation.instance
    revision = owner.state_revision
    inputs(manager, gamepad, menu, "menu", "a")
    assert menu.card().label == "Tune"
    inputs(manager, gamepad, menu, "a", "right")
    assert menu.card().value == "Ember"
    assert manager.get_scene_state()["look"]["palette_id"] == "ember"
    assert owner.state_revision == revision + 1
    assert runtime._runtime._animation.instance is instance
    assert manager._canonical_receiver_runtime is runtime

    focus(menu, "Animation")
    menu.press("a")
    focus(menu, "curtain density")
    menu.press("right")
    assert manager.get_scene_state()["animation"]["parameters"]["curtain_density"] == .19
    assert manager._canonical_receiver_runtime is runtime
    assert runtime._runtime._animation.instance is instance



def test_horizontal_navigation_steps_through_the_home_menu(instrument):
    manager, _owner, _runner, _gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    menu.open()
    assert menu.card().label == "Tune"
    menu.press("left")
    assert menu.card().label == "Browse"
    menu.press("right")
    assert menu.card().label == "Tune"
    menu.press("right")
    assert menu.card().label == "Playback"
    menu.press("left")
    assert menu.card().label == "Tune"



def test_numeric_hold_updates_live_and_undoes_the_whole_gesture(instrument):
    manager, owner, _runner, _gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    menu.open()
    menu.press("a")
    focus(menu, "Speed")
    assert menu.card().level == pytest.approx(.048)
    for button in ("right", "repeat-right", "fast-right"):
        menu.press(button)
    assert menu.card().value == "0.83"
    assert menu.card().hint == "Y UNDO"
    assert menu.card().level == pytest.approx(.083)
    menu.press("y")
    assert manager.get_scene_state()["look"]["pace"] == .48
    assert menu.card().hint == "Y REDO"
    menu.press("y")
    assert manager.get_scene_state()["look"]["pace"] == .83
    menu.press("left")
    assert manager.get_scene_state()["look"]["pace"] == .78
    menu.press("y")
    assert manager.get_scene_state()["look"]["pace"] == .83

    revision = owner.state_revision
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_clock")})
    assert reconcile_wall_menu(manager, _gamepad, menu, revision, scheduled=True)
    menu.press("y")
    assert manager.get_scene_state()["animation"]["component_id"] == "firefly_synchrony"



def test_horizontal_repeat_stays_with_the_original_setting(instrument):
    manager, _owner, _runner, _gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    menu.open()
    menu.press("a")
    focus(menu, "Speed")
    for button in ("right", "down", "fast-right"):
        menu.press(button)
    assert menu.card().label == "Scene light"
    assert manager.get_scene_state()["look"]["presentation_brightness"] == .64
    menu.press("right")
    assert manager.get_scene_state()["look"]["presentation_brightness"] == .65


def test_range_bar_changes_only_when_its_value_changes():
    renderer = WallMenuFrame()
    low = MenuCard("Tune", "Speed", "0.1", level=.05)
    high = MenuCard("Tune", "Speed", "1.6", level=.8)
    first = renderer.render(low)
    assert first is renderer.render(low)
    second = renderer.render(high)
    assert not np.array_equal(first.panel[:, 7:9], second.panel[:, 7:9])



def test_sticks_tune_current_scene_without_opening_menu(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    before = manager.get_scene_state()
    revision = owner.state_revision
    inputs(manager, gamepad, menu, StickMotion(0, 2), StickMotion(2, 1))
    after = manager.get_scene_state()
    assert owner.state_revision == revision + 1
    assert after["look"]["palette_id"] == "ember"
    assert after["animation"]["parameters"]["glow_intensity"] > before["animation"]["parameters"]["glow_intensity"]
    assert after["animation"]["component_id"] == before["animation"]["component_id"]
    assert after["widgets"] == before["widgets"]
    assert menu.card() is None and not menu.is_open

    inputs(manager, gamepad, menu, StickMotion(1, 2), StickMotion(3, -1))
    assert manager.get_scene_state()["look"] == {
        "palette_id": "ember", "pace": .48, "presentation_brightness": .59,
    }
    inputs(manager, gamepad, menu, StickMotion(3, 2, 10))
    menu.open()
    inputs(manager, gamepad, menu, StickMotion(1, 2))
    assert manager.get_scene_state()["look"]["pace"] == .58
    menu.close()
    manager.set_output_brightness(0)
    inputs(manager, gamepad, menu, StickMotion(1, 2))
    assert manager.get_scene_state()["look"]["pace"] == .58




def test_fireworks_stick_tuning_preserves_in_flight_shells_and_trails(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_conway")})
    runtime = manager._canonical_receiver_runtime
    animation = runtime._runtime._animation.instance
    manager._scene_v2_monotonic_elapsed = lambda: 1.1

    def frame(elapsed):
        return runtime.render(ScenePresentationContext(manager._canonical_receiver_scene, elapsed,
                                                       datetime.now().astimezone()))

    for index in range(1, 34):
        frame(index / 30)
    before = frame(1.1).pixels.copy()
    assert animation._sparks and animation._rockets
    old_rocket = animation._rockets[-1]
    old_spark = animation._sparks[-1]
    original_settings = old_rocket.settings
    trail_before = animation._trail.copy()
    long_trail_before = animation._long_trail.copy()
    inputs(manager, gamepad, menu, StickMotion(2, 2, 2))
    inputs(manager, gamepad, menu, StickMotion(0, 2, 2))
    assert runtime._runtime._animation.instance is animation
    assert animation._rockets[-1] is old_rocket and animation._sparks[-1] is old_spark
    assert old_rocket.settings is original_settings
    assert animation._shell_settings.shell_population == 68
    assert animation._shell_settings.trails == .64
    np.testing.assert_array_equal(animation._trail, trail_before)
    np.testing.assert_array_equal(animation._long_trail, long_trail_before)
    np.testing.assert_array_equal(frame(1.1).pixels, before)
    for index in range(34, 57):
        frame(index / 30)
    assert any(rocket.settings is animation._shell_settings for rocket in animation._rockets)
    assert any(spark.settings is original_settings for spark in animation._sparks)
    assert menu.card() is None


def test_dpad_browses_with_hud_closed_and_keeps_tetris_player_controls(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_conway")})
    before = manager.get_scene_state()
    runtime = manager._canonical_receiver_runtime
    inputs(manager, gamepad, menu, "left")
    assert manager.get_scene_state()["look"]["palette_id"] != before["look"]["palette_id"]
    assert manager._canonical_receiver_runtime is runtime
    inputs(manager, gamepad, menu, "right")
    assert manager.get_scene_state() == before
    inputs(manager, gamepad, menu, "down", StickMotion(0, 2))
    down = manager.get_scene_state()
    assert down["animation"]["component_id"] != before["animation"]["component_id"]
    assert down["look"] == before["look"] and menu.card() is None
    inputs(manager, gamepad, menu, "up")
    assert manager.get_scene_state()["animation"]["component_id"] == "fireworks"
    assert handle_command(manager, "start_scene", {"scene": starter("human_avalanche_factory")})
    inputs(manager, gamepad, menu, "down")
    assert manager.get_scene_state()["animation"]["component_id"] != "tetris"
    menu._run("start_tetris_player", {}, close=True)
    before = manager.get_scene_state()
    inputs(manager, gamepad, menu, "up", "left")
    assert manager.get_scene_state() == before
    assert manager.nimbus_game()[1] == "playing"


def test_hud_shoulder_stick_jumps_to_editable_binding(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_conway")})
    menu.open()
    before = manager.get_scene_state()
    revision = owner.state_revision
    inputs(manager, gamepad, menu, StickMotion(0, 2))
    assert owner.state_revision == revision
    inputs(manager, gamepad, menu, StickMotion(2, 1, 2))
    assert menu.card().label == "shell population"
    assert menu.card().value == "57"
    inputs(manager, gamepad, menu, StickMotion(2, 1, 2, repeated=True))
    assert menu.card().value == "68"
    inputs(manager, gamepad, menu, "y")
    assert manager.get_scene_state() == before
    assert menu.card().value == "46"
    inputs(manager, gamepad, menu, "right")
    assert menu.card().value == "57"
    inputs(manager, gamepad, menu, StickMotion(3, 1, 10))
    assert menu.card().label == "Speed"
    assert menu.card().value == "1.1"
    inputs(manager, gamepad, menu, "b")
    assert menu.is_open and menu.card().label == "Tune"


def test_hud_scene_launch_discards_stick_input_from_previous_scene(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_conway")})
    menu.open()
    menu._starters()
    discarded = []
    gamepad.discard_pending = lambda: discarded.append(True)
    inputs(manager, gamepad, menu, "a", StickMotion(0, 2, 2))
    assert manager.get_scene_state() == starter()
    assert menu.card().label == "Boreal Hush"
    assert discarded == [True]


def test_hud_stacks_four_axes_in_order_and_keeps_headings_scrolling(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_conway")})
    clock = [100.0]
    menu.clock = lambda: clock[0]
    menu.open()
    renderer = WallMenuFrame()
    original = manager.get_scene_state()
    inputs(manager, gamepad, menu, StickMotion(2, 1, 2))
    sections = stick_sections(menu)
    assert [card.stick_axis for card, _ in sections] == [1, 0, 3, 2]
    assert [card.label for card, _ in sections] == ["launch cadence", "trails", "gravity", "shell population"]
    _, left, _, right = sections
    assert left[0].label == "trails" and left[0].value == "0.46"
    assert right[0].label == "shell population" and right[0].value == "57"
    updated = manager.get_scene_state()
    assert updated["animation"]["parameters"] == {
        **original["animation"]["parameters"], "shell_population": 57,
    }
    stack = renderer.render_sections(menu.hud_sections())
    assert [panel.start for panel in stack.panels[1:]] == [
        HEIGHT - SHOULDER_HEIGHT - depth * STICK_CARD_HEIGHT for depth in (1, 2, 3, 4)
    ]
    for index, panel in enumerate(stack.panels[1:]):
        np.testing.assert_array_equal(panel.panel[16, -1], STICK_BANK_COLORS[2])
        edge = 0 if index < 2 else -1
        assert np.all(panel.panel[edge] == STICK_BANK_COLORS[2])
    lower = stack.panels[-1]
    safe = installed_plant_geometry(ROOT).safe[:, lower.start:lower.start + STICK_CARD_HEIGHT]
    assert not safe.all()
    assert np.all(np.any(lower.panel[~safe] != 0, axis=1))
    assert renderer.render_sections(menu.hud_sections()) is stack
    frame = np.full((33, 138, 3), 11, dtype=np.uint8)
    stack.apply(frame)
    for panel in stack.panels:
        np.testing.assert_array_equal(frame[:, panel.start:panel.start + panel.panel.shape[1]], panel.panel)

    clock[0] += 2.0
    inputs(manager, gamepad, menu, StickMotion(1, 1, 2))
    left, _, _, right = stick_sections(menu)
    assert left[0].label == "launch cadence" and left[1] == 8
    assert right[0].label == "shell population" and right[0].top_color == STICK_BANK_COLORS[2]
    clock[0] += 2.0
    left_before, _, _, right_before = stick_sections(menu)
    inputs(manager, gamepad, menu, StickMotion(1, 1, 2))
    left_after, _, _, right_after = stick_sections(menu)
    assert left_after[0].value != left_before[0].value and left_after[1] == left_before[1] == 16
    assert right_after[1] == right_before[1] == 16
    menu.press("right")
    assert stick_sections(menu)[0][1] == 16
    assert len(STICK_BANK_COLORS) == len(set(STICK_BANK_COLORS)) == 16
    menu.press("b")
    assert not stick_sections(menu)


def test_stick_feedback_pins_arrow_and_highlights_only_changed_parameter(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_conway")})
    clock = [100.0]
    menu.clock = lambda: clock[0]
    inputs(manager, gamepad, menu, StickBank(2))
    renderer = WallMenuFrame()

    def top_panel():
        return stick_panels(renderer, menu)[0].panel[:, ::-1]

    idle_down = top_panel().copy()
    clock[0] += .5
    idle_up = top_panel().copy()
    assert not np.array_equal(idle_down[1:4, 2:7], idle_up[1:4, 2:7])
    assert tuple(idle_down[1, 5]) == MUTED
    assert tuple(idle_up[1, 3]) == MUTED
    inputs(manager, gamepad, menu, StickMotion(1, 1, 2))
    edited = top_panel()
    assert tuple(edited[1, 3]) == WHITE
    assert np.any(np.all(edited[5:-1, 2:7] == WHITE, axis=2))
    assert [c.stick_changed for c, _ in stick_sections(menu)] == [True, False, False, False]

    clock[0] += STICK_FEEDBACK_SECONDS
    assert not stick_sections(menu)[0][0].stick_direction
    assert np.any(np.all(top_panel()[5:-1, 2:7] == ACCENT, axis=2))
    scene = manager.get_scene_state()
    scene["animation"]["parameters"]["launch_cadence"] = 4.0
    assert handle_command(manager, "tune_scene", {"scene": scene})
    revision = owner.state_revision
    inputs(manager, gamepad, menu, StickMotion(1, 2, 2))
    card, _ = stick_sections(menu)[0]
    assert card.stick_direction == 1 and not card.stick_changed
    assert owner.state_revision == revision
    inputs(manager, gamepad, menu, StickMotion(1, -1, 2), StickMotion(0, -1, 2))
    cards = [card for card, _ in stick_sections(menu)]
    assert [card.stick_direction for card in cards] == [-1, -1, 0, 0]
    assert [card.stick_changed for card in cards] == [True, True, False, False]
    assert tuple(top_panel()[1, 5]) == WHITE


def test_short_parameter_titles_scroll_and_unused_rows_only_show_dim_dashes(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_conway")})
    clock = [100.0]
    menu.clock = lambda: clock[0]
    inputs(manager, gamepad, menu, StickBank(10))
    renderer = WallMenuFrame()
    first = stick_panels(renderer, menu)
    clock[0] += 1.25
    later = stick_panels(renderer, menu)
    # Speed fits. Compare its text pixels separately from the cycling arrow.
    assert not np.array_equal(first[2].panel[5:-1, -7:-2], later[2].panel[5:-1, -7:-2])
    assert np.any(np.all(first[2].panel[5:-1, -7:-2] == ACCENT, axis=2))
    assert np.any(np.all(later[2].panel[5:-1, -7:-2] == ACCENT, axis=2))

    inputs(manager, gamepad, menu, StickMotion(1, 1, 10))
    after = stick_panels(renderer, menu)
    for index in (0, 1, 3):
        card, _ = stick_sections(menu)[index]
        assert (card.label, card.value, card.stick_direction) == ("", "--", 0)
        assert first[index] is later[index] is after[index]
        pixels = after[index].panel[:, ::-1]
        edge = 0 if index < 2 else -1
        assert np.all(pixels[1:-1, 0] == DISABLED_EDGE)
        assert np.all(pixels[edge] == DISABLED_EDGE)
        assert np.all(pixels[1:-1, 2:7] == 9)
        dashes = np.all(pixels == DISABLED_TEXT, axis=2)
        assert dashes.sum() == 6 and dashes[:, 10].sum() == 6


def test_shoulder_hold_opens_four_axes_and_release_hides_momentary_hud(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_conway")})
    original = manager.get_scene_state()
    revision = owner.state_revision
    inputs(manager, gamepad, menu, StickBank(2))
    assert menu.momentary and len(stick_sections(menu)) == 4
    assert owner.state_revision == revision
    inputs(manager, gamepad, menu, StickBank(10), StickMotion(3, 1, 10))
    assert menu.momentary and menu.card().label == "Speed"
    assert owner.state_revision == revision + 1
    before_release = manager.get_scene_state()
    inputs(manager, gamepad, menu, StickBank(0), "down")
    assert not menu.is_open and not menu.hud_sections()
    assert manager.get_scene_state() == before_release


def test_manual_hud_stays_open_and_bank_switch_does_not_edit_scene(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_conway")})
    original = manager.get_scene_state()
    revision = owner.state_revision
    menu.open()
    for bank, names in ((2, ("launch cadence", "trails", "gravity", "shell population")),
                        (10, ("", "", "Speed", "")),
                        (0, ("", "Palette", "Scene light", "burst size"))):
        inputs(manager, gamepad, menu, StickBank(bank))
        sections = stick_sections(menu)
        assert tuple(card.label for card, _ in sections) == names
        assert all(card.top_color == STICK_BANK_COLORS[bank] for card, _ in sections)
        assert manager.get_scene_state() == original and owner.state_revision == revision
    inputs(manager, gamepad, menu, StickBank(2), StickMotion(3, 1, 2))
    assert stick_sections(menu)[2][0].label == "gravity"
    assert manager.get_scene_state()["animation"]["parameters"]["gravity"] > .48
    inputs(manager, gamepad, menu, "b")
    inputs(manager, gamepad, menu, StickBank(0))
    assert not stick_sections(menu)


def test_animation_title_shows_for_one_second_and_stacks_above_a_held_bank(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_conway")})
    clock = [100.0]
    menu.clock = lambda: clock[0]
    inputs(manager, gamepad, menu, "down")
    title, = menu.hud_sections()
    label = manager.plugin_loader.get_plugin(manager.current_animation_name).ANIMATION_NAME
    assert title[0].label == label and title[0].section == "Animation title"
    inputs(manager, gamepad, menu, StickBank(2))
    stack = WallMenuFrame().render_sections(menu.hud_sections())
    assert len(stack.panels) == 6
    assert stack.panels[0].start == HEIGHT - SHOULDER_HEIGHT
    assert stack.panels[1].start == HEIGHT - SHOULDER_HEIGHT - TITLE_HEIGHT
    assert stack.panels[-1].start == HEIGHT - SHOULDER_HEIGHT - TITLE_HEIGHT - 4 * STICK_CARD_HEIGHT
    clock[0] = 100.999
    assert menu.hud_sections()[1][0] == title[0]
    clock[0] = 101.0
    assert len(menu.hud_sections()) == 5
    inputs(manager, gamepad, menu, StickBank(0), "right")
    assert not menu.hud_sections()


def test_shoulder_banks_tune_animation_plant_and_widget_axes_without_hud(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_clock")})
    original = manager.get_scene_state()
    banks = [menu._stick_bank(original, bank) for bank in range(16)]
    bindings = [slot for bank in banks for slot in bank if slot is not None]
    assert len(bindings) == len(set(bindings))
    for bank in range(16):
        trial = manager.get_scene_state()
        menu._apply_stick_motions(trial, [StickMotion(axis, 1, bank) for axis in range(4)])
        normalize_composer_scene({"origin": "composer", "scene": trial}, menu.catalog)

    revision = owner.state_revision
    inputs(manager, gamepad, menu, StickMotion(0, 1, 2), StickMotion(1, 1, 2))
    assert owner.state_revision == revision + 1
    updated = manager.get_scene_state()
    assert updated["look"] == original["look"]
    assert sum(updated["animation"]["parameters"][key] != value
               for key, value in original["animation"]["parameters"].items()) == 2

    inputs(manager, gamepad, menu, StickMotion(2, 1, 8), StickMotion(3, -1, 8))
    updated = manager.get_scene_state()
    assert updated["plants"]["effects"]["strengths"]["hue_shift"] == .1
    assert updated["background"] == original["background"]

    inputs(manager, gamepad, menu, StickMotion(0, 1, 4), StickMotion(1, 1, 4))
    placement = manager.get_scene_state()["widgets"][0]["placement"]
    assert placement["strip_translation"] == original["widgets"][0]["placement"]["strip_translation"] + 2
    assert placement["led_translation"] == original["widgets"][0]["placement"]["led_translation"] + 4
    assert not menu.is_open and menu.card() is None


def test_flow_field_silk_l2_r2_does_not_alias_other_banks(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["animation"].update(component_id="flow_field_silk", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    original = manager.get_scene_state()
    revision = owner.state_revision
    declared = [slot for bank in STICK_BANKS for slot in bank if slot is not None]
    resolved = [slot for bank in range(16) for slot in menu._stick_bank(original, bank) if slot is not None]
    assert len(declared) == len(set(declared))
    assert len(resolved) == len(set(resolved))
    menu.open()
    inputs(manager, gamepad, menu, StickBank(12), StickMotion(0, 2, 12), StickMotion(2, 2, 12))
    assert [(card.label, card.value) for card, _ in stick_sections(menu)] == [("", "--")] * 4
    assert manager.get_scene_state() == original and owner.state_revision == revision
    inputs(manager, gamepad, menu, StickBank(0))
    assert [card.label for card, _ in stick_sections(menu)] == ["", "Palette", "Scene light", "density"]


def test_base_palette_changes_once_per_flick(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    inputs(manager, gamepad, menu, StickMotion(0, 2))
    revision = owner.state_revision
    inputs(manager, gamepad, menu, StickMotion(0, 2, repeated=True))
    assert owner.state_revision == revision
    assert manager.get_scene_state()["look"]["palette_id"] == "ember"


def test_right_stick_steps_are_larger_than_menu_steps_and_clamp(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    opening = manager.get_scene_state()["animation"]["parameters"]["glow_intensity"]
    inputs(manager, gamepad, menu, StickMotion(2, 1))
    fine = manager.get_scene_state()["animation"]["parameters"]["glow_intensity"]
    inputs(manager, gamepad, menu, StickMotion(2, 2))
    coarse = manager.get_scene_state()["animation"]["parameters"]["glow_intensity"]
    definition = menu._descriptor(manager.get_scene_state()["animation"]).parameter_schema["glow_intensity"]
    step = (definition["max"] - definition["min"]) / 10
    assert fine - opening == pytest.approx(step)
    assert coarse - fine == pytest.approx(2 * step)
    for _ in range(10):
        inputs(manager, gamepad, menu, StickMotion(2, 2))
    assert manager.get_scene_state()["animation"]["parameters"]["glow_intensity"] == definition["max"]
    revision = owner.state_revision
    inputs(manager, gamepad, menu, StickMotion(2, 2))
    assert owner.state_revision == revision
    assert menu.card() is None


def test_stick_takeover_leaves_the_hud_hidden(instrument):
    manager, _owner, runner, gamepad, menu = instrument
    definition = menu.playlists.save({"name": "Test", "entries": [
        {"entry_id": str(uuid4()), "label": "Hush", "duration_seconds": 30, "scene": starter()}
    ]})
    menu._start_playlist(definition["id"])
    inputs(manager, gamepad, menu, StickMotion(3, 1, 10))
    assert runner.status()["phase"] == "overridden"
    assert manager.get_scene_state()["look"]["pace"] == .53
    assert menu.card() is None


def test_first_stick_edit_keeps_both_cards_visible_during_playlist_takeover(instrument):
    manager, _owner, runner, gamepad, menu = instrument
    scene = starter("aurora_conway")
    definition = menu.playlists.save({"name": "Test", "entries": [
        {"entry_id": str(uuid4()), "label": "Neon Crackle", "duration_seconds": 30, "scene": scene}
    ]})
    menu._start_playlist(definition["id"])
    menu.open()
    inputs(manager, gamepad, menu, StickMotion(1, 1, 2))
    assert runner.status()["phase"] == "overridden"
    (left, _), _, _, (right, _) = stick_sections(menu)
    assert (left.label, right.label) == ("launch cadence", "shell population")
    assert right.value == "46"


def test_sticks_do_not_tune_while_playing_or_wake_stopped_output(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("human_avalanche_factory")})
    inputs(manager, gamepad, menu, "a")
    assert manager.nimbus_game()[1] == "playing"
    revision = owner.state_revision
    inputs(manager, gamepad, menu, StickMotion(1, 2))
    assert owner.state_revision == revision
    assert handle_command(manager, "stop_scene", {})
    revision = owner.state_revision
    inputs(manager, gamepad, menu, StickMotion(3, 2))
    assert owner.state_revision == revision
    assert not manager.is_running


def test_flame_aim_uses_logical_wall_coordinates_and_rejects_stale_basis(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["animation"].update(component_id="flame_burst", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime = manager._canonical_receiver_runtime
    flame = runtime._runtime._animation.instance
    old_basis = manager.nimbus_point_input()[0]
    revision = owner.state_revision

    gamepad.shoulder_bank = 3
    inputs(manager, gamepad, menu, "up", "a", StickMotion(0, 2, 3), StickMotion(1, 2, 3))
    assert menu.card().marker == (18, 77)
    assert flame._pending_ignition is None
    inputs(manager, gamepad, menu, ChordPress("a", 3))
    assert flame._pending_ignition == pytest.approx((18 / 32 * 2 - 1, 77 / 137 * 2 - 1, 1))
    assert owner.state_revision == revision
    assert manager._canonical_receiver_runtime is runtime
    assert manager.nimbus_game() == (None, None)

    scene["look"]["palette_id"] = "ember"
    assert handle_command(manager, "start_scene", {"scene": scene})
    component_id, scene_digest = old_basis
    with pytest.raises(ValueError, match="Active Scene"):
        manager.dispatch_canonical_interaction(component_id, scene_digest, "primary", 18, 77)
    assert manager._canonical_receiver_runtime._runtime._animation.instance._pending_ignition is None


@pytest.mark.parametrize(("component_id", "field", "value", "target_y"), [
    ("flame_burst", "flare_size", .8, 77),
    ("fluid_tank", "flow_rate", 4, 69),
    ("lava_lamp", "interaction_strength", 2, 77),
])
def test_elemental_aim_matches_direct_gesture_and_tunes_in_place(instrument, component_id, field, value, target_y):
    from web.composer_final_preview import InstalledFinalSceneRuntime

    manager, owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["animation"].update(component_id=component_id, parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime = manager._canonical_receiver_runtime
    canonical = manager._canonical_receiver_scene
    fixed_time = datetime.fromisoformat("2026-11-13T19:00:00-05:00")
    reference = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    initial = ScenePresentationContext(canonical, 0, fixed_time)
    reference.render(initial)
    runtime.render(initial)

    gamepad.shoulder_bank = 3
    inputs(manager, gamepad, menu, StickMotion(0, 2, 3), StickMotion(1, 2, 3))
    assert menu.card().marker == (18, target_y)
    inputs(manager, gamepad, menu, ChordPress("a", 3))
    assert reference.dispatch_animation_interaction(canonical, "primary", 18, target_y)
    frame = ScenePresentationContext(canonical, .1, fixed_time)
    np.testing.assert_array_equal(runtime.render(frame).pixels, reference.render(frame).pixels)
    assert manager.nimbus_game() == (None, None)

    revision = owner.state_revision
    instance = runtime._runtime._animation.instance
    scene = manager.get_scene_state()
    scene["animation"]["parameters"][field] = value
    assert handle_command(manager, "start_scene", {"scene": scene})
    assert manager._canonical_receiver_runtime is runtime
    assert runtime._runtime._animation.instance is instance
    assert instance.params[field] == value and owner.state_revision == revision + 1


@pytest.mark.parametrize(("component_id", "target_y"), [
    ("cellular_tapestry", 69), ("frostwork", 77), ("reaction_diffusion_garden", 77),
    ("wind_in_the_reeds", 77), ("conway_life", 77), ("cyclic_reef", 77),
])
def test_growing_pattern_aim_reaches_existing_runtime(instrument, component_id, target_y):
    from web.composer_final_preview import InstalledFinalSceneRuntime

    manager, _owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["animation"].update(component_id=component_id, parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime = manager._canonical_receiver_runtime
    canonical = manager._canonical_receiver_scene
    fixed_time = datetime.fromisoformat("2026-11-13T19:00:00-05:00")
    initial = ScenePresentationContext(canonical, 0, fixed_time)
    runtime.render(initial)
    direct = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    direct.render(initial)
    gamepad.shoulder_bank = 3
    inputs(manager, gamepad, menu, StickMotion(0, 2, 3), StickMotion(1, 2, 3))
    assert menu.card().marker == (18, target_y)
    inputs(manager, gamepad, menu, ChordPress("a", 3))
    assert direct.dispatch_animation_interaction(canonical, "primary", 18, target_y)
    for step in range(1, 20):
        frame = ScenePresentationContext(canonical, step / 20, fixed_time)
        np.testing.assert_array_equal(runtime.render(frame).pixels, direct.render(frame).pixels)
    assert manager._canonical_receiver_runtime is runtime
    assert manager.get_scene_state() == canonical.scene


@pytest.mark.parametrize("component_id", [
    "ocean_waves", "rain_on_glass", "waterfall_veil", "tidal_bioluminescence", "plant_glow",
])
def test_weather_pulse_tracks_nimbus_aim_and_expires(instrument, component_id):
    from web.composer_final_preview import InstalledFinalSceneRuntime, installed_plant_geometry

    manager, _owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["look"]["pace"] = 1.0
    scene["animation"].update(component_id=component_id, parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime = manager._canonical_receiver_runtime
    canonical = manager._canonical_receiver_scene
    fixed_time = datetime.fromisoformat("2026-11-13T19:00:00-05:00")
    initial = ScenePresentationContext(canonical, 0, fixed_time)
    runtime.render(initial)
    direct = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    direct.render(initial)
    untouched = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    base = untouched.render(initial).pixels.copy()
    x, y = 16, 69
    other_globes = None
    if component_id == "plant_glow":
        geometry = installed_plant_geometry(ROOT)
        named_regions = geometry.globe_region_masks.values()
        target = next(region for region in named_regions if region.any())
        x, y = map(int, np.argwhere(target).mean(axis=0))
        y = 69 + 4 * round((y - 69) / 4)
        other_globes = geometry.globes & ~target
    gamepad.shoulder_bank = 3
    inputs(manager, gamepad, menu, StickMotion(0, x - 16, 3), StickMotion(1, (y - 69) // 4, 3))
    assert menu.card().marker == (x, y)
    inputs(manager, gamepad, menu, ChordPress("a", 3))
    assert direct.dispatch_animation_interaction(canonical, "primary", x, y)
    assert not direct.dispatch_animation_interaction(canonical, "primary", x, y)
    for step in range(25):
        frame = ScenePresentationContext(canonical, step / 20, fixed_time)
        actual = runtime.render(frame)
        np.testing.assert_array_equal(actual.pixels, direct.render(frame).pixels)
        ambient = untouched.render(frame)
        overlay = actual.foreground.pixels
        assert np.all(overlay[:, :3] <= overlay[:, 3:4])
        if step == 0:
            changed = np.any(actual.pixels != base, axis=1).reshape(33, 138)
            assert np.any(changed[max(0, x - 4):x + 5, max(0, y - 4):y + 5])
            if other_globes is not None:
                assert not changed[other_globes].any()
        elif step == 24:
            np.testing.assert_array_equal(actual.pixels, ambient.pixels)
    assert manager._canonical_receiver_runtime is runtime
    assert manager.get_scene_state() == canonical.scene


def test_tidal_scene_edits_retain_active_wake(instrument):
    from web.composer_final_preview import InstalledFinalSceneRuntime

    manager, _owner, _runner, _gamepad, _menu = instrument
    scene = starter()
    scene["animation"].update(component_id="tidal_bioluminescence", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime = manager._canonical_receiver_runtime
    canonical = manager._canonical_receiver_scene
    fixed_time = datetime.fromisoformat("2026-11-13T19:00:00-05:00")
    assert runtime.dispatch_animation_interaction(canonical, "primary", 16, 69)
    runtime.render(ScenePresentationContext(canonical, 0, fixed_time))
    scene = manager.get_scene_state()
    scene["animation"]["parameters"].update(mood="ember", background="luminous", seed=4502)
    manager._scene_v2_monotonic_elapsed = lambda: 0.0
    assert handle_command(manager, "start_scene", {"scene": scene})
    assert manager._canonical_receiver_runtime is runtime
    edited = ScenePresentationContext(manager._canonical_receiver_scene, .2, fixed_time)
    fresh = InstalledFinalSceneRuntime(manager.scene_v2_component_catalog(), ROOT)
    # The seed edit restarts seeded advection at the next frame in both renders.
    actual = runtime.render(edited).pixels.reshape(33, 138, 3)
    expected = fresh.render(edited).pixels.reshape(33, 138, 3)
    assert np.any(actual[11:22, 62:77] != expected[11:22, 62:77])
    np.testing.assert_array_equal(actual[:10], expected[:10])


def test_plant_glow_nimbus_reports_missing_geometry(instrument, monkeypatch):
    from animation.core.plant_awareness import InstallationGeometryContact
    from web.composer_final_preview import InstalledFinalSceneRuntime

    manager, _owner, _runner, gamepad, menu = instrument
    missing = InstallationGeometryContact.unavailable(33, 138, status="missing calibration")
    monkeypatch.setattr(InstalledFinalSceneRuntime, "_installation_geometry", lambda self: missing.geometry)
    monkeypatch.setattr(manager, "get_installation_profile_runtime_view", lambda: None)
    scene = starter()
    scene["animation"].update(component_id="plant_glow", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    before = manager.get_scene_state()
    gamepad.shoulder_bank = 3
    inputs(manager, gamepad, menu, StickMotion(0, 1, 3), ChordPress("a", 3))
    assert menu.card().value == "No plant calibration"
    assert manager.get_scene_state() == before


@pytest.mark.parametrize(("component_id", "seconds", "aim"), [
    ("aurora_curtains", .85, (16, 77)), ("cloud_canyon", 1.1, (16, 77)),
    ("desert_wind", 1, (16, 77)), ("moonlit_fog_banks", 1.2, (16, 77)),
    ("night_train_windows", 1.15, (16, 69)), ("circadian_window", 3, (16, 69)),
])
def test_atmospheric_accent_expiration_and_direct_input_parity(instrument, component_id, seconds, aim):
    from web.composer_final_preview import InstalledFinalSceneRuntime

    manager, _owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["look"]["pace"] = 1.0
    scene["animation"].update(component_id=component_id, parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime = manager._canonical_receiver_runtime
    canonical = manager._canonical_receiver_scene
    wall_time = datetime.fromisoformat("2026-11-13T02:00:00-05:00")
    initial = ScenePresentationContext(canonical, 0, wall_time)
    runtime.render(initial)
    direct = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    direct.render(initial)
    untouched = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    base = untouched.render(initial).pixels.copy()
    gamepad.shoulder_bank = 3
    inputs(manager, gamepad, menu, StickMotion(1, 2, 3))
    assert menu.card().marker == aim
    inputs(manager, gamepad, menu, ChordPress("a", 3))
    assert direct.dispatch_animation_interaction(canonical, "primary", *aim)
    received = []
    for elapsed in (0, .1, .2, .3, seconds / 2, seconds + .15):
        frame = ScenePresentationContext(canonical, elapsed, wall_time)
        actual = runtime.render(frame).pixels.copy()
        np.testing.assert_array_equal(actual, direct.render(frame).pixels)
        ambient = untouched.render(frame).pixels
        received.append(not np.array_equal(actual, ambient))
    assert any(received[:-1])
    assert not received[-1]
    assert manager.get_scene_state() == canonical.scene
    assert manager._canonical_receiver_runtime is runtime
    if component_id == "circadian_window":
        assert runtime._runtime._animation.instance.semantic_snapshot()["circadian_hour"] == 2.0
        np.testing.assert_array_equal(base, untouched.render(initial).pixels)


def test_circadian_hud_discloses_clock_and_fixed_controls(instrument):
    manager, _owner, _runner, _gamepad, menu = instrument
    scene = starter()
    scene["animation"].update(component_id="circadian_window", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    rows = {row.name: row for row in menu._animation_rows()}
    assert "Clock offset" in rows and "Fixed time rate" not in rows
    clock = menu._numeric_stick_bindings(manager.get_scene_state()["animation"], ("animation", "parameters"))
    offset_slot = next(index for index, binding in enumerate(clock) if binding and binding.label == "Clock offset")
    rows["Fixed hour or clock (-1)"].adjust(1, steps=4)
    assert manager.get_scene_state()["animation"]["parameters"]["hour"] == 0
    rows = {row.name: row for row in menu._animation_rows()}
    assert "Clock offset" not in rows and "Fixed time rate" in rows
    fixed = menu._numeric_stick_bindings(manager.get_scene_state()["animation"], ("animation", "parameters"))
    rate_slot = next(index for index, binding in enumerate(fixed) if binding and binding.label == "Fixed time rate")
    assert len(fixed) == len(clock)
    assert fixed[offset_slot] is None and clock[rate_slot] is None


def test_circadian_returns_to_clock_on_the_same_frozen_frame(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["look"]["pace"] = 0
    scene["animation"].update(component_id="circadian_window", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime = manager._canonical_receiver_runtime
    canonical = manager._canonical_receiver_scene
    wall_time = datetime.fromisoformat("2026-11-13T02:00:00-05:00")
    frame = ScenePresentationContext(canonical, 0, wall_time)
    renderer = runtime._runtime._animation.instance
    renderer._circadian_hour = 2.0
    renderer._last_key = None
    ambient = runtime.render(frame).pixels.copy()
    gamepad.shoulder_bank = 3
    inputs(manager, gamepad, menu, ChordPress("a", 3))
    assert not np.array_equal(runtime.render(frame).pixels, ambient)
    inputs(manager, gamepad, menu, StickMotion(0, 16, 3), ChordPress("a", 3))
    np.testing.assert_array_equal(runtime.render(frame).pixels, ambient)
    assert manager.get_scene_state() == canonical.scene


@pytest.mark.parametrize(("component_id", "aim"), [
    ("gradient", (16, 77)), ("rainbow", (16, 69)), ("wave", (16, 77)),
    ("sparkle", (16, 77)), ("quasicrystal_bloom", (16, 77)), ("living_stained_glass", (16, 77)),
])
def test_color_accent_matches_direct_input_and_leaves_ambient_state(instrument, component_id, aim):
    from web.composer_final_preview import InstalledFinalSceneRuntime

    manager, _owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["look"]["pace"] = 1
    scene["animation"].update(component_id=component_id, parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime, canonical = manager._canonical_receiver_runtime, manager._canonical_receiver_scene
    wall_time = datetime.fromisoformat("2026-11-13T02:00:00-05:00")
    direct = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    untouched = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    for index in range(31):
        frame = ScenePresentationContext(canonical, index / 150, wall_time)
        runtime.render(frame)
        direct.render(frame)
        untouched.render(frame)
    gamepad.shoulder_bank = 3
    inputs(manager, gamepad, menu, StickMotion(1, 2, 3))
    assert menu.card().marker == aim
    inputs(manager, gamepad, menu, ChordPress("a", 3))
    assert direct.dispatch_animation_interaction(canonical, "primary", *aim)
    assert not direct.dispatch_animation_interaction(canonical, "primary", *aim)
    had_accent = False
    for index in range(30, 245):
        frame = ScenePresentationContext(canonical, index / 150, wall_time)
        pixels = runtime.render(frame).pixels.copy()
        np.testing.assert_array_equal(pixels, direct.render(frame).pixels)
        baseline = untouched.render(frame).pixels
        if index < 103:
            had_accent |= not np.array_equal(pixels, baseline)
        if index == 244:
            np.testing.assert_array_equal(pixels, baseline)
    assert had_accent
    assert manager.get_scene_state() == canonical.scene
    assert manager._canonical_receiver_runtime is runtime
    actual = runtime._runtime._animation.instance
    baseline = untouched._runtime._animation.instance
    if component_id == "sparkle":
        assert actual.semantic_snapshot() == baseline.semantic_snapshot()
        assert actual._rng.bit_generator.state == baseline._rng.bit_generator.state
        assert actual._hue_rng.bit_generator.state == baseline._hue_rng.bit_generator.state
    if component_id == "living_stained_glass":
        np.testing.assert_array_equal(actual.pane_ids, baseline.pane_ids)
        np.testing.assert_array_equal(actual.seeds, baseline.seeds)


def test_solid_darkness_sparkle_manual_only_and_still_directions(instrument):
    from web.composer_final_preview import InstalledFinalSceneRuntime

    _manager, _owner, _runner, _gamepad, menu = instrument
    scene = starter()
    scene["look"].update(palette_id="rainbow", pace=1)
    for component_id, parameters in (("solid", {"glow": 0}), ("sparkle", {"density": 0, "night": 0}),
                                     ("rainbow", {"direction": 0}), ("wave", {"direction": 0})):
        scene["animation"].update(component_id=component_id, parameters=parameters)
        canonical = normalize_composer_scene({"origin": "composer", "scene": scene}, menu.catalog)
        runtime = InstalledFinalSceneRuntime(menu.catalog, ROOT)
        start = runtime.render(ScenePresentationContext(canonical, 0)).foreground.pixels.copy()
        later = runtime.render(ScenePresentationContext(canonical, 1)).foreground.pixels.copy()
        np.testing.assert_array_equal(start, later)
        if component_id in ("solid", "sparkle"):
            assert not later[:, :3].any()
        if component_id == "sparkle":
            assert runtime.dispatch_animation_interaction(canonical, "primary", 0, 0)
            burst = runtime.render(ScenePresentationContext(canonical, 1)).foreground.pixels
            assert 0 < np.count_nonzero(burst[:, :3].any(axis=1)) <= 6
        if component_id in ("rainbow", "wave"):
            direction = next(row for row in menu._parameter_rows(("animation",), canonical.scene["animation"]) if row.name == "direction")
            assert direction.value == "Still"


@pytest.mark.parametrize("component_id", [
    "fish_tank", "mini_metro", "firefly_synchrony", "flow_field_silk", "living_ecosystem", "physarum_network",
])
def test_swarm_aim_changes_simulation_and_matches_direct_input(instrument, component_id):
    from web.composer_final_preview import InstalledFinalSceneRuntime

    manager, _owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["look"]["pace"] = 1.0
    scene["animation"].update(component_id=component_id, parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime, canonical = manager._canonical_receiver_runtime, manager._canonical_receiver_scene
    direct = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    untouched = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    wall_time = datetime.fromisoformat("2026-11-13T02:00:00-05:00")
    for index in range(31):
        frame = ScenePresentationContext(canonical, index / 150, wall_time)
        runtime.render(frame)
        direct.render(frame)
        untouched.render(frame)
    animation = runtime._runtime._animation.instance
    ambient = untouched._runtime._animation.instance
    if component_id == "mini_metro":
        train = animation.trains[0]
        station, position = animation.line_stops[train.line][0]
        tx, ty = animation.station_xy[station]
        x = int(tx)
        y = 69 + 4 * round((137 - int(ty) - 69) / 4)
        for instance in (animation, ambient, direct._runtime._animation.instance):
            instance.trains[0].last_stop = station
            instance.trains[0].pos = float(position)
            instance.trains[0].dwell = .25
    else:
        x, y = 16, 69
    gamepad.shoulder_bank = 3
    inputs(manager, gamepad, menu, StickMotion(0, x - 16, 3), StickMotion(1, (y - 69) // 4, 3))
    assert menu.card().marker == (x, y)
    inputs(manager, gamepad, menu, ChordPress("a", 3))
    assert direct.dispatch_animation_interaction(canonical, "primary", x, y)
    assert not direct.dispatch_animation_interaction(canonical, "primary", x, y)
    changed = False
    for index in range(30, 125):
        frame = ScenePresentationContext(canonical, index / 150, wall_time)
        actual = runtime.render(frame).pixels.copy()
        np.testing.assert_array_equal(actual, direct.render(frame).pixels)
        changed |= not np.array_equal(actual, untouched.render(frame).pixels)
    assert changed
    semantic = {
        "fish_tank": "angle", "mini_metro": "trains", "firefly_synchrony": "phase",
        "flow_field_silk": "filaments", "living_ecosystem": "herd", "physarum_network": "trail",
    }[component_id]
    if component_id == "mini_metro":
        assert animation._held_station == station and animation._used_hold
        assert animation.semantic_snapshot() != ambient.semantic_snapshot()
        assert all(len(queue) <= animation.MAX_WAIT for queue in animation.waiting)
        assert all(len(train.cargo) <= animation.MAX_CARGO for train in animation.trains)
    else:
        assert not np.array_equal(getattr(animation, semantic), getattr(ambient, semantic))
    if component_id == "living_ecosystem":
        assert manager.get_current_status()["animation_stats"]["herd_count"] == len(animation.herd)
    elif component_id == "physarum_network":
        assert manager.get_current_status()["animation_stats"]["agent_count"] == len(animation.x)
    assert manager.get_scene_state() == canonical.scene
    assert manager._canonical_receiver_runtime is runtime


def test_physarum_rejects_nutrient_in_globe_even_without_habitat(instrument):
    from web.composer_final_preview import InstalledFinalSceneRuntime, installed_plant_geometry

    _manager, _owner, _runner, _gamepad, menu = instrument
    scene = starter()
    scene["plants"]["effects"] = {"version": 1, "active": [], "strengths": {}}
    scene["animation"].update(component_id="physarum_network", parameters={"agent_count": 1800, "density": 2})
    canonical = normalize_composer_scene({"origin": "composer", "scene": scene}, menu.catalog)
    runtime = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    runtime.render(ScenePresentationContext(canonical, 0))
    geometry = installed_plant_geometry(ROOT)
    globe = tuple(map(int, np.argwhere(geometry.globes)[0]))
    assert not runtime.dispatch_animation_interaction(canonical, "primary", *globe)
    assert runtime._runtime._animation.instance.get_runtime_stats()["agent_count"] == 1800
    free = tuple(map(int, np.argwhere(~geometry.obstacle)[0]))
    assert runtime.dispatch_animation_interaction(canonical, "primary", *free)


def test_gif_transport_uses_variable_durations_preserves_speed_phase_and_scene(instrument, monkeypatch):
    manager, _owner, _runner, gamepad, menu = instrument
    renderer_type = menu.catalog.require(provider="python", component_id="gif_animation", version=1).factory
    calls = []

    def decode(self, path):
        calls.append(path.name)
        frames = [np.full((33 * 138, 4), value, dtype=np.uint8) for value in (20, 60, 100)]
        return frames, [.1, .3, .7]

    monkeypatch.setattr(renderer_type, "_decode_gif", decode)
    scene = starter()
    scene["look"]["pace"] = 1
    scene["animation"].update(component_id="gif_animation", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime = manager._canonical_receiver_runtime
    renderer = runtime._runtime._animation.instance
    canonical = manager._canonical_receiver_scene

    def frame(elapsed):
        result = runtime.render(ScenePresentationContext(manager._canonical_receiver_scene, elapsed))
        return int(result.foreground.pixels[0, 0])

    gamepad.shoulder_bank = 3
    assert frame(.09) == 20 and frame(.2) == 60
    inputs(manager, gamepad, menu, ChordPress("a", 3))
    assert menu.card().value == "Play/pause"
    assert frame(.2) == frame(2) == 60 and renderer._paused
    inputs(manager, gamepad, menu, StickMotion(0, 8, 3), ChordPress("a", 3))
    assert menu.card().value == "Step"
    assert frame(2) == frame(3) == 100
    inputs(manager, gamepad, menu, StickMotion(0, -13, 3), ChordPress("a", 3))
    assert menu.card().value == "Restart"
    assert frame(3) == 20
    inputs(manager, gamepad, menu, StickMotion(0, 5, 3), ChordPress("a", 3))
    assert frame(3) == 20 and not renderer._paused
    assert frame(3.2) == 60
    assert calls == [canonical.scene["animation"]["parameters"]["gif_name"]]

    scene = manager.get_scene_state()
    scene["animation"]["parameters"]["playback_speed"] = 2
    manager._scene_v2_monotonic_elapsed = lambda: 3.2
    assert handle_command(manager, "start_scene", {"scene": scene})
    assert manager._canonical_receiver_runtime is runtime
    assert frame(3.2) == 60 and frame(3.32) == 100
    assert len(calls) == 1
    inputs(manager, gamepad, menu, StickMotion(0, 16, 3), ChordPress("a", 3))
    assert menu.card().value == "Next"
    assert frame(3.33) == 20 and len(calls) == 2 and calls[0] != calls[1]
    inputs(manager, gamepad, menu, StickMotion(0, -32, 3), ChordPress("a", 3))
    assert menu.card().value == "Previous"
    assert frame(3.34) == 20 and calls[-1] == calls[0]
    assert manager.get_scene_state() == scene


def test_column_inspection_steps_and_resumes_from_held_column(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["look"]["pace"] = 1
    scene["animation"].update(component_id="column_sequence", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime, canonical = manager._canonical_receiver_runtime, manager._canonical_receiver_scene
    renderer = runtime._runtime._animation.instance

    def column(elapsed):
        runtime.render(ScenePresentationContext(canonical, elapsed))
        return renderer.cadence_snapshot()["active_column"]

    assert column(0) == 0 and column(1.5) is None
    gamepad.shoulder_bank = 3
    inputs(manager, gamepad, menu, ChordPress("a", 3))
    assert menu.card().value == "Hold/free" and column(12) == column(49) == 6
    inputs(manager, gamepad, menu, StickMotion(0, 16, 3), ChordPress("a", 3))
    assert menu.card().value == "Next" and column(50) == 7
    inputs(manager, gamepad, menu, ChordPress("a", 3))
    assert column(51) == 8
    inputs(manager, gamepad, menu, StickMotion(0, -32, 3), ChordPress("a", 3))
    assert menu.card().value == "Previous" and column(52) == 7
    inputs(manager, gamepad, menu, StickMotion(0, 16, 3), ChordPress("a", 3))
    assert column(53) == 7 and column(54.4) is None and column(55.1) == 8
    manager.set_output_brightness(0)
    inputs(manager, gamepad, menu, StickMotion(0, 16, 3), ChordPress("a", 3))
    assert manager.output_brightness == manager.controller.current_brightness == 0
    assert not menu.aiming and renderer._pending_control is None
    assert manager.get_scene_state() == canonical.scene


@pytest.mark.parametrize(("component_id", "field", "y"), [
    ("ascii_drop", "fall_speed", 69), ("emoji", "pulse_hz", 69),
    ("christmas_tree", "twinkle_hz", 53), ("pixel_chase", "pixels_per_second", 69),
])
def test_media_accent_matches_direct_input_and_zero_endpoint(instrument, component_id, field, y):
    from web.composer_final_preview import InstalledFinalSceneRuntime

    manager, _owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["look"]["pace"] = 1
    scene["animation"].update(component_id=component_id, parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime, canonical = manager._canonical_receiver_runtime, manager._canonical_receiver_scene
    direct = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    untouched = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    initial = ScenePresentationContext(canonical, 0)
    for renderer in (runtime, direct, untouched):
        renderer.render(initial)
    gamepad.shoulder_bank = 3
    inputs(manager, gamepad, menu, StickMotion(1, (y - 69) // 4, 3), ChordPress("a", 3))
    assert direct.dispatch_animation_interaction(canonical, "primary", 16, y)
    changed = False
    for t in (0, .1, .25, .45, .65, 1, 1.5):
        frame = ScenePresentationContext(canonical, t)
        actual = runtime.render(frame).pixels.copy()
        np.testing.assert_array_equal(actual, direct.render(frame).pixels)
        ambient = untouched.render(frame).pixels
        changed |= not np.array_equal(actual, ambient)
        if t == 1.5:
            np.testing.assert_array_equal(actual, ambient)
    assert changed
    stopped = manager.get_scene_state()
    stopped["animation"]["parameters"][field] = 0
    if component_id == "pixel_chase":
        stopped["animation"]["parameters"]["color_cycle_speed"] = 0
    assert handle_command(manager, "start_scene", {"scene": stopped})
    runtime, canonical = manager._canonical_receiver_runtime, manager._canonical_receiver_scene
    zero = runtime.render(ScenePresentationContext(canonical, 0)).foreground.pixels.copy()
    elapsed = .05 if component_id == "christmas_tree" else .1
    second = runtime.render(ScenePresentationContext(canonical, elapsed)).foreground.pixels
    if component_id == "christmas_tree":
        # Compare ornaments below the star. Snow can still move with steady ornaments.
        for image in (zero, second):
            snow = np.all(image[:, :3] == (150, 205, 255), axis=1)
            image[snow, :3] = 0
        cover = zero[:, :3].any(axis=1) & second[:, :3].any(axis=1)
        np.testing.assert_array_equal(zero[cover], second[cover])
    else:
        np.testing.assert_array_equal(zero, second)


def test_fireworks_manual_launch_is_aimed_bounded_and_keeps_shell_settings(instrument):
    from web.composer_final_preview import InstalledFinalSceneRuntime

    manager, _owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["look"]["pace"] = 1
    scene["animation"].update(component_id="fireworks", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime = manager._canonical_receiver_runtime
    cadence = next(row for row in menu._animation_rows() if row.name == "launch cadence")
    cadence.adjust(-1, steps=1000)
    assert manager._canonical_receiver_runtime is runtime
    assert manager.get_scene_state()["animation"]["parameters"]["launch_cadence"] == 0
    canonical = manager._canonical_receiver_scene
    expected = InstalledFinalSceneRuntime(menu.catalog, ROOT)
    actual = runtime._runtime._animation.instance
    for elapsed in (0, .25, .5):
        frame = ScenePresentationContext(canonical, elapsed)
        assert not runtime.render(frame).foreground.pixels.any()
        expected.render(frame)
    gamepad.shoulder_bank = 3
    inputs(manager, gamepad, menu, StickMotion(0, 9, 3), StickMotion(1, 9, 3), ChordPress("a", 3))
    assert menu.card().marker == (25, 69)
    assert expected.dispatch_animation_interaction(canonical, "primary", 25, 69)
    assert not expected.dispatch_animation_interaction(canonical, "primary", 25, 69)
    frame = ScenePresentationContext(canonical, .55)
    np.testing.assert_array_equal(runtime.render(frame).pixels, expected.render(frame).pixels)
    assert len(actual._rockets) == 1 and actual._manual_launch_count == 1
    rocket = actual._rockets[0]
    assert rocket.x == 25 / 32 and rocket.settings.shell_population == 54
    scene = manager.get_scene_state()
    scene["animation"]["parameters"].update(shell_population=120, burst_style="palm", crackle=1)
    manager._scene_v2_monotonic_elapsed = lambda: .55
    assert handle_command(manager, "start_scene", {"scene": scene})
    assert actual._rockets[0] is rocket and rocket.settings.shell_population == 54
    for tick in range(14, 150):
        inputs(manager, gamepad, menu, ChordPress("a", 3))
        rendered = runtime.render(ScenePresentationContext(manager._canonical_receiver_scene, tick / 24))
        assert len(actual._rockets) <= 5 and len(actual._sparks) <= 720
        assert np.all(rendered.foreground.pixels[:, :3] <= rendered.foreground.pixels[:, 3:4])
    assert actual._launch_phase == 0
    assert 5 < actual._manual_launch_count < 130


def test_reaction_diffusion_manual_reseed_and_initial_density(instrument):
    manager, _owner, _runner, _gamepad, menu = instrument
    scene = starter()
    scene["animation"].update(component_id="reaction_diffusion_garden", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime = manager._canonical_receiver_runtime
    interval = next(row for row in menu._animation_rows() if row.name == "Auto reseed interval")
    interval.adjust(-1, steps=24)
    assert manager.get_scene_state()["animation"]["parameters"]["perturbation_interval"] == 0
    assert manager._canonical_receiver_runtime is runtime
    scene = manager.get_scene_state()
    scene["animation"]["parameters"]["density"] = 1.5
    assert handle_command(manager, "start_scene", {"scene": scene})
    assert manager._canonical_receiver_runtime is not runtime


def test_invalid_catalog_parameter_cannot_change_active_runtime(instrument):
    manager, owner, _runner, _gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    before = manager.get_scene_state()
    runtime = manager._canonical_receiver_runtime
    revision = owner.state_revision
    with pytest.raises(ValueError, match="curtain_density"):
        menu._edit(("animation", "parameters", "curtain_density"), {"invalid": True})
    assert manager.get_scene_state() == before
    assert manager._canonical_receiver_runtime is runtime
    assert owner.state_revision == revision


def test_pace_changes_keep_the_current_phase(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    manager._scene_v2_monotonic_elapsed = lambda: 12.0
    runtime = manager._canonical_receiver_runtime._runtime
    original = manager._canonical_receiver_scene
    parameters = original.scene["animation"]["parameters"]
    descriptor = runtime._descriptor(original.scene["animation"])
    old_phase = runtime._context(original, descriptor, parameters, 12).phase_time
    menu._edit(("look", "pace"), 9.95)
    new = manager._canonical_receiver_scene
    assert runtime._context(new, descriptor, parameters, 12).phase_time == pytest.approx(old_phase)
    assert runtime._context(new, descriptor, parameters, 14).phase_time == pytest.approx(old_phase + 19.9)

    inputs(manager, gamepad, menu, StickMotion(3, 1, 10))
    assert manager.get_scene_state()["look"]["pace"] == 10
    menu._speed_row(10).select()
    frozen = manager._canonical_receiver_scene
    assert frozen.scene["look"]["pace"] == 0
    assert runtime._context(frozen, descriptor, parameters, 50).phase_time == pytest.approx(old_phase)

    menu._speed_row(0).select()
    assert manager.get_scene_state()["look"]["pace"] == 1
    menu._speed_row(1).adjust(1, steps=1)
    assert manager.get_scene_state()["look"]["pace"] == 1.05


def test_browser_style_pace_command_keeps_runtime_and_clock(instrument):
    manager, _owner, _runner, _gamepad, _menu = instrument
    scene = starter()
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime = manager._canonical_receiver_runtime
    manager._scene_v2_monotonic_elapsed = lambda: 12.0
    scene["look"]["pace"] = 8.0
    assert handle_command(manager, "start_scene", {"scene": scene, "global_settings": {"output": {"power": True}}})
    assert manager._canonical_receiver_runtime is runtime
    assert manager.get_scene_state()["look"]["pace"] == 8.0

def test_high_pace_survives_numbered_and_playlist_storage(instrument):
    _manager, _owner, runner, _gamepad, menu = instrument
    scene = starter()
    scene["look"]["pace"] = 9.975
    scene["look"]["presentation_brightness"] = 2
    canonical = normalize_composer_scene({"origin": "composer", "scene": scene}, menu.catalog)
    menu.numbered_presets.save(1, canonical)
    assert menu.numbered_presets.load(1).identity == canonical.identity
    assert runner._validate_scene(canonical.scene) == canonical.scene

    scene["look"]["pace"] = 10.01
    with pytest.raises(ValueError, match="look pace"):
        normalize_composer_scene({"origin": "composer", "scene": scene}, menu.catalog)
    scene["look"].update(pace=10, presentation_brightness=2.01)
    with pytest.raises(ValueError, match="presentation_brightness"):
        normalize_composer_scene({"origin": "composer", "scene": scene}, menu.catalog)


def test_browse_defaults_and_authored_presentation(instrument):
    manager, _owner, _runner, _gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    gallery = gallery_entries(menu.catalog, menu.presets)
    item = next(item for item in gallery if item["component_id"] == "fireworks")
    preset = next(preset for preset in menu.presets.choices("fireworks") if preset.get("presentation", {}).get("look"))
    menu.open()
    menu._animation_presets(item)
    assert manager.get_scene_state()["animation"]["component_id"] == "aurora_curtains"
    focus(menu, preset["name"])
    menu.press("a")
    assert manager.get_scene_state()["look"] == preset["presentation"]["look"]
    assert manager.get_scene_state()["animation"]["component_id"] == "fireworks"
    assert menu.is_open
    assert menu.card().detail == "PLAYING"
    assert menu.card().hint == "Y TUNE"
    revision = menu.owner.state_revision
    menu.press("a")
    assert menu.owner.state_revision == revision
    menu.press("down")
    assert menu.card().detail != "PLAYING"
    menu.press("a")
    assert menu.card().detail == "PLAYING"
    menu.press("y")
    assert menu.card().section == "Tune"


def test_menu_covers_all_catalog_parameter_definitions(instrument):
    _manager, _owner, _runner, _gamepad, menu = instrument
    rows = gallery_entries(menu.catalog, menu.presets)
    assert len(rows) == len([d for d in menu.catalog.descriptors if d.role.value == "animation"])
    for component in rows:
        component_id = component["component_id"]
        params = component["parameters"]
        fields = menu._parameter_rows(("animation",), component)
        assert set(component["preferred_parameters"]) <= set(params)
        names = {definition["label"] for definition in component["parameter_schema"].values()}
        if component_id == "circadian_window":
            names.remove("Fixed time rate")
        assert {row.name for row in fields} == names
        for field in fields:
            if any(definition["label"] == field.name and definition.get("control") == "fixed"
                   for definition in component["parameter_schema"].values()):
                assert field.value and field.hint == "FIXED"
            else:
                assert field.adjust or field.select, (component_id, field)
    for descriptor in menu.catalog.descriptors:
        if descriptor.role.value == "widget":
            component = {"component_id": descriptor.component_id, "provider": "python",
                         "version": descriptor.version, "parameters": descriptor.default_parameters()}
            assert len(menu._parameter_rows(("widgets", 0, "component"), component)) == len(component["parameters"])


def test_catalog_dpad_changes_validate_as_scenes(instrument, monkeypatch):
    _manager, _owner, _runner, _gamepad, menu = instrument
    edits = []
    monkeypatch.setattr(menu, "_edit", lambda path, value: edits.append((path[-1], value)))
    for item in gallery_entries(menu.catalog, menu.presets):
        label = item["name"]
        scene = menu._with_animation(item, None)
        component = scene["animation"]
        defaults = component["parameters"].copy()
        for row in menu._parameter_rows(("animation",), component):
            if row.adjust is None:
                continue
            for direction in (-1, 1):
                for steps in (1, 5):
                    row.adjust(direction, steps=steps)
            assert edits, (label, row.name)
            for key, value in edits:
                component["parameters"] = {**defaults, key: value}
                result = normalize_composer_scene({"origin": "composer", "scene": scene}, menu.catalog)
                assert result.scene["animation"]["parameters"][key] == value, (label, row.name, value)
            edits.clear()
            component["parameters"] = defaults.copy()


def test_gif_clip_picker_commits_through_nimbus(instrument):
    manager, _owner, _runner, _gamepad, menu = instrument
    scene = starter()
    scene["animation"].update(component_id="gif_animation", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    original = manager.get_scene_state()["animation"]["parameters"]["gif_name"]
    menu.open()
    menu.press("a")  # Tune
    focus(menu, "Animation")
    menu.press("a")
    focus(menu, "Clip")
    menu.press("a")
    menu.press("down")
    menu.press("a")
    assert menu.card().label == "Clip"
    assert manager.get_scene_state()["animation"]["parameters"]["gif_name"] != original


@pytest.mark.parametrize(("game_id", "stat"), [
    ("snake", "moves"), ("maze_chase", "mode_timer"), ("pinball", "_sim_time"),
    ("pixel_quest", "run_time"), ("canopy_cup", "simulation_time"),
])
def test_player_clock_pauses_across_hud_and_resumes_without_catchup(instrument, game_id, stat):
    manager, owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["look"]["pace"] = 1.0
    scene["background"]["parameters"]["gain"] = 0.0
    scene["widgets"] = []
    scene["animation"].update(component_id=game_id, parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime, state = manager.nimbus_game()
    assert state == "ready"
    instance = runtime._runtime._animation.instance
    revision = owner.state_revision

    def frame(t):
        return runtime.render(ScenePresentationContext(manager._canonical_receiver_scene, t,
                                                       datetime.now().astimezone())).pixels.copy()

    inputs(manager, gamepad, menu, "a")
    for i in range(1, 30):
        image = frame(i / 60)
    before = getattr(instance, stat)
    inputs(manager, gamepad, menu, "menu")
    pixels = frame(50)
    np.testing.assert_array_equal(pixels, image)
    assert getattr(instance, stat) == before
    focus(menu, "Controls")
    menu.press("a")
    assert menu.card().section == "Controls"
    assert menu.card().value in {key.upper() for key in runtime.input_capability().actions}
    inputs(manager, gamepad, menu, "menu")
    assert not menu.is_open and menu.card().hint == "A RESUME"
    np.testing.assert_array_equal(frame(100), pixels)
    inputs(manager, gamepad, menu, "a")
    np.testing.assert_array_equal(frame(120), image)
    assert getattr(instance, stat) == before
    for i in range(1, 30):
        frame(120 + i / 60)
    assert getattr(instance, stat) != before
    assert manager.nimbus_game() == (runtime, "playing") and owner.state_revision == revision


@pytest.mark.parametrize("game_id", ["snake", "maze_chase", "pinball", "pixel_quest", "canopy_cup"])
def test_nimbus_buttons_change_gameplay_through_production_frames(instrument, game_id):
    manager, _owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["look"]["pace"] = 1.0
    scene["animation"].update(component_id=game_id, parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime, _ = manager.nimbus_game()
    game = runtime._runtime._animation.instance
    canonical = manager._canonical_receiver_scene
    inputs(manager, gamepad, menu, "a")

    def frames(start, stop):
        for index in range(start, stop):
            runtime.render(ScenePresentationContext(canonical, index / 60, datetime.now().astimezone()))

    if game_id == "snake":
        from animation.plugins.snake import OPPOSITE, PLAYER_DIRECTIONS

        head = game.snakes[0].head
        current = game.snakes[0].direction
        button, direction = next((button, direction) for button, direction in PLAYER_DIRECTIONS.items()
                                 if direction not in (current, OPPOSITE[current])
                                 and game._advance(head, direction) not in game._occupied())
        inputs(manager, gamepad, menu, button)
        frames(1, 9)
        assert game.snakes[0].head == game._advance(head, direction)
    elif game_id == "maze_chase":
        from animation.plugins.maze_chase import PLAYER_DIRECTIONS

        player = game.player
        game.game_state = "playing"
        player.progress = 0.0
        cell = player.cell
        button, direction = next((button, direction) for button, direction in PLAYER_DIRECTIONS.items()
                                 if direction != player.direction and direction in game._available(cell))
        inputs(manager, gamepad, menu, button)
        frames(1, 9)
        assert player.cell == game._neighbor(cell, direction)
    elif game_id == "pinball":
        game.ball_y = game.height - 18.0
        game.ball_vx = 0.0
        game.ball_vy = 40.0
        frames(1, 3)
        assert game.ball_vy > 0
        inputs(manager, gamepad, menu, "a")
        frames(3, 7)
        assert game.ball_vy < 0
    elif game_id == "pixel_quest":
        game._begin_stage(1)
        game._spawn_monster()
        monster = game.current_monster
        frames(1, 25)
        assert monster.hp == monster.max_hp
        inputs(manager, gamepad, menu, "b")
        frames(25, 31)
        assert monster.hp < monster.max_hp and game.hero_mp == game.hero_max_mp - 5
    else:
        # Keep opponent collisions out of the steering/braking assertion.
        game._start_heat(0)
        racer = game.racers[game.PLAYER_RACER]
        game.racers = [racer]
        frames(1, 24)
        assert racer.grounded
        start_x = racer.x
        inputs(manager, gamepad, menu, "right", "a")
        frames(24, 39)
        assert racer.x > start_x and racer.y < game.route_platforms[0].y - 2
        inputs(manager, gamepad, menu, "release-right")
        frames(39, 55)
        assert racer.vx == pytest.approx(0.0)


def test_tetris_hud_tuning_keeps_the_paused_board(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("human_avalanche_factory")})
    inputs(manager, gamepad, menu, "a")
    runtime, _ = manager.nimbus_game()
    game = runtime._runtime._animation.instance
    runtime.render(ScenePresentationContext(manager._canonical_receiver_scene, 0, datetime.now().astimezone()))
    piece, = game.active_pieces
    before = game._effective_drop_speed()
    inputs(manager, gamepad, menu, "menu")
    menu.press("a")
    focus(menu, "Animation")
    menu.press("a")
    assert menu.card().label == "fall rate"
    menu.press("right")
    assert game.active_pieces[0] is piece
    assert game._effective_drop_speed() > before
    inputs(manager, gamepad, menu, "menu")
    assert manager.nimbus_game() == (runtime, "paused")


def test_baja_uses_the_catalog_presets_and_clears_controls_on_pause(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    item = next(item for item in gallery_entries(menu.catalog, menu.presets) if item["component_id"] == "baja_rally")
    assert len(menu.presets.choices("baja_rally")) == 5
    preset = next(preset for preset in menu.presets.choices("baja_rally") if preset["preset_id"] == "spine-charge")
    assert preset["parameters"]["race_speed"] == 1.6 and "brightness" not in preset["parameters"]
    scene = menu._with_animation(item, preset)
    scene["look"]["pace"] = 1
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime, state = manager.nimbus_game()
    canonical = manager._canonical_receiver_scene
    game = runtime._runtime._animation.instance
    assert state == "ready"
    inputs(manager, gamepad, menu, "a")
    inputs(manager, gamepad, menu, "right")
    for step in range(1, 90):
        runtime.render(ScenePresentationContext(canonical, step / 60))
    assert game.hero.lane > .2 and game.hero.progress > 0
    inputs(manager, gamepad, menu, "release-right", "a", "b")
    for step in range(90, 110):
        runtime.render(ScenePresentationContext(canonical, step / 60))
    assert game.hero.nitro_uses == 1 and game._braking
    assert game._steering == set()
    inputs(manager, gamepad, menu, "left", "menu")
    assert manager.nimbus_game() == (runtime, "paused")
    assert not game._steering and not game._braking and not game._boost_requested
    before = game.logical_state()
    runtime.render(ScenePresentationContext(canonical, 50))
    assert game.logical_state() == before
    focus(menu, "Controls")
    menu.press("a")
    focus(menu, "Nitro boost")
    assert menu.card().value == "A"
    inputs(manager, gamepad, menu, "menu")
    inputs(manager, gamepad, menu, "a")
    runtime.render(ScenePresentationContext(canonical, 51))
    assert game.logical_state() == before
    assert manager.get_scene_state() == canonical.scene


def test_tetris_controls_disclose_inner_release_and_no_input_bleed(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter("human_avalanche_factory")})
    inputs(manager, gamepad, menu, "a")
    runtime, _ = manager.nimbus_game()
    game = runtime._runtime._animation.instance
    canonical = manager._canonical_receiver_scene
    runtime.render(ScenePresentationContext(canonical, 0))
    inputs(manager, gamepad, menu, "menu")
    focus(menu, "Controls")
    menu.press("a")
    focus(menu, "Release inner pieces")
    assert menu.card().value == "Y"
    inputs(manager, gamepad, menu, "y")
    before = len(game.active_pieces)
    inputs(manager, gamepad, menu, "menu")
    assert menu.card().hint == "A RESUME"
    inputs(manager, gamepad, menu, "a")
    runtime.render(ScenePresentationContext(canonical, .1))
    assert len(game.active_pieces) == before
    inputs(manager, gamepad, menu, "y")
    runtime.render(ScenePresentationContext(canonical, .2))
    assert len(game.active_pieces) > before


def test_paused_canopy_cup_repaints_for_a_palette_edit(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    scene = starter()
    scene["animation"].update(component_id="canopy_cup", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    runtime, _ = manager.nimbus_game()
    game = runtime._runtime._animation.instance
    inputs(manager, gamepad, menu, "a")
    inputs(manager, gamepad, menu, "menu")
    inputs(manager, gamepad, menu, "menu")

    def frame(t):
        return runtime.render(ScenePresentationContext(manager._canonical_receiver_scene, t,
                                                       datetime.now().astimezone())).pixels.copy()

    before = frame(1)
    state = game.logical_state()
    inputs(manager, gamepad, menu, "right")
    assert not np.array_equal(frame(2), before)
    assert game.logical_state() == state


def test_nimbus_play_takes_over_the_current_playlist_game(instrument):
    manager, _owner, runner, gamepad, menu = instrument
    scene = starter("human_avalanche_factory")
    definition = menu.playlists.save({"name": "Games", "entries": [
        {"entry_id": str(uuid4()), "label": "Avalanche Factory", "duration_seconds": 30, "scene": scene},
    ]})
    menu._start_playlist(definition["id"])
    runtime, _ = manager.nimbus_game()
    before = manager.get_scene_state()
    inputs(manager, gamepad, menu, "a")
    assert runner.status()["phase"] == "overridden"
    assert manager.nimbus_game() == (runtime, "playing")
    assert manager.get_scene_state() == before


def test_widget_text_position_and_save_as(instrument):
    manager, _owner, _runner, _gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    menu.open()
    menu._tune()
    focus(menu, "Widgets")
    menu.press("a")
    focus(menu, "Add Message")
    menu.press("a")
    focus(menu, "text")
    menu.press("a")
    for button in ("up", "up", "a"):
        menu.press(button)
    assert menu.picker.text.endswith("🍆")
    assert manager.get_scene_state()["widgets"][0]["component"]["parameters"]["text"] == "HI🔥"
    menu.press("y")
    assert manager.get_scene_state()["widgets"][0]["component"]["parameters"]["text"] == "HI🔥🍆"
    focus(menu, "Move")
    menu.press("a")
    menu.press("right")
    assert manager.get_scene_state()["widgets"][0]["placement"]["strip_translation"] == 1
    for button in ("b", "b", "b"):
        menu.press(button)
    focus(menu, "Save Look")
    for button in ("a", "a", "right", "a", "y"):
        menu.press(button)
    look = menu.looks.list()[0]
    assert look["name"] == "AB"
    assert menu.saved_revision == 1
    saved = menu.looks.get(look["id"])["scene"]
    assert saved == manager.get_scene_state()
    assert "output" not in saved
    assert manager.output_brightness == 80


def test_scene_support_metadata_round_trips_widgets_and_background(instrument):
    manager, _owner, _runner, _gamepad, menu = instrument
    scene = starter("aurora_clock")
    clock = scene["widgets"][0]
    clock["component"]["parameters"]["color"] = [10, 22, 47]
    scene["background"]["parameters"]["seed"] = 999999
    assert handle_command(manager, "start_scene", {"scene": scene})
    seed = next(row for row in menu._background_rows() if row.name == "Stored seed")
    assert seed.value == "999999" and not seed.adjust
    message_descriptor = menu.catalog.require(provider="python", component_id="emoji_arranger", version=1)
    menu._add_widget("Message", message_descriptor)
    current = manager.get_scene_state()
    clock, message = current["widgets"]
    widget_path = ("widgets", 0, "component")
    clock_rows = {row.name: row for row in menu._parameter_rows(widget_path, clock["component"])}
    clock_rows["Clock offset"].adjust(1, steps=1)
    updated = manager.get_scene_state()["widgets"][0]["component"]
    assert updated["parameters"]["clock_offset_minutes"] == 1
    offset = next(row for row in menu._parameter_rows(widget_path, updated) if row.name == "Clock offset")
    offset.adjust(1, steps=5)
    assert manager.get_scene_state()["widgets"][0]["component"]["parameters"]["clock_offset_minutes"] == 61
    message_rows = {row.name: row for row in menu._parameter_rows(("widgets", 1, "component"), message["component"])}
    assert {"Text along strip", "Text across strips"} <= message_rows.keys()
    message_rows["scroll speed"].adjust(1, steps=1)
    saved = manager.get_scene_state()
    assert saved["widgets"][1]["component"]["parameters"]["scroll_speed"] == .05
    assert saved["widgets"][1]["placement"] == message["placement"]
    assert saved["background"]["parameters"]["seed"] == 999999
    assert saved["look"]["presentation_brightness"] == scene["look"]["presentation_brightness"]
    assert manager.output_brightness == 80


def test_wall_clock_and_message_advance_while_game_is_paused(instrument):
    from web.composer_final_preview import InstalledFinalSceneRuntime

    manager, _owner, _runner, gamepad, menu = instrument
    scene = starter("aurora_clock")
    scene["look"]["pace"] = 1
    scene["animation"].update(component_id="snake", parameters={})
    assert handle_command(manager, "start_scene", {"scene": scene})
    message = menu.catalog.require(provider="python", component_id="emoji_arranger", version=1)
    menu._add_widget("Message", message)
    saved = manager.get_scene_state()
    saved["widgets"][1]["component"]["parameters"]["scroll_speed"] = 2
    assert handle_command(manager, "start_scene", {"scene": saved})
    menu.close()
    gamepad.shoulder_bank = 0
    inputs(manager, gamepad, menu, "a")
    runtime, state = manager.nimbus_game()
    assert state == "playing"
    canonical = manager._canonical_receiver_scene
    wall_time = datetime.fromisoformat("2026-11-13T20:15:05-05:00")
    runtime.render(ScenePresentationContext(canonical, 0, wall_time))
    inputs(manager, gamepad, menu, "menu")
    assert manager.nimbus_game()[1] == "paused"
    first = runtime.render(ScenePresentationContext(canonical, 1, wall_time)).pixels.copy()
    slot = runtime._runtime._animation.instance
    board = slot.semantic_snapshot()
    next_clock = datetime.fromisoformat("2026-11-13T20:15:07-05:00")
    second = runtime.render(ScenePresentationContext(canonical, 4, next_clock)).pixels.copy()
    assert not np.array_equal(first, second) and slot.semantic_snapshot() == board
    assert all(widget.descriptor.input_capability.mode == "none" for widget in runtime._runtime._widgets.values())
    for widget in runtime._runtime._widgets.values():
        assert widget.instance._revision >= 2
    for luminance in (0, .54, 1):
        variants = []
        for seed in (0, 999999):
            candidate = deepcopy(canonical.scene)
            candidate["background"]["parameters"].update(gain=luminance, seed=seed)
            variants.append(normalize_composer_scene({"origin": "composer", "scene": candidate}, menu.catalog))
        frames = [InstalledFinalSceneRuntime(menu.catalog, ROOT).render(ScenePresentationContext(candidate, 0, wall_time)).pixels
                  for candidate in variants]
        np.testing.assert_array_equal(*frames)


def test_playlist_takes_over_only_on_manual_change_and_stop_retains_entry(instrument):
    manager, owner, runner, _gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    definition = menu.playlists.save({"name": "Evening", "entries": [
        {"entry_id": str(uuid4()), "label": "Hush", "duration_seconds": 30, "scene": starter()}
    ]})
    menu._start_playlist(definition["id"])
    revision = owner.state_revision
    menu.open()
    menu.press("up")
    menu.press("a")
    assert owner.state_revision == revision
    assert runner.status()["phase"] == "running"
    menu.close()
    menu.open()
    menu.press("a")
    menu.press("right")
    assert runner.status()["phase"] == "overridden"
    assert menu.card().label == "Manual"
    menu._start_playlist(definition["id"])
    menu._stop_playlist(runner.status()["run_id"])
    assert runner.status()["phase"] == "stopped"
    assert manager.is_running



def test_external_commands_dismiss_menu_and_scheduled_commands_refresh_it(instrument):
    manager, owner, _runner, gamepad, menu = instrument
    assert handle_command(manager, "start_scene", {"scene": starter()})
    discarded = []
    gamepad.discard_pending = lambda: discarded.append(True)
    menu.open()
    menu.press("a")
    focus(menu, "Animation")
    menu.press("a")
    revision = owner.state_revision
    assert handle_command(manager, "start_scene", {"scene": starter("aurora_clock")})
    assert reconcile_wall_menu(manager, gamepad, menu, revision, scheduled=True)
    assert menu.card().section == "Tune"
    assert menu.card().label == "Animation"
    revision = owner.state_revision
    assert handle_command(manager, "set_output_brightness", {"brightness": 32})
    assert reconcile_wall_menu(manager, gamepad, menu, revision)
    assert not menu.is_open
    assert len(discarded) == 2
    assert manager.output_brightness == 32


def test_dark_wake_disconnect_and_tetris(instrument):
    manager, _owner, _runner, gamepad, menu = instrument
    manager.set_output_brightness(0)
    inputs(manager, gamepad, menu, "menu")
    assert menu.card().label == "Browse"
    assert manager.output_brightness == 16
    assert not manager.is_running
    inputs(manager, gamepad, menu, "disconnect")
    assert not menu.is_open
    assert handle_command(manager, "start_scene", {"scene": starter()})
    assert handle_command(manager, "stop_scene", {})
    inputs(manager, gamepad, menu, "menu")
    assert manager.is_running
    assert menu.card().label == "Tune"
    menu._run("start_tetris_player", {}, close=True)
    runtime, _ = manager.nimbus_game()
    game = runtime._runtime._animation.instance
    assert game.player_control
    inputs(manager, gamepad, menu, "menu", "b")
    assert menu.is_open
    assert game.player_control and manager.nimbus_game()[1] == "paused"
    inputs(manager, gamepad, menu, "disconnect")
    assert not menu.is_open
    assert not game.player_control


def test_overlay_is_cached_and_refreshes_idle_output(instrument):
    manager, _owner, _runner, _gamepad, _menu = instrument
    renderer = WallMenuFrame()
    card = MenuCard("Tune", "Palette", "Mist")
    pixels = renderer.render(card)
    assert pixels is renderer.render(card, 8)
    assert pixels.start == HEIGHT - CARD_HEIGHT
    manager.set_wall_menu_pixels(pixels)
    assert np.any(manager.controller.pixels)
    manager.set_wall_menu_pixels(None)
    assert not np.any(manager.controller.pixels)


def test_overlay_refreshes_an_unchanged_running_scene():
    manager = AnimationManager(SimulatedWall(), auto_start=False)
    scene = starter()
    scene["look"]["pace"] = 0
    try:
        assert handle_command(manager, "start_scene", {"scene": scene})
        before = manager.controller.pixels.copy()
        card = WallMenuFrame().render(MenuCard("Tune", "Palette", "Mist"))
        manager.set_wall_menu_pixels(card)
        _wait_until(lambda: not np.array_equal(manager.controller.pixels, before))
        manager.set_wall_menu_pixels(None)
        _wait_until(lambda: np.array_equal(manager.controller.pixels, before))
    finally:
        manager.stop_animation()


def _wait_until(condition):
    deadline = time.monotonic() + 2
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(.01)


def _save_process(path, name, entered, release):
    class CoordinatedStore(SceneLookStore):
        def _records(self):
            result = super()._records()
            entered.set()
            if release is not None:
                assert release.wait(10)
            return result

    canonical = normalize_composer_scene({"origin": "composer", "scene": starter()},
                                         current_component_catalog())
    CoordinatedStore(path).save_as(name, canonical)


def test_simultaneous_process_saves_preserve_both_looks(tmp_path):
    context = multiprocessing.get_context("spawn")
    path = tmp_path / "looks.json"
    entered, release, second_entered = (context.Event() for _ in range(3))
    first = context.Process(target=_save_process, args=(path, "Browser", entered, release))
    second = context.Process(target=_save_process, args=(path, "Wall", second_entered, None))
    first.start()
    try:
        assert entered.wait(10)
        second.start()
        assert not second_entered.wait(.3)
    finally:
        release.set()
        first.join(10)
        if second.pid:
            second.join(10)
    assert first.exitcode == second.exitcode == 0
    assert {record["name"] for record in SceneLookStore(path).list()} == {"Browser", "Wall"}


def test_delayed_nimbus_observation_preserves_newer_browser_edit():
    import subprocess

    source = (ROOT / "web/static/js/composer_slice.js").read_text()
    start = source.index("  let nimbusLooksRevision")
    end = source.index("  document.addEventListener('visibilitychange'", start)
    script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const makeScene = id => ({animation: {component_id: id}});
const observation = {freshness: 'fresh', controller_session_id: 'controller',
                     controller_state_revision: 1, nimbus: {scene_revision: 1},
                     scene: makeScene('old_nimbus_scene')};
const state = {intent: 1, scene: makeScene('initial'), wall: {}, publication: {}};
let release, clears = 0;
const delayed = new Promise(resolve => { release = resolve; });
let answer = delayed;
const sandbox = {
  state, structuredClone, api: '/api/composer', clientId: 'browser',
  requestJson: url => url.includes('observed') ? answer : Promise.resolve({}),
  intentIsCurrent: intent => intent === state.intent,
  syncFrameRateObservation() {}, applyScene() {}, schedulePreview() {},
  renderLibrary() {}, renderStatus() {}, clearHistory() { clears++; },
  $: () => ({})
};
vm.runInNewContext(process.argv[1] + ';this.poll = refreshStatus;', sandbox);
(async () => {
  const oldPoll = sandbox.poll();
  state.intent++;
  state.scene = makeScene('newer_browser_edit');
  release(observation);
  await oldPoll;
  assert.equal(state.scene.animation.component_id, 'newer_browser_edit');
  assert.equal(clears, 0);
  answer = Promise.resolve({...observation, scene: makeScene('new_nimbus_scene'),
                            controller_state_revision: 3, nimbus: {scene_revision: 3}});
  await sandbox.poll();
  assert.equal(state.scene.animation.component_id, 'new_nimbus_scene');
  assert.equal(clears, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(["node", "-e", script, source[start:end]], capture_output=True,
                            text=True, timeout=5)
    assert result.returncode == 0, result.stderr
