"""Real v5 core: durable pairing, controller receipts and transport reservations."""
import json

import pytest

from drivers import cribbage_session as authority
from drivers.cribbage_ble import advertisement, decode_message
from drivers.cribbage_session import CribbageSession, Result
from tests.unit.cribbage_pairing import admit, packet
from tests.unit import test_cribbage_authority as fixtures
from tests.unit.test_cribbage_authority import saved, start_local

native = fixtures.native


def control(session, node=101, operation=1, **changes):
    data = {"type": "control", "protocol": 5, "from": node, "op": operation,
            "game": saved(session)["game_id"], "term": saved(session)["term"],
            "command": 0, "player": 0, "value": 3, "pass": False}
    data.update(changes)
    return json.dumps(data, separators=(",", ":")).encode()


@pytest.fixture
def session(tmp_path, native):
    value = CribbageSession(tmp_path / "game.json", library=native)
    yield value
    value.close()


def test_runtime_pairing_persists_and_old_native_record_migrates_without_score_loss(tmp_path, native):
    path = tmp_path / "game.json"
    with_session = CribbageSession(path, library=native)
    start_local(with_session)
    assert with_session.request("local_action", 0, 81, 0)
    old = saved(with_session)
    old["last_operation"] = [9, 8, 7, 6]
    with_session.close()
    path.write_text(json.dumps(old))  # The exact v4 persistence representation.
    migrated = CribbageSession(path, library=native)
    try:
        record = json.loads(path.read_text())
        assert set(record) == {"pairing", "state", "controllers", "bindings"}
        assert saved(migrated)["scores"] == old["scores"]
        assert saved(migrated)["last_operation"] == old["last_operation"]
        assert saved(migrated)["term"] == old["term"] + 1
        assert saved(migrated)["leader_id"] == old["leader_id"]
        pairing = record["pairing"]
        assert 0 <= pairing["pairing_id"] <= 20 and pairing["session_id"] > 0
        assert migrated.discovery()[17] == migrated.latest().pairing == pairing["pairing_id"]
    finally:
        migrated.close()
    resumed = CribbageSession(path, library=native)
    try:
        assert json.loads(path.read_text())["pairing"] == pairing
        assert saved(resumed)["scores"] == old["scores"]
        assert json.loads(resumed.wire())["session"] == pairing["session_id"]
    finally:
        resumed.close()


def test_mirrored_controller_receipt_survives_resume_and_is_distinct_from_players(session, native):
    token = admit(session, 101, 5, 0, 0x123456789ABC, kind=2)
    assert token is not None and session.latest().connected == 0
    for operation, player in ((1, 0), (2, 1)):
        assert session.receive(token, control(session, operation=operation, command=2, player=player, value=0)) == Result.OK
    assert session.receive(token, control(session, operation=3, command=1, value=0)) == Result.OK
    pending = control(session, operation=4, value=7, **{"pass": True})
    assert session.receive(token, pending) == Result.OK
    before = saved(session)
    assert session.receive(token, pending) == Result.DUPLICATE and saved(session) == before
    assert json.loads(session.path.read_text())["controllers"] == [[101, 0, 0, 0], [4, 0, 0, 0]]
    path = session.path
    session.close()
    resumed = CribbageSession(path, library=native)
    try:
        token = admit(resumed, 101, 5, 0, 0x123456789ABC, kind=2)
        assert token is not None
        assert resumed.receive(token, pending) == Result.WRONG_AUTHORITY
        assert resumed.receive(token, control(resumed, operation=4)) == Result.DUPLICATE
        assert resumed.receive(token, control(resumed, operation=5, value=2)) == Result.OK
        assert saved(resumed)["scores"] == [9, 0, 0, 0]
        wire = json.loads(resumed.wire())
        assert wire["controllers"][0] == 101 and wire["controlOps"][0] == 5
    finally:
        resumed.close()


