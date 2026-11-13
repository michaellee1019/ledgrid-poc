"""Nonblocking input from the paired original SteelSeries Nimbus."""

from dataclasses import dataclass
import logging
import os
import struct
import time


DEVICE_PATH = "/dev/ledgrid-nimbus"
REPORT_SIZE = 17
MAX_REPORTS_PER_POLL = 64
REOPEN_SECONDS = 1.0
FIRST_REPEAT_SECONDS = 0.25
REPEAT_SECONDS = 0.10
FAST_REPEAT_SECONDS = 0.75

# The unnumbered GC-00004 input report has pressure bytes for the D-pad
# and face buttons, followed by a Menu bit in byte 12.
PRESSURE_ACTIONS = ("rotate-left", "right", "down", "left", "rotate-right", "drop", "release")
MENU_BUTTONS = ("up", "right", "down", "left", "a", "b", "y")
MENU_REPEAT = frozenset({"up", "right", "down", "left"})
REPEAT_ACTIONS = frozenset({"left", "right", "down"})
logger = logging.getLogger(__name__)

STICK_CENTER = 20
STICK_ENGAGE = 36
STICK_DIAGONAL = 64
STICK_FULL = 100
SHOULDER_PRESS = 32
SHOULDER_RELEASE = 16
STICK_REPEAT_SECONDS = .18


@dataclass(frozen=True)
class StickBank:
    bank: int


@dataclass(frozen=True)
class StickMotion:
    axis: int  # LX, LY, RX, RY
    steps: int
    bank: int = 0  # L1, R1, L2, R2 in ascending bit order
    repeated: bool = False


