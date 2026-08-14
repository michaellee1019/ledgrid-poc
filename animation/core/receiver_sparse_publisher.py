"""Manager-facing sparse foreground publication policy.

The multi-device controller owns wire serialization, receiver slicing,
acknowledgement proof, and partial-wall compensation.  This module owns the
smaller controller-process lifecycle above that transport: a fresh authority
session, monotonically ordered foreground generations, clock-overlay lease
renewal, periodic authoritative repair, and fail-closed recovery after a
transport operation cannot prove a healthy wall.

Publication is deliberately synchronous.  ``OverlayFrame`` buffers are reused
by animation plugins, so returning only after the controller has consumed the
pixels preserves buffer ownership without an extra full-wall copy.
"""

from __future__ import annotations

import math
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

import numpy as np

from animation.core.presentation_contracts import (
    ForegroundStalePolicy,
    OverlayFrame,
)


UINT32_MAX = (1 << 32) - 1
UINT64_MAX = (1 << 64) - 1
CONTROLLER_SESSION_BYTES = 16


@dataclass(frozen=True)
class ReceiverForegroundBinding:
    """Receiver presentation context to which one foreground is bound."""

    scene_revision: int
    scene_epoch: int
    base_revision: int

    def __post_init__(self) -> None:
        for name in ("scene_revision", "scene_epoch", "base_revision"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an unsigned 64-bit integer")
            if not 0 <= value <= UINT64_MAX:
                raise ValueError(f"{name} must fit in an unsigned 64-bit integer")
        # The v1 receiver compositor binds foreground to the active
        # presentation-context revision.  It has no independent base counter.
        if self.base_revision != self.scene_revision:
            raise ValueError(
                "base_revision must equal scene_revision for the compiled "
                "receiver background"
            )


class ReceiverSparsePublisher:
    """Publish one aggregate premultiplied-RGBA foreground transactionally.

    A newly constructed publisher has a fresh random controller session but no
    receiver authority until its first successful full snapshot.  ``publish``
    always sends that first snapshot authoritatively, even when the source says
    its cached frame is unchanged.

    Operational controller failures return ``False`` and are recorded in
    :meth:`get_status`.  Invalid caller input raises before controller I/O.
    """

    def __init__(
        self,
        controller: Any,
        *,
        stale_policy: ForegroundStalePolicy | str = (
            ForegroundStalePolicy.CLEAR_AFTER_LEASE
        ),
        lease_ms: int = 3_000,
        renewal_interval_seconds: float = 1.0,
        repair_interval_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
        session_factory: Callable[[], bytes] = lambda: secrets.token_bytes(
            CONTROLLER_SESSION_BYTES
        ),
        generation_limit: int = UINT64_MAX,
    ) -> None:
        required = (
            "publish_sparse_overlay",
            "renew_sparse_overlay",
            "clear_sparse_overlay",
        )
        missing = [name for name in required if not callable(getattr(controller, name, None))]
        if missing:
            raise TypeError(
                "controller does not implement sparse foreground APIs: "
                + ", ".join(missing)
            )
        try:
            policy = ForegroundStalePolicy(stale_policy)
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported foreground stale policy") from exc
        if policy is not ForegroundStalePolicy.CLEAR_AFTER_LEASE:
            raise ValueError(
                "active clock foreground requires clear_after_lease stale policy"
            )
        if isinstance(lease_ms, bool) or not isinstance(lease_ms, int):
            raise TypeError("lease_ms must be an unsigned 32-bit integer")
        if not 1 <= lease_ms <= UINT32_MAX:
            raise ValueError("lease_ms must be between 1 and 4294967295")
        renewal = self._positive_seconds(
            "renewal_interval_seconds", renewal_interval_seconds
        )
        repair = self._positive_seconds(
            "repair_interval_seconds", repair_interval_seconds
        )
        if renewal * 1000.0 >= lease_ms:
            raise ValueError("renewal interval must be shorter than the foreground lease")
        if not callable(monotonic):
            raise TypeError("monotonic must be callable")
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        if (
            isinstance(generation_limit, bool)
            or not isinstance(generation_limit, int)
        ):
            raise TypeError("generation_limit must be an unsigned 64-bit integer")
        if not 3 <= generation_limit <= UINT64_MAX:
            raise ValueError("generation_limit must be between 3 and uint64 maximum")

        total_leds = getattr(controller, "total_leds", None)
        if (
            isinstance(total_leds, bool)
            or not isinstance(total_leds, int)
            or total_leds <= 0
        ):
            raise TypeError("controller.total_leds must be a positive integer")

        self.controller = controller
        self.total_leds = total_leds
        self.stale_policy = policy
        self.lease_ms = lease_ms
        self.renewal_interval_seconds = renewal
        self.repair_interval_seconds = repair
        self._monotonic = monotonic
        self._session_factory = session_factory
        self._generation_limit = generation_limit
        self._lock = threading.RLock()

        self._session_id = self._make_session()
        self._generation = 0
        self._binding: Optional[ReceiverForegroundBinding] = None
        self._authority_known = True
        self._active = False
        self._repair_required = True
        self._closed = False
        self._close_result: Optional[bool] = None
        self._last_clock_at: Optional[float] = None
        self._last_lease_at: Optional[float] = None
        self._last_repair_at: Optional[float] = None
        self._last_success_at: Optional[float] = None
        self._last_operation = "initialized"
        self._last_error: Optional[str] = None
        self._driver_status: dict[str, Any] = {}
        self._counts = {
            "full_snapshots": 0,
            "delta_generations": 0,
            "renewals": 0,
            "clears": 0,
            "session_rotations": 0,
            "failures": 0,
            "idle_calls": 0,
        }

    @staticmethod
    def _positive_seconds(name: str, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a positive finite number")
        result = float(value)
        if not math.isfinite(result) or result <= 0.0:
            raise ValueError(f"{name} must be a positive finite number")
        return result

    @staticmethod
    def _uint64(name: str, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an unsigned 64-bit integer")
        if not 0 <= value <= UINT64_MAX:
            raise ValueError(f"{name} must fit in an unsigned 64-bit integer")
        return value

    def _make_session(self) -> bytes:
        value = self._session_factory()
        if not isinstance(value, bytes):
            raise TypeError("session_factory must return bytes")
        if len(value) != CONTROLLER_SESSION_BYTES:
            raise ValueError(
                f"session_factory must return {CONTROLLER_SESSION_BYTES} bytes"
            )
        return value

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("sparse foreground publisher is closed")

    def _now(self, value: Optional[float]) -> float:
        raw = self._monotonic() if value is None else value
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise TypeError("publication time must be a finite non-negative number")
        current = float(raw)
        if not math.isfinite(current) or current < 0.0:
            raise ValueError("publication time must be a finite non-negative number")
        if self._last_clock_at is not None and current < self._last_clock_at:
            raise ValueError("publication monotonic time moved backwards")
        self._last_clock_at = current
        return current

    def _normalize_pixels(self, pixels: Any) -> np.ndarray:
        array = np.asarray(pixels)
        expected = (self.total_leds, 4)
        if array.shape != expected:
            raise ValueError(
                f"foreground pixels must have shape {expected}, got {array.shape}"
            )
        if array.dtype != np.uint8:
            raise TypeError("foreground pixels must use uint8 premultiplied RGBA")
        if np.any(array[:, :3] > array[:, 3:4]):
            raise ValueError("foreground RGB must not exceed alpha")
        return array if array.flags.c_contiguous else np.ascontiguousarray(array)

    def _normalize_dirty_ranges(
        self, dirty_ranges: Any
    ) -> tuple[tuple[int, int], ...]:
        if dirty_ranges is None:
            raise ValueError("changed delta publication requires dirty_ranges")
        normalized: list[tuple[int, int]] = []
        prior_end = 0
        for index, item in enumerate(dirty_ranges):
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise TypeError(f"dirty range {index} must be a (start, end) pair")
            start, end = item
            if (
                isinstance(start, bool)
                or not isinstance(start, (int, np.integer))
                or isinstance(end, bool)
                or not isinstance(end, (int, np.integer))
            ):
                raise TypeError(f"dirty range {index} bounds must be integers")
            first, last = int(start), int(end)
            if first < 0 or last > self.total_leds or first >= last:
                raise ValueError(
                    f"dirty range {index} [{first}, {last}) is out of bounds or empty"
                )
            if normalized and first < prior_end:
                raise ValueError("dirty ranges must be sorted and non-overlapping")
            normalized.append((first, last))
            prior_end = last
        return tuple(normalized)

    @staticmethod
    def _make_binding(
        scene_revision: int, scene_epoch: int, base_revision: Optional[int]
    ) -> ReceiverForegroundBinding:
        revision = ReceiverSparsePublisher._uint64(
            "scene_revision", scene_revision
        )
        base = revision if base_revision is None else base_revision
        return ReceiverForegroundBinding(revision, scene_epoch, base)

    def _validate_binding_transition(
        self, candidate: ReceiverForegroundBinding
    ) -> bool:
        prior = self._binding
        if prior is None or prior == candidate:
            return prior is None
        if candidate.scene_revision <= prior.scene_revision:
            raise ValueError(
                "a changed receiver binding must advance scene_revision"
            )
        return True

    def _capture_driver_status(self) -> dict[str, Any]:
        getter = getattr(self.controller, "get_stats", None)
        if not callable(getter):
            return {}
        try:
            stats = getter()
        except Exception:
            return {}
        if not isinstance(stats, Mapping):
            return {}
        aggregate = stats.get("aggregate")
        if not isinstance(aggregate, Mapping):
            return {}
        foreground = aggregate.get("local_background")
        return dict(foreground) if isinstance(foreground, Mapping) else {}

    def _set_failure(self, operation: str, error: Any) -> None:
        self._counts["failures"] += 1
        self._last_operation = operation
        self._last_error = str(error)
        self._active = False
        self._repair_required = True

    def _rotate_session(self, operation: str) -> bytes:
        self._session_id = self._make_session()
        self._generation = 0
        self._binding = None
        self._authority_known = True
        self._active = False
        self._repair_required = True
        self._last_lease_at = None
        self._last_repair_at = None
        self._counts["session_rotations"] += 1
        self._last_operation = operation
        return self._session_id

    def begin_new_session(self) -> bytes:
        """Invalidate local authority; the next publication is a full snapshot."""
        with self._lock:
            self._ensure_open()
            return self._rotate_session("new_session")

    @property
    def controller_session_id(self) -> bytes:
        """Return the exact immutable session token for presentation staging."""
        with self._lock:
            # The internal token is already immutable.  Converting through a
            # memoryview also keeps this boundary independent of that storage
            # detail should the implementation change later.
            return bytes(memoryview(self._session_id))

    def _ensure_publish_headroom(self) -> None:
        # The transport reserves one newer generation for compensation after a
        # failed publish.  Rotate before attempting a value that cannot retain
        # that headroom.
        if self._generation >= self._generation_limit - 1:
            self._rotate_session("generation_rollover")

    def _reconcile_publish_failure(
        self, attempted_generation: int, driver_status: Mapping[str, Any]
    ) -> None:
        state = driver_status.get("state")
        operation = driver_status.get("operation")
        if state == "foreground_repair_required":
            # Delta preflight touched no receiver and retained the committed
            # generation.  A full snapshot may safely follow it.
            if not self._reported_generation_matches(
                driver_status, self._generation
            ):
                self._authority_known = False
                self._rotate_session("repair_generation_mismatch_new_session")
                return
            self._authority_known = True
            return
        if operation == "foreground_publish_failed" and not driver_status.get(
            "cleanup_errors"
        ):
            # MultiDeviceLEDController compensates every touched board with the
            # immediately newer clear generation, including rejected preflight
            # paths whose local authority must still advance consistently.
            compensated = attempted_generation + 1
            if not self._reported_generation_matches(driver_status, compensated):
                self._authority_known = False
                self._rotate_session("compensation_generation_mismatch_new_session")
                return
            self._generation = compensated
            self._authority_known = True
            return
        # A failed compensation means receiver authority may differ.  A new
        # session and full snapshot are the only safe continuation.
        self._authority_known = False
        self._rotate_session("publish_failure_new_session")

    @staticmethod
    def _reported_generation_matches(
        driver_status: Mapping[str, Any], expected: int
    ) -> bool:
        """Accept absent legacy status, but reject a contradictory generation."""
        reported = driver_status.get("foreground_generation")
        if reported is None:
            return True
        return (
            not isinstance(reported, bool)
            and isinstance(reported, int)
            and reported == expected
        )

    def _publish_generation(
        self,
        pixels: np.ndarray,
        *,
        binding: ReceiverForegroundBinding,
        present_at_scene_time_us: int,
        dirty_ranges: Optional[tuple[tuple[int, int], ...]],
        full_snapshot: bool,
        now: float,
        reason: str,
    ) -> bool:
        rolled_over = self._generation >= self._generation_limit - 1
        self._ensure_publish_headroom()
        # Rollover discards the old binding and necessarily makes this a full
        # snapshot in the fresh session.
        if self._binding is None:
            full_snapshot = True
            dirty_ranges = None
        if rolled_over:
            reason = "generation_rollover_snapshot"
        prior_generation = self._generation
        generation = prior_generation + 1
        try:
            accepted = bool(self.controller.publish_sparse_overlay(
                pixels,
                controller_session_id=self._session_id,
                generation=generation,
                prior_generation=prior_generation,
                scene_revision=binding.scene_revision,
                scene_epoch=binding.scene_epoch,
                base_revision=binding.base_revision,
                lease_ms=self.lease_ms,
                present_at_scene_time_us=present_at_scene_time_us,
                dirty_ranges=dirty_ranges,
                full_snapshot=full_snapshot,
            ))
        except Exception as exc:
            self._driver_status = self._capture_driver_status()
            self._set_failure(f"{reason}_exception", exc)
            self._authority_known = False
            self._rotate_session("publish_exception_new_session")
            self._last_error = str(exc)
            return False

        self._driver_status = self._capture_driver_status()
        if not accepted:
            error = self._driver_status.get("error", "controller rejected publication")
            self._set_failure(f"{reason}_failed", error)
            self._reconcile_publish_failure(generation, self._driver_status)
            # Session reconciliation may change the operation label; retain the
            # user-visible failed operation and its diagnostic.
            self._last_operation = f"{reason}_failed"
            self._last_error = str(error)
            return False

        self._generation = generation
        self._binding = binding
        self._authority_known = True
        self._active = True
        self._repair_required = False
        self._last_lease_at = now
        self._last_success_at = now
        self._last_operation = reason
        self._last_error = None
        if full_snapshot:
            self._last_repair_at = now
            self._counts["full_snapshots"] += 1
        else:
            self._counts["delta_generations"] += 1
        return True

    def publish(
        self,
        pixels: Any,
        *,
        changed: bool,
        dirty_ranges: Any = None,
        scene_revision: int,
        scene_epoch: int,
        base_revision: Optional[int] = None,
        present_at_scene_time_us: int,
        now: Optional[float] = None,
    ) -> bool:
        """Publish, renew, repair, or reuse the current aggregate foreground."""
        with self._lock:
            self._ensure_open()
            if not isinstance(changed, bool):
                raise TypeError("changed must be boolean")
            current = self._now(now)
            binding = self._make_binding(scene_revision, scene_epoch, base_revision)
            schedule = self._uint64(
                "present_at_scene_time_us", present_at_scene_time_us
            )
            if not changed and dirty_ranges not in (None, (), []):
                raise ValueError("changed=False cannot carry dirty_ranges")
            normalized_ranges = (
                self._normalize_dirty_ranges(dirty_ranges)
                if changed and dirty_ranges is not None
                else None
            )
            binding_changed = self._validate_binding_transition(binding)
            initial = self._generation == 0 or not self._active
            repair_due = (
                self._last_repair_at is not None
                and current - self._last_repair_at >= self.repair_interval_seconds
            )
            full_snapshot = bool(
                initial
                or binding_changed
                or self._repair_required
                or repair_due
                or (changed and dirty_ranges is None)
            )

            if full_snapshot or changed:
                foreground = self._normalize_pixels(pixels)
                ranges = None
                if not full_snapshot:
                    ranges = normalized_ranges
                reason = (
                    "initial_snapshot" if self._generation == 0
                    else "binding_snapshot" if binding_changed
                    else "repair_snapshot" if self._repair_required or repair_due
                    else "changed_snapshot" if dirty_ranges is None
                    else "delta"
                )
                return self._publish_generation(
                    foreground,
                    binding=binding,
                    present_at_scene_time_us=schedule,
                    dirty_ranges=ranges,
                    full_snapshot=full_snapshot,
                    now=current,
                    reason=reason,
                )

            renewal_due = (
                self._last_lease_at is None
                or current - self._last_lease_at >= self.renewal_interval_seconds
            )
            if not renewal_due:
                self._counts["idle_calls"] += 1
                self._last_operation = "idle"
                return True
            return self._renew(current)

    def publish_frame(
        self,
        frame: OverlayFrame,
        *,
        scene_revision: int,
        scene_epoch: int,
        base_revision: Optional[int] = None,
        present_at_scene_time_us: int,
        now: Optional[float] = None,
    ) -> bool:
        """Convenience boundary for the existing manager ``OverlayFrame``."""
        if not isinstance(frame, OverlayFrame):
            raise TypeError("frame must be an OverlayFrame")
        return self.publish(
            frame.pixels,
            changed=frame.changed,
            dirty_ranges=frame.dirty_ranges,
            scene_revision=scene_revision,
            scene_epoch=scene_epoch,
            base_revision=base_revision,
            present_at_scene_time_us=present_at_scene_time_us,
            now=now,
        )

    def _renew(self, now: float) -> bool:
        generation = self._generation
        try:
            accepted = bool(self.controller.renew_sparse_overlay(
                controller_session_id=self._session_id,
                generation=generation,
                lease_ms=self.lease_ms,
            ))
        except Exception as exc:
            self._driver_status = self._capture_driver_status()
            self._set_failure("renew_exception", exc)
            self._authority_known = False
            self._rotate_session("renew_exception_new_session")
            self._last_error = str(exc)
            return False

        self._driver_status = self._capture_driver_status()
        if not accepted:
            error = self._driver_status.get("error", "controller rejected renewal")
            self._set_failure("renew_failed", error)
            if (
                self._driver_status.get("state") == "foreground_cleared"
                and not self._driver_status.get("cleanup_errors")
                and generation < self._generation_limit
                and self._reported_generation_matches(
                    self._driver_status, generation + 1
                )
            ):
                self._generation = generation + 1
                self._authority_known = True
            else:
                self._authority_known = False
                self._rotate_session("renew_failure_new_session")
            self._last_operation = "renew_failed"
            self._last_error = str(error)
            return False

        self._active = True
        self._repair_required = False
        self._last_lease_at = now
        self._last_success_at = now
        self._last_operation = "renew"
        self._last_error = None
        self._counts["renewals"] += 1
        return True

    def force_repair(self) -> None:
        """Require a complete snapshot on the next publication without I/O."""
        with self._lock:
            self._ensure_open()
            self._repair_required = True
            self._active = False
            self._last_operation = "repair_requested"

    def clear(self) -> bool:
        """Remove the foreground, revealing the receiver-local background."""
        with self._lock:
            self._ensure_open()
            if not self._active and not self._authority_known:
                self._set_failure(
                    "clear_unavailable", "foreground authority is not known"
                )
                return False
            if not self._active:
                if self._last_error is not None:
                    self._last_operation = "clear_unavailable"
                    return False
                self._last_operation = "already_clear"
                return True
            if self._binding is None or not self._authority_known:
                self._set_failure(
                    "clear_unavailable", "foreground authority is not known"
                )
                return False
            if self._generation >= self._generation_limit:
                self._set_failure(
                    "clear_exhausted", "foreground generation counter is exhausted"
                )
                return False
            generation = self._generation + 1
            try:
                accepted = bool(self.controller.clear_sparse_overlay(
                    controller_session_id=self._session_id,
                    generation=generation,
                    scene_revision=self._binding.scene_revision,
                ))
            except Exception as exc:
                self._driver_status = self._capture_driver_status()
                self._set_failure("clear_exception", exc)
                self._authority_known = False
                self._rotate_session("clear_exception_new_session")
                self._last_error = str(exc)
                return False
            self._driver_status = self._capture_driver_status()
            if not accepted:
                error = self._driver_status.get("error", "controller rejected clear")
                self._set_failure("clear_failed", error)
                # Multi-device clear drops its controller-side authority after
                # any board disagreement.  Match that reset with a fresh local
                # session before another publication.
                self._authority_known = False
                self._rotate_session("clear_failure_new_session")
                self._last_operation = "clear_failed"
                self._last_error = str(error)
                return False
            self._generation = generation
            self._active = False
            self._repair_required = True
            self._last_operation = "clear"
            self._last_error = None
            self._counts["clears"] += 1
            return True

    def close(self, *, clear: bool = True) -> bool:
        """Optionally clear once, then permanently reject further I/O."""
        with self._lock:
            if self._closed:
                return bool(self._close_result)
            result = self.clear() if clear else True
            self._closed = True
            self._close_result = result
            self._active = False
            self._last_operation = "closed" if result else "closed_with_error"
            return result

    def get_status(self) -> dict[str, Any]:
        """Return a detached status snapshot without touching the controller."""
        with self._lock:
            healthy = bool(
                not self._closed
                and self._active
                and self._authority_known
                and not self._repair_required
                and self._last_error is None
            )
            return {
                "healthy": healthy,
                "active": self._active,
                "closed": self._closed,
                "authority_known": self._authority_known,
                "repair_required": self._repair_required,
                "stale_policy": self.stale_policy.value,
                "lease_ms": self.lease_ms,
                "renewal_interval_seconds": self.renewal_interval_seconds,
                "repair_interval_seconds": self.repair_interval_seconds,
                "controller_session_id": self._session_id.hex(),
                "generation": self._generation,
                "binding": None if self._binding is None else {
                    "scene_revision": self._binding.scene_revision,
                    "scene_epoch": self._binding.scene_epoch,
                    "base_revision": self._binding.base_revision,
                },
                "last_operation": self._last_operation,
                "last_error": self._last_error,
                "last_lease_at": self._last_lease_at,
                "last_repair_at": self._last_repair_at,
                "last_success_at": self._last_success_at,
                "driver_status": dict(self._driver_status),
                "counts": dict(self._counts),
            }


__all__ = [
    "ReceiverForegroundBinding",
    "ReceiverSparsePublisher",
]
