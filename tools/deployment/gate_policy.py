"""Evidence and identity policy for optional deterministic deployment gates.

This module deliberately does not implement a cache store.  It answers the two
questions that must be settled before a cache is added to the deployment
coordinator:

* is there enough normal-attempt evidence for this gate to be worth caching?
* would a previously serialized success be safe to reuse for these exact inputs?

Keeping persistence out of this module prevents the presence of cache-key code
from silently turning caching on before Phase 0C receipts provide the required
timing evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping, Optional, Sequence, Tuple, Union

MINIMUM_NORMAL_ATTEMPTS = 20
MINIMUM_REGULAR_DURATION_SECONDS = 5.0
REGULAR_ATTEMPT_FRACTION = 0.75
CACHE_RECORD_VERSION = 1

FileContent = Union[bytes, bytearray, memoryview, str]


class CacheEligibility(str, Enum):
    """Outcome of the evidence gate; only ``ELIGIBLE`` permits future work."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INELIGIBLE_EXTERNAL = "ineligible_external"
    INELIGIBLE_NONDETERMINISTIC = "ineligible_nondeterministic"
    BELOW_COST_THRESHOLD = "below_cost_threshold"
    NO_MATERIAL_BENEFIT = "no_material_benefit"
    ELIGIBLE = "eligible"


class GateDisposition(str, Enum):
    """Receipt-visible disposition for an individual gate invocation."""

    EXECUTED = "executed"
    CACHED = "cached"
    SKIPPED = "skipped"


class CacheProbeReason(str, Enum):
    FORCED = "forced"
    POLICY_DISABLED = "policy_disabled"
    MISSING = "missing"
    CORRUPT = "corrupt"
    IDENTITY_MISMATCH = "identity_mismatch"
    HIT = "hit"


@dataclass(frozen=True)
class GateTiming:
    """One gate timing extracted from a Phase 0C deployment receipt."""

    receipt_id: str
    duration_seconds: float
    normal_attempt: bool = True
    succeeded: bool = True

    def __post_init__(self) -> None:
        if not self.receipt_id:
            raise ValueError("receipt_id must not be empty")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ValueError("duration_seconds must be finite and non-negative")


@dataclass(frozen=True)
class GateDescriptor:
    gate_id: str
    gate_version: str
    deterministic: bool
    local_only: bool

    def __post_init__(self) -> None:
        if not self.gate_id or not self.gate_version:
            raise ValueError("gate_id and gate_version must not be empty")


@dataclass(frozen=True)
class GateCacheDecision:
    gate_id: str
    gate_version: str
    eligibility: CacheEligibility
    reason: str
    normal_success_count: int
    threshold_sample_count: int
    median_seconds: Optional[float]

    @property
    def permits_cache_implementation(self) -> bool:
        return self.eligibility is CacheEligibility.ELIGIBLE


