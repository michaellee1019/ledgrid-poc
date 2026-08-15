"""Portable installation-profile transaction engine and in-memory fake.

The engine operates on the structural :class:`InstallationProfileWall` and
:class:`InstallationProfileReceiver` protocols, so a transport adapter can use
the same preflight/stage/verify/commit/compensate orchestration as the fake.
This module itself deliberately performs no transport, filesystem, SPI, or
firmware work.  One candidate binds a canonical/global profile content ID to
four receiver-specific payload content IDs, while the included fake models a
bounded disposable cache and its active, staged, and rollback pins.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from animation.core.installation_profile import encode_installation_profile
from animation.core.installation_profile_library import ResolvedInstallationProfile
from animation.core.installation_profile_topology import slice_installation_profile


RECEIVER_COUNT: Final = 4
RECEIVER_IDS: Final = tuple(range(RECEIVER_COUNT))


class InstallationProfileTransactionError(RuntimeError):
    """An operational receiver-profile transaction failed."""


# Adapter transport/filesystem failures are operational and compensateable.
# Programmer errors and BaseException control flow deliberately propagate.
_OPERATIONAL_EXCEPTIONS: Final = (InstallationProfileTransactionError, OSError)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _content_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    try:
        decoded = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(
            f"{field} must be a lowercase SHA-256 hex digest"
        ) from exc
    if value != value.lower() or decoded.hex() != value:
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _receiver_id(value: object) -> int:
    if type(value) is not int or value not in RECEIVER_IDS:
        raise ValueError("receiver_id must be an integer from 0 through 3")
    return value


def _immutable_payload(value: object, *, field: str) -> bytes:
    if type(value) is not bytes:
        raise TypeError(f"{field} must be immutable bytes")
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


@dataclass(frozen=True)
class InstallationProfileCacheBinding:
    """One receiver's payload binding to a canonical/global profile."""

    profile_id: str
    payload_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "profile_id", _content_id(self.profile_id, field="profile_id")
        )
        object.__setattr__(
            self,
            "payload_digest",
            _content_id(self.payload_digest, field="payload_digest"),
        )


@dataclass(frozen=True, init=False)
class InstallationProfileCandidate:
    """One global profile identity and its four immutable receiver slices."""

    profile_id: str
    receiver_payloads: tuple[bytes, bytes, bytes, bytes]
    receiver_payload_digests: tuple[str, str, str, str]

    def __init__(
        self, profile_id: str, receiver_payloads: Mapping[int, bytes]
    ) -> None:
        normalized_id = _content_id(profile_id, field="profile_id")
        if not isinstance(receiver_payloads, Mapping):
            raise TypeError("receiver_payloads must be a mapping by receiver ID")
        keys = tuple(receiver_payloads.keys())
        if (
            len(keys) != RECEIVER_COUNT
            or any(type(key) is not int for key in keys)
            or set(keys) != set(RECEIVER_IDS)
        ):
            raise ValueError(
                "receiver_payloads must contain each receiver ID 0,1,2,3 exactly once"
            )
        payloads = tuple(
            _immutable_payload(
                receiver_payloads[receiver_id],
                field=f"receiver_payloads[{receiver_id}]",
            )
            for receiver_id in RECEIVER_IDS
        )
        digests = tuple(_sha256(payload) for payload in payloads)
        object.__setattr__(self, "profile_id", normalized_id)
        object.__setattr__(self, "receiver_payloads", payloads)
        object.__setattr__(self, "receiver_payload_digests", digests)

    def payload_for(self, receiver_id: int) -> bytes:
        return self.receiver_payloads[_receiver_id(receiver_id)]

    def binding_for(self, receiver_id: int) -> InstallationProfileCacheBinding:
        normalized_id = _receiver_id(receiver_id)
        return InstallationProfileCacheBinding(
            profile_id=self.profile_id,
            payload_digest=self.receiver_payload_digests[normalized_id],
        )


def candidate_from_resolved(
    resolved: ResolvedInstallationProfile,
) -> InstallationProfileCandidate:
    """Build the exact four canonical receiver payloads from a managed resolve.

    The helper is intentionally strict: the global encoded view must match its
    managed content ID, receiver keys must be exactly ``0..3``, and every
    receiver view must equal a fresh slice of the global profile under the
    resolved topology.  This prevents an adapter from installing bytes whose
    logical receiver binding drifted from the topology used during resolution.
    """

    if not isinstance(resolved, ResolvedInstallationProfile):
        raise TypeError("resolved must be a ResolvedInstallationProfile")
    canonical_global = encode_installation_profile(resolved.global_profile)
    if type(resolved.encoded) is not bytes or resolved.encoded != canonical_global:
        raise ValueError("resolved global profile bytes are not canonical")
    if canonical_global[68:100].hex() != resolved.id:
        raise ValueError("resolved profile ID does not match its canonical content ID")
    if not isinstance(resolved.receiver_profiles, Mapping):
        raise TypeError("resolved receiver_profiles must be a mapping")
    keys = tuple(resolved.receiver_profiles.keys())
    if (
        len(keys) != RECEIVER_COUNT
        or any(type(key) is not int for key in keys)
        or set(keys) != set(RECEIVER_IDS)
    ):
        raise ValueError(
            "resolved receiver_profiles must contain receiver IDs 0,1,2,3 exactly once"
        )

    expected_profiles = slice_installation_profile(
        resolved.global_profile, resolved.topology
    )
    payloads: dict[int, bytes] = {}
    for receiver_id in RECEIVER_IDS:
        payload = encode_installation_profile(resolved.receiver_profiles[receiver_id])
        expected = encode_installation_profile(expected_profiles[receiver_id])
        if payload != expected:
            raise ValueError(
                f"resolved receiver profile {receiver_id} does not match its topology slice"
            )
        payloads[receiver_id] = payload
    return InstallationProfileCandidate(resolved.id, payloads)


