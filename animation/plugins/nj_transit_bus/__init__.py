#!/usr/bin/env python3
"""NJ Transit departure board for the next three buses.

The default stop is Clinton St at 1st St (20491) on the 126 toward Port
Authority. That is the New York-bound stop one block east of 1st & Willow.
The 126 does not board toward PABT on Willow at 1st; Willow Ave at 2nd St
(20529) is the nearby Willow Avenue stop, and those buses are headed back
to Hoboken.

Credentials stay in the controller environment: ``NJT_API_KEY`` (or
``NJT_TOKEN``), or ``NJT_USERNAME`` and ``NJT_PASSWORD``. The animation
polls BUSDATA ``getBusDV`` off the render thread and shows the minutes
until the next three arrivals under a bus sprite.
"""

from __future__ import annotations

import json
import math
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np

from animation import AnimationBase
from animation.core.plant_awareness import PLANT_MODIFIER_IDS


Color = Tuple[float, float, float]
EASTERN = ZoneInfo("America/New_York")
DEFAULT_BASE = "https://pcsdata.njtransit.com/api/BUSDV2"
DEFAULT_ROUTE = "126"
DEFAULT_STOP = "20491"
DEFAULT_DIRECTION = "New York"
MESSAGE_BAND = 7
TOKEN_TTL_SECONDS = 20 * 60 * 60
SOURCE_HZ = 8

# 3x5 glyphs, rows encoded with the left column in bit 2.
FONT = {
    "0": (7, 5, 5, 5, 7), "1": (2, 6, 2, 2, 7),
    "2": (7, 1, 7, 4, 7), "3": (7, 1, 7, 1, 7),
    "4": (5, 5, 7, 1, 1), "5": (7, 4, 7, 1, 7),
    "6": (7, 4, 7, 5, 7), "7": (7, 1, 1, 1, 1),
    "8": (7, 5, 7, 5, 7), "9": (7, 5, 7, 1, 7),
    "A": (2, 5, 7, 5, 5), "B": (6, 5, 6, 5, 6),
    "C": (7, 4, 4, 4, 7), "D": (6, 5, 5, 5, 6),
    "E": (7, 4, 6, 4, 7), "F": (7, 4, 6, 4, 4),
    "G": (7, 4, 5, 5, 7), "H": (5, 5, 7, 5, 5),
    "I": (7, 2, 2, 2, 7), "J": (1, 1, 1, 5, 7),
    "K": (5, 5, 6, 5, 5), "L": (4, 4, 4, 4, 7),
    "M": (5, 7, 7, 5, 5), "N": (5, 7, 7, 7, 5),
    "O": (7, 5, 5, 5, 7), "P": (7, 5, 7, 4, 4),
    "Q": (7, 5, 5, 7, 1), "R": (6, 5, 6, 5, 5),
    "S": (7, 4, 7, 1, 7), "T": (7, 2, 2, 2, 2),
    "U": (5, 5, 5, 5, 7), "V": (5, 5, 5, 5, 2),
    "W": (5, 5, 7, 7, 5), "X": (5, 5, 2, 5, 5),
    "Y": (5, 5, 2, 2, 2), "Z": (7, 1, 2, 4, 7),
    "-": (0, 0, 7, 0, 0), ".": (0, 0, 0, 0, 2),
    " ": (0, 0, 0, 0, 0),
}

BUS_ROWS = (
    "....YYYYYYYYYYYYY....",
    "..YYYYYYYYYYYYYYYYY..",
    ".YYWWYYWWYYWWYYYYYY..",
    ".YYWWYYWWYYWWYYYYYY..",
    ".YYYYYYYYYYYYYYYYYYY.",
    ".YYYYYYYYYYYYYYYDDHH.",
    "..YYYYYYYYYYYYYYYYY..",
    "...KRRK.......KRRK...",
    "....KKK........KKK...",
)
BUS_WIDTH = len(BUS_ROWS[0])
BUS_HEIGHT = len(BUS_ROWS)

BUS_COLORS = {
    "Y": (255.0, 176.0, 24.0),
    "W": (12.0, 28.0, 64.0),
    "K": (28.0, 30.0, 38.0),
    "R": (236.0, 240.0, 248.0),
    "D": (186.0, 78.0, 16.0),
    "H": (255.0, 250.0, 230.0),
}
HEADLIGHT_OFF = (110.0, 84.0, 36.0)

BACKGROUND = np.array((1.0, 3.0, 10.0), dtype=np.float32)
PANEL = np.array((6.0, 14.0, 32.0), dtype=np.float32)
BORDER = np.array((36.0, 78.0, 148.0), dtype=np.float32)
STREAK = np.array((48.0, 32.0, 8.0), dtype=np.float32)
ROUTE_COLOR = (176.0, 214.0, 255.0)
DEST_COLOR = (255.0, 196.0, 110.0)
UNIT_COLOR = (150.0, 118.0, 64.0)
LATER_COLOR = (255.0, 176.0, 64.0)
SOON_COLOR = (255.0, 128.0, 36.0)
DUE_COLOR = (255.0, 244.0, 214.0)
DASH_COLOR = (78.0, 88.0, 112.0)
MESSAGE_COLOR = (255.0, 206.0, 120.0)

