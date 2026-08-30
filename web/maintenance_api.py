"""Guarded, named maintenance-frame contracts.

This module is deliberately not a framebuffer API.  The only frames it can
produce are the five reviewed diagnostic shapes below, with a small bounded
intensity and duration.  A host transport may use :class:`MaintenanceRunner`
to apply those trusted frames, but it must acknowledge the same controller
session, state revision, and receiver roster before the diagnostic is allowed
to run.

Keeping this boundary independent of Flask lets the command-line calibration
tools and a future guarded Composer affordance share one fail-closed contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
import uuid
from typing import Any, Callable, Mapping, Sequence

from drivers.led_layout import (
    DEFAULT_LEDS_PER_STRIP,
    DEFAULT_STRIP_COUNT,
    WALL_RECEIVER_GLOBAL_STRIP_OFFSETS,
    WALL_RECEIVER_STRIP_COUNTS,
)


MAINTENANCE_SCHEMA = "ledgrid.maintenance-frame-request"
MAINTENANCE_SCHEMA_VERSION = 1
MAX_DURATION_SECONDS = 30.0
MAX_INTENSITY = 64
TAIL_RECEIVER_ID = len(WALL_RECEIVER_STRIP_COUNTS) - 1
DIAGNOSTICS = frozenset(
    {
        "receiver_band",
        "strip_ramp",
        "direction_sentinel",
        "sparse_boundary",
        "tail_lane_probe",
    }
)


class MaintenanceRequestError(ValueError):
    """A maintenance request is malformed, unsafe, or stale."""


class MaintenanceRunError(RuntimeError):
    """A trusted maintenance frame could not be completed safely."""


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _uint(value: Any, label: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MaintenanceRequestError(f"{label} must be a non-negative integer")
    if maximum is not None and value > maximum:
        raise MaintenanceRequestError(f"{label} must be at most {maximum}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MaintenanceRequestError(f"{label} must be a non-empty string")
    return value


def _exact_mapping(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise MaintenanceRequestError(f"{label} must contain exactly {sorted(keys)}")
    return value


def _receiver_for_strip(strip: int) -> int:
    for receiver_id, (offset, count) in enumerate(
        zip(WALL_RECEIVER_GLOBAL_STRIP_OFFSETS, WALL_RECEIVER_STRIP_COUNTS)
    ):
        if offset <= strip < offset + count:
            return receiver_id
    raise MaintenanceRequestError("strip is outside the installed wall geometry")


@dataclass(frozen=True)
class MaintenanceIdentity:
    """Exact controller and receiver identity observed before a diagnostic."""

    controller_session_id: str
    controller_state_revision: int
    receiver_roster_digest: str

    @classmethod
    def from_mapping(cls, value: Any, *, label: str) -> "MaintenanceIdentity":
        raw = _exact_mapping(
            value,
            {
                "controller_session_id",
                "controller_state_revision",
                "receiver_roster_digest",
            },
            label,
        )
        digest = _text(raw["receiver_roster_digest"], f"{label}.receiver_roster_digest")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise MaintenanceRequestError(f"{label}.receiver_roster_digest must be a SHA-256")
        return cls(
            controller_session_id=_text(raw["controller_session_id"], f"{label}.controller_session_id"),
            controller_state_revision=_uint(
                raw["controller_state_revision"],
                f"{label}.controller_state_revision",
            ),
            receiver_roster_digest=digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "controller_session_id": self.controller_session_id,
            "controller_state_revision": self.controller_state_revision,
            "receiver_roster_digest": self.receiver_roster_digest,
        }


def receiver_roster_digest(roster: Any) -> str:
    """Digest one complete connected logical receiver roster fail-closed."""

    if not isinstance(roster, Sequence) or isinstance(roster, (str, bytes)):
        raise MaintenanceRequestError("receiver_roster must be an array")
    normalized: list[dict[str, Any]] = []
    for expected_id, item in enumerate(roster):
        if not isinstance(item, Mapping):
            raise MaintenanceRequestError("receiver_roster entries must be objects")
        logical_id = _uint(item.get("logical_device"), "receiver logical_device")
        if logical_id != expected_id:
            raise MaintenanceRequestError("receiver_roster must be in exact logical order")
        if item.get("connected") is False:
            raise MaintenanceRequestError(f"receiver {logical_id} is disconnected")
        # A stable receiver identity is intentionally smaller than the broad
        # status payload. Connectivity is proved by the controller's complete
        # frame receipt, not guessed from its immutable startup binding.
        normalized.append(
            {
                "logical_device": logical_id,
                "route": item.get("route"),
                "hardware_serial": item.get("hardware_serial"),
                "firmware_sha256": item.get("firmware_sha256"),
            }
        )
    if len(normalized) != len(WALL_RECEIVER_STRIP_COUNTS):
        raise MaintenanceRequestError("receiver_roster does not cover the installed wall")
    return _sha256(normalized)


def identity_from_status(status: Mapping[str, Any]) -> MaintenanceIdentity:
    """Extract a diagnostic identity only from a complete current status."""

    if not isinstance(status, Mapping):
        raise MaintenanceRequestError("controller status is unavailable")
    return MaintenanceIdentity(
        controller_session_id=_text(status.get("controller_session_id"), "controller_session_id"),
        controller_state_revision=_uint(
            status.get("controller_state_revision"), "controller_state_revision"
        ),
        receiver_roster_digest=receiver_roster_digest(status.get("receiver_roster")),
    )


@dataclass(frozen=True)
class MaintenanceRequest:
    """A bounded request for exactly one reviewed diagnostic shape."""

    request_id: str
    diagnostic: str
    target: dict[str, int]
    intensity: int
    duration_seconds: float
    expected_identity: MaintenanceIdentity
    provenance: dict[str, str]

    @classmethod
    def from_mapping(cls, value: Any) -> "MaintenanceRequest":
        raw = _exact_mapping(
            value,
            {
                "schema",
                "schema_version",
                "request_id",
                "diagnostic",
                "target",
                "intensity",
                "duration_seconds",
                "expected_identity",
                "provenance",
            },
            "maintenance request",
        )
        if raw["schema"] != MAINTENANCE_SCHEMA or raw["schema_version"] != MAINTENANCE_SCHEMA_VERSION:
            raise MaintenanceRequestError("unsupported maintenance request schema")
        request_id = _text(raw["request_id"], "request_id")
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError
        except (ValueError, AttributeError) as exc:
            raise MaintenanceRequestError("request_id must be a lowercase UUID") from exc
        diagnostic = raw["diagnostic"]
        if diagnostic not in DIAGNOSTICS:
            raise MaintenanceRequestError("diagnostic must be a reviewed named diagnostic")
        intensity = _uint(raw["intensity"], "intensity", maximum=MAX_INTENSITY)
        if intensity == 0:
            raise MaintenanceRequestError("intensity must be positive")
        duration = raw["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise MaintenanceRequestError("duration_seconds must be numeric")
        duration = float(duration)
        if not 0.0 < duration <= MAX_DURATION_SECONDS:
            raise MaintenanceRequestError(
                f"duration_seconds must be greater than zero and at most {MAX_DURATION_SECONDS}"
            )
        target = _normalize_target(diagnostic, raw["target"])
        provenance = _normalize_provenance(raw["provenance"])
        return cls(
            request_id=request_id,
            diagnostic=diagnostic,
            target=target,
            intensity=intensity,
            duration_seconds=duration,
            expected_identity=MaintenanceIdentity.from_mapping(
                raw["expected_identity"], label="expected_identity"
            ),
            provenance=provenance,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MAINTENANCE_SCHEMA,
            "schema_version": MAINTENANCE_SCHEMA_VERSION,
            "request_id": self.request_id,
            "diagnostic": self.diagnostic,
            "target": dict(self.target),
            "intensity": self.intensity,
            "duration_seconds": self.duration_seconds,
            "expected_identity": self.expected_identity.to_dict(),
            "provenance": dict(self.provenance),
        }


def _normalize_target(diagnostic: str, value: Any) -> dict[str, int]:
    target = value if isinstance(value, Mapping) else None
    if target is None:
        raise MaintenanceRequestError("target must be an object")
    if diagnostic in {"receiver_band", "sparse_boundary"}:
        raw = _exact_mapping(target, {"receiver_id"}, "target")
        receiver_id = _uint(raw["receiver_id"], "target.receiver_id")
        if receiver_id >= len(WALL_RECEIVER_STRIP_COUNTS):
            raise MaintenanceRequestError("target.receiver_id is outside the installed roster")
        return {"receiver_id": receiver_id}
    if diagnostic in {"strip_ramp", "direction_sentinel"}:
        raw = _exact_mapping(target, {"strip"}, "target")
        strip = _uint(raw["strip"], "target.strip")
        if strip >= DEFAULT_STRIP_COUNT:
            raise MaintenanceRequestError("target.strip is outside the installed wall")
        return {"strip": strip}
    raw = _exact_mapping(target, {"receiver_id", "lane"}, "target")
    receiver_id = _uint(raw["receiver_id"], "target.receiver_id")
    lane = _uint(raw["lane"], "target.lane", maximum=7)
    if receiver_id != TAIL_RECEIVER_ID:
        raise MaintenanceRequestError("tail_lane_probe may target only the tail receiver")
    return {"receiver_id": receiver_id, "lane": lane}


def _normalize_provenance(value: Any) -> dict[str, str]:
    raw = _exact_mapping(value, {"operator", "source_revision", "purpose"}, "provenance")
    source_revision = _text(raw["source_revision"], "provenance.source_revision")
    if len(source_revision) not in {40, 64} or any(
        char not in "0123456789abcdef" for char in source_revision
    ):
        raise MaintenanceRequestError("provenance.source_revision must be a commit SHA")
    return {
        "operator": _text(raw["operator"], "provenance.operator"),
        "source_revision": source_revision,
        "purpose": _text(raw["purpose"], "provenance.purpose"),
    }


def target_receivers(request: MaintenanceRequest) -> tuple[int, ...]:
    """Return the exact receiver(s) that must acknowledge this request."""

    if "receiver_id" in request.target:
        return (request.target["receiver_id"],)
    return (_receiver_for_strip(request.target["strip"]),)


def build_frame(request: MaintenanceRequest) -> tuple[tuple[int, int, int], ...]:
    """Build a trusted named diagnostic frame; callers cannot supply pixels."""

    frame = [(0, 0, 0)] * (DEFAULT_STRIP_COUNT * DEFAULT_LEDS_PER_STRIP)
    level = request.intensity

    def set_pixel(strip: int, row: int, color: tuple[int, int, int]) -> None:
        frame[strip * DEFAULT_LEDS_PER_STRIP + row] = color

    if request.diagnostic == "receiver_band":
        receiver_id = request.target["receiver_id"]
        first = WALL_RECEIVER_GLOBAL_STRIP_OFFSETS[receiver_id]
        for strip in range(first, first + WALL_RECEIVER_STRIP_COUNTS[receiver_id]):
            for row in range(DEFAULT_LEDS_PER_STRIP):
                set_pixel(strip, row, (level, level, level))
    elif request.diagnostic == "strip_ramp":
        strip = request.target["strip"]
        for row in range(DEFAULT_LEDS_PER_STRIP):
            value = max(1, round(level * (row + 1) / DEFAULT_LEDS_PER_STRIP))
            set_pixel(strip, row, (value, value, value))
    elif request.diagnostic == "direction_sentinel":
        strip = request.target["strip"]
        for row, color in (
            (0, (level, 0, 0)),
            (DEFAULT_LEDS_PER_STRIP // 2, (level, level, level)),
            (DEFAULT_LEDS_PER_STRIP - 1, (0, 0, level)),
        ):
            set_pixel(strip, row, color)
    elif request.diagnostic == "sparse_boundary":
        receiver_id = request.target["receiver_id"]
        first = WALL_RECEIVER_GLOBAL_STRIP_OFFSETS[receiver_id]
        last = first + WALL_RECEIVER_STRIP_COUNTS[receiver_id] - 1
        for strip, row in ((first, 0), (first, DEFAULT_LEDS_PER_STRIP - 1), (last, 0), (last, DEFAULT_LEDS_PER_STRIP - 1)):
            set_pixel(strip, row, (level, level, level))
    else:  # tail_lane_probe; transport uses the reviewed lane descriptor.
        strip = WALL_RECEIVER_GLOBAL_STRIP_OFFSETS[TAIL_RECEIVER_ID]
        for row, color in ((0, (level, 0, 0)), (DEFAULT_LEDS_PER_STRIP - 1, (0, level, 0))):
            set_pixel(strip, row, color)
    return tuple(frame)


def frame_digest(request: MaintenanceRequest) -> str:
    """Return a reproducible digest for proof without accepting frame input."""

    packed = bytes(channel for pixel in build_frame(request) for channel in pixel)
    return hashlib.sha256(packed).hexdigest()


def _require_identity(actual: MaintenanceIdentity, expected: MaintenanceIdentity, label: str) -> None:
    if actual != expected:
        raise MaintenanceRunError(
            f"{label} identity is stale or changed: expected {expected.to_dict()}, "
            f"observed {actual.to_dict()}"
        )


class MaintenanceRunner:
    """Run one named frame with mandatory acknowledgement and cleanup.

    ``apply`` receives the normalized request and the internally generated
    frame. ``restore`` receives the exact status captured before mutation.
    Both callbacks live behind a locally guarded or authenticated boundary;
    this class rejects an unguarded caller before it reads or changes output.
    """

    def __init__(
        self,
        *,
        status: Callable[[], Mapping[str, Any]],
        apply: Callable[[MaintenanceRequest, tuple[tuple[int, int, int], ...]], Mapping[str, Any]],
        restore: Callable[[Mapping[str, Any]], None],
        safe_idle: Callable[[], None],
        locally_guarded: bool,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._status = status
        self._apply = apply
        self._restore = restore
        self._safe_idle = safe_idle
        self._locally_guarded = locally_guarded
        self._sleep = sleep

    def run(self, request: MaintenanceRequest) -> dict[str, Any]:
        if not self._locally_guarded:
            raise MaintenanceRunError("maintenance output requires a local guard or authentication")
        before: Mapping[str, Any] | None = None
        applied = False
        receipt: dict[str, Any] | None = None
        failure: BaseException | None = None
        cleanup_error: BaseException | None = None
        try:
            before = self._status()
            _require_identity(identity_from_status(before), request.expected_identity, "preflight")
            # Treat a transport exception as potentially mutating.  The
            # controller may have accepted the request immediately before a
            # connection drops, so it still needs a restore attempt.
            applied = True
            acknowledgement = self._apply(request, build_frame(request))
            self._validate_acknowledgement(acknowledgement, request)
            self._sleep(request.duration_seconds)
            _require_identity(
                identity_from_status(self._status()), request.expected_identity, "completion"
            )
            receipt = {
                "schema": "ledgrid.maintenance-frame-receipt",
                "schema_version": 1,
                "request": request.to_dict(),
                "frame_digest": frame_digest(request),
                "target_receivers": list(target_receivers(request)),
                "acknowledgement": dict(acknowledgement),
                "restored": False,
            }
        except BaseException as exc:
            failure = exc
        finally:
            if applied and before is not None:
                try:
                    self._restore(before)
                    if receipt is not None:
                        receipt["restored"] = True
                except BaseException as exc:
                    cleanup_error = exc
                    try:
                        self._safe_idle()
                    except BaseException as idle_exc:
                        cleanup_error = MaintenanceRunError(
                            f"restore failed ({exc}); safe idle also failed ({idle_exc})"
                        )
        if failure is not None:
            raise MaintenanceRunError(f"maintenance diagnostic failed: {failure}") from failure
        if cleanup_error is not None:
            raise MaintenanceRunError(f"maintenance diagnostic restore failed: {cleanup_error}") from cleanup_error
        if receipt is None:  # pragma: no cover - defensive completeness
            raise MaintenanceRunError("maintenance diagnostic returned no receipt")
        return receipt

    @staticmethod
    def _validate_acknowledgement(
        acknowledgement: Mapping[str, Any], request: MaintenanceRequest
    ) -> None:
        if not isinstance(acknowledgement, Mapping):
            raise MaintenanceRunError("maintenance acknowledgement is unavailable")
        required = {
            "request_id",
            "controller_session_id",
            "controller_state_revision",
            "receiver_roster_digest",
            "acknowledged_receivers",
        }
        if set(acknowledgement) != required:
            raise MaintenanceRunError("maintenance acknowledgement has an unexpected shape")
        if acknowledgement.get("request_id") != request.request_id:
            raise MaintenanceRunError("maintenance acknowledgement belongs to another request")
        _require_identity(
            MaintenanceIdentity.from_mapping(
                {
                    key: acknowledgement[key]
                    for key in (
                        "controller_session_id",
                        "controller_state_revision",
                        "receiver_roster_digest",
                    )
                },
                label="acknowledgement",
            ),
            request.expected_identity,
            "acknowledgement",
        )
        acknowledged = acknowledgement.get("acknowledged_receivers")
        if not isinstance(acknowledged, list) or any(
            isinstance(value, bool) or not isinstance(value, int) for value in acknowledged
        ):
            raise MaintenanceRunError("maintenance acknowledgement receiver list is invalid")
        # A maintenance frame is a complete wall frame: even its dark pixels
        # are sent to every receiver.  Acknowledging only the illuminated
        # receiver could hide a disconnect or partial transfer elsewhere.
        missing = set(range(len(WALL_RECEIVER_STRIP_COUNTS))) - set(acknowledged)
        if missing:
            raise MaintenanceRunError(
                f"maintenance acknowledgement is partial; missing receivers {sorted(missing)}"
            )