class InstallationProfileTransactionPhase(str, Enum):
    PREFLIGHT = "preflight"
    STAGE = "stage"
    VERIFY = "verify"
    COMMIT = "commit"
    COMPENSATE = "compensate"


class FakeInstallationProfileFaultEffect(str, Enum):
    REJECT = "reject"
    CORRUPT_STAGED_PAYLOAD = "corrupt_staged_payload"


@dataclass(frozen=True)
class FakeInstallationProfileFault:
    """One deterministic fault injected into the non-hardware fake."""

    receiver_id: int
    phase: InstallationProfileTransactionPhase
    effect: FakeInstallationProfileFaultEffect = (
        FakeInstallationProfileFaultEffect.REJECT
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "receiver_id", _receiver_id(self.receiver_id))
        try:
            phase = InstallationProfileTransactionPhase(self.phase)
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported installation-profile fault phase") from exc
        try:
            effect = FakeInstallationProfileFaultEffect(self.effect)
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported installation-profile fault effect") from exc
        if phase is InstallationProfileTransactionPhase.COMPENSATE:
            raise ValueError("compensation fault injection is intentionally unsupported")
        if (
            effect is FakeInstallationProfileFaultEffect.CORRUPT_STAGED_PAYLOAD
            and phase is not InstallationProfileTransactionPhase.STAGE
        ):
            raise ValueError("staged-payload corruption is valid only during stage")
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "effect", effect)


@dataclass(frozen=True)
class InstallationProfileTransactionOperation:
    phase: InstallationProfileTransactionPhase
    receiver_id: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "receiver_id", _receiver_id(self.receiver_id))
        object.__setattr__(
            self, "phase", InstallationProfileTransactionPhase(self.phase)
        )


class InstallationProfileWallHealth(str, Enum):
    HEALTHY = "healthy"
    NO_ACTIVE = "no_active"
    MIXED_GENERATION = "mixed_generation"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class InstallationProfileReceiverStatus:
    """Immutable receiver state consumed by wall and transaction adapters."""

    receiver_id: int
    capacity_bytes: int
    reserve_bytes: int
    used_bytes: int
    available_bytes: int
    cached_digests: tuple[str, ...]
    pinned_digests: tuple[str, ...]
    staged_binding: InstallationProfileCacheBinding | None
    active_binding: InstallationProfileCacheBinding | None
    rollback_binding: InstallationProfileCacheBinding | None
    cache_integrity_ok: bool
    write_count: int
    eviction_count: int
    stage_count: int
    verify_count: int
    commit_count: int
    compensation_count: int


@dataclass(frozen=True)
class InstallationProfileWallStatus:
    """Immutable four-receiver health summary returned by a wall adapter."""

    health: InstallationProfileWallHealth
    active_profile_id: str | None
    mixed_generation: bool
    receiver_statuses: tuple[
        InstallationProfileReceiverStatus,
        InstallationProfileReceiverStatus,
        InstallationProfileReceiverStatus,
        InstallationProfileReceiverStatus,
    ]

    @property
    def healthy(self) -> bool:
        return self.health is InstallationProfileWallHealth.HEALTHY

    @property
    def no_active(self) -> bool:
        return self.health is InstallationProfileWallHealth.NO_ACTIVE


@dataclass(frozen=True)
class InstallationProfileTransactionResult:
    success: bool
    changed: bool
    profile_id: str
    failed_phase: InstallationProfileTransactionPhase | None
    failed_receiver_id: int | None
    compensated: bool
    error: str | None
    operations: tuple[InstallationProfileTransactionOperation, ...]
    wall_status: InstallationProfileWallStatus


@dataclass(frozen=True)
class _ProfileCachePreflight:
    receiver_id: int
    binding: InstallationProfileCacheBinding
    evictions: tuple[str, ...]
    remove_corrupt_candidate: bool
    write_required: bool


@dataclass(frozen=True)
class InstallationProfileReceiverSnapshot:
    """Immutable active/staged/rollback state captured before mutation."""

    staged_binding: InstallationProfileCacheBinding | None
    active_binding: InstallationProfileCacheBinding | None
    rollback_binding: InstallationProfileCacheBinding | None


# Compatibility names remain part of the fake's public API.
FakeInstallationProfileReceiverStatus = InstallationProfileReceiverStatus
FakeInstallationProfileWallStatus = InstallationProfileWallStatus
_ProfileReceiverSnapshot = InstallationProfileReceiverSnapshot