def test_reserved_random_transport_color_survives_sleep_disconnect_and_resume(session, native):
    token = admit(session, 101, 5, 0, 0x123456789ABC, player=0)
    assert token is not None
    start_local(session, (1,))
    notice = json.dumps({"type": "status", "protocol": 5, "fromNodeId": 101, "state": "sleep"}).encode()
    assert session.receive(token, notice) == Result.OK
    session.disconnect(token)
    path = session.path
    session.close()
    resumed = CribbageSession(path, library=native)
    try:
        assert resumed.latest().roster == 3 and resumed.latest().connected == 0
        assert admit(resumed, 102, 5, 0, 0x123456789ABD, player=0) is None
        assert admit(resumed, 101, 5, 0, 0x223456789ABC, player=0) is None
        assert admit(resumed, 101, 5, 1, 0x123456789ABC, player=0) is None
        assert admit(resumed, 101, 5, 0, 0x123456789ABC, player=0) is not None
        assert resumed.latest().sleeping == 0
    finally:
        resumed.close()


def test_recovery_retains_binding_outside_frozen_roster_and_new_game_reopens_it(session, native):
    token = admit(session, 101, 5, 1, 0x123456789ABC, player=0)
    assert session.request("set_lobby_mode", 0, 2)
    start_local(session, (1,))
    session.disconnect(token)
    path = session.path
    session.close()
    resumed = CribbageSession(path, library=native)
    try:
        assert resumed.latest().roster == 2
        assert admit(resumed, 101, 5, 1, 0x123456789ABC, player=0) is None
        assert resumed.new_game()
        assert admit(resumed, 101, 5, 1, 0x123456789ABC, player=0) is not None
    finally:
        resumed.close()


