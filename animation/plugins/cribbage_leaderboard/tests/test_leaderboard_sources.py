"""Snapshot acceptance and lifecycle at both existing transport boundaries."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from animation.plugins.cribbage_leaderboard.state import (
    MemoryLeaderboardSource,
    SerialLeaderboardSource,
    parse_state_document,
)
from drivers.wall_roles import SpiLeaderboardSource


def _document(**changes):
    document = {
        "type": "state", "protocol": 4, "started": True, "turn": 2,
        "scores": [8, 15, 4, 21], "connected": 15, "roster": 15,
        "term": 3, "version": 9,
    }
    document.update(changes)
    return document


@pytest.fixture(params=["serial", "spi"])
def reader(request):
    # Keep scheduling deterministic while exercising the real ingestion loops.
    with patch("threading.Thread") as thread:
        source = (
            SerialLeaderboardSource("unused-test-port")
            if request.param == "serial" else SpiLeaderboardSource()
        )
        thread.return_value.start.assert_called_once_with()
        assert thread.call_args.kwargs["daemon"] is True
        source._stop.wait = Mock(return_value=True)

        def ingest(raw):
            source._stop.clear()
            device = MagicMock()
            if request.param == "serial":
                device.__enter__.return_value = device

                def readline():
                    source._stop.set()
                    return raw

                device.readline.side_effect = readline
                serial = SimpleNamespace(Serial=Mock(return_value=device))
                with patch.dict(sys.modules, {"serial": serial}):
                    source._run()
                serial.Serial.assert_called_once_with(
                    "unused-test-port", 115200, timeout=0.4
                )
                device.__exit__.assert_called_once()
            else:
                source._opener = Mock(return_value=device)
                device.xfer2.return_value = b"LGSC" + len(raw).to_bytes(2, "big") + raw
                source._run()
                device.close.assert_called_once_with()
            return source.latest()

        yield source, ingest, 0.6 if request.param == "serial" else 1.2
        source.close()


def test_source_revision_ordering(reader):
    source, ingest, _ = reader
    assert source.latest() is None
    for term, version, expected_revision in [
        (3, 9, (3, 9)),
        (2, 100, (3, 9)),  # A larger version cannot override an older term.
        (3, 8, (3, 9)),
        (3, 10, (3, 10)),
        (4, 0, (4, 0)),  # A new term can reset the version.
        (3, 100, (4, 0)),
    ]:
        state = ingest(json.dumps(_document(term=term, version=version)).encode())
        assert state.revision() == expected_revision


def test_equal_revisions_replace_connection_and_status_fields(reader):
    source, ingest, _ = reader
    first = ingest(json.dumps(_document()).encode())
    replacement = _document(started=False, turn=0, connected=1, roster=3)
    raw = json.dumps(replacement).encode()
    if isinstance(source, SerialLeaderboardSource):
        raw = b"STATE " + raw
    updated = ingest(raw)
    assert updated == parse_state_document(replacement)
    assert updated is not first
    assert updated.revision() == first.revision()


@pytest.mark.parametrize("raw", [
    b"STATE-WAIT id=1", b"{", b"\xff", b"null", b"[]",
    *[
        json.dumps(_document(term=99, version=99, **changes)).encode()
        for changes in [
            {"type": "player"}, {"protocol": 3}, {"started": 1},
            {"turn": True}, {"turn": 6}, {"scores": [1, 2, 3]},
            {"scores": [True, 2, 3, 4]}, {"scores": [-2147483649, 2, 3, 4]},
            {"scores": [2147483648, 2, 3, 4]}, {"connected": 16}, {"roster": -1},
        ]
    ],
    json.dumps(_document(term=True)).encode(),
    json.dumps(_document(version=1.5)).encode(),
    b'{"type":"state","protocol":4}',
])
def test_malformed_input_cannot_establish_or_replace_state(reader, raw):
    source, ingest, _ = reader
    assert ingest(raw) is None
    valid = ingest(json.dumps(_document()).encode())
    assert valid is not None
    assert ingest(raw) is valid
    assert source.latest() is valid


def test_existing_score_bounds_and_optional_masks(reader):
    _, ingest, _ = reader
    document = _document(scores=[-999, 9999, 0, 121])
    del document["connected"], document["roster"]
    state = ingest(json.dumps(document).encode())
    assert state.scores == (-999, 9999, 0, 121)
    assert (state.connected, state.roster) == (0, 0)


def test_transport_errors_retry_with_existing_delay(reader):
    source, _, _ = reader
    source._stop.wait.side_effect = [False, True]
    opener = Mock(side_effect=OSError("test device unavailable"))
    if isinstance(source, SerialLeaderboardSource):
        with patch.dict(sys.modules, {"serial": SimpleNamespace(Serial=opener)}):
            source._run()
    else:
        source._opener = opener
        source._run()
    assert opener.call_count == 2
    assert source._stop.wait.call_args_list == [call(1.0), call(1.0)]
    assert source.latest() is None


def test_close_sets_stop_and_keeps_bounded_join(reader):
    source, ingest, timeout = reader
    state = ingest(json.dumps(_document()).encode())
    source.close()
    assert source._stop.is_set()
    source._thread.join.assert_called_once_with(timeout=timeout)
    assert source.latest() is state
    opener = Mock(side_effect=AssertionError("closed source reopened a device"))
    source._opener = opener
    with patch.dict(sys.modules, {"serial": SimpleNamespace(Serial=opener)}):
        source._run()
    opener.assert_not_called()


def test_memory_source_remains_directly_replaceable():
    source = MemoryLeaderboardSource()
    assert source.latest() is None
    state = parse_state_document(_document())
    source.state = state
    assert source.latest() is state
    source.state = None
    source.close()
    assert source.latest() is None


@pytest.mark.parametrize("first_import", [
    "drivers.wall_roles", "animation.plugins.cribbage_leaderboard.state",
])
def test_import_orders_and_preview_never_load_hardware(first_import):
    script = f"""
import importlib
import sys
from types import SimpleNamespace

class NoHardware:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {{'serial', 'spidev', 'scorebot', 'dbus_next'}}:
            raise AssertionError('hardware import: ' + fullname)

sys.meta_path.insert(0, NoHardware())
importlib.import_module({first_import!r})
from animation.plugins.cribbage_leaderboard import CribbageLeaderboardAnimation

for port in ('', 'preview'):
    animation = CribbageLeaderboardAnimation(
        SimpleNamespace(strip_count=33, leds_per_strip=138), {{'serial_port': port}}
    )
    animation.generate_frame(0.5, 0)
    assert animation._preview
    animation.cleanup()
assert not {{'serial', 'spidev', 'scorebot', 'dbus_next'}} & sys.modules.keys()
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[4],
        capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