class InstallationProfileReceiver(Protocol):
    """Structural receiver boundary required by the transaction engine.

    Concrete adapters own cache/transport details. Plans returned by
    ``preflight_profile`` are opaque to the engine and are passed back only to
    the same receiver's ``stage_profile`` call. Adapters translate protocol and
    state failures to ``InstallationProfileTransactionError`` and may propagate
    transport/filesystem ``OSError`` subclasses; both trigger compensation.
    Programmer errors deliberately propagate.
    """

    receiver_id: int

    @property
    def active_binding(self) -> InstallationProfileCacheBinding | None: ...

    @property
    def staged_binding(self) -> InstallationProfileCacheBinding | None: ...

    def binding_is_valid(
        self, binding: InstallationProfileCacheBinding | None
    ) -> bool: ...

    def transaction_snapshot(self) -> InstallationProfileReceiverSnapshot: ...

    def preflight_profile(
        self, binding: InstallationProfileCacheBinding, payload: bytes
    ) -> object: ...

    def stage_profile(
        self, plan: object, payload: bytes, *, corrupt_payload: bool
    ) -> None: ...

    def verify_profile(
        self, binding: InstallationProfileCacheBinding, payload: bytes
    ) -> None: ...

    def commit_profile(
        self,
        binding: InstallationProfileCacheBinding,
        prior_active: InstallationProfileCacheBinding | None,
    ) -> None: ...

    def compensate_profile(
        self, snapshot: InstallationProfileReceiverSnapshot
    ) -> None: ...


class InstallationProfileWall(Protocol):
    """Structural four-receiver wall boundary required by the engine."""

    receivers: Sequence[InstallationProfileReceiver]

    def status(self) -> InstallationProfileWallStatus: ...


