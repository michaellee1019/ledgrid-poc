"""Cribbage score receiver on SPI0 CE2.

The sixth wall ESP32 is the board selected by that chip select. The host
sends SetRole there. The board answers on the following transfer with an
``LGSC`` snapshot at the start of MISO. LED receivers use the other chip
selects and never receive this command.
"""

from __future__ import annotations

import binascii
from pathlib import Path
import threading
from typing import Callable, Optional

from drivers.led_layout import SCORE_BRIDGE_SPI


SET_ROLE = 0x0C
ROLE_LED = 0
ROLE_SCORE = 1
# Pad the command so the transfer is long enough to clock a 352-byte snapshot
# back. The firmware ignores every byte after the role.
ROLE_PACKET_BYTES = 352
TRAILER_MAGIC = b"LGSC"
TRAILER_HEADER = 6
SPI_SPEED_HZ = 20_000_000


def role_packet(role: int = ROLE_SCORE) -> bytes:
    if role not in (ROLE_LED, ROLE_SCORE):
        raise ValueError("role must be led or score")
    return bytes((SET_ROLE, role)) + bytes(ROLE_PACKET_BYTES - 2)


def parse_score_trailer(response: bytes) -> Optional[bytes]:
    """Return the JSON body, b"" when the board is listening, or None."""

    if len(response) < TRAILER_HEADER or response[:4] != TRAILER_MAGIC:
        return None
    length = int.from_bytes(response[4:6], "big")
    if length > ROLE_PACKET_BYTES or len(response) < TRAILER_HEADER + length:
        return None
    return bytes(response[TRAILER_HEADER : TRAILER_HEADER + length])


def score_bridge_path(route: tuple[int, int] = SCORE_BRIDGE_SPI) -> Path:
    bus, device = route
    return Path(f"/dev/spidev{bus}.{device}")


def open_score_spi(route: tuple[int, int] = SCORE_BRIDGE_SPI):
    spidev = __import__("spidev")
    spi = spidev.SpiDev()
    bus, device = route
    spi.open(bus, device)
    spi.max_speed_hz = SPI_SPEED_HZ
    spi.mode = 0
    return spi


def _encode_role_envelope(payload: bytes) -> bytes:
    """CRC-covered word-aligned envelope, matching the LED receiver command."""

    semantic_length = len(payload)
    unpadded = 4 + semantic_length + 2
    wire = bytearray(unpadded + (-unpadded) % 4)
    wire[0] = 0x0B
    wire[1] = 1
    wire[2] = (semantic_length >> 8) & 0xFF
    wire[3] = semantic_length & 0xFF
    wire[4 : 4 + semantic_length] = payload
    crc = binascii.crc_hqx(wire[:-2], 0xFFFF)
    wire[-2] = (crc >> 8) & 0xFF
    wire[-1] = crc & 0xFF
    return bytes(wire)


def exchange_role(spi, role: int = ROLE_SCORE) -> bytes:
    wire = _encode_role_envelope(role_packet(role))
    return bytes(spi.xfer2(wire))


def poll_score_bridge(opener: Optional[Callable[[], object]] = None) -> Optional[bytes]:
    """Tell SPI0 CE2 to receive scores and return the previous snapshot."""

    open_device = opener or open_score_spi
    spi = open_device()
    try:
        response = exchange_role(spi, ROLE_SCORE)
    finally:
        close = getattr(spi, "close", None)
        if callable(close):
            close()
    return parse_score_trailer(response)


def assign_score_bridge(opener: Optional[Callable[[], object]] = None) -> bool:
    """Arm the sixth board. A missing spidev node leaves the LED wall running."""

    if opener is None and not score_bridge_path().exists():
        return False
    try:
        poll_score_bridge(opener)
    except Exception as exc:
        print(f"score bridge SPI0 CE2 could not be armed: {exc}", flush=True)
        return False
    return True


class SpiLeaderboardSource:
    """Poll SPI0 CE2 for cribbage snapshots."""

    def __init__(self, opener: Optional[Callable[[], object]] = None) -> None:
        self._opener = opener
        self._state = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="cribbage-score-bridge", daemon=True
        )
        self._thread.start()

    def latest(self):
        with self._lock:
            return self._state

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.2)

    def _remember(self, payload: bytes) -> None:
        import json

        from animation.plugins.cribbage_leaderboard.state import parse_state_document

        try:
            state = parse_state_document(json.loads(payload))
        except (ValueError, json.JSONDecodeError, UnicodeError):
            return
        with self._lock:
            if self._state is not None and state.revision() < self._state.revision():
                return
            self._state = state

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                payload = poll_score_bridge(self._opener)
                if payload:
                    self._remember(payload)
            except Exception:
                pass
            if self._stop.wait(1.0):
                return
