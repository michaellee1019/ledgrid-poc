"""Nonblocking input from the paired original SteelSeries Nimbus."""

import logging
import os
import time


DEVICE_PATH = "/dev/ledgrid-nimbus"
REPORT_SIZE = 17
MAX_REPORTS_PER_POLL = 64
REOPEN_SECONDS = 1.0
FIRST_REPEAT_SECONDS = 0.25
REPEAT_SECONDS = 0.10

# The unnumbered GC-00004 input report has pressure bytes for the D-pad
# and face buttons, followed by a Menu bit in byte 12.
PRESSURE_ACTIONS = ("rotate-left", "right", "down", "left", "rotate-right", "drop", "release")
REPEAT_ACTIONS = frozenset({"left", "right", "down"})
logger = logging.getLogger(__name__)


class NimbusGamepad:
    def __init__(self, path: str = DEVICE_PATH, *, clock=time.monotonic) -> None:
        self.path = path
        self._clock = clock
        self._fd: int | None = None
        self._next_open = 0.0
        self._open_error: int | None = None
        self._target: object | None = None
        self._discard_pending = False
        self._held: tuple[str, ...] = ()
        self._repeat_at: dict[str, float] = {}

    @property
    def connected(self) -> bool:
        return self._fd is not None and os.path.exists(self.path)

    def _open(self, now: float) -> bool:
        if now < self._next_open:
            return False
        self._next_open = now + REOPEN_SECONDS
        try:
            self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        except FileNotFoundError:
            return False
        except OSError as exc:
            if exc.errno != self._open_error:
                logger.warning("Could not open Nimbus input %s: %s", self.path, exc)
                self._open_error = exc.errno
            return False
        self._open_error = None
        return True

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._held = ()
        self._repeat_at.clear()

    def poll(self, target: object | None) -> tuple[str, ...]:
        """Drain a bounded batch, keeping held inputs within one live game."""
        now = self._clock()
        if target is not self._target:
            self._target = target
            self._discard_pending = True
            self._repeat_at.clear()
        if self._fd is None and not self._open(now):
            return ()
        assert self._fd is not None

        actions = []
        for _ in range(MAX_REPORTS_PER_POLL):
            try:
                report = os.read(self._fd, 64)
            except BlockingIOError:
                self._discard_pending = False
                break
            except OSError:
                report = b""
            if not report:
                self.close()
                self._next_open = now + REOPEN_SECONDS
                return ("disconnect",) if target is not None else ()
            if len(report) != REPORT_SIZE:
                continue

            held = ("toggle-player",) if report[12] & 1 else ()
            held += tuple(action for pressure, action in zip(report, PRESSURE_ACTIONS) if pressure)
            if target is not None and not self._discard_pending:
                self._record_edges(held, actions, now)
            self._held = held

        for action, deadline in self._repeat_at.items():
            if now >= deadline:
                actions.append(action)
                self._repeat_at[action] = now + REPEAT_SECONDS
        return tuple(actions)

    def _record_edges(self, held: tuple[str, ...], actions: list[str], now: float) -> None:
        for action in self._held:
            if action not in held:
                self._repeat_at.pop(action, None)
        for action in held:
            if action not in self._held:
                actions.append(action)
                if action in REPEAT_ACTIONS:
                    self._repeat_at[action] = now + FIRST_REPEAT_SECONDS