class FakeInstallationProfileReceiver:
    """A bounded receiver-profile cache with no hardware side effects."""

    def __init__(
        self, receiver_id: int, *, capacity_bytes: int, reserve_bytes: int = 0
    ) -> None:
        self.receiver_id = _receiver_id(receiver_id)
        if type(capacity_bytes) is not int or capacity_bytes <= 0:
            raise ValueError("capacity_bytes must be a positive integer")
        if (
            type(reserve_bytes) is not int
            or reserve_bytes < 0
            or reserve_bytes > capacity_bytes
        ):
            raise ValueError(
                "reserve_bytes must be an integer between zero and capacity_bytes"
            )
        self.capacity_bytes = capacity_bytes
        self.reserve_bytes = reserve_bytes
        self._cache: dict[str, bytes] = {}
        self._last_used: dict[str, int] = {}
        self._clock = 0
        self._staged_binding: InstallationProfileCacheBinding | None = None
        self._active_binding: InstallationProfileCacheBinding | None = None
        self._rollback_binding: InstallationProfileCacheBinding | None = None
        self._write_count = 0
        self._eviction_count = 0
        self._stage_count = 0
        self._verify_count = 0
        self._commit_count = 0
        self._compensation_count = 0

    @property
    def usable_bytes(self) -> int:
        return self.capacity_bytes - self.reserve_bytes

    @property
    def used_bytes(self) -> int:
        return sum(len(payload) for payload in self._cache.values())

    @property
    def staged_binding(self) -> InstallationProfileCacheBinding | None:
        return self._staged_binding

    @property
    def active_binding(self) -> InstallationProfileCacheBinding | None:
        return self._active_binding

    @property
    def rollback_binding(self) -> InstallationProfileCacheBinding | None:
        return self._rollback_binding

    @property
    def cached_digests(self) -> tuple[str, ...]:
        return tuple(sorted(self._cache))

    @property
    def pinned_digests(self) -> tuple[str, ...]:
        bindings = (
            self._active_binding,
            self._rollback_binding,
            self._staged_binding,
        )
        return tuple(
            sorted(
                {
                    binding.payload_digest
                    for binding in bindings
                    if binding is not None
                }
            )
        )

    def _touch(self, digest: str) -> None:
        self._clock += 1
        self._last_used[digest] = self._clock

    def _payload_is_valid(self, digest: str) -> bool:
        payload = self._cache.get(digest)
        return payload is not None and _sha256(payload) == digest

    def _binding_is_valid(
        self, binding: InstallationProfileCacheBinding | None
    ) -> bool:
        return binding is None or self._payload_is_valid(binding.payload_digest)

    def binding_is_valid(
        self, binding: InstallationProfileCacheBinding | None
    ) -> bool:
        """Return whether a binding is absent or backed by valid cached bytes."""

        return self._binding_is_valid(binding)

    def cache_inactive(self, payload: bytes) -> str:
        """Seed one unpinned content-addressed entry for deterministic tests."""

        immutable = _immutable_payload(payload, field="payload")
        digest = _sha256(immutable)
        existing = self._cache.get(digest)
        if existing is not None:
            if existing != immutable:
                raise InstallationProfileTransactionError(
                    "cached digest is bound to mismatched bytes"
                )
            self._touch(digest)
            return digest
        if self.used_bytes + len(immutable) > self.usable_bytes:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} cache seed exceeds capacity reserve"
            )
        self._cache[digest] = immutable
        self._touch(digest)
        self._write_count += 1
        return digest

    def seed_active(self, profile_id: str, payload: bytes) -> None:
        """Seed a valid active binding without simulating a transaction."""

        normalized_id = _content_id(profile_id, field="profile_id")
        digest = self.cache_inactive(payload)
        self._active_binding = InstallationProfileCacheBinding(
            normalized_id, digest
        )

    def seed_rollback(self, profile_id: str, payload: bytes) -> None:
        """Seed a valid rollback binding without simulating a transaction."""

        normalized_id = _content_id(profile_id, field="profile_id")
        digest = self.cache_inactive(payload)
        self._rollback_binding = InstallationProfileCacheBinding(
            normalized_id, digest
        )

    def delete_cached_payload(self, payload_digest: str) -> bool:
        """Delete one inactive cache entry; active/staged/rollback pins refuse."""

        digest = _content_id(payload_digest, field="payload_digest")
        if digest in self.pinned_digests:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} refuses deletion of pinned payload"
            )
        if digest not in self._cache:
            return False
        del self._cache[digest]
        self._last_used.pop(digest, None)
        self._eviction_count += 1
        return True

    def corrupt_cached_payload(self, payload_digest: str) -> None:
        """Deterministically corrupt one existing entry for integrity tests."""

        digest = _content_id(payload_digest, field="payload_digest")
        payload = self._cache.get(digest)
        if payload is None:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} does not cache {digest}"
            )
        corrupted = bytearray(payload)
        corrupted[0] ^= 0xFF
        self._cache[digest] = bytes(corrupted)

    def status(self) -> InstallationProfileReceiverStatus:
        integrity_ok = all(
            _sha256(payload) == digest for digest, payload in self._cache.items()
        )
        return InstallationProfileReceiverStatus(
            receiver_id=self.receiver_id,
            capacity_bytes=self.capacity_bytes,
            reserve_bytes=self.reserve_bytes,
            used_bytes=self.used_bytes,
            available_bytes=self.usable_bytes - self.used_bytes,
            cached_digests=self.cached_digests,
            pinned_digests=self.pinned_digests,
            staged_binding=self._staged_binding,
            active_binding=self._active_binding,
            rollback_binding=self._rollback_binding,
            cache_integrity_ok=integrity_ok,
            write_count=self._write_count,
            eviction_count=self._eviction_count,
            stage_count=self._stage_count,
            verify_count=self._verify_count,
            commit_count=self._commit_count,
            compensation_count=self._compensation_count,
        )

    def _snapshot(self) -> _ProfileReceiverSnapshot:
        return _ProfileReceiverSnapshot(
            staged_binding=self._staged_binding,
            active_binding=self._active_binding,
            rollback_binding=self._rollback_binding,
        )

    def transaction_snapshot(self) -> InstallationProfileReceiverSnapshot:
        """Capture the binding state needed for exact compensation."""

        return self._snapshot()

    def _preflight(
        self,
        binding: InstallationProfileCacheBinding,
        payload: bytes,
    ) -> _ProfileCachePreflight:
        if self._staged_binding is not None:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} already has a staged profile"
            )
        if binding.payload_digest != _sha256(payload):
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} candidate payload hash mismatch"
            )

        existing = self._cache.get(binding.payload_digest)
        existing_valid = existing == payload
        corrupt_candidate = existing is not None and not existing_valid
        if corrupt_candidate and binding.payload_digest in self.pinned_digests:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} candidate cache entry is corrupt and pinned"
            )

        projected_used = self.used_bytes
        if corrupt_candidate:
            projected_used -= len(existing)
        write_required = not existing_valid
        if write_required:
            projected_used += len(payload)

        shortfall = max(0, projected_used - self.usable_bytes)
        protected = set(self.pinned_digests)
        candidates = sorted(
            (
                digest
                for digest in self._cache
                if digest not in protected and digest != binding.payload_digest
            ),
            key=lambda digest: (self._last_used[digest], digest),
        )
        evictions: list[str] = []
        for digest in candidates:
            if shortfall <= 0:
                break
            evictions.append(digest)
            shortfall -= len(self._cache[digest])
        if shortfall > 0:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} has insufficient cache capacity "
                "after reserve and active/rollback/staged pins"
            )
        return _ProfileCachePreflight(
            receiver_id=self.receiver_id,
            binding=binding,
            evictions=tuple(evictions),
            remove_corrupt_candidate=corrupt_candidate,
            write_required=write_required,
        )

    def preflight_profile(
        self, binding: InstallationProfileCacheBinding, payload: bytes
    ) -> object:
        """Plan a candidate without mutating receiver state."""

        return self._preflight(binding, payload)

    def _stage(
        self,
        plan: _ProfileCachePreflight,
        payload: bytes,
        *,
        corrupt_payload: bool,
    ) -> None:
        if plan.receiver_id != self.receiver_id:
            raise InstallationProfileTransactionError("preflight plan receiver mismatch")
        if plan.binding.payload_digest != _sha256(payload):
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} stage payload hash mismatch"
            )
        for digest in plan.evictions:
            if digest in self.pinned_digests or digest not in self._cache:
                raise InstallationProfileTransactionError(
                    f"receiver {self.receiver_id} preflight eviction plan became invalid"
                )
        for digest in plan.evictions:
            del self._cache[digest]
            self._last_used.pop(digest, None)
            self._eviction_count += 1

        digest = plan.binding.payload_digest
        if plan.remove_corrupt_candidate:
            if digest in self.pinned_digests:
                raise InstallationProfileTransactionError(
                    f"receiver {self.receiver_id} corrupt candidate became pinned"
                )
            self._cache.pop(digest, None)
            self._last_used.pop(digest, None)
        if plan.write_required:
            stored = payload
            if corrupt_payload:
                corrupted = bytearray(payload)
                corrupted[0] ^= 0xFF
                stored = bytes(corrupted)
            self._cache[digest] = stored
            self._write_count += 1
        elif corrupt_payload:
            if digest in self.pinned_digests:
                raise InstallationProfileTransactionError(
                    f"receiver {self.receiver_id} refuses corruption of a pinned cache hit"
                )
            corrupted = bytearray(self._cache[digest])
            corrupted[0] ^= 0xFF
            self._cache[digest] = bytes(corrupted)
            self._write_count += 1
        self._touch(digest)
        self._staged_binding = plan.binding
        self._stage_count += 1
        if self.used_bytes > self.usable_bytes:
            raise AssertionError("profile cache reserve was violated after staging")

    def stage_profile(
        self, plan: object, payload: bytes, *, corrupt_payload: bool
    ) -> None:
        """Apply one opaque preflight plan and stage its candidate binding."""

        if not isinstance(plan, _ProfileCachePreflight):
            raise TypeError("plan must be this receiver's profile preflight result")
        self._stage(plan, payload, corrupt_payload=corrupt_payload)

    def _verify(
        self,
        binding: InstallationProfileCacheBinding,
        payload: bytes,
    ) -> None:
        self._verify_count += 1
        if self._staged_binding != binding:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} staged profile binding mismatch"
            )
        cached = self._cache.get(binding.payload_digest)
        if cached != payload or cached is None or _sha256(cached) != binding.payload_digest:
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} staged profile hash mismatch"
            )

    def verify_profile(
        self, binding: InstallationProfileCacheBinding, payload: bytes
    ) -> None:
        """Verify that staged bytes and their binding match the candidate."""

        self._verify(binding, payload)

    def _commit(
        self,
        binding: InstallationProfileCacheBinding,
        prior_active: InstallationProfileCacheBinding | None,
    ) -> None:
        if self._staged_binding != binding or not self._binding_is_valid(binding):
            raise InstallationProfileTransactionError(
                f"receiver {self.receiver_id} cannot commit an unverified profile"
            )
        self._rollback_binding = prior_active
        self._active_binding = binding
        self._staged_binding = None
        self._touch(binding.payload_digest)
        self._commit_count += 1

    def commit_profile(
        self,
        binding: InstallationProfileCacheBinding,
        prior_active: InstallationProfileCacheBinding | None,
    ) -> None:
        """Activate a verified binding while retaining the prior generation."""

        self._commit(binding, prior_active)

    def _compensate(self, snapshot: _ProfileReceiverSnapshot) -> None:
        self._staged_binding = snapshot.staged_binding
        self._active_binding = snapshot.active_binding
        self._rollback_binding = snapshot.rollback_binding
        for binding in (
            snapshot.staged_binding,
            snapshot.active_binding,
            snapshot.rollback_binding,
        ):
            if binding is not None and not self._binding_is_valid(binding):
                raise AssertionError(
                    "profile compensation lost a previously pinned payload"
                )
        protected = set(self.pinned_digests)
        for digest, payload in tuple(self._cache.items()):
            if digest not in protected and _sha256(payload) != digest:
                del self._cache[digest]
                self._last_used.pop(digest, None)
        self._compensation_count += 1

    def compensate_profile(
        self, snapshot: InstallationProfileReceiverSnapshot
    ) -> None:
        """Restore the exact binding snapshot after a transaction failure."""

        if not isinstance(snapshot, InstallationProfileReceiverSnapshot):
            raise TypeError("snapshot must be an InstallationProfileReceiverSnapshot")
        self._compensate(snapshot)


