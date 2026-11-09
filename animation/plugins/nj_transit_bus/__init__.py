#!/usr/bin/env python3
"""NJ Transit arrival overlay for the next three buses.

The default stop is Clinton St at 1st St (20491) on the 126 toward Port
Authority. That is the New York-bound stop one block east of 1st & Willow.
The 126 does not board toward PABT on Willow at 1st; Willow Ave at 2nd St
(20529) is the nearby Willow Avenue stop, and those buses are headed back
to Hoboken.

Credentials stay in the controller environment: ``NJT_API_KEY`` (or
``NJT_TOKEN``), or ``NJT_USERNAME`` and ``NJT_PASSWORD``. The overlay polls
BUSDATA ``getBusDV`` off the render thread and paints a bus emoji above the
minutes until the next three arrivals, centered like Clock and Emoji Message.
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
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np

from animation import AnimationBase
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import OverlayFrame, ResolvedScene
from animation.libraries.clock_face import ClockFaceRenderer


EASTERN = ZoneInfo("America/New_York")
DEFAULT_BASE = "https://pcsdata.njtransit.com/api/BUSDV2"
DEFAULT_ROUTE = "126"
DEFAULT_STOP = "20491"
DEFAULT_DIRECTION = "New York"
TOKEN_TTL_SECONDS = 20 * 60 * 60
DIGIT_HEIGHT = 5
DIGIT_ADVANCE = 4
EMOJI_TIME_GAP = 3
TIME_ROW_GAP = 2
BUS_EMOJI = (
    ".HHHHHHHHH.",
    "HFFF.F.FFFH",
    "HFFFFFFFFFH",
    "HHHHHHHHHHH",
    "H.HH...HH.H",
    ".HH.....HH.",
    "...........",
)
BUS_EMOJI_WIDTH = len(BUS_EMOJI[0])
BUS_EMOJI_HEIGHT = len(BUS_EMOJI)
_EMOJI_ROLES = MappingProxyType({"F": "message", "H": "highlight", "E": "ink", "M": "ink"})

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


def _dirty_ranges(previous: np.ndarray, current: np.ndarray) -> tuple[tuple[int, int], ...]:
    changed = np.flatnonzero(np.any(previous != current, axis=1))
    if not changed.size:
        return ()
    starts = changed[np.r_[True, np.diff(changed) != 1]]
    ends = changed[np.r_[np.diff(changed) != 1, True]] + 1
    return tuple((int(start), int(end)) for start, end in zip(starts, ends))


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
class OverlayLayout:
    emoji: Tuple[int, int]
    times: Tuple[Tuple[int, int, str], ...]


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


def overlay_layout(
    minutes: Sequence[Optional[int]],
    *,
    width: int,
    height: int,
) -> OverlayLayout:
    """Centered bus emoji above three minute rows, visual y=0 at the top."""
    slots = list(minutes)[:3]
    while len(slots) < 3:
        slots.append(None)
    stack_h = (
        BUS_EMOJI_HEIGHT + EMOJI_TIME_GAP
        + 3 * DIGIT_HEIGHT + 2 * TIME_ROW_GAP
    )
    top = max(0, (height - stack_h) // 2)
    emoji = ((width - BUS_EMOJI_WIDTH) // 2, top)
    y = top + BUS_EMOJI_HEIGHT + EMOJI_TIME_GAP
    times = []
    for index, value in enumerate(slots):
        text = "--" if value is None else str(min(99, max(0, int(value))))
        glyph_w = max(0, len(text) * DIGIT_ADVANCE - 1)
        times.append(((width - glyph_w) // 2, y, text))
        y += DIGIT_HEIGHT
        if index < 2:
            y += TIME_ROW_GAP
    return OverlayLayout(emoji=emoji, times=tuple(times))


class NjTransitBusAnimation(AnimationBase):
    """A transparent bus-times plane composed over the current scene."""

    ANIMATION_NAME = "NJ Transit Bus"
    ANIMATION_DESCRIPTION = (
        "Bus emoji above the next three 126 arrival times in minutes, "
        "centered like Clock and Emoji Message. Default stop is Clinton St "
        "at 1st, one block from 1st & Willow. Set NJT_API_KEY, or "
        "NJT_USERNAME and NJT_PASSWORD, in the controller environment."
    )
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"
    COMPONENT_ID, COMPONENT_VERSION, PROVIDER, ROLE = "nj_transit_bus", 1, "python", "widget"
    FRAME_FORMAT = "rgba_uint8_premultiplied_strip_major"
    TIMING_POLICY, PALETTE_POLICY = "wall_clock", "semantic"
    PALETTE_ROLES = ("message", "highlight")
    DEFAULTS = MappingProxyType({
        "route": DEFAULT_ROUTE, "stop": DEFAULT_STOP, "direction": DEFAULT_DIRECTION,
        "poll_seconds": 30, "arrival_hold_seconds": 45, "api_base": DEFAULT_BASE,
    })
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID, version=COMPONENT_VERSION, provider=PROVIDER, role=ROLE,
        timing_policy=TIMING_POLICY, alpha_behavior="premultiplied_rgba",
        palette_policy=PALETTE_POLICY, plant_capabilities=("effect_intent",),
        fidelity_exceptions=(), defaults=DEFAULTS,
        parameter_normalizer=lambda values: NjTransitBusAnimation._normalized_parameters(values),
    )
    SEMANTIC_PALETTES = MappingProxyType({
        "neutral": MappingProxyType({"message": (150, 255, 218), "highlight": (225, 255, 244), "ink": (8, 22, 19)}),
        "mist": MappingProxyType({"message": (170, 228, 245), "highlight": (235, 252, 255), "ink": (9, 25, 38)}),
        "spectrum": MappingProxyType({"message": (54, 238, 230), "highlight": (231, 181, 255), "ink": (17, 5, 36)}),
        "ember": MappingProxyType({"message": (255, 202, 92), "highlight": (255, 239, 179), "ink": (48, 10, 4)}),
        "rainbow": MappingProxyType({"message": (40, 255, 140), "highlight": (255, 230, 70), "ink": (18, 0, 36)}),
    })
    FONT = ClockFaceRenderer.FONT

    def __init__(self, controller, config: Optional[Mapping[str, Any]] = None):
        self._authored_config = dict(config or {})
        super().__init__(controller, self._authored_config)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(self._authored_config)
        self.width, self.height = self.get_strip_info()
        self._buffers = tuple(np.zeros((self.get_pixel_count(), 4), dtype=np.uint8) for _ in range(2))
        self._last_pixels = self._buffers[0]
        self._last_key: tuple[Any, ...] | None = None
        self._revision = 0
        self._presentation_context: ResolvedScene | None = None
        self._client = NjTransitClient()
        self._lock = threading.Lock()
        self._snapshot = ArrivalBoard()
        self._last_error = ""
        self._stop_poll = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        return {
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
            "poll_seconds": {
                "type": "int", "min": 15, "max": 180, "default": 30,
                "description": "How often to refresh arrivals",
            },
            "arrival_hold_seconds": {
                "type": "int", "min": 0, "max": 180, "default": 45,
                "description": "How long a due bus stays on the overlay",
            },
            "api_base": {
                "type": "str", "default": DEFAULT_BASE,
                "description": (
                    "BUSDATA base URL. Test keys use "
                    "https://testpcsdata.njtransit.com/api/BUSDV2"
                ),
            },
        }

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(f"NJ Transit Bus does not accept non-local parameters: {sorted(unknown)!r}")
        result = dict(cls.DEFAULTS)
        result.update(values)
        for name in ("route", "stop", "direction", "api_base"):
            value = result[name]
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
            result[name] = value
        for name, low, high in (("poll_seconds", 15, 180), ("arrival_hold_seconds", 0, 180)):
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} must be an integer from {low} to {high}")
            result[name] = int(value)
        return result

    def update_parameters(self, new_params: Mapping[str, Any]) -> None:
        unknown = set(new_params) - set(self.DEFAULTS)
        if unknown:
            raise ValueError(f"NJ Transit Bus does not accept non-local parameters: {sorted(unknown)!r}")
        self.params = self._normalized_parameters({**self.params, **dict(new_params)})
        self._last_key = None

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        descriptor = new.descriptor
        if (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value) != (
            self.COMPONENT_ID, self.COMPONENT_VERSION, self.PROVIDER, self.ROLE,
        ):
            raise ValueError("NJ Transit Bus received a context for another component")
        if new.palette is None or not isinstance(new.palette.get("palette_id"), str):
            raise ValueError("NJ Transit Bus requires a semantic Scene v2 palette")
        self._presentation_context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._presentation_context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> OverlayFrame:
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

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

    def generate_frame(self, time_elapsed: float, frame_count: int) -> OverlayFrame:
        del time_elapsed, frame_count
        if self._thread is None and self._has_credentials():
            self.start()
        if self._presentation_context is not None:
            self.params = self._normalized_parameters(self._presentation_context.parameters)
            palette_id = str(self._presentation_context.palette["palette_id"])
        else:
            palette_id = "neutral"
        now = self._clock_now()
        board = self._board()
        minutes = displayed_minutes(
            board, now, float(self.params.get("arrival_hold_seconds", 45)),
        )
        key = (minutes, palette_id, tuple(sorted(self.params.items())), board.status)
        if key == self._last_key:
            return OverlayFrame(self._last_pixels, revision=self._revision, changed=False)
        output = self._buffers[1] if self._last_pixels is self._buffers[0] else self._buffers[0]
        output.fill(0)
        self._paint(output, minutes, palette_id)
        ranges = _dirty_ranges(self._last_pixels, output)
        self._last_pixels = output
        self._last_key = key
        self._revision += 1
        return OverlayFrame(output, revision=self._revision, changed=True, dirty_ranges=ranges)

    def overlay_layout(self, minutes: Sequence[Optional[int]]) -> OverlayLayout:
        return overlay_layout(minutes, width=self.width, height=self.height)

    def get_runtime_stats(self) -> Dict[str, Any]:
        board = self._board()
        now = self._clock_now()
        minutes = displayed_minutes(
            board, now, float(self.params.get("arrival_hold_seconds", 45)),
        )
        age = None
        if board.fetched_at is not None:
            age = round(max(0.0, (now - board.fetched_at).total_seconds()), 1)
        return {
            "status": board.status,
            "credentials": self._credential_kind(),
            "route": self._text_setting("route", DEFAULT_ROUTE) or DEFAULT_ROUTE,
            "stop": self._text_setting("stop", DEFAULT_STOP) or DEFAULT_STOP,
            "direction": self._text_setting("direction", DEFAULT_DIRECTION),
            "minutes": [("" if value is None else value) for value in minutes],
            "fetched_age_seconds": age,
            "last_error": self._last_error,
        }

    def _paint(self, output: np.ndarray, minutes: Sequence[Optional[int]], palette_id: str) -> None:
        visual = output.reshape(self.width, self.height, 4)[:, ::-1]
        palette = self.SEMANTIC_PALETTES.get(palette_id, self.SEMANTIC_PALETTES["neutral"])
        layout = overlay_layout(minutes, width=self.width, height=self.height)
        self._paint_emoji(visual, layout.emoji[0], layout.emoji[1], palette)
        color = palette["message"]
        for x, y, text in layout.times:
            self._paint_text(visual, text, x, y, color)

    def _paint_emoji(
        self,
        visual: np.ndarray,
        x: int,
        y: int,
        palette: Mapping[str, tuple[int, int, int]],
    ) -> None:
        for row, pattern_row in enumerate(BUS_EMOJI):
            yy = y + row
            if not 0 <= yy < self.height:
                continue
            for column, cell in enumerate(pattern_row):
                role = _EMOJI_ROLES.get(cell)
                xx = x + column
                if role is None or not 0 <= xx < self.width:
                    continue
                visual[xx, yy, :3] = palette[role]
                visual[xx, yy, 3] = 255

    def _paint_text(
        self,
        visual: np.ndarray,
        text: str,
        x: int,
        y: int,
        color: tuple[int, int, int],
    ) -> None:
        cursor = x
        for character in text:
            for row, bits in enumerate(self.FONT.get(character, self.FONT[" "])):
                for column in range(3):
                    if bits & (1 << (2 - column)):
                        xx, yy = cursor + column, y + row
                        if 0 <= xx < self.width and 0 <= yy < self.height:
                            visual[xx, yy, :3] = color
                            visual[xx, yy, 3] = 255
            cursor += DIGIT_ADVANCE

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
            route=self._text_setting("route", DEFAULT_ROUTE) or DEFAULT_ROUTE,
            stop=self._text_setting("stop", DEFAULT_STOP) or DEFAULT_STOP,
            direction=self._text_setting("direction", DEFAULT_DIRECTION),
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


__all__ = [
    "ArrivalBoard",
    "BUS_EMOJI",
    "BusArrival",
    "DEFAULT_STOP",
    "NjTransitBusAnimation",
    "NjTransitClient",
    "NjTransitError",
    "OverlayLayout",
    "displayed_minutes",
    "encode_multipart",
    "minutes_from_phrase",
    "overlay_layout",
    "parse_board",
]