def evaluate_cache_candidate(
    descriptor: GateDescriptor,
    timings: Sequence[GateTiming],
    *,
    materially_improves_workflow: bool,
    minimum_normal_attempts: int = MINIMUM_NORMAL_ATTEMPTS,
    duration_threshold_seconds: float = MINIMUM_REGULAR_DURATION_SECONDS,
    regular_fraction: float = REGULAR_ATTEMPT_FRACTION,
) -> GateCacheDecision:
    """Evaluate measured receipts without enabling or writing a cache.

    Failed, interrupted, diagnostic, and forced attempts are not normal success
    evidence. Duplicate receipt IDs are rejected so one attempt cannot be counted
    repeatedly to manufacture eligibility.
    """

    if minimum_normal_attempts < 1:
        raise ValueError("minimum_normal_attempts must be positive")
    if not math.isfinite(duration_threshold_seconds) or duration_threshold_seconds < 0:
        raise ValueError("duration_threshold_seconds must be finite and non-negative")
    if not math.isfinite(regular_fraction) or not 0 < regular_fraction <= 1:
        raise ValueError("regular_fraction must be in (0, 1]")

    receipt_ids = [timing.receipt_id for timing in timings]
    if len(receipt_ids) != len(set(receipt_ids)):
        raise ValueError("gate timings contain duplicate receipt IDs")

    normal = [
        timing.duration_seconds
        for timing in timings
        if timing.normal_attempt and timing.succeeded
    ]
    threshold_count = sum(value >= duration_threshold_seconds for value in normal)
    median = statistics.median(normal) if normal else None

    def decision(eligibility: CacheEligibility, reason: str) -> GateCacheDecision:
        return GateCacheDecision(
            gate_id=descriptor.gate_id,
            gate_version=descriptor.gate_version,
            eligibility=eligibility,
            reason=reason,
            normal_success_count=len(normal),
            threshold_sample_count=threshold_count,
            median_seconds=median,
        )

    if not descriptor.local_only:
        return decision(
            CacheEligibility.INELIGIBLE_EXTERNAL,
            "external, hardware, readiness, and physical gates are never cacheable",
        )
    if not descriptor.deterministic:
        return decision(
            CacheEligibility.INELIGIBLE_NONDETERMINISTIC,
            "only deterministic gates are cacheable",
        )
    if len(normal) < minimum_normal_attempts:
        return decision(
            CacheEligibility.INSUFFICIENT_EVIDENCE,
            f"need {minimum_normal_attempts} normal successful receipt timings; "
            f"have {len(normal)}",
        )
    required_threshold_count = math.ceil(len(normal) * regular_fraction)
    if threshold_count < required_threshold_count:
        return decision(
            CacheEligibility.BELOW_COST_THRESHOLD,
            f"only {threshold_count}/{len(normal)} normal attempts cost at least "
            f"{duration_threshold_seconds:g}s; need {required_threshold_count}",
        )
    if not materially_improves_workflow:
        return decision(
            CacheEligibility.NO_MATERIAL_BENEFIT,
            "measured cost does not materially improve the observed workflow",
        )
    return decision(
        CacheEligibility.ELIGIBLE,
        "receipt evidence permits a separately reviewed cache implementation",
    )


@dataclass(frozen=True)
class GateCacheInputs:
    """Complete, reviewable identity inputs for a deterministic local gate."""

    gate_id: str
    gate_version: str
    selected_source_contents: Mapping[str, FileContent]
    dirty_manifest: FileContent
    lockfile_contents: Mapping[str, FileContent]
    interpreter_identity: str
    platform_identity: str
    toolchain_identity: str
    command_arguments: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.gate_id or not self.gate_version:
            raise ValueError("gate_id and gate_version must not be empty")
        if not self.selected_source_contents:
            raise ValueError("selected_source_contents must not be empty")
        if not self.lockfile_contents:
            raise ValueError("lockfile_contents must not be empty")
        for value, name in (
            (self.interpreter_identity, "interpreter_identity"),
            (self.platform_identity, "platform_identity"),
            (self.toolchain_identity, "toolchain_identity"),
        ):
            if not value:
                raise ValueError(f"{name} must not be empty")
        if not self.command_arguments or not all(
            isinstance(argument, str) and argument
            for argument in self.command_arguments
        ):
            raise ValueError("command_arguments must contain non-empty strings")
        _validate_content_map(self.selected_source_contents, "selected source")
        _validate_content_map(self.lockfile_contents, "lockfile")
        _content_bytes(self.dirty_manifest)


def _content_bytes(content: FileContent) -> bytes:
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(content, (bytes, bytearray, memoryview)):
        return bytes(content)
    raise TypeError("cache identity contents must be bytes-like or str")


def _validate_content_map(contents: Mapping[str, FileContent], label: str) -> None:
    for path, content in contents.items():
        parsed = PurePosixPath(path)
        if not path or parsed.is_absolute() or ".." in parsed.parts:
            raise ValueError(f"unsafe {label} path: {path!r}")
        _content_bytes(content)