GAP_BUS = 3
GAP_CAPTION = 2
GAP_UNIT = 4
GAP_TIME = 4
CAPTION_HEIGHT = 5

_MINUTES = re.compile(r"(\d+)\s*m(?:in(?:ute)?s?)?\b", re.IGNORECASE)
_CLOCK = re.compile(
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap]m)?",
    re.IGNORECASE,
)
_STAMP = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap]m)?",
    re.IGNORECASE,
)
_ROUTE_NUMBER = re.compile(r"\d+")
_BOILERPLATE = "customer service message"


def _glyph_bits(rows: Sequence[int]) -> np.ndarray:
    glyph = np.zeros((3, 5), dtype=bool)
    for y, bits in enumerate(rows):
        for x in range(3):
            glyph[x, y] = bool(bits & (1 << (2 - x)))
    return glyph


GLYPHS = {char: _glyph_bits(rows) for char, rows in FONT.items()}


@lru_cache(maxsize=128)
def _scaled_glyph(char: str, scale: int) -> np.ndarray:
    glyph = GLYPHS.get(char, GLYPHS[" "])
    if scale <= 1:
        return glyph
    return np.repeat(np.repeat(glyph, scale, axis=0), scale, axis=1)


def text_width(text: str, scale: int = 1, spacing: int = 1) -> int:
    if not text:
        return 0
    return len(text) * (3 * scale + spacing) - spacing


def _bus_sprite(headlight_on: bool) -> Tuple[np.ndarray, np.ndarray]:
    image = np.zeros((BUS_WIDTH, BUS_HEIGHT, 3), dtype=np.float32)
    mask = np.zeros((BUS_WIDTH, BUS_HEIGHT), dtype=bool)
    for y, row in enumerate(BUS_ROWS):
        for x, cell in enumerate(row):
            if cell == ".":
                continue
            if cell == "H" and not headlight_on:
                image[x, y] = HEADLIGHT_OFF
            else:
                image[x, y] = BUS_COLORS[cell]
            mask[x, y] = True
    return image, mask


BUS_ON, BUS_ON_MASK = _bus_sprite(True)
BUS_OFF, BUS_OFF_MASK = _bus_sprite(False)


@dataclass(frozen=True)
class BusArrival:
    due_at: datetime
    minutes_reported: int
    route: str
    destination: str
    vehicle_id: str


@dataclass(frozen=True)
class ArrivalBoard:
    arrivals: Tuple[BusArrival, ...] = ()
    message: str = ""
    fetched_at: Optional[datetime] = None
    status: str = "waiting"


@dataclass(frozen=True)
class StackLayout:
    scale: int
    unit_scale: int
    bus: Tuple[int, int]
    route: Tuple[int, int, str]
    destination: Tuple[int, int, str]
    unit: Tuple[int, int, str]
    times: Tuple[Tuple[int, int, str], ...]
    stack: Tuple[int, int, int, int]


class NjTransitError(RuntimeError):
    """A BUSDATA call failed without exposing credentials."""