def _four_values(value: int | Sequence[int], *, field: str) -> tuple[int, ...]:
    if type(value) is int:
        return (value,) * RECEIVER_COUNT
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be an integer or four-integer sequence")
    normalized = tuple(value)
    if len(normalized) != RECEIVER_COUNT or any(
        type(item) is not int for item in normalized
    ):
        raise ValueError(f"{field} must contain exactly four integers")
    return normalized


class FakeInstallationProfileWall:
    """Exactly four deterministic in-memory receiver profile caches."""

    def __init__(
        self,
        *,
        capacity_bytes: int | Sequence[int],
        reserve_bytes: int | Sequence[int] = 0,
    ) -> None:
        capacities = _four_values(capacity_bytes, field="capacity_bytes")
        reserves = _four_values(reserve_bytes, field="reserve_bytes")
        self.receivers = tuple(
            FakeInstallationProfileReceiver(
                receiver_id,
                capacity_bytes=capacities[receiver_id],
                reserve_bytes=reserves[receiver_id],
            )
            for receiver_id in RECEIVER_IDS
        )

    def receiver(self, receiver_id: int) -> FakeInstallationProfileReceiver:
        return self.receivers[_receiver_id(receiver_id)]

    def seed_active(self, candidate: InstallationProfileCandidate) -> None:
        """Seed one unanimous active generation on all four fake receivers."""

        if not isinstance(candidate, InstallationProfileCandidate):
            raise TypeError("candidate must be an InstallationProfileCandidate")
        for receiver_id, receiver in enumerate(self.receivers):
            receiver.seed_active(
                candidate.profile_id, candidate.payload_for(receiver_id)
            )

    def seed_rollback(self, candidate: InstallationProfileCandidate) -> None:
        """Seed one unanimous rollback generation on all four fake receivers."""

        if not isinstance(candidate, InstallationProfileCandidate):
            raise TypeError("candidate must be an InstallationProfileCandidate")
        for receiver_id, receiver in enumerate(self.receivers):
            receiver.seed_rollback(
                candidate.profile_id, candidate.payload_for(receiver_id)
            )

    def status(self) -> InstallationProfileWallStatus:
        statuses = tuple(receiver.status() for receiver in self.receivers)
        active_ids = tuple(
            status.active_binding.profile_id
            if status.active_binding is not None
            else None
            for status in statuses
        )
        mixed = len(set(active_ids)) > 1
        active_profile_id = active_ids[0] if not mixed else None
        active_valid = all(
            receiver._binding_is_valid(receiver.active_binding)
            for receiver in self.receivers
        )
        cache_integrity = all(status.cache_integrity_ok for status in statuses)
        if mixed:
            health = InstallationProfileWallHealth.MIXED_GENERATION
        elif active_profile_id is None and cache_integrity:
            health = InstallationProfileWallHealth.NO_ACTIVE
        elif active_profile_id is not None and active_valid and cache_integrity:
            health = InstallationProfileWallHealth.HEALTHY
        else:
            health = InstallationProfileWallHealth.DEGRADED
        return InstallationProfileWallStatus(
            health=health,
            active_profile_id=active_profile_id,
            mixed_generation=mixed,
            receiver_statuses=statuses,  # type: ignore[arg-type]
        )


