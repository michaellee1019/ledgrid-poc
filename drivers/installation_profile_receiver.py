"""Real SPI adapter for the portable installation-profile transaction engine."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Sequence

from animation.core.installation_profile_transaction import (
    InstallationProfileCacheBinding,
    InstallationProfileReceiverSnapshot,
    InstallationProfileReceiverStatus,
    InstallationProfileTransactionError,
    InstallationProfileWallHealth,
    InstallationProfileWallStatus,
    RECEIVER_COUNT,
)
from drivers.spi_controller import (
    CAPABILITY_INSTALLATION_PROFILE_V1,
    CAPABILITY_STATUS_V5,
    MAX_PROFILE_CHUNK_BYTES,
    SPI_RESPONSE_QUEUE_DEPTH,
)


PROFILE_RESULT_OK = 1
PROFILE_TRANSFER_PREFLIGHT_READY = 1
PROFILE_TRANSFER_RECEIVING = 2
PROFILE_TRANSFER_STAGED = 4


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _binding_from_status(status: dict[str, Any], name: str):
    profile_id = status.get(f"receiver_profile_{name}_global_digest")
    payload_digest = status.get(f"receiver_profile_{name}_payload_digest")
    if profile_id is None and payload_digest is None:
        return None
    if profile_id is None or payload_digest is None:
        raise InstallationProfileTransactionError(
            f"receiver reported incomplete {name} profile binding"
        )
    try:
        return InstallationProfileCacheBinding(profile_id, payload_digest)
    except (TypeError, ValueError) as exc:
        raise InstallationProfileTransactionError(
            f"receiver reported invalid {name} profile binding"
        ) from exc


@dataclass(frozen=True)
class SpiInstallationProfileReceiverSnapshot(InstallationProfileReceiverSnapshot):
    """Binding snapshot with an adapter-private CAS generation."""

    state_generation: int = field(default=0, compare=False)


@dataclass(frozen=True)
class SpiInstallationProfilePreflightPlan:
    receiver_id: int
    binding: InstallationProfileCacheBinding
    payload_size: int
    preflight_token: int
    state_generation: int
    strip_origin: int
    reversed_strip_order: bool


class SpiInstallationProfileReceiver:
    """One logical receiver implementing the structural transaction boundary."""

    def __init__(
        self, receiver_id: int, device: object, *, enabled: bool = False
    ) -> None:
        if type(receiver_id) is not int or not 0 <= receiver_id < RECEIVER_COUNT:
            raise ValueError(
                f"receiver_id must be an integer from 0 through {RECEIVER_COUNT - 1}"
            )
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        self.receiver_id = receiver_id
        self.device = device
        self._enabled = enabled
        self._status: dict[str, Any] | None = None
        self._active_binding: InstallationProfileCacheBinding | None = None
        self._staged_binding: InstallationProfileCacheBinding | None = None
        self._rollback_binding: InstallationProfileCacheBinding | None = None
        self._state_generation = 0

    @property
    def active_binding(self):
        return self._active_binding

    @property
    def staged_binding(self):
        return self._staged_binding

    @property
    def rollback_binding(self):
        return self._rollback_binding

    @staticmethod
    def _operation_error(operation: str, exc: Exception):
        if isinstance(exc, (InstallationProfileTransactionError, OSError)):
            return exc
        return InstallationProfileTransactionError(f"{operation} failed: {exc}")

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise InstallationProfileTransactionError(
                "receiver_geometry_profile rollout gate is disabled"
            )

    def _apply_status(self, status: object) -> dict[str, Any]:
        if not isinstance(status, dict):
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} returned no profile status"
            )
        version = int(status.get("receiver_status_version", 0) or 0)
        capabilities = int(status.get("receiver_capabilities", 0) or 0)
        required = CAPABILITY_INSTALLATION_PROFILE_V1 | CAPABILITY_STATUS_V5
        if version < 5 or capabilities & required != required:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} lacks installation-profile status v5"
            )
        logical = status.get("receiver_logical_device")
        if logical != self.receiver_id:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} reported logical identity {logical!r}"
            )
        active = _binding_from_status(status, "active")
        staged = _binding_from_status(status, "staged")
        rollback = _binding_from_status(status, "rollback")
        transfer = _binding_from_status(status, "transfer")
        flags = status.get("receiver_profile_flags")
        if type(flags) is not int or not 0 <= flags <= 0x7F:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} reported invalid profile flags"
            )
        advertised_bindings = (
            ("active", 0x08, active),
            ("staged", 0x10, staged),
            ("rollback", 0x20, rollback),
            ("transfer", 0x40, transfer),
        )
        for name, bit, binding in advertised_bindings:
            if bool(flags & bit) != (binding is not None):
                raise InstallationProfileTransactionError(
                    f"receiver {self.receiver_id} reported inconsistent {name} binding flags"
                )
        generation = status.get("receiver_profile_state_generation")
        if type(generation) is not int or not 0 <= generation <= 0xFFFFFFFFFFFFFFFF:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} reported invalid profile generation"
            )
        self._status = dict(status)
        self._active_binding = active
        self._staged_binding = staged
        self._rollback_binding = rollback
        self._state_generation = generation
        return self._status

    def refresh(self) -> dict[str, Any]:
        """Clock a causally fresh negotiated v5 snapshot from the two-deep queue."""

        self._require_enabled()
        status = None
        try:
            # The first query may only discover the v5 capability. Two queued
            # legacy snapshots plus one negotiated extension require one extra
            # transfer beyond the ordinary fresh-status drain.
            for _ in range(SPI_RESPONSE_QUEUE_DEPTH + 2):
                status = self.device.query_receiver_status()
            return self._apply_status(status)
        except Exception as exc:
            raise self._operation_error(
                f"receiver {self.receiver_id} profile status refresh", exc
            )

    def _accept_command_status(self, status: object, operation: str):
        parsed = self._apply_status(status)
        result = int(parsed.get("receiver_profile_result", 0) or 0)
        if result != PROFILE_RESULT_OK:
            name = parsed.get("receiver_profile_result_name", result)
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} {operation} rejected: {name}"
            )
        return parsed

    def _call(self, operation: str, method_name: str, **kwargs):
        self._require_enabled()
        try:
            method = getattr(self.device, method_name)
            return self._accept_command_status(method(**kwargs), operation)
        except Exception as exc:
            raise self._operation_error(
                f"receiver {self.receiver_id} {operation}", exc
            )

    def binding_is_valid(self, binding):
        if binding is None:
            return True
        if not isinstance(binding, InstallationProfileCacheBinding):
            return False
        if self._status is None:
            self.refresh()
        assert self._status is not None
        if not self._status.get("receiver_profile_cache_integrity_ok", False):
            return False
        return binding in (
            self._active_binding,
            self._staged_binding,
            self._rollback_binding,
        )

    def transaction_snapshot(self):
        if self._status is None:
            self.refresh()
        return SpiInstallationProfileReceiverSnapshot(
            staged_binding=self._staged_binding,
            active_binding=self._active_binding,
            rollback_binding=self._rollback_binding,
            state_generation=self._state_generation,
        )

    @staticmethod
    def _profile_geometry(payload: bytes):
        if type(payload) is not bytes or len(payload) < 20 or payload[:4] != b"LGIP":
            raise InstallationProfileTransactionError(
                "receiver profile payload has no valid LGIP header"
            )
        flags = int.from_bytes(payload[8:12], "big")
        if flags & ~1:
            raise InstallationProfileTransactionError(
                "receiver profile payload has unsupported flags"
            )
        return int.from_bytes(payload[16:18], "big"), bool(flags & 1)

    def preflight_profile(self, binding, payload):
        if not isinstance(binding, InstallationProfileCacheBinding):
            raise TypeError("binding must be an InstallationProfileCacheBinding")
        if type(payload) is not bytes or _sha256(payload) != binding.payload_digest:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} preflight payload hash mismatch"
            )
        strip_origin, reversed_order = self._profile_geometry(payload)
        status = self._call(
            "profile preflight",
            "profile_preflight",
            profile_id=binding.profile_id,
            payload_digest=binding.payload_digest,
            payload_size=len(payload),
        )
        token = status.get("receiver_profile_preflight_token")
        if (
            status.get("receiver_profile_transfer_state")
            != PROFILE_TRANSFER_PREFLIGHT_READY
            or not status.get("receiver_profile_preflight_can_stage")
            or type(token) is not int
            or token <= 0
        ):
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} returned dishonest profile preflight status"
            )
        return SpiInstallationProfilePreflightPlan(
            receiver_id=self.receiver_id,
            binding=binding,
            payload_size=len(payload),
            preflight_token=token,
            state_generation=self._state_generation,
            strip_origin=strip_origin,
            reversed_strip_order=reversed_order,
        )

    def stage_profile(self, plan, payload, *, corrupt_payload):
        if not isinstance(plan, SpiInstallationProfilePreflightPlan):
            raise TypeError("plan must be this adapter's profile preflight result")
        if corrupt_payload:
            raise ValueError("corrupt_payload is supported only by the in-memory fake")
        if (
            plan.receiver_id != self.receiver_id
            or type(payload) is not bytes
            or len(payload) != plan.payload_size
            or _sha256(payload) != plan.binding.payload_digest
        ):
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} profile stage plan mismatch"
            )
        status = self._call(
            "profile begin",
            "profile_begin",
            preflight_token=plan.preflight_token,
            profile_id=plan.binding.profile_id,
            payload_digest=plan.binding.payload_digest,
            payload_size=plan.payload_size,
            logical_receiver_id=self.receiver_id,
            strip_origin=plan.strip_origin,
            reversed_strip_order=plan.reversed_strip_order,
        )
        if self._staged_binding == plan.binding:
            return
        if status.get("receiver_profile_transfer_state") != PROFILE_TRANSFER_RECEIVING:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} did not enter profile receiving state"
            )
        if (
            status.get("receiver_profile_transfer_global_digest")
            != plan.binding.profile_id
            or status.get("receiver_profile_transfer_payload_digest")
            != plan.binding.payload_digest
        ):
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} began the wrong profile transfer"
            )
        offset = int(status.get("receiver_profile_received_bytes", 0) or 0)
        if not 0 <= offset <= len(payload):
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} reported invalid profile resume offset"
            )
        while offset < len(payload):
            chunk = payload[offset:offset + MAX_PROFILE_CHUNK_BYTES]
            status = self._call(
                "profile chunk", "profile_chunk", offset=offset, data=chunk
            )
            received = status.get("receiver_profile_received_bytes")
            if received != offset + len(chunk):
                raise InstallationProfileTransactionError(
                    f"receiver {self.receiver_id} did not acknowledge exact chunk end"
                )
            offset = received
        status = self._call(
            "profile finalize",
            "profile_finalize",
            profile_id=plan.binding.profile_id,
            payload_digest=plan.binding.payload_digest,
        )
        if (
            self._staged_binding != plan.binding
            or status.get("receiver_profile_transfer_state") != PROFILE_TRANSFER_STAGED
        ):
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} finalized a dishonest staged binding"
            )

    def verify_profile(self, binding, payload):
        if type(payload) is not bytes or _sha256(payload) != binding.payload_digest:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} verify payload hash mismatch"
            )
        self._call(
            "profile verify",
            "profile_verify",
            profile_id=binding.profile_id,
            payload_digest=binding.payload_digest,
        )
        if self._staged_binding != binding or not self.binding_is_valid(binding):
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} did not verify the exact staged binding"
            )

    @staticmethod
    def _bindings_match_snapshot(receiver, snapshot):
        return (
            receiver.active_binding == snapshot.active_binding
            and receiver.staged_binding == snapshot.staged_binding
            and receiver.rollback_binding == snapshot.rollback_binding
        )

    def commit_profile(self, binding, prior_active):
        expected_generation = self._state_generation
        try:
            self._call(
                "profile activation",
                "profile_activate",
                expected_generation=expected_generation,
                profile_id=binding.profile_id,
                payload_digest=binding.payload_digest,
            )
        except (InstallationProfileTransactionError, OSError) as original:
            try:
                self.refresh()
            except (InstallationProfileTransactionError, OSError):
                raise original
            if (
                self._active_binding == binding
                and self._staged_binding is None
                and self._rollback_binding == prior_active
                and self.binding_is_valid(binding)
                and self.binding_is_valid(prior_active)
            ):
                return
            raise original
        if (
            self._active_binding != binding
            or self._staged_binding is not None
            or self._rollback_binding != prior_active
            or not self.binding_is_valid(binding)
            or not self.binding_is_valid(prior_active)
        ):
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} activation did not commit exact bindings"
            )

    def compensate_profile(self, snapshot):
        if not isinstance(snapshot, InstallationProfileReceiverSnapshot):
            raise TypeError("snapshot must be an InstallationProfileReceiverSnapshot")
        try:
            current = self.refresh()
            self._call(
                "profile restore",
                "profile_restore",
                expected_generation=current["receiver_profile_state_generation"],
                active_binding=snapshot.active_binding,
                staged_binding=snapshot.staged_binding,
                rollback_binding=snapshot.rollback_binding,
            )
        except (InstallationProfileTransactionError, OSError) as original:
            try:
                self.refresh()
            except (InstallationProfileTransactionError, OSError):
                raise original
            if self._bindings_match_snapshot(self, snapshot):
                return
            raise original
        if not self._bindings_match_snapshot(self, snapshot):
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} restore did not reproduce exact bindings"
            )
        for binding in (
            snapshot.active_binding,
            snapshot.staged_binding,
            snapshot.rollback_binding,
        ):
            if not self.binding_is_valid(binding):
                raise InstallationProfileTransactionError(
                    f"receiver {self.receiver_id} restored an invalid binding"
                )

    def status(self):
        status = self.refresh()
        bindings = tuple(
            binding
            for binding in (
                self._active_binding,
                self._staged_binding,
                self._rollback_binding,
            )
            if binding is not None
        )
        cached = tuple(sorted({binding.payload_digest for binding in bindings}))
        return InstallationProfileReceiverStatus(
            receiver_id=self.receiver_id,
            capacity_bytes=int(status["receiver_profile_capacity_bytes"]),
            reserve_bytes=int(status["receiver_profile_reserve_bytes"]),
            used_bytes=int(status["receiver_profile_used_bytes"]),
            available_bytes=max(
                0,
                int(status["receiver_profile_free_bytes"])
                - int(status["receiver_profile_reserve_bytes"]),
            ),
            cached_digests=cached,
            pinned_digests=cached,
            staged_binding=self._staged_binding,
            active_binding=self._active_binding,
            rollback_binding=self._rollback_binding,
            cache_integrity_ok=bool(
                status["receiver_profile_cache_integrity_ok"]
            ),
            write_count=int(status["receiver_profile_writes"]),
            eviction_count=int(status["receiver_profile_evictions"]),
            stage_count=int(status["receiver_profile_stages"]),
            verify_count=int(status["receiver_profile_verifies"]),
            commit_count=int(status["receiver_profile_activations"]),
            compensation_count=int(status["receiver_profile_restores"]),
        )


class SpiInstallationProfileWall:
    """Exact-roster wall facade with the same health contract as the fake."""

    def __init__(
        self, devices: Sequence[object], *, enabled: bool = False
    ) -> None:
        if isinstance(devices, (str, bytes)) or len(devices) != RECEIVER_COUNT:
            raise ValueError(
                f"profile wall requires exactly {RECEIVER_COUNT} receiver devices"
            )
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        self.receivers = tuple(
            SpiInstallationProfileReceiver(
                receiver_id, device, enabled=enabled
            )
            for receiver_id, device in enumerate(devices)
        )

    def status(self):
        statuses = tuple(receiver.status() for receiver in self.receivers)
        active_ids = tuple(
            status.active_binding.profile_id
            if status.active_binding is not None
            else None
            for status in statuses
        )
        mixed = len(set(active_ids)) > 1
        active_profile_id = active_ids[0] if not mixed else None
        integrity = all(status.cache_integrity_ok for status in statuses)
        active_valid = all(
            receiver.binding_is_valid(receiver.active_binding)
            for receiver in self.receivers
        )
        if mixed:
            health = InstallationProfileWallHealth.MIXED_GENERATION
        elif active_profile_id is None and integrity:
            health = InstallationProfileWallHealth.NO_ACTIVE
        elif active_profile_id is not None and integrity and active_valid:
            health = InstallationProfileWallHealth.HEALTHY
        else:
            health = InstallationProfileWallHealth.DEGRADED
        return InstallationProfileWallStatus(
            health=health,
            active_profile_id=active_profile_id,
            mixed_generation=mixed,
            receiver_statuses=statuses,
        )


__all__ = [
    "SpiInstallationProfilePreflightPlan",
    "SpiInstallationProfileReceiver",
    "SpiInstallationProfileReceiverSnapshot",
    "SpiInstallationProfileWall",
]