def _clean_field(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ")


def encode_multipart(fields: Mapping[str, Any]) -> Tuple[bytes, str]:
    boundary = "----ledgridNjTransit7f3a"
    chunks = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        header = f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
        chunks.append(header.encode("utf-8"))
        chunks.append(_clean_field(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def _decode_payload(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    payload = json.loads(text)
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload


def post_form(url: str, fields: Mapping[str, Any], timeout: float) -> Any:
    body, boundary = encode_multipart(fields)
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(1_000_001)
    except urllib.error.HTTPError as exc:
        raw = exc.read(1_000_001)
    except (TimeoutError, socket.timeout) as exc:
        raise NjTransitError("timeout") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise NjTransitError("timeout") from exc
        raise NjTransitError("network unavailable") from exc
    if len(raw) > 1_000_000:
        raise NjTransitError("response too large")
    try:
        return _decode_payload(raw)
    except ValueError as exc:
        raise NjTransitError("unreadable response") from exc


def _lower_keys(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    return {str(key).lower(): value for key, value in item.items()}


def _is_invalid_token(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    message = str(payload.get("errorMessage") or payload.get("errormessage") or "")
    return "invalid token" in message.lower()


def _error_text(payload: Any, token: str) -> str:
    if not isinstance(payload, dict):
        return ""
    message = payload.get("errorMessage") or payload.get("errormessage") or ""
    text = " ".join(str(message).split())
    if not text:
        return ""
    if token and token in text:
        return "request rejected"
    return text[:160]


def _apply_ampm(hour: int, ampm: str) -> int:
    if not ampm:
        return hour
    hour = hour % 12
    if ampm.lower().startswith("p"):
        hour += 12
    return hour


def _minutes_until(moment: datetime, now: datetime) -> Optional[int]:
    delta = (moment - now).total_seconds() / 60.0
    if delta < -1.0:
        return None
    if delta <= 0:
        return 0
    return min(180, int(math.ceil(delta - 1e-6)))


def _minutes_from_clock(text: str, now: datetime) -> Optional[int]:
    match = _CLOCK.search(text)
    if match is None:
        return None
    hour = _apply_ampm(int(match.group(1)), match.group(4) or "")
    minute = int(match.group(2))
    second = int(match.group(3) or 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    local = now.astimezone(EASTERN)
    candidate = local.replace(hour=hour, minute=minute, second=second, microsecond=0)
    return _minutes_until(candidate, local)


def _minutes_from_stamp(text: str, now: datetime) -> Optional[int]:
    match = _STAMP.search(text)
    if match is None:
        return None
    month, day, year = (int(match.group(index)) for index in (1, 2, 3))
    hour = _apply_ampm(int(match.group(4)), match.group(7) or "")
    minute = int(match.group(5))
    second = int(match.group(6) or 0)
    try:
        moment = datetime(year, month, day, hour, minute, second, tzinfo=EASTERN)
    except ValueError:
        return None
    return _minutes_until(moment, now.astimezone(EASTERN))


def minutes_from_phrase(phrase: str, now: datetime) -> Optional[int]:
    """Turn a BUSDATA departure phrase into whole minutes from now."""
    text = " ".join(str(phrase or "").split())
    if not text:
        return None
    lowered = text.lower()
    match = _MINUTES.search(lowered)
    if match is not None:
        return min(180, int(match.group(1)))
    if any(word in lowered for word in ("due", "approach", "arriv", "board")):
        return 0
    return _minutes_from_clock(lowered, now)


def departure_minutes(trip: Mapping[str, Any], now: datetime) -> Optional[int]:
    phrase = trip.get("departuretime")
    minutes = minutes_from_phrase(str(phrase or ""), now)
    if minutes is not None:
        return minutes
    return _minutes_from_stamp(str(trip.get("sched_dep_time") or ""), now)


def _route_matches(public_route: str, route: str) -> bool:
    found = _ROUTE_NUMBER.search(str(public_route or ""))
    wanted = _ROUTE_NUMBER.search(str(route or ""))
    if found is None or wanted is None:
        return False
    return found.group(0) == wanted.group(0)


def service_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    raw = payload.get("message")
    if isinstance(raw, dict):
        text = str(raw.get("message") or "")
    elif isinstance(raw, str):
        text = raw
    else:
        text = ""
    text = " ".join(text.split())
    if not text or _BOILERPLATE in text.lower():
        return ""
    return text[:180]


def _trip_list(payload: Any) -> Sequence[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    trips = payload.get("DVTrip")
    if trips is None:
        trips = payload.get("dvtrip")
    if isinstance(trips, dict):
        return [trips]
    if isinstance(trips, list):
        return [item for item in trips if isinstance(item, dict)]
    return []


def parse_board(payload: Any, *, route: str, now: datetime, limit: int = 8) -> ArrivalBoard:
    """Keep the soonest matching trips and a non-boilerplate service message."""
    arrivals = []
    for raw in _trip_list(payload):
        trip = _lower_keys(raw)
        public_route = str(trip.get("public_route") or "")
        if not _route_matches(public_route, route):
            continue
        minutes = departure_minutes(trip, now)
        if minutes is None:
            continue
        arrivals.append(BusArrival(
            due_at=now + timedelta(minutes=minutes),
            minutes_reported=minutes,
            route=str(route),
            destination=" ".join(str(trip.get("header") or "").split()),
            vehicle_id=str(trip.get("vehicle_id") or "").strip(),
        ))
    arrivals.sort(key=lambda item: (item.due_at, item.vehicle_id))
    unique = []
    seen = set()
    for arrival in arrivals:
        key = arrival.vehicle_id or f"{arrival.due_at.isoformat()}:{arrival.destination}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(arrival)
        if len(unique) >= limit:
            break
    return ArrivalBoard(
        arrivals=tuple(unique),
        message=service_message(payload),
        fetched_at=now,
        status="live",
    )


def displayed_minutes(
    board: ArrivalBoard,
    now: datetime,
    hold_seconds: float,
    count: int = 3,
) -> Tuple[Optional[int], ...]:
    """Project stored due times onto the minutes shown on the wall."""
    hold = timedelta(seconds=max(0.0, float(hold_seconds)))
    active = [
        arrival for arrival in board.arrivals
        if arrival.due_at + hold >= now
    ]
    slots = []
    for arrival in active[:count]:
        remaining = (arrival.due_at - now).total_seconds() / 60.0
        if remaining <= 0:
            slots.append(0)
        else:
            slots.append(min(99, int(math.ceil(remaining - 1e-6))))
    while len(slots) < count:
        slots.append(None)
    return tuple(slots)


class NjTransitClient:
    """BUSDATA session. Tokens are cached; passwords are never logged."""

    def __init__(self, post: Callable[..., Any] = post_form):
        self._post = post
        self._token = ""
        self._env_token = ""
        self._minted_at = 0.0

    def board(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        token: str,
        route: str,
        stop: str,
        direction: str,
        now: datetime,
        timeout: float = 8.0,
    ) -> ArrivalBoard:
        payload = self._call(
            base_url,
            "getBusDV",
            {"stop": stop, "direction": direction, "route": route, "IP": ""},
            username=username,
            password=password,
            token=token,
            timeout=timeout,
        )
        return parse_board(payload, route=route, now=now)

    def _call(
        self,
        base_url: str,
        endpoint: str,
        fields: Mapping[str, Any],
        *,
        username: str,
        password: str,
        token: str,
        timeout: float,
    ) -> Any:
        supplied = token
        for attempt in range(2):
            current = self._ensure_token(
                base_url, username, password, supplied, timeout, force=attempt > 0,
            )
            payload = self._post(
                self._url(base_url, endpoint),
                {"token": current, **fields},
                timeout,
            )
            if _is_invalid_token(payload) and attempt == 0 and username and password:
                self._token = ""
                self._minted_at = 0.0
                supplied = ""
                continue
            if _is_invalid_token(payload):
                raise NjTransitError("invalid token")
            message = _error_text(payload, current)
            if message:
                raise NjTransitError(message)
            return payload
        raise NjTransitError("invalid token")

    def _ensure_token(
        self,
        base_url: str,
        username: str,
        password: str,
        token: str,
        timeout: float,
        *,
        force: bool,
    ) -> str:
        if token:
            if token != self._env_token:
                self._env_token = token
                self._token = token
                self._minted_at = 0.0
            return token
        fresh = (
            not force
            and self._token
            and (self._minted_monotonic() - self._minted_at) < TOKEN_TTL_SECONDS
        )
        if fresh:
            return self._token
        if not username or not password:
            raise NjTransitError("missing credentials")
        payload = self._post(
            self._url(base_url, "authenticateUser"),
            {"username": username, "password": password},
            timeout,
        )
        authenticated = str(
            payload.get("Authenticated") if isinstance(payload, dict) else ""
        ).lower()
        minted = ""
        if isinstance(payload, dict):
            minted = str(payload.get("UserToken") or payload.get("usertoken") or "")
        if authenticated != "true" or not minted:
            raise NjTransitError("authentication failed")
        self._token = minted
        self._env_token = ""
        self._minted_at = self._minted_monotonic()
        return minted

    @staticmethod
    def _minted_monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def _url(base_url: str, endpoint: str) -> str:
        return str(base_url).rstrip("/") + "/" + endpoint


def _blit(canvas: np.ndarray, glyph: np.ndarray, x: int, y: int, color: Color) -> None:
    width, height = glyph.shape
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(canvas.shape[0], x + width)
    y1 = min(canvas.shape[1], y + height)
    if x0 >= x1 or y0 >= y1:
        return
    cut = glyph[x0 - x:x1 - x, y0 - y:y1 - y]
    if not np.any(cut):
        return
    region = canvas[x0:x1, y0:y1]
    region[cut] = np.maximum(region[cut], np.asarray(color, dtype=np.float32))


def _blit_image(
    canvas: np.ndarray,
    image: np.ndarray,
    mask: np.ndarray,
    x: int,
    y: int,
) -> None:
    width, height = mask.shape
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(canvas.shape[0], x + width)
    y1 = min(canvas.shape[1], y + height)
    if x0 >= x1 or y0 >= y1:
        return
    cut = mask[x0 - x:x1 - x, y0 - y:y1 - y]
    if not np.any(cut):
        return
    region = canvas[x0:x1, y0:y1]
    sprite = image[x0 - x:x1 - x, y0 - y:y1 - y]
    region[cut] = np.maximum(region[cut], sprite[cut])


def _paint_text(
    canvas: np.ndarray,
    text: str,
    x: int,
    y: int,
    color: Color,
    scale: int,
) -> None:
    cursor = x
    spacing = 1
    for char in text.upper():
        glyph = _scaled_glyph(char if char in GLYPHS else " ", scale)
        _blit(canvas, glyph, cursor, y, color)
        cursor += glyph.shape[0] + spacing


class NjTransitBusAnimation(AnimationBase):
    """Bus sprite above the next three arrival times, in minutes."""

    ANIMATION_NAME = "NJ Transit Bus"
    ANIMATION_DESCRIPTION = (
        "Next three 126 buses toward Port Authority. A bus sits above the "
        "minute counts for Clinton St at 1st, one block from 1st & Willow. "
        "Set NJT_API_KEY, or NJT_USERNAME and NJT_PASSWORD, in the controller "
        "environment and restart the animation."
    )
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    PLANT_MODIFIER_SUPPORT = frozenset(PLANT_MODIFIER_IDS)

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "route": DEFAULT_ROUTE,
            "stop": DEFAULT_STOP,
            "direction": DEFAULT_DIRECTION,
            "destination": "PABT",
            "scale": 4,
            "poll_seconds": 30,
            "arrival_hold_seconds": 45,
            "show_service_message": True,
            "glow": 0.35,
            "api_base": DEFAULT_BASE,
        })
        self.params = {**self.default_params, **self.config}
        self.width, self.height = self.get_strip_info()
        self._yn = np.arange(self.height, dtype=np.float32)[None, :] / max(1, self.height - 1)
        self._canvas = np.zeros((self.width, self.height, 3), dtype=np.float32)
        self._marks = np.zeros_like(self._canvas)
        self._client = NjTransitClient()
        self._lock = threading.Lock()
        self._snapshot = ArrivalBoard()
        self._last_error = ""
        self._stop_poll = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_render_key = None
        self._last_frame = None
        self._plant_layout_key = None
        self._plant_layout_offset = (0, 0)
        self._plant_obstacle_overlap = 0
        self._plant_clearance_overlap = 0

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema["speed"]["description"] = "Pace of the street lights and service-message scroll"
        schema.update({
            "route": {
                "type": "str", "default": DEFAULT_ROUTE,
                "description": "NJ Transit route number",
            },
            "stop": {
                "type": "str", "default": DEFAULT_STOP,
                "description": (
                    "Bus stop number. 20491 is Clinton St at 1st St, the "
                    "PABT-bound 126 stop one block east of 1st & Willow. "
                    "20529 is Willow Ave at 2nd St, headed to Hoboken."
                ),
            },
            "direction": {
                "type": "str", "default": DEFAULT_DIRECTION,
                "description": "BUSDATA direction. Blank disables the direction filter.",
            },
            "destination": {
                "type": "str", "default": "PABT",
                "description": "Short destination label drawn under the bus",
            },
            "scale": {
                "type": "int", "min": 2, "max": 4, "default": 4,
                "description": "Digit scale for the minute counts",
            },
            "poll_seconds": {
                "type": "int", "min": 15, "max": 180, "default": 30,
                "description": "How often to refresh arrivals",
            },
            "arrival_hold_seconds": {
                "type": "int", "min": 0, "max": 180, "default": 45,
                "description": "How long a due bus stays on the board",
            },
            "show_service_message": {
                "type": "bool", "default": True,
                "description": "Scroll a non-boilerplate NJ Transit service message",
            },
            "glow": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 0.35,
                "description": "Halo around the bus and the minute counts",
            },
            "api_base": {
                "type": "str", "default": DEFAULT_BASE,
                "description": (
                    "BUSDATA base URL. Test keys use "
                    "https://testpcsdata.njtransit.com/api/BUSDV2"
                ),
            },
        })
        return schema

    def start(self):
        super().start()
        self._stop_poll.clear()
        if self._thread is not None and self._thread.is_alive():
            return
        if not self._has_credentials():
            return
        self._thread = threading.Thread(
            target=self._poll_loop, name="nj-transit-bus", daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop_poll.set()
        super().stop()

    def cleanup(self):
        self.stop()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    def update_parameters(self, new_params: Dict[str, Any]):
        super().update_parameters(new_params)
        self._last_render_key = None

    def generate_frame(self, time_elapsed: float, frame_count: int):
        now = self._clock_now()
        board = self._board()
        minutes = displayed_minutes(
            board, now, float(self.params.get("arrival_hold_seconds", 45)),
        )
        tick = int(max(0.0, float(time_elapsed)) * SOURCE_HZ)
        message = board.message if self.params.get("show_service_message", True) else ""
        render_key = (
            tick, minutes, message, board.status,
            tuple(sorted(self.params.items())),
        )
        if render_key == self._last_render_key and self._last_frame is not None:
            return self.rendered_frame(self._last_frame, changed=False)

        self._render_background(tick)
        marks = self._compose_marks(minutes, tick)
        marks = self._place_marks(marks)
        self._paint_panel(marks)
        self._add_glow(marks, float(self.params.get("glow", 0.35)))
        np.maximum(self._canvas, marks, out=self._canvas)
        self._paint_message(message, tick)

        frame = self.next_frame_buffer(clear=False)
        np.clip(self._canvas, 0, 255, out=self._canvas)
        # Visual y=0 is the top of the wall. Physical LED 0 is the bottom.
        frame[:] = self._canvas[:, ::-1, :].reshape((-1, 3))
        self.apply_brightness_array(frame, out=frame)
        self._last_render_key = render_key
        self._last_frame = frame
        return self.rendered_frame(frame)

    def get_runtime_stats(self) -> Dict[str, Any]:
        board = self._board()
        now = self._clock_now()
        minutes = displayed_minutes(
            board, now, float(self.params.get("arrival_hold_seconds", 45)),
        )
        age = None
        if board.fetched_at is not None:
            age = round(max(0.0, (now - board.fetched_at).total_seconds()), 1)
        stats = {
            "status": board.status,
            "credentials": self._credential_kind(),
            "route": self._route_label(),
            "stop": self._stop_label(),
            "direction": self._direction_label(),
            "destination": self._destination_label(),
            "minutes": [("" if value is None else value) for value in minutes],
            "service_message": board.message,
            "fetched_age_seconds": age,
            "last_error": self._last_error,
        }
        if self._plant_placement_enabled():
            stats.update({
                "plant_layout_offset": self._plant_layout_offset,
                "plant_face_obstacle_overlap": self._plant_obstacle_overlap,
                "plant_face_clearance_overlap": self._plant_clearance_overlap,
            })
        return stats

    def stack_layout(self, minutes: Sequence[Optional[int]]) -> StackLayout:
        """Visual positions for the bus and the three minute rows."""
        scale = self._fit_scale()
        unit_scale = self._unit_scale()
        stack_h = self._stack_height(scale, unit_scale)
        top = max(0, (self.height - MESSAGE_BAND - stack_h) // 2)
        bus_x = (self.width - BUS_WIDTH) // 2
        y = top
        bus = (bus_x, y)
        y += BUS_HEIGHT + GAP_BUS
        route, destination = self._caption_pair()
        route_w = text_width(route, 1)
        dest_w = text_width(destination, 1) if destination else 0
        route_x = max(0, (self.width - route_w) // 2)
        dest_x = max(0, (self.width - dest_w) // 2) if destination else route_x
        route_pos = (route_x, y, route)
        y += CAPTION_HEIGHT + 2
        dest_pos = (dest_x, y, destination)
        y += CAPTION_HEIGHT + GAP_CAPTION
        unit = "MIN"
        unit_x = max(0, (self.width - text_width(unit, unit_scale)) // 2)
        unit_pos = (unit_x, y, unit)
        y += 5 * unit_scale + GAP_UNIT
        cell = text_width("00", scale)
        cell_x = max(0, (self.width - cell) // 2)
        slots = list(minutes)[:3]
        while len(slots) < 3:
            slots.append(None)
        times = []
        for index, value in enumerate(slots):
            text = self._slot_text(value)
            glyph_w = text_width(text, scale)
            glyph_x = cell_x + max(0, (cell - glyph_w) // 2)
            times.append((glyph_x, y, text))
            y += 5 * scale
            if index < 2:
                y += GAP_TIME
        left = min(bus_x, cell_x, route_x, dest_x)
        right = max(
            bus_x + BUS_WIDTH, cell_x + cell, route_x + route_w, dest_x + dest_w,
        )
        stack = (left, top, max(1, right - left), max(1, y - top))
        return StackLayout(
            scale=scale,
            unit_scale=unit_scale,
            bus=bus,
            route=route_pos,
            destination=dest_pos,
            unit=unit_pos,
            times=tuple(times),
            stack=stack,
        )

    def _compose_marks(
        self,
        minutes: Sequence[Optional[int]],
        tick: int,
    ) -> np.ndarray:
        self._marks.fill(0)
        layout = self.stack_layout(minutes)
        sprite = BUS_ON if self._headlight_on(minutes, tick) else BUS_OFF
        mask = BUS_ON_MASK if self._headlight_on(minutes, tick) else BUS_OFF_MASK
        _blit_image(self._marks, sprite, mask, layout.bus[0], layout.bus[1])
        _paint_text(self._marks, layout.route[2], layout.route[0], layout.route[1], ROUTE_COLOR, 1)
        if layout.destination[2]:
            _paint_text(
                self._marks, layout.destination[2],
                layout.destination[0], layout.destination[1], DEST_COLOR, 1,
            )
        _paint_text(
            self._marks, layout.unit[2], layout.unit[0], layout.unit[1],
            UNIT_COLOR, layout.unit_scale,
        )
        for index, (x, y, text) in enumerate(layout.times):
            value = minutes[index] if index < len(minutes) else None
            _paint_text(self._marks, text, x, y, self._digit_color(value, index, tick), layout.scale)
        return self._marks

    def _clock_now(self) -> datetime:
        return datetime.now(EASTERN)

    def _board(self) -> ArrivalBoard:
        with self._lock:
            return self._snapshot

    def _poll_loop(self) -> None:
        while not self._stop_poll.is_set():
            started = time.monotonic()
            try:
                board = self._fetch_snapshot()
                if self._stop_poll.is_set():
                    return
                with self._lock:
                    self._snapshot = board
                    self._last_error = ""
            except Exception as exc:
                message = str(exc)[:160] if isinstance(exc, NjTransitError) else exc.__class__.__name__
                with self._lock:
                    self._last_error = message
                    previous = self._snapshot
                    status = "stale" if previous.fetched_at is not None else "error"
                    self._snapshot = replace(previous, status=status)
            remaining = self._poll_interval() - (time.monotonic() - started)
            if self._stop_poll.wait(max(0.05, remaining)):
                return

    def _fetch_snapshot(self) -> ArrivalBoard:
        username, password, token = self._credentials()
        return self._client.board(
            base_url=str(self.params.get("api_base") or DEFAULT_BASE),
            username=username,
            password=password,
            token=token,
            route=self._route_label(),
            stop=self._stop_label(),
            direction=self._direction_label(),
            now=self._clock_now(),
            timeout=8.0,
        )

    def _credentials(self) -> Tuple[str, str, str]:
        token = (os.environ.get("NJT_API_KEY") or os.environ.get("NJT_TOKEN") or "").strip()
        username = (os.environ.get("NJT_USERNAME") or "").strip()
        password = (os.environ.get("NJT_PASSWORD") or "").strip()
        return username, password, token

    def _has_credentials(self) -> bool:
        username, password, token = self._credentials()
        return bool(token or (username and password))

    def _credential_kind(self) -> str:
        username, password, token = self._credentials()
        if token:
            return "token"
        if username and password:
            return "username"
        return "missing"

    def _poll_interval(self) -> float:
        try:
            value = float(self.params.get("poll_seconds", 30))
        except (TypeError, ValueError):
            value = 30.0
        if not math.isfinite(value):
            value = 30.0
        return min(180.0, max(15.0, value))

    def _text_setting(self, key: str, default: str) -> str:
        if key not in self.params or self.params.get(key) is None:
            return default
        return " ".join(str(self.params.get(key)).split())[:40]

    def _route_label(self) -> str:
        return self._text_setting("route", DEFAULT_ROUTE) or DEFAULT_ROUTE

    def _stop_label(self) -> str:
        return self._text_setting("stop", DEFAULT_STOP) or DEFAULT_STOP

    def _direction_label(self) -> str:
        return self._text_setting("direction", DEFAULT_DIRECTION)

    def _destination_label(self) -> str:
        return (self._text_setting("destination", "PABT") or "PABT")[:12]

    def _caption_pair(self) -> Tuple[str, str]:
        route = self._route_label()[:8]
        destination = self._destination_label()
        while route and text_width(route, 1) > self.width - 1:
            route = route[:-1]
        while destination and text_width(destination, 1) > self.width - 1:
            destination = destination[:-1].rstrip()
        return route, destination

    def _fit_scale(self) -> int:
        try:
            requested = int(self.params.get("scale", 4))
        except (TypeError, ValueError):
            requested = 4
        requested = max(1, min(4, requested))
        for scale in range(requested, 0, -1):
            unit_scale = 2 if scale >= 2 and text_width("MIN", 2) <= self.width - 2 else 1
            height = self._stack_height(scale, unit_scale)
            width = max(text_width("00", scale), BUS_WIDTH if scale >= 2 else text_width("00", scale))
            if height <= self.height - MESSAGE_BAND - 1 and width <= self.width:
                return scale
        return 1

    def _unit_scale(self) -> int:
        if self._fit_scale() >= 2 and text_width("MIN", 2) <= self.width - 2:
            return 2
        return 1

    @staticmethod
    def _stack_height(scale: int, unit_scale: int) -> int:
        return (
            BUS_HEIGHT + GAP_BUS
            + CAPTION_HEIGHT + 2 + CAPTION_HEIGHT + GAP_CAPTION
            + 5 * unit_scale + GAP_UNIT
            + 3 * (5 * scale) + 2 * GAP_TIME
        )

    @staticmethod
    def _slot_text(value: Optional[int]) -> str:
        if value is None:
            return "--"
        return str(min(99, max(0, int(value))))

    def _headlight_on(self, minutes: Sequence[Optional[int]], tick: int) -> bool:
        nearest = next((value for value in minutes if value is not None), None)
        period = 2 if nearest is not None and nearest <= 2 else 4
        return (tick // period) % 2 == 0

    def _digit_color(self, value: Optional[int], index: int, tick: int) -> Color:
        if value is None:
            return DASH_COLOR
        if value <= 2:
            pulse = 0.72 + 0.28 * ((tick // 2) % 2)
            return tuple(channel * pulse for channel in DUE_COLOR)
        if index == 0 or value <= 6:
            return SOON_COLOR
        return LATER_COLOR

    def _render_background(self, tick: int) -> None:
        self._canvas[:] = BACKGROUND
        self._canvas[:, :, 2] += self._yn * 14.0
        self._canvas[:, :, 0] += (1.0 - self._yn) * 5.0
        try:
            speed = float(self.params.get("speed", 1.0))
        except (TypeError, ValueError):
            speed = 1.0
        if speed <= 0:
            return
        phase = int(tick * max(0.0, speed))
        spacing = 16
        columns = (0, self.width - 1) if self.width > 1 else (0,)
        ys = np.arange(self.height)
        on = np.mod(ys - phase, spacing) < 2
        for column in columns:
            self._canvas[column, on] = np.maximum(self._canvas[column, on], STREAK)

    def _paint_panel(self, marks: np.ndarray) -> None:
        occupied = np.argwhere(np.any(marks > 0, axis=2))
        if occupied.size == 0:
            return
        min_x, min_y = occupied.min(axis=0)
        max_x, max_y = occupied.max(axis=0)
        x0 = max(0, int(min_x) - 2)
        y0 = max(0, int(min_y) - 2)
        x1 = min(self.width, int(max_x) + 3)
        y1 = min(self.height - MESSAGE_BAND, int(max_y) + 3)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        self._canvas[x0:x1, y0:y1] = PANEL
        self._canvas[x0:x1, y0] = BORDER
        self._canvas[x0:x1, y1 - 1] = BORDER
        self._canvas[x0, y0:y1] = BORDER
        self._canvas[x1 - 1, y0:y1] = BORDER

    def _add_glow(self, marks: np.ndarray, amount: float) -> None:
        if amount <= 0:
            return
        scaled = marks * (0.28 * amount)
        glow = np.zeros_like(marks)
        glow[1:] = np.maximum(glow[1:], scaled[:-1])
        glow[:-1] = np.maximum(glow[:-1], scaled[1:])
        glow[:, 1:] = np.maximum(glow[:, 1:], scaled[:, :-1])
        glow[:, :-1] = np.maximum(glow[:, :-1], scaled[:, 1:])
        self._canvas += glow

    def _paint_message(self, message: str, tick: int) -> None:
        text = " ".join(str(message or "").upper().split())
        if not text or not self.params.get("show_service_message", True):
            return
        allowed = "".join(char if char in GLYPHS else " " for char in text)
        allowed = " ".join(allowed.split())
        if not allowed:
            return
        y = self.height - 6
        width = text_width(allowed, 1)
        try:
            speed = max(0.0, float(self.params.get("speed", 1.0)))
        except (TypeError, ValueError):
            speed = 1.0
        if width <= self.width:
            _paint_text(self._canvas, allowed, (self.width - width) // 2, y, MESSAGE_COLOR, 1)
            return
        span = width + max(8, self.width // 2)
        offset = int(tick * speed) % span
        _paint_text(self._canvas, allowed, -offset, y, MESSAGE_COLOR, 1)
        _paint_text(self._canvas, allowed, span - offset, y, MESSAGE_COLOR, 1)

    def _plant_placement_enabled(self) -> bool:
        if bool(self.params.get("plant_aware", False)):
            return True
        return any(
            self.plant_modifier_strength(modifier) > 0.0
            for modifier in self.PLANT_MODIFIER_SUPPORT
        )

    def _place_marks(self, marks: np.ndarray) -> np.ndarray:
        if not self._plant_placement_enabled():
            self._plant_layout_offset = (0, 0)
            self._plant_obstacle_overlap = 0
            self._plant_clearance_overlap = 0
            return marks
        occupied = np.any(marks > 0, axis=2)
        masks = self.get_plant_masks()
        cache_key = (occupied.tobytes(), id(masks))
        if cache_key != self._plant_layout_key:
            self._remember_offset(occupied, masks)
            self._plant_layout_key = cache_key
        dx, dy = self._plant_layout_offset
        if dx == 0 and dy == 0:
            return marks
        shifted = np.zeros_like(marks)
        coordinates = np.argwhere(occupied)
        shifted[coordinates[:, 0] + dx, coordinates[:, 1] + dy] = marks[
            coordinates[:, 0], coordinates[:, 1]
        ]
        return shifted

    def _remember_offset(self, occupied: np.ndarray, masks) -> None:
        coordinates = np.argwhere(occupied)
        if coordinates.size == 0:
            self._plant_layout_offset = (0, 0)
            self._plant_obstacle_overlap = 0
            self._plant_clearance_overlap = 0
            return
        min_x, min_y = coordinates.min(axis=0)
        max_x, max_y = coordinates.max(axis=0)
        dx_values = np.arange(-int(min_x), self.width - int(max_x), dtype=np.intp)
        dy_values = np.arange(-int(min_y), self.height - int(max_y), dtype=np.intp)
        if dx_values.size == 0 or dy_values.size == 0:
            self._plant_layout_offset = (0, 0)
            self._plant_obstacle_overlap = int(np.count_nonzero(occupied))
            self._plant_clearance_overlap = 0
            return
        shifted_x = coordinates[:, 0, None, None] + dx_values[None, :, None]
        shifted_y = coordinates[:, 1, None, None] + dy_values[None, None, :]
        globes = masks.globes[:, ::-1]
        foliage = masks.foliage[:, ::-1]
        clearance = masks.clearance[:, ::-1]
        globe_counts = np.count_nonzero(globes[shifted_x, shifted_y], axis=0)
        foliage_counts = np.count_nonzero(foliage[shifted_x, shifted_y], axis=0)
        clearance_counts = np.count_nonzero(clearance[shifted_x, shifted_y], axis=0)
        weighted = globe_counts * 12 + foliage_counts * 4 + clearance_counts
        direct = globe_counts + foliage_counts
        distance = np.abs(dx_values)[:, None] + np.abs(dy_values)[None, :]
        order = np.lexsort((
            np.broadcast_to(np.abs(dx_values)[:, None], weighted.shape).ravel(),
            np.broadcast_to(np.abs(dy_values)[None, :], weighted.shape).ravel(),
            distance.ravel(),
            direct.ravel(),
            weighted.ravel(),
        ))
        dx_index, dy_index = np.unravel_index(int(order[0]), weighted.shape)
        self._plant_layout_offset = (int(dx_values[dx_index]), int(dy_values[dy_index]))
        self._plant_obstacle_overlap = int(direct[dx_index, dy_index])
        self._plant_clearance_overlap = int(clearance_counts[dx_index, dy_index])