class InstallationProfileTransaction:
    """Preflight, stage, verify, and commit across one structural wall adapter.

    Receiver/profile errors plus ``OSError`` transport failures (including
    ``TimeoutError``) are operational failures and enter compensation after
    mutation starts. Programmer errors and process-control ``BaseException``
    subclasses are intentionally not swallowed.
    """

    def __init__(self, wall: InstallationProfileWall) -> None:
        receivers = getattr(wall, "receivers", None)
        status = getattr(wall, "status", None)
        if (
            isinstance(receivers, (str, bytes))
            or not isinstance(receivers, Sequence)
            or len(receivers) != RECEIVER_COUNT
            or not callable(status)
        ):
            raise TypeError("wall must implement the four-receiver profile wall interface")
        required_methods = (
            "binding_is_valid",
            "transaction_snapshot",
            "preflight_profile",
            "stage_profile",
            "verify_profile",
            "commit_profile",
            "compensate_profile",
        )
        for receiver_id, receiver in enumerate(receivers):
            if getattr(receiver, "receiver_id", None) != receiver_id or any(
                not callable(getattr(receiver, method, None))
                for method in required_methods
            ):
                raise TypeError(
                    "wall receivers must implement the profile receiver interface "
                    "in logical ID order"
                )
        self.wall = wall
        self.receivers = tuple(receivers)

    @staticmethod
    def _fault_map(
        faults: Sequence[FakeInstallationProfileFault],
    ) -> dict[
        tuple[InstallationProfileTransactionPhase, int],
        FakeInstallationProfileFault,
    ]:
        if isinstance(faults, (str, bytes)) or not isinstance(faults, Sequence):
            raise TypeError("faults must be a sequence of fake profile faults")
        result: dict[
            tuple[InstallationProfileTransactionPhase, int],
            FakeInstallationProfileFault,
        ] = {}
        for fault in faults:
            if not isinstance(fault, FakeInstallationProfileFault):
                raise TypeError("faults must contain FakeInstallationProfileFault")
            key = (fault.phase, fault.receiver_id)
            if key in result:
                raise ValueError("duplicate fake profile fault boundary")
            result[key] = fault
        return result

    @staticmethod
    def _reject_fault(
        fault: FakeInstallationProfileFault | None,
        *,
        phase: InstallationProfileTransactionPhase,
        receiver_id: int,
    ) -> None:
        if fault is not None and fault.effect is FakeInstallationProfileFaultEffect.REJECT:
            raise InstallationProfileTransactionError(
                f"injected {phase.value} failure at receiver {receiver_id}"
            )

    def _candidate_is_active(
        self, candidate: InstallationProfileCandidate
    ) -> bool:
        return all(
            receiver.active_binding == candidate.binding_for(receiver_id)
            and receiver.binding_is_valid(receiver.active_binding)
            for receiver_id, receiver in enumerate(self.receivers)
        )

    def _candidate_is_exactly_committed(
        self,
        candidate: InstallationProfileCandidate,
        prior_snapshots: Sequence[InstallationProfileReceiverSnapshot],
    ) -> bool:
        """Require exact active, staged, and rollback state on every receiver."""

        for receiver_id, receiver in enumerate(self.receivers):
            snapshot = receiver.transaction_snapshot()
            expected = candidate.binding_for(receiver_id)
            expected_rollback = prior_snapshots[receiver_id].active_binding
            if (
                snapshot.active_binding != expected
                or snapshot.staged_binding is not None
                or snapshot.rollback_binding != expected_rollback
                or not receiver.binding_is_valid(snapshot.active_binding)
                or not receiver.binding_is_valid(snapshot.rollback_binding)
            ):
                return False
        return True

    @staticmethod
    def _degraded_wall_status(
        status: InstallationProfileWallStatus,
    ) -> InstallationProfileWallStatus:
        """Return a fail-closed aggregate when receiver recovery is unproven."""

        if status.health in (
            InstallationProfileWallHealth.MIXED_GENERATION,
            InstallationProfileWallHealth.DEGRADED,
        ):
            return status
        return InstallationProfileWallStatus(
            health=InstallationProfileWallHealth.DEGRADED,
            active_profile_id=None,
            mixed_generation=status.mixed_generation,
            receiver_statuses=status.receiver_statuses,
        )

    @staticmethod
    def _snapshot_validation_error(
        receiver: InstallationProfileReceiver,
        expected: InstallationProfileReceiverSnapshot,
    ) -> str | None:
        """Describe binding drift or invalid restored pins for one receiver."""

        actual = receiver.transaction_snapshot()
        if actual != expected:
            return "binding snapshot differs from the pre-transaction state"
        invalid = tuple(
            name
            for name, binding in (
                ("staged", actual.staged_binding),
                ("active", actual.active_binding),
                ("rollback", actual.rollback_binding),
            )
            if not receiver.binding_is_valid(binding)
        )
        if invalid:
            return f"restored {','.join(invalid)} binding is not valid"
        return None

    def install(
        self,
        candidate: InstallationProfileCandidate,
        *,
        faults: Sequence[FakeInstallationProfileFault] = (),
    ) -> InstallationProfileTransactionResult:
        """Install a candidate or compensate to the exact prior binding state.

        Invalid caller input raises before fake-receiver work.  Operational
        capacity, integrity, state, and injected failures return a failed result
        with the final compensated wall status.
        """

        if not isinstance(candidate, InstallationProfileCandidate):
            raise TypeError("candidate must be an InstallationProfileCandidate")
        fault_map = self._fault_map(faults)
        operations: list[InstallationProfileTransactionOperation] = []
        initial_status = self.wall.status()
        if (
            self._candidate_is_active(candidate)
            and initial_status.healthy
            and all(
                receiver.staged_binding is None for receiver in self.receivers
            )
        ):
            return InstallationProfileTransactionResult(
                success=True,
                changed=False,
                profile_id=candidate.profile_id,
                failed_phase=None,
                failed_receiver_id=None,
                compensated=False,
                error=None,
                operations=(),
                wall_status=initial_status,
            )
        if initial_status.health not in (
            InstallationProfileWallHealth.HEALTHY,
            InstallationProfileWallHealth.NO_ACTIVE,
        ):
            return InstallationProfileTransactionResult(
                success=False,
                changed=False,
                profile_id=candidate.profile_id,
                failed_phase=InstallationProfileTransactionPhase.PREFLIGHT,
                failed_receiver_id=None,
                compensated=False,
                error="profile transaction requires a healthy unanimous or no-active wall",
                operations=(),
                wall_status=initial_status,
            )
        if any(receiver.staged_binding is not None for receiver in self.receivers):
            return InstallationProfileTransactionResult(
                success=False,
                changed=False,
                profile_id=candidate.profile_id,
                failed_phase=InstallationProfileTransactionPhase.PREFLIGHT,
                failed_receiver_id=None,
                compensated=False,
                error="profile transaction requires no pre-existing staged binding",
                operations=(),
                wall_status=initial_status,
            )

        snapshots = tuple(
            receiver.transaction_snapshot() for receiver in self.receivers
        )
        plans: list[object] = []
        mutation_started = False
        failed_phase: InstallationProfileTransactionPhase | None = None
        failed_receiver_id: int | None = None
        try:
            for receiver_id, receiver in enumerate(self.receivers):
                failed_phase = InstallationProfileTransactionPhase.PREFLIGHT
                failed_receiver_id = receiver_id
                operations.append(
                    InstallationProfileTransactionOperation(
                        failed_phase, receiver_id
                    )
                )
                fault = fault_map.get((failed_phase, receiver_id))
                self._reject_fault(
                    fault, phase=failed_phase, receiver_id=receiver_id
                )
                plans.append(
                    receiver.preflight_profile(
                        candidate.binding_for(receiver_id),
                        candidate.payload_for(receiver_id),
                    )
                )

            for receiver_id, receiver in enumerate(self.receivers):
                failed_phase = InstallationProfileTransactionPhase.STAGE
                failed_receiver_id = receiver_id
                operations.append(
                    InstallationProfileTransactionOperation(
                        failed_phase, receiver_id
                    )
                )
                fault = fault_map.get((failed_phase, receiver_id))
                self._reject_fault(
                    fault, phase=failed_phase, receiver_id=receiver_id
                )
                # From this boundary onward the receiver may have applied its
                # preflight eviction plan before reporting a stage error.
                mutation_started = True
                receiver.stage_profile(
                    plans[receiver_id],
                    candidate.payload_for(receiver_id),
                    corrupt_payload=(
                        fault is not None
                        and fault.effect
                        is FakeInstallationProfileFaultEffect.CORRUPT_STAGED_PAYLOAD
                    ),
                )

            for receiver_id, receiver in enumerate(self.receivers):
                failed_phase = InstallationProfileTransactionPhase.VERIFY
                failed_receiver_id = receiver_id
                operations.append(
                    InstallationProfileTransactionOperation(
                        failed_phase, receiver_id
                    )
                )
                fault = fault_map.get((failed_phase, receiver_id))
                self._reject_fault(
                    fault, phase=failed_phase, receiver_id=receiver_id
                )
                receiver.verify_profile(
                    candidate.binding_for(receiver_id),
                    candidate.payload_for(receiver_id),
                )

            for receiver_id, receiver in enumerate(self.receivers):
                failed_phase = InstallationProfileTransactionPhase.COMMIT
                failed_receiver_id = receiver_id
                operations.append(
                    InstallationProfileTransactionOperation(
                        failed_phase, receiver_id
                    )
                )
                fault = fault_map.get((failed_phase, receiver_id))
                self._reject_fault(
                    fault, phase=failed_phase, receiver_id=receiver_id
                )
                receiver.commit_profile(
                    candidate.binding_for(receiver_id),
                    snapshots[receiver_id].active_binding,
                )

            final_status = self.wall.status()
            if (
                not self._candidate_is_exactly_committed(candidate, snapshots)
                or not final_status.healthy
                or final_status.active_profile_id != candidate.profile_id
            ):
                raise InstallationProfileTransactionError(
                    "profile commit did not produce the exact healthy unanimous candidate"
                )
            return InstallationProfileTransactionResult(
                success=True,
                changed=True,
                profile_id=candidate.profile_id,
                failed_phase=None,
                failed_receiver_id=None,
                compensated=False,
                error=None,
                operations=tuple(operations),
                wall_status=final_status,
            )
        except _OPERATIONAL_EXCEPTIONS as exc:
            compensation_errors: list[str] = []
            compensated = False
            if mutation_started:
                for receiver_id, (receiver, snapshot) in enumerate(
                    zip(self.receivers, snapshots)
                ):
                    operations.append(
                        InstallationProfileTransactionOperation(
                            InstallationProfileTransactionPhase.COMPENSATE,
                            receiver_id,
                        )
                    )
                    try:
                        receiver.compensate_profile(snapshot)
                    except _OPERATIONAL_EXCEPTIONS as compensation_exc:
                        compensation_errors.append(
                            f"receiver {receiver_id} compensation failed: "
                            f"{compensation_exc}"
                        )
                for receiver_id, (receiver, snapshot) in enumerate(
                    zip(self.receivers, snapshots)
                ):
                    try:
                        validation_error = self._snapshot_validation_error(
                            receiver, snapshot
                        )
                    except _OPERATIONAL_EXCEPTIONS as validation_exc:
                        validation_error = (
                            "post-compensation verification failed: "
                            f"{validation_exc}"
                        )
                    if validation_error is not None:
                        compensation_errors.append(
                            f"receiver {receiver_id} {validation_error}"
                        )
                compensated = not compensation_errors
            try:
                final_status = self.wall.status()
            except _OPERATIONAL_EXCEPTIONS as status_exc:
                compensation_errors.append(
                    f"final wall status failed: {status_exc}"
                )
                final_status = self._degraded_wall_status(initial_status)
                compensated = False
            if mutation_started and not compensated:
                final_status = self._degraded_wall_status(final_status)
            error = str(exc)
            if compensation_errors:
                error = f"{error}; compensation incomplete: " + "; ".join(
                    compensation_errors
                )
            return InstallationProfileTransactionResult(
                success=False,
                changed=mutation_started and not compensated,
                profile_id=candidate.profile_id,
                failed_phase=failed_phase,
                failed_receiver_id=failed_receiver_id,
                compensated=compensated,
                error=error,
                operations=tuple(operations),
                wall_status=final_status,
            )


__all__ = [
    "FakeInstallationProfileFault",
    "FakeInstallationProfileFaultEffect",
    "FakeInstallationProfileReceiver",
    "FakeInstallationProfileReceiverStatus",
    "FakeInstallationProfileWall",
    "FakeInstallationProfileWallStatus",
    "InstallationProfileCacheBinding",
    "InstallationProfileCandidate",
    "InstallationProfileReceiver",
    "InstallationProfileReceiverSnapshot",
    "InstallationProfileReceiverStatus",
    "InstallationProfileTransaction",
    "InstallationProfileTransactionError",
    "InstallationProfileTransactionOperation",
    "InstallationProfileTransactionPhase",
    "InstallationProfileTransactionResult",
    "InstallationProfileWall",
    "InstallationProfileWallHealth",
    "InstallationProfileWallStatus",
    "RECEIVER_COUNT",
    "RECEIVER_IDS",
    "candidate_from_resolved",
]