def _content_manifest(contents: Mapping[str, FileContent]) -> list[dict[str, Any]]:
    manifest = []
    for path in sorted(contents):
        data = _content_bytes(contents[path])
        manifest.append(
            {
                "path": path,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return manifest


def build_cache_identity(inputs: GateCacheInputs) -> str:
    """Return a domain-separated SHA-256 identity for every declared input."""

    payload = {
        "schema": "ledgrid.gate-cache-identity.v1",
        "gate_id": inputs.gate_id,
        "gate_version": inputs.gate_version,
        "selected_source": _content_manifest(inputs.selected_source_contents),
        "dirty_manifest": {
            "size": len(_content_bytes(inputs.dirty_manifest)),
            "sha256": hashlib.sha256(_content_bytes(inputs.dirty_manifest)).hexdigest(),
        },
        "lockfiles": _content_manifest(inputs.lockfile_contents),
        "interpreter": inputs.interpreter_identity,
        "platform": inputs.platform_identity,
        "toolchain": inputs.toolchain_identity,
        "argv": list(inputs.command_arguments),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(b"ledgrid-gate-cache-v1\0" + encoded).hexdigest()


@dataclass(frozen=True)
class CacheProbe:
    disposition: GateDisposition
    reason: CacheProbeReason
    result_digest: Optional[str] = None


def probe_cache_record(
    raw_record: Optional[bytes],
    *,
    expected_identity: str,
    policy_decision: GateCacheDecision,
    force: bool = False,
) -> CacheProbe:
    """Validate an optional record; every unsafe case degrades to execution.

    ``force`` is intended for the explicit complete test command. It is checked
    before record parsing so even a valid success cannot suppress the gate.
    """

    if force:
        return CacheProbe(GateDisposition.EXECUTED, CacheProbeReason.FORCED)
    if not policy_decision.permits_cache_implementation:
        return CacheProbe(GateDisposition.EXECUTED, CacheProbeReason.POLICY_DISABLED)
    if raw_record is None:
        return CacheProbe(GateDisposition.EXECUTED, CacheProbeReason.MISSING)
    try:
        decoded = json.loads(raw_record.decode("utf-8"))
        if not isinstance(decoded, dict) or set(decoded) != {
            "schema_version",
            "gate_id",
            "gate_version",
            "identity",
            "outcome",
            "result_digest",
        }:
            raise ValueError("cache record shape mismatch")
        if decoded["schema_version"] != CACHE_RECORD_VERSION:
            raise ValueError("cache record version mismatch")
        for name in ("gate_id", "gate_version", "identity", "result_digest"):
            if not isinstance(decoded[name], str) or not decoded[name]:
                raise ValueError(f"invalid {name}")
        if decoded["outcome"] != "passed":
            raise ValueError("only successful gates can be reused")
        if len(decoded["result_digest"]) != 64 or any(
            character not in "0123456789abcdef"
            for character in decoded["result_digest"]
        ):
            raise ValueError("result_digest is not canonical SHA-256")
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return CacheProbe(GateDisposition.EXECUTED, CacheProbeReason.CORRUPT)
    # Treat descriptor drift as an identity mismatch even if a malformed or
    # malicious store copies the expected aggregate digest into another gate's
    # record.  This also makes corruption diagnosis more useful to operators.
    if (
        decoded["gate_id"] != policy_decision.gate_id
        or decoded["gate_version"] != policy_decision.gate_version
        or decoded["identity"] != expected_identity
    ):
        return CacheProbe(GateDisposition.EXECUTED, CacheProbeReason.IDENTITY_MISMATCH)
    return CacheProbe(
        GateDisposition.CACHED,
        CacheProbeReason.HIT,
        result_digest=decoded["result_digest"],
    )


def skipped_gate() -> GateDisposition:
    """Return the explicit receipt classification for an out-of-scope gate."""

    return GateDisposition.SKIPPED