def test_failed_binding_persistence_stops_input_and_publication(session, monkeypatch):
    before = session.path.read_bytes()
    monkeypatch.setattr(authority, "durable_save", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(RuntimeError, match="save failed"):
        admit(session, 101, 5, 1, 0x123456789ABC)
    assert session.path.read_bytes() == before and not session.peers
    with pytest.raises(RuntimeError, match="save failed"):
        session.wire()
    with pytest.raises(RuntimeError, match="save failed"):
        session.discovery()


@pytest.mark.parametrize("field,value", [("from", True), ("op", True), ("command", True),
    ("player", True), ("value", True), ("term", True), ("pass", 1), ("player", 4), ("command", 3)])
def test_control_exact_types_before_native_entry(session, monkeypatch, field, value):
    token = admit(session, 101, 5, 1, 0x123456789ABC, kind=2)
    def forbidden(*_args):
        raise AssertionError("Malformed control reached native core")
    monkeypatch.setattr(session._game, "controller_action", forbidden)
    assert session.receive(token, control(session, **{field: value})) == Result.INVALID_ARGUMENT


def test_pairing_characteristic_consistency_and_foreign_session_rejected(session):
    live = packet(session, 101)
    assert session.admit(102, 5, 1, 0x123456789ABC, live) is None
    foreign = bytearray(live)
    foreign[13] ^= 1
    assert session.admit(101, 5, 1, 0x123456789ABC, bytes(foreign)) is None
    assert session.latest().connected == 0
    token = admit(session, 101, 5, 1, 0x123456789ABC, kind=3)
    assert token is not None and session.receive(token, control(session)) == Result.WRONG_AUTHORITY


@pytest.mark.parametrize("field", ["pairing", "controllers", "bindings"])
def test_incomplete_v5_record_fails_without_replacing_original(session, native, field):
    path = session.path
    record = json.loads(path.read_text())
    del record[field]
    session.close()
    original = json.dumps(record)
    path.write_text(original)
    with pytest.raises((ValueError, KeyError, TypeError, RuntimeError)):
        CribbageSession(path, library=native)
    assert path.read_text() == original


def test_discovery_advertisement_is_compact_and_updates_from_native_occupied_mask(session):
    advert = advertisement(session.discovery())
    from dbus_next.service import ServiceInterface
    before = ServiceInterface._get_all_property_values
    values = []
    before(advert, lambda _interface, properties, _user_data, error: values.append((properties, error)))
    props, error = values[0]
    assert error is None and props["Type"].value == "peripheral"
    assert props["ManufacturerData"].value[0xFFFF].value == session.discovery()[2:]
    assert len(session.discovery()) + 2 + 3 <= 31
    assert admit(session, 101, 5, 1, 0x123456789ABC) is not None
    assert session.discovery() != advert.packet and session.discovery()[21] == 1
    advert.packet = session.discovery()
    advert.active = True
    advert.Release()
    assert not advert.active


def test_control_lobby_range_and_v4_payload_fail_closed(session):
    with pytest.raises(ValueError, match="lobby"):
        decode_message(control(session, command=2, value=3), 5)
    with pytest.raises(ValueError, match="protocol"):
        decode_message(control(session, protocol=4), 5)


def test_blue_z_finds_fresh_manufacturer_only_peer_and_refreshes_advertisement(session, monkeypatch):
    import asyncio
    import threading
    from dbus_next import Variant
    import dbus_next.aio
    from drivers.cribbage_ble import AD_MANAGER, BlueZCentral, DEVICE

    class Bus:
        async def connect(self):
            return self

        def export(self, path, advert):
            self.advert = advert

        def add_message_handler(self, _handler):
            pass

        def disconnect(self):
            self.closed = True

    bus = Bus()
    monkeypatch.setattr(dbus_next.aio, "MessageBus", lambda **_kwargs: bus)
    central = BlueZCentral.__new__(BlueZCentral)
    central.session, central.identity = session, {"adapter": "hci0"}
    central.links, central.error, central._stop = {}, "", threading.Event()
    central._peer_errors = {}
    path = "/org/bluez/hci0/dev_fresh"
    offered = packet(session, 101)
    advertisements, filters, peers = [], [], []
    iterations = 0

    async def call(_path, interface, member, signature="", body=None, **_kwargs):
        if member == "GetNameOwner":
            return [":1.42"]
        if member == "SetDiscoveryFilter":
            filters.append(body[0])
        if interface == AD_MANAGER and member == "RegisterAdvertisement":
            if bus.advert.Type != "peripheral":
                raise RuntimeError("Pi rejects random-address changes while scanning")
            advertisements.append(bus.advert.packet)
        return []

    async def objects():
        nonlocal iterations
        iterations += 1
        if iterations == 2:
            central._stop.set()
        foreign = bytearray(offered)
        foreign[13] ^= 1
        return {path: {DEVICE: {"ManufacturerData": {0xFFFF: Variant("ay", offered[2:])}}},
                path + "_foreign": {DEVICE: {"ManufacturerData": {0xFFFF: Variant("ay", bytes(foreign)[2:])}}}}

    async def peer(candidate):
        assert not session.peers, "Discovery must never admit peers before live GATT validation"
        peers.append(candidate)
        assert admit(session, 101, 5, 1, 0x123456789ABC) is not None

    async def properties(_path, _interface):
        return {"Powered": True}

    central.call, central.objects = call, objects
    central.peer, central.properties = peer, properties
    asyncio.run(central.run())
    assert peers == [path] and not central.error and bus.closed
    assert set(filters[0]) == {"Transport"}
    assert len(advertisements) == 2 and advertisements[0][21] == 0 and advertisements[1][21] == 1


def test_saved_controller_receipt_cannot_lose_its_validated_transport_binding(session, native):
    token = admit(session, 101, 5, 1, 0x123456789ABC, kind=2)
    assert session.receive(token, control(session)) == Result.OK  # Consumed before Start, no score effect.
    path = session.path
    record = json.loads(path.read_text())
    record["bindings"] = []
    session.close()
    original = json.dumps(record)
    path.write_text(original)
    with pytest.raises(ValueError, match="controller binding"):
        CribbageSession(path, library=native)
    assert path.read_text() == original


def test_v5_wire_limit_checks_bytes_before_accepting_valid_json(session):
    raw = control(session)
    padded = raw + b" " * (512 - len(raw))
    assert decode_message(padded, 5)["type"] == "control"
    with pytest.raises(ValueError, match="size"):
        decode_message(padded + b" ", 5)


def test_pairing_number_invalidates_render_cache_without_native_calls(session, monkeypatch):
    from dataclasses import replace
    from animation.core.manager import PreviewLEDController
    from animation.plugins import cribbage_leaderboard as renderer

    texts = []
    blit = renderer._blit
    def record(view, x, y, text, color):
        texts.append(text)
        return blit(view, x, y, text, color)
    monkeypatch.setattr(renderer, "_blit", record)
    def forbidden():
        raise AssertionError("Renderer accessed the native library")
    monkeypatch.setattr(session._game, "pairing_packet", forbidden)
    game = renderer.CribbageLeaderboardAnimation(PreviewLEDController(33, 138), source=session)
    session._view = replace(session.latest(), pairing=20)
    assert game.generate_frame(.5, 1).changed and "PAIR 20" in texts
    assert not game.generate_frame(.5, 2).changed
    session._view = replace(session.latest(), pairing=19)
    assert game.generate_frame(.5, 3).changed and "PAIR 19" in texts
