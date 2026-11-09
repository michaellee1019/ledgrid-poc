"""Parse cribbage leaderboard snapshots forwarded by the wall bridge.

The wire object is the leaderboard's ``state`` JSON. Its field names and
protocol number live in ``cribbage_board_robot/lib/scorebot/src``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import threading
from typing import Optional


# scorebot::kWireProtocolVersion
PROTOCOL_VERSION = 4

ROLE_RED = 2
ROLE_BLUE = 3
ROLE_GREEN = 4
ROLE_WHITE = 5
PLAYER_ROLES = (ROLE_RED, ROLE_BLUE, ROLE_GREEN, ROLE_WHITE)
PLAYER_NAMES = ("RED", "BLUE", "GREEN", "WHITE")
PLAYER_COLORS = (
    (255, 0, 0),
    (0, 0, 255),
    (0, 255, 0),
    (255, 255, 255),
)


@dataclass(frozen=True)
class LeaderboardState:
    """One authoritative scoreboard snapshot."""

    started: bool
    turn: int
    scores: tuple[int, int, int, int]
    connected: int
    roster: int
    term: int
    version: int

    def revision(self) -> tuple[int, int]:
        return (self.term, self.version)


def _exact_int(value: object) -> bool:
    return type(value) is int


def _mask(value: object) -> int:
    if not _exact_int(value) or not 0 <= value <= 0x0F:
        raise ValueError("player mask is out of range")
    return value


def parse_state_document(payload: object) -> LeaderboardState:
    """Return a snapshot, or raise ValueError when the document is not one."""

    if not isinstance(payload, dict):
        raise ValueError("state must be an object")
    if payload.get("type") != "state" or payload.get("protocol") != PROTOCOL_VERSION:
        raise ValueError("state protocol does not match")
    scores = payload.get("scores")
    if (
        not isinstance(scores, list)
        or len(scores) != 4
        or any(not _exact_int(score) or score < -999 or score > 9999 for score in scores)
    ):
        raise ValueError("scores must be four small integers")
    turn = payload.get("turn")
    if not _exact_int(turn) or turn not in {0, 1, *PLAYER_ROLES}:
        raise ValueError("turn is not a known role")
    started = payload.get("started")
    if type(started) is not bool:
        raise ValueError("started must be boolean")
    if not _exact_int(payload.get("term")) or not _exact_int(payload.get("version")):
        raise ValueError("revision must be integers")
    return LeaderboardState(
        started=started,
        turn=turn,
        scores=(scores[0], scores[1], scores[2], scores[3]),
        connected=_mask(payload.get("connected", 0)),
        roster=_mask(payload.get("roster", 0)),
        term=payload["term"],
        version=payload["version"],
    )


def parse_state_line(line: str) -> Optional[LeaderboardState]:
    """Parse one USB line. Non-snapshot lines return None."""

    text = line.strip()
    if text.startswith("STATE "):
        text = text[6:].strip()
    if not text.startswith("{"):
        return None
    try:
        return parse_state_document(json.loads(text))
    except (ValueError, json.JSONDecodeError):
        return None


class MemoryLeaderboardSource:
    """Test double that serves one snapshot."""

    def __init__(self, state: Optional[LeaderboardState] = None) -> None:
        self.state = state

    def latest(self) -> Optional[LeaderboardState]:
        return self.state

    def close(self) -> None:
        return None


class SerialLeaderboardSource:
    """Background reader for ``STATE {json}`` lines from ESP32 6."""

    def __init__(self, port: str) -> None:
        self._port = port
        self._state: Optional[LeaderboardState] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="cribbage-leaderboard", daemon=True
        )
        self._thread.start()

    def latest(self) -> Optional[LeaderboardState]:
        with self._lock:
            return self._state

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=0.6)

    def _remember(self, state: LeaderboardState) -> None:
        with self._lock:
            if self._state is not None and state.revision() < self._state.revision():
                return
            self._state = state

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                # The browser catalog cannot import host-only pyserial. Load it
                # by name only when a wall port is actually configured.
                serial = __import__("serial")
                with serial.Serial(self._port, 115200, timeout=0.4) as handle:
                    while not self._stop.is_set():
                        raw = handle.readline()
                        if not raw:
                            continue
                        parsed = parse_state_line(raw.decode("utf-8", errors="replace"))
                        if parsed is not None:
                            self._remember(parsed)
            except Exception:
                if self._stop.wait(1.0):
                    return