class _StickAxis:
    def __init__(self, index):
        self.index = index
        self.reset(0)

    def reset(self, value):
        self.armed = abs(value) <= STICK_CENTER
        self.sign = 0
        self.magnitude = 0
        self.repeat_at = None

    def read(self, value, threshold, now, bank):
        if abs(value) <= STICK_CENTER:
            self.reset(value)
        elif self.armed and abs(value) >= threshold:
            self.armed = False
            self.sign = 1 if value > 0 else -1
            self.magnitude = abs(value)
            self.repeat_at = now + FIRST_REPEAT_SECONDS
            return self.motion(bank)
        elif self.sign:
            self.magnitude = value * self.sign
            if self.magnitude <= STICK_CENTER:
                self.reset(value)
        return None

    def repeat(self, now, bank):
        if self.repeat_at is not None and now >= self.repeat_at:
            self.repeat_at = now + STICK_REPEAT_SECONDS
            return self.motion(bank, repeated=True)
        return None

    def motion(self, bank, *, repeated=False):
        steps = self.sign * (2 if self.magnitude >= STICK_FULL else 1)
        return StickMotion(self.index, steps, bank, repeated)


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
        self._pressed_at: dict[str, float] = {}
        self._axes = tuple(_StickAxis(index) for index in range(4))
        self._stick_values = (0, 0, 0, 0)
        self._stick_bank = 0
        self._hud_bank = 0
        self._tuning_sticks = False

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
        self._stick_values = (0, 0, 0, 0)
        self._stick_bank = 0
        self._hud_bank = 0
        self._clear_repeats()
        self._reset_sticks()

    def poll(self, target: object | None, *, menu_buttons=False, navigating=False,
             tuning_sticks=False, browsing=False, track_bank=False) -> tuple[str | StickBank | StickMotion, ...]:
        """Read a bounded batch, keeping held inputs within their current mode."""
        now = self._clock()
        watch_bank = navigating or track_bank
        if not watch_bank:
            self._hud_bank = 0
        if tuning_sticks != self._tuning_sticks:
            self._tuning_sticks = tuning_sticks
            self._reset_sticks()
        if target is not self._target:
            self._target = target
            self.discard_pending()
        if self._fd is None:
            if not self._open(now):
                return ()
            self._discard_pending = True
        assert self._fd is not None

        actions = []
        motions = {}
        for _ in range(MAX_REPORTS_PER_POLL):
            try:
                report = os.read(self._fd, 64)
            except BlockingIOError:
                if self._discard_pending:
                    self._reset_sticks()
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

            bank = self._read_shoulders(report)
            if bank != self._stick_bank:
                self._stick_bank = bank
                # A centered axis can move in the same report as a new chord.
                # A previously held axis must return to center first.
                self._reset_sticks()
                motions.clear()
            # The Nimbus reports LX, LY, RX, RY with right and up positive.
            self._stick_values = struct.unpack_from("4b", report, 13)
            menu = "menu" if menu_buttons else "toggle-player"
            buttons = MENU_BUTTONS if menu_buttons else PRESSURE_ACTIONS
            held = (menu,) if report[12] & 1 else ()
            held += tuple(action for pressure, action in zip(report, buttons) if pressure)
            if target is not None and not self._discard_pending:
                if menu_buttons and menu in held and menu not in self._held:
                    # Menu owns its report. Leave later reports in the device
                    # queue so the controller can read them in the new mode.
                    self._held = held
                    return (*actions, menu)
                repeat = MENU_REPEAT if navigating else () if browsing else REPEAT_ACTIONS
                self._record_edges(held, actions, now, repeat)
                if tuning_sticks:
                    for axis, value in zip(self._axes, self._stick_values):
                        sibling = self._stick_values[axis.index ^ 1]
                        threshold = STICK_DIAGONAL if abs(value) < abs(sibling) else STICK_ENGAGE
                        motion = axis.read(value, threshold, now, bank)
                        if motion is not None:
                            motions[axis.index] = motion
            self._held = held

        for action, deadline in self._repeat_at.items():
            if now >= deadline:
                if navigating and action in ("left", "right"):
                    prefix = "fast-" if now - self._pressed_at[action] >= FAST_REPEAT_SECONDS else "repeat-"
                    actions.append(prefix + action)
                else:
                    actions.append(action)
                self._repeat_at[action] = now + REPEAT_SECONDS
        if watch_bank and target is not None and not self._discard_pending and self._hud_bank != self._stick_bank:
            self._hud_bank = self._stick_bank
            actions.insert(0, StickBank(self._hud_bank))
        if tuning_sticks:
            for axis in self._axes:
                motion = axis.repeat(now, self._stick_bank)
                if motion is not None:
                    motions[axis.index] = motion
            actions.extend(motions[index] for index in sorted(motions))
        return tuple(actions)

    def handoff(self, target: object) -> None:
        """Keep the button baseline while cancelling repeats at a local transition."""
        self._target = target
        self._clear_repeats()
        self._reset_sticks()

    def discard_pending(self) -> None:
        self._discard_pending = True
        self._clear_repeats()
        self._reset_sticks()

    def _read_shoulders(self, report):
        # GC-00004 pressure offsets 8..11 are L1, R1, L2, R2.
        bank = 0
        for index, pressure in enumerate(report[8:12]):
            bit = 1 << index
            threshold = SHOULDER_RELEASE if self._stick_bank & bit else SHOULDER_PRESS
            if pressure >= threshold:
                bank |= bit
        return bank

    def _reset_sticks(self):
        for axis, value in zip(self._axes, self._stick_values):
            axis.reset(value)

    def _clear_repeats(self):
        self._repeat_at.clear()
        self._pressed_at.clear()

    def _record_edges(self, held: tuple[str, ...], actions: list[str], now: float, repeat) -> None:
        for action in self._held:
            if action not in held:
                self._repeat_at.pop(action, None)
                self._pressed_at.pop(action, None)
        for action in held:
            if action not in self._held:
                actions.append(action)
                if action in repeat:
                    self._repeat_at[action] = now + FIRST_REPEAT_SECONDS
                    self._pressed_at[action] = now
