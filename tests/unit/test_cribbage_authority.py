"""Real Scorebot ABI1 authority integration; test identities never ship as config."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from drivers import cribbage_session as authority
from drivers.cribbage_session import CribbageSession, Library, Result, State

ROOT = Path(__file__).resolve().parents[2]
RED, BLUE, GREEN = 2934574912, 860931256, 2934574676
RED_MAC, BLUE_MAC, GREEN_MAC = 0x983DAEEA0F41, 0x64E83350C4B9, 0x983DAEEA0E55


@pytest.fixture(scope="session")
def native(tmp_path_factory):
    build = tmp_path_factory.mktemp("native")
    environment = {**os.environ, "SCOREBOT_LEADER_NODE_ID": "0x10203040u",
                   "SCOREBOT_LEADER_BLUETOOTH_MAC": "0x123456789abcULL"}
    subprocess.run(["make", "-C", str(ROOT / "vendor/scorebot/native"),
                    "BUILD=" + str(build), "all"], env=environment, check=True, capture_output=True)
    suffix = "dylib" if sys.platform == "darwin" else "so"
    return Library(build / ("libscorebot." + suffix))


@pytest.fixture
def session(tmp_path, native):
    handle = CribbageSession(tmp_path / "game.json", library=native)
    yield handle
    handle.close()


def saved(session):
    return json.loads(session.path.read_text())


def start_local(session, players=(0, 1)):
    for player in players:
        assert session.request("set_lobby_mode", player, 0)
    assert session.request("start")


def player_message(node=RED, **changes):
    data = {"type": "player", "protocol": 4, "fromNodeId": node, "game": 0,
            "score": 3, "turnPassed": False, "operationId": 1}
    data.update(changes)
    return json.dumps(data).encode()


def rewrite(session, **changes):
    """Trusted test setup uses the real native read/restore boundary."""
    with session.lock:
        values = session._game.read().to_dict()
        values.update(changes)
        assert session._game.restore(State.from_dict(values)) == Result.OK
        session._commit(session._game.read())


def test_zero_board_local_game_and_native_persistence(session):
    start_local(session)
    assert session.latest().connected == 0
    assert session.request("local_action", 0, 4, 0)
    assert session.request("local_action", 0, 0, 1)
    assert session.latest().scores == (4, 0, 0, 0)
    assert session.latest().turn == 3
    assert session.request("local_action", 0, -2, 1)  # Out-of-turn correction.
    assert session.latest().turn == 3
    assert not session.request("local_action", 0, 0, 0)  # OK is zero; NO_CHANGE isn't success.
    snapshot = saved(session)
    assert snapshot["scores"] == [2, 0, 0, 0]
    assert "abi_version" in snapshot and "last_operation" in snapshot
    assert "operations" not in snapshot and "protocol" not in snapshot


def test_mixed_play_duplicates_sleep_disconnect_takeover_and_rejoin(session):
    token = session.admit(RED, 4, 1, RED_MAC)
    assert token is not None
    assert session.request("set_lobby_mode", 1, 0)
    assert session.request("start")
    assert session.receive(token, player_message(turnPassed=True)) == Result.OK
    before = saved(session)
    assert session.receive(token, player_message(turnPassed=True)) == Result.DUPLICATE
    assert saved(session) == before
    assert session.latest().turn == 3
    notice = json.dumps({"type": "status", "protocol": 4, "fromNodeId": RED, "state": "sleep"}).encode()
    assert session.receive(token, notice) == Result.OK
    assert saved(session)["sleeping_mask"] == 1
    session.disconnect(token)
    assert session.latest().connected == 0 and session.latest().roster == 3
    assert session.request("local_action", 1, 1, 1)
    assert session.latest().turn == 2  # Sleeping red remains turn-eligible.
    assert session.request("local_action", 0, -1, 0)  # Nimbus takeover.
    rejoined = session.admit(RED, 4, 1, RED_MAC)
    assert rejoined is not None and rejoined is not token
    assert session.latest().sleeping == 0
    assert session.receive(token, player_message(operationId=2)) == Result.WRONG_AUTHORITY
    assert session.receive(rejoined, player_message(operationId=2)) == Result.OK
    assert session.latest().scores == (5, 1, 0, 0)
    assert session.admit(GREEN, 4, 1, GREEN_MAC) is None  # Frozen roster.


def test_unexpected_disconnect_keeps_roster_and_turn(session):
    token = session.admit(RED, 4, 1, RED_MAC)
    start_local(session, (1,))
    turn = session.latest().turn
    session.disconnect(token)
    assert session.latest().roster == 3 and session.latest().turn == turn
    assert session.request("local_action", 0, 5, 1)
    assert session.latest().turn == 3


def test_consumed_physical_overflow_is_durable_but_local_overflow_is_unchanged(session):
    token = session.admit(RED, 4, 1, RED_MAC)
    assert session.request("start")
    assert session.request("local_action", 0, 0x7FFFFFFF, 0)
    before = saved(session)
    assert not session.request("local_action", 0, 1, 0)
    assert saved(session) == before
    assert session.receive(token, player_message(score=1, operationId=19)) == Result.SCORE_OUT_OF_RANGE
    after = saved(session)
    assert after["scores"] == before["scores"]
    assert after["version"] == before["version"] + 1
    assert after["last_operation"][0] == 19
    assert json.loads(session.wire())["operations"][0] == 19
    assert session.receive(token, player_message(score=1, operationId=19)) == Result.DUPLICATE


def test_new_game_changes_epoch_and_revision_even_in_lobby(session):
    token = session.admit(RED, 4, 1, RED_MAC)
    assert session.request("start")
    assert session.receive(token, player_message(operationId=8)) == Result.OK
    before = saved(session)
    assert session.new_game()
    after = saved(session)
    assert after["game_id"] == before["game_id"] + 1
    assert after["version"] > before["version"] and after["term"] == before["term"]
    assert after["last_operation"] == before["last_operation"]
    assert after["started"] == 0 and after["scores"] == [0] * 4
    assert session.admit(BLUE, 4, 1, BLUE_MAC) is not None  # Admission reopened.
    assert session.new_game()  # reset() returns NO_CHANGE in a lobby.
    assert saved(session)["version"] > after["version"]
    assert session.request("start")
    before = saved(session)
    assert session.receive(token, player_message(operationId=99)) == Result.WRONG_GAME
    assert saved(session) == before


@pytest.mark.parametrize("field", ["game_id", "version"])
def test_new_game_refuses_counter_exhaustion(session, field):
    rewrite(session, **{field: 0xFFFFFFFF})
    before = saved(session)
    assert not session.new_game()
    assert saved(session) == before
    if field == "version":
        assert not session.request("set_lobby_mode", 0, 0)
        assert saved(session) == before


def test_resume_persists_new_term_and_drops_links(session, native):
    token = session.admit(RED, 4, 1, RED_MAC)
    start_local(session, (1,))
    session.receive(token, player_message())
    before = saved(session)
    session.close()
    with_session = CribbageSession(session.path, library=native)
    try:
        after = saved(with_session)
        assert after["term"] == before["term"] + 1
        assert after["scores"] == before["scores"]
        assert after["last_operation"] == before["last_operation"]
        assert after["roster_mask"] == 3 and after["connected_mask"] == 0
        assert json.loads(with_session.wire())["term"] == after["term"]
    finally:
        with_session.close()


def test_one_process_lease_prevents_competing_authorities(session, native):
    before = saved(session)
    with pytest.raises(BlockingIOError):
        CribbageSession(session.path, library=native)
    assert saved(session) == before
    assert session.request("set_lobby_mode", 0, 0)


@pytest.mark.parametrize("content", ["{", "null", '{}', '{"abi_version":true}'])
def test_invalid_saved_state_is_not_discarded(tmp_path, native, content):
    path = tmp_path / "game.json"
    path.write_text(content)
    with pytest.raises((ValueError, TypeError, AttributeError, RuntimeError)):
        CribbageSession(path, library=native)
    assert path.read_text() == content


def test_failed_resume_never_publishes_or_changes_saved_scores(session, native, monkeypatch):
    start_local(session)
    session.request("local_action", 0, 22, 0)
    before = saved(session)
    session.close()
    monkeypatch.setattr(authority, "durable_save", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(RuntimeError, match="save failed"):
        CribbageSession(session.path, library=native)
    assert saved(session) == before


@pytest.mark.parametrize("after_replace", [False, True])
def test_failed_action_persistence_stops_input_and_acknowledgements(session, monkeypatch, after_replace):
    start_local(session)
    view = session.latest()
    old = saved(session)
    real_save = authority.durable_save
    def fail(path, values):
        if after_replace:
            real_save(path, values)
        raise OSError("injected interrupted save")
    monkeypatch.setattr(authority, "durable_save", fail)
    with pytest.raises(RuntimeError, match="save failed"):
        session.request("local_action", 0, 3, 0)
    assert session.latest() is view and session.error
    with pytest.raises(RuntimeError):
        session.wire()
    with pytest.raises(RuntimeError):
        session.request("local_action", 0, 3, 0)
    assert saved(session)["scores"][0] == (3 if after_replace else old["scores"][0])


def test_exhausted_resume_term_refused(session, native):
    rewrite(session, term=0xFFFFFFFF)
    session.close()
    before = saved(session)
    with pytest.raises(RuntimeError, match="REJECTED"):
        CribbageSession(session.path, library=native)
    assert saved(session) == before


def test_all_native_calls_serialized_including_read_admit_and_close(session, monkeypatch):
    start_local(session)
    active_calls = 0
    peak = 0
    guard = threading.Lock()
    def watched(function):
        def invoke(*args, **kwargs):
            nonlocal active_calls, peak
            with guard:
                active_calls += 1
                peak = max(peak, active_calls)
            try:
                time.sleep(0.001)  # Model ctypes releasing the GIL.
                return function(*args, **kwargs)
            finally:
                with guard:
                    active_calls -= 1
        return invoke
    for name in ("read", "local_action", "set_connected", "close"):
        monkeypatch.setattr(session._game, name, watched(getattr(session._game, name)))
    monkeypatch.setattr(session.library, "admit_peer", watched(session.library.admit_peer))
    def act(index):
        if index % 3:
            session.request("local_action", 0, 1, 0)
        else:
            session.disconnect(session.admit(RED, 4, 1, RED_MAC))
        session.wire()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(act, range(30)))
    session.close()
    assert peak == 1


@pytest.mark.parametrize("changes", [
    {"protocol": True}, {"protocol": 3}, {"fromNodeId": True}, {"fromNodeId": 0},
    {"fromNodeId": 0x100000000}, {"score": True}, {"score": 1.0},
    {"score": 0x80000000}, {"score": -0x80000001}, {"turnPassed": 1},
    {"operationId": True}, {"operationId": 0}, {"game": False}, {"game": -1},
    {"type": "state"}, {"fromNodeId": BLUE}, {"extra": "unsupported"},
])
def test_malformed_or_spoofed_messages_cannot_score(session, changes):
    token = session.admit(RED, 4, 1, RED_MAC)
    session.request("start")
    before = saved(session)
    assert session.receive(token, player_message(**changes)) == Result.INVALID_ARGUMENT
    assert saved(session) == before
    assert json.loads(session.wire())["scores"] == before["scores"]


@pytest.mark.parametrize("raw", [b"", b"[1]", b"null", b"{", b"\xff", b"x" * 353,
    b'{"type":"player","protocol":4,"protocol":4,"fromNodeId":1}',
])
def test_bad_json_is_rejected_without_crashing(session, raw):
    token = session.admit(RED, 4, 1, RED_MAC)
    assert session.receive(token, raw) == Result.INVALID_ARGUMENT


@pytest.mark.parametrize("node,protocol,public,address", [
    (RED, 3, 1, RED_MAC), (RED, 4, 0, RED_MAC), (BLUE, 4, 1, RED_MAC),
    (RED, 4, 1, RED_MAC ^ (1 << 40)), (0, 4, 1, RED_MAC),
])
def test_admission_checks_full_public_address_and_characteristic_identity(session, node, protocol, public, address):
    assert session.admit(node, protocol, public, address) is None
    assert session.latest().connected == 0


def test_display_is_read_only_and_never_rostered(session):
    address = 0x1234AABBCCDE
    token = session.admit((address - 1) & 0xFFFFFFFF, 4, 1, address)
    assert token is not None
    start_local(session)
    before = saved(session)
    assert session.receive(token, player_message()) == Result.WRONG_AUTHORITY
    session.disconnect(token)
    assert saved(session) == before and session.latest().roster == 3


def test_sleep_notice_requires_admitted_matching_player(session):
    token = session.admit(RED, 4, 1, RED_MAC)
    session.request("start")
    before = saved(session)
    raw = json.dumps({"type": "status", "protocol": 4, "fromNodeId": BLUE, "state": "sleep"}).encode()
    assert session.receive(token, raw) == Result.INVALID_ARGUMENT
    assert saved(session) == before


def test_wrong_configured_authority_is_visible_and_does_not_create_saved_game(tmp_path, native):
    path = tmp_path / "game.json"
    with pytest.raises(ValueError, match="identity"):
        CribbageSession(path, library=native, identity={"node": 123, "mac": "12:34:56:78:9A:BC"})
    assert not path.exists()


def test_failed_new_game_restore_stops_publication(session, monkeypatch):
    start_local(session)
    old = saved(session)
    monkeypatch.setattr(session._game, "restore", lambda *_: Result.INVALID_ARGUMENT)
    with pytest.raises(RuntimeError, match="New Game restore"):
        session.new_game()
    assert saved(session) == old
    with pytest.raises(RuntimeError):
        session.wire()


def test_nimbus_lobby_review_scoring_selection_clear_resume_and_new_game(session):
    from animation.core.manager import PreviewLEDController
    from animation.plugins.cribbage_leaderboard import CribbageLeaderboardAnimation
    game = CribbageLeaderboardAnimation(PreviewLEDController(33, 138), source=session)
    game.set_player_control(True)
    for button in ("right", "down", "right", "a"):
        game.handle_player_button(button)
    assert not session.latest().started and game._page == "start-confirm"
    game.handle_player_button("a")
    assert session.latest().started and session.latest().roster == 3
    game.handle_player_button("up")
    assert game._selection == 0 and session.latest().turn == 2
    for button in ("right", "right", "x"):
        game.handle_player_button(button)
    assert session.latest().scores[0] == 2 and game._drafts == [0] * 4
    game.handle_player_button("a")
    assert session.latest().turn == 3
    game.handle_player_button("right")
    assert game.handle_player_button("b") is None and game._drafts == [0] * 4
    assert game.handle_player_button("b") == "leave"
    game.set_player_control(True)
    game.handle_player_button("y")
    game.handle_player_button("a")  # Resume doesn't reset.
    assert session.latest().scores[0] == 2
    game.handle_player_button("y")
    game.handle_player_button("down")
    game.handle_player_button("a")
    assert session.latest().started and game._page == "new-confirm"
    game.handle_player_button("a")
    assert not session.latest().started and saved(session)["game_id"] == 1


def test_failed_nimbus_action_keeps_draft_and_physical_inputs_do_not_clear_it(session):
    from animation.core.manager import PreviewLEDController
    from animation.plugins.cribbage_leaderboard import CribbageLeaderboardAnimation
    token = session.admit(RED, 4, 1, RED_MAC)
    session.request("start")
    session.request("local_action", 0, 0x7FFFFFFF, 0)
    game = CribbageLeaderboardAnimation(PreviewLEDController(33, 138), source=session)
    game.set_player_control(True)
    game.handle_player_button("right")
    game.handle_player_button("x")
    assert game._drafts[0] == 1 and game._feedback == "REJECTED"
    assert session.receive(token, player_message(score=-1)) == Result.OK
    assert game._drafts[0] == 1
    game.handle_player_button("x")
    assert game._drafts[0] == 0
    game.handle_player_button("right")
    game.set_player_paused(True)  # Composer owns input, physical play continues.
    assert game._drafts == [0] * 4
    assert session.receive(token, player_message(score=-1, operationId=2)) == Result.OK
    game.cleanup()
    assert session.receive(token, player_message(score=-1, operationId=3)) == Result.OK


def test_render_reads_only_cached_host_snapshots_and_handles_int32_scores(session, monkeypatch):
    import numpy as np
    from animation.core.manager import PreviewLEDController
    from animation.plugins.cribbage_leaderboard import CribbageLeaderboardAnimation, _score_text
    start_local(session)
    rewrite(session, scores=[-0x80000000, 0x7FFFFFFF, 0, 0])
    def forbidden(*_args):
        raise AssertionError("native call or disk write during render")
    monkeypatch.setattr(session._game, "read", forbidden)
    monkeypatch.setattr(authority, "durable_save", forbidden)
    game = CribbageLeaderboardAnimation(PreviewLEDController(33, 138), source=session)
    game.set_player_control(True)
    pixels = game.generate_frame(0.5, 1).pixels.copy()
    assert pixels.shape == (4554, 3) and pixels.dtype == np.uint8 and pixels.any()
    assert len(_score_text(session.latest(), 0)) <= 8
    assert "E" in _score_text(session.latest(), 0)
    assert not game.generate_frame(0.5, 2).changed


def test_blue_z_peer_real_native_updates_acks_heartbeats_and_disconnects(session):
    import asyncio
    from drivers.cribbage_ble import BlueZCentral, CHARACTERISTIC, DEVICE, DOWNLINK, IDENTITY, PROTOCOL, SERVICE, UPLINK
    central = BlueZCentral.__new__(BlueZCentral)
    central.session = session
    central.links, central.error = {}, ""
    central._stop = threading.Event()
    path = "/org/bluez/hci0/dev_test"
    service_path = path + "/service"
    chars = {uuid: service_path + "/char" + str(index) for index, uuid in enumerate((IDENTITY, PROTOCOL, UPLINK, DOWNLINK))}
    writes, calls = [], []
    async def properties(_path, _interface):
        return {"ServicesResolved": True, "AddressType": "public", "Address": "98:3D:AE:EA:0F:41"}
    async def objects():
        return {service_path: {"org.bluez.GattService1": {"Device": path, "UUID": SERVICE}},
                **{name: {CHARACTERISTIC: {"UUID": uuid, "Service": service_path}} for uuid, name in chars.items()}}
    async def call(target, interface, member, signature="", body=None, **kwargs):
        calls.append((target, interface, member))
        if member == "ReadValue":
            return [(RED if target == chars[IDENTITY] else 4).to_bytes(4 if target == chars[IDENTITY] else 2, "little")]
        if member == "WriteValue":
            from dbus_next import Message
            Message(destination="org.bluez", path=target, interface=interface, member=member,
                    signature=signature, body=body)._marshall()  # Real D-Bus representation.
            writes.append((time.monotonic(), json.loads(body[0])))
        return []
    central.call, central.properties, central.objects = call, properties, objects
    async def exercise():
        task = asyncio.create_task(central.peer(path))
        await asyncio.sleep(0.1)
        assert session.latest().connected == 1
        session.request("set_lobby_mode", 1, 0)
        session.request("start")
        queue = central.links[path]["queue"]
        queue.put_nowait(player_message(turnPassed=True))
        queue.put_nowait(player_message(turnPassed=True))
        queue.put_nowait(player_message(fromNodeId=BLUE))
        await asyncio.sleep(0.25)
        assert session.latest().scores[0] == 3
        assert session.request("local_action", 0, 2, 0)
        await asyncio.sleep(1.75)
        central._stop.set()
        await task
    asyncio.run(exercise())
    assert not central.error and len(writes) >= 6
    assert any(wire["scores"][0] == 5 and wire["turn"] == 3 for _, wire in writes)
    assert writes[-1][1] == writes[-2][1]
    assert 1.4 <= writes[-1][0] - writes[-2][0] < 1.8
    assert session.latest().connected == 0 and session.latest().roster == 3
    assert calls[-1] == (path, DEVICE, "Disconnect")


def test_preview_parameter_never_controls_the_live_game(session):
    from animation.core.manager import PreviewLEDController
    from animation.plugins.cribbage_leaderboard import CribbageLeaderboardAnimation
    game = CribbageLeaderboardAnimation(PreviewLEDController(33, 138), {"serial_port": "preview"}, source=session)
    before = saved(session)
    game.set_player_control(True)
    for button in ("right", "a", "a", "x"):
        game.handle_player_button(button)
    assert saved(session) == before


def test_production_nimbus_routing_menu_scene_handoff_keeps_shared_session(session, tmp_path):
    from functools import partial
    from types import SimpleNamespace
    from animation.core.manager import AnimationManager
    from ipc.playlist_runtime import PlaylistRunner
    from ipc.runtime_control import controller_activation_coordinator
    from scripts.simulate_wall_composer import SimulatedWall
    from scripts.start_server import handle_command, nimbus_command, poll_wall_composer
    from web.composer_content import gallery_entries
    from web.starter_looks import get_starter
    from web.wall_composer import WallComposer
    wall = SimulatedWall()
    wall.cribbage_session = session
    manager = AnimationManager(wall, auto_start=False)
    manager.set_output_brightness(80)
    manager._launch_animation_loop = lambda: setattr(manager, "is_running", True)
    owner = controller_activation_coordinator(manager)
    runner = PlaylistRunner(manager, owner)
    gamepad = SimpleNamespace(connected=True, shoulder_bank=0, poll=lambda *_a, **_kw: (),
                              discard_pending=lambda: None, handoff=lambda _target: None)
    menu = WallComposer(manager, owner, runner, partial(nimbus_command, manager, owner, gamepad), ROOT, tmp_path, tmp_path)
    item = next(item for item in gallery_entries(menu.catalog, menu.presets) if item["component_id"] == "cribbage_leaderboard")
    scene = menu._with_animation(item, None)
    def press(*buttons):
        gamepad.poll = lambda *_a, **_kw: buttons
        poll_wall_composer(manager, gamepad, menu)
    try:
        assert handle_command(manager, "start_scene", {"scene": scene})
        press("a")
        assert manager.nimbus_game()[1] == "playing"
        for button in ("right", "down", "right", "a", "a", "right", "x"):
            press(button)
        assert session.latest().scores[1] == 1
        press("right")
        press("menu", "a")  # Handoff discards the rest of this report.
        assert menu.is_open and session.latest().scores[1] == 1
        assert session.request("local_action", 0, 4, 0)
        menu.close()
        press("a")
        press("x")  # Draft was discarded by Menu.
        assert session.latest().scores[1] == 1
        press("b")
        assert manager.nimbus_game()[1] == "ready"
        press("a")
        press("right")
        assert handle_command(manager, "start_scene", {"scene": get_starter("aurora")["scene"]})
        assert session.request("local_action", 1, 2, 0)
        assert handle_command(manager, "start_scene", {"scene": scene})
        press("a")
        press("x")
        assert session.latest().scores == (4, 3, 0, 0)
        assert manager.output_brightness == 80
    finally:
        manager.stop_animation(clear_leds=False)


def test_deployment_packages_pinned_source_and_checks_pi_build_before_start(tmp_path, monkeypatch):
    from tools.deployment import deploy_entrypoint, deploy_target
    paths = deploy_entrypoint.source_paths(ROOT)
    required = {Path("vendor/scorebot/native/Makefile"), Path("vendor/scorebot/native/scorebot.cpp"),
                Path("vendor/scorebot/native/python/scorebot.py"), Path("vendor/scorebot/src/BoardRole.cpp"),
                Path("vendor/scorebot/test/native/test_c_api.c"), Path("vendor/scorebot/platformio.pi.ini")}
    assert required <= set(paths)
    assert not any("build" in path.parts and path.parts[0] == "vendor" for path in paths)
    assert (ROOT / "vendor/scorebot/REVISION").read_text().strip() == "af383b408234fb17d4078d93dd23e28c10fcf20f"
    calls = []
    app = tmp_path / "app"
    app.mkdir()
    (app / "requirements-pi.lock").write_text("")
    monkeypatch.setattr(deploy_target, "stop_ledgrid_service", lambda: None)
    monkeypatch.setattr(deploy_target, "install_app", lambda _root: app)
    monkeypatch.setattr(deploy_target, "ensure_python_build_dependencies", lambda: None)
    monkeypatch.setattr(deploy_target, "command", lambda *args, **kwargs: calls.append(tuple(map(str, args))))
    monkeypatch.setattr(deploy_target, "write_service", lambda *_: {})
    monkeypatch.setattr(deploy_target, "flash_firmware", lambda *_: None)
    monkeypatch.setattr(deploy_target, "readiness", lambda *_: None)
    deploy_target.deploy(tmp_path, "python", 1)
    check = next(index for index, call in enumerate(calls) if str(app / "tools/build_scorebot.py") in call)
    start = next(index for index, call in enumerate(calls) if "start" in call)
    assert check < start and "--configure" in calls[check]


def test_build_uses_the_same_verified_identity_for_native_and_player_profile(tmp_path, monkeypatch, capsys):
    from tools import build_scorebot
    identity_path = tmp_path / "identity.json"
    monkeypatch.setattr(build_scorebot, "public_adapter", lambda _adapter: "12:34:56:78:9A:BC")
    calls = []
    monkeypatch.setattr(build_scorebot.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    build_scorebot.build(identity_path, configure=True)
    identity = json.loads(identity_path.read_text())
    assert identity == {"node": 0x56789ABC, "mac": "12:34:56:78:9A:BC", "adapter": "hci0"}
    args, kwargs = calls[0]
    assert args[0][-1] == "check"
    assert kwargs["env"]["SCOREBOT_LEADER_NODE_ID"] == str(identity["node"])
    assert kwargs["env"]["SCOREBOT_LEADER_BLUETOOTH_MAC"] == "0x123456789ABCULL"
    output = capsys.readouterr().out
    assert "SCOREBOT_LEADER_NODE_ID=" + str(identity["node"]) in output
    assert "SCOREBOT_LEADER_BLUETOOTH_MAC=0x123456789ABCULL" in output
    assert "platformio.pi.ini" in output and "pi_players" in output
    monkeypatch.setattr(build_scorebot, "public_adapter", lambda _adapter: "12:34:56:78:9A:BD")
    with pytest.raises(ValueError, match="differs"):
        build_scorebot.build(identity_path)
    assert len(calls) == 1


def test_build_refuses_missing_identity_or_nonpublic_adapter(tmp_path, monkeypatch):
    from tools import build_scorebot
    with pytest.raises(ValueError, match="Configure"):
        build_scorebot.build(tmp_path / "identity.json")
    monkeypatch.setattr(build_scorebot.subprocess, "check_output", lambda *args, **kwargs: 's "random"')
    with pytest.raises(ValueError, match="public"):
        build_scorebot.public_adapter("hci0")


def test_all_local_host_can_disable_ble_and_missing_identity_is_visible(tmp_path, native, monkeypatch):
    from drivers import cribbage_runtime
    (tmp_path / "config").mkdir()
    identity_path = tmp_path / "config/scorebot_identity.json"
    identity_path.write_text(json.dumps({"node": 0x10203040, "mac": "12:34:56:78:9A:BC", "adapter": "hci0"}))
    real_session = CribbageSession
    monkeypatch.setattr(authority, "CribbageSession", lambda path, identity: real_session(path, library=native, identity=identity))
    monkeypatch.setenv("SCOREBOT_BLE", "0")
    host = cribbage_runtime.ScorebotHost(tmp_path)
    try:
        assert host.transport is None and not host.status()["error"]
        start_local(host.session)
        assert host.session.request("local_action", 0, 9, 0)
        assert host.status()["scores"] == [9, 0, 0, 0]
    finally:
        host.close()
    identity_path.unlink()
    unavailable = cribbage_runtime.ScorebotHost(tmp_path)
    assert "unavailable" in unavailable.status()["error"]
    assert json.loads((tmp_path / "run_state/cribbage.json").read_text())["scores"][0] == 9
    unavailable.close()


def test_first_deployment_relative_control_path_uses_personal_data_root(tmp_path, native, monkeypatch):
    from drivers.cribbage_runtime import ScorebotHost, scorebot_data_root
    from tools.deployment.deploy_target import DATA_DIRECTORIES, link_data
    app, data = tmp_path / "app", tmp_path / "data"
    app.mkdir()
    (app / "config").mkdir()
    for name in DATA_DIRECTORIES:
        (data / name).mkdir(parents=True)
    link_data(tmp_path, app, data)
    identity = {"node": 0x10203040, "mac": "12:34:56:78:9A:BC", "adapter": "hci0"}
    (data / "config/scorebot_identity.json").write_text(json.dumps(identity))
    assert not (app / "config/scorebot_identity.json").exists()
    monkeypatch.chdir(app)
    monkeypatch.delenv("LEDGRID_DATA_DIR", raising=False)
    assert scorebot_data_root("run_state/control.json") == data
    monkeypatch.setenv("LEDGRID_DATA_DIR", str(data))
    assert scorebot_data_root("unrelated/control.json") == data
    real_session = CribbageSession
    monkeypatch.setattr(authority, "CribbageSession", lambda path, identity: real_session(path, library=native, identity=identity))
    monkeypatch.setenv("SCOREBOT_BLE", "0")
    host = ScorebotHost(scorebot_data_root("run_state/control.json"))
    try:
        assert not host.status()["error"]
        start_local(host.session)
        assert (data / "run_state/cribbage.json").is_file()
    finally:
        host.close()


def test_scores_and_controls_avoid_installed_foliage_and_globes(session):
    import numpy as np
    from animation.core.manager import PreviewLEDController
    from animation.plugins.cribbage_leaderboard import CribbageLeaderboardAnimation
    from web.composer_final_preview import installed_plant_geometry
    geometry = installed_plant_geometry(ROOT)
    mask = (geometry.foliage | geometry.globes)[:, ::-1]
    game = CribbageLeaderboardAnimation(PreviewLEDController(33, 138), source=session)
    game.set_player_control(True)
    for started in (False, True):
        if started:
            start_local(session, (0, 1, 2, 3))
            session.request("local_action", 0, -0x80000000, 0)
        for page in ("", "options", "new-confirm", "start-confirm"):
            game._page = page
            frame = game.generate_frame(1.1, 1)
            canvas = frame.pixels.reshape(33, 138, 3)[:, ::-1]
            assert not np.any(canvas[mask]), "committed score or control text is behind an obstruction"
