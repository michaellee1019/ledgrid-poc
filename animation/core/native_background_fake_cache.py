"""Deterministic in-memory receiver cache model for native-background tests.

This module intentionally has no transport, SPI, firmware, or filesystem
behavior.  It models capacity preflight, shared-payload installation, unanimous
activation, and best-effort compensation before those operations are mapped to
real receivers in a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from animation.core.native_background_library import ResolvedNativeBackground


class FakeNativeBackgroundError(RuntimeError):
    """A fake cache transaction failed or could not prove recovery."""


FailureInjector = Callable[[str, int], None]


@dataclass(frozen=True)
class FakeNativeBinding:
    bundle_digest: str
    payload_digest: str


@dataclass
class FakeNativeReceiver:
    logical_id: int
    capacity: int = 1024 * 1024
    reserve: int = 64 * 1024
    payloads: dict[str, bytes] = field(default_factory=dict)
    installed: dict[str, FakeNativeBinding] = field(default_factory=dict)
    active: FakeNativeBinding | None = None
    staged: FakeNativeBinding | None = None

    @property
    def used(self) -> int:
        return sum(len(payload) for payload in self.payloads.values())

    def can_store(self, digest: str, payload_size: int) -> bool:
        additional = 0 if digest in self.payloads else payload_size
        return self.used + additional + self.reserve <= self.capacity


@dataclass(frozen=True)
class FakeNativeTransactionResult:
    bundle_digest: str
    payload_digest: str
    outcome: str
    recovered: bool
    active_by_receiver: Mapping[int, str | None]


class FakeNativeReceiverWall:
    """Exact-roster cache/install/activation model with failure injection."""

    def __init__(
        self,
        receivers: tuple[FakeNativeReceiver, ...] | None = None,
        *,
        failure_injector: FailureInjector | None = None,
    ) -> None:
        self.receivers = receivers or tuple(
            FakeNativeReceiver(logical_id) for logical_id in range(5)
        )
        logical_ids = tuple(receiver.logical_id for receiver in self.receivers)
        if (
            not logical_ids
            or any(type(logical_id) is not int or logical_id < 0 for logical_id in logical_ids)
            or len(set(logical_ids)) != len(logical_ids)
        ):
            raise ValueError(
                "fake native wall requires a non-empty roster of unique "
                "non-negative logical receiver IDs"
            )
        self._failure_injector = failure_injector or (lambda _phase, _logical_id: None)

    @staticmethod
    def _binding(resolved: ResolvedNativeBackground) -> FakeNativeBinding:
        return FakeNativeBinding(
            bundle_digest=resolved.bundle_digest,
            payload_digest=resolved.payload_digest,
        )

    def _fail(self, phase: str, receiver: FakeNativeReceiver) -> None:
        self._failure_injector(phase, receiver.logical_id)

    def _active_map(self) -> dict[int, str | None]:
        return {
            receiver.logical_id: (
                receiver.active.bundle_digest if receiver.active is not None else None
            )
            for receiver in self.receivers
        }

    def install(
        self, resolved: ResolvedNativeBackground
    ) -> FakeNativeTransactionResult:
        """Preflight all receivers, then stage and verify one immutable binding."""

        binding = self._binding(resolved)
        payload = resolved.payload
        for receiver in self.receivers:
            self._fail("preflight", receiver)
            if not receiver.can_store(binding.payload_digest, len(payload)):
                raise FakeNativeBackgroundError(
                    f"receiver {receiver.logical_id} lacks cache capacity"
                )

        before = tuple(
            (dict(receiver.payloads), dict(receiver.installed), receiver.staged)
            for receiver in self.receivers
        )
        try:
            for receiver in self.receivers:
                self._fail("stage", receiver)
                existing = receiver.payloads.get(binding.payload_digest)
                if existing is not None and existing != payload:
                    raise FakeNativeBackgroundError(
                        f"receiver {receiver.logical_id} has a conflicting payload"
                    )
                receiver.payloads.setdefault(binding.payload_digest, payload)
                receiver.staged = binding
            for receiver in self.receivers:
                self._fail("verify", receiver)
                if receiver.payloads.get(binding.payload_digest) != payload:
                    raise FakeNativeBackgroundError(
                        f"receiver {receiver.logical_id} staged corrupt payload bytes"
                    )
            for receiver in self.receivers:
                receiver.installed[binding.bundle_digest] = binding
                receiver.staged = None
        except Exception as exc:
            recovered = True
            for receiver, snapshot in zip(self.receivers, before):
                try:
                    self._fail("compensate_install", receiver)
                    receiver.payloads, receiver.installed, receiver.staged = snapshot
                except Exception:
                    recovered = False
            raise FakeNativeBackgroundError(
                f"native install failed; recovered={str(recovered).lower()}: {exc}"
            ) from exc

        return FakeNativeTransactionResult(
            bundle_digest=binding.bundle_digest,
            payload_digest=binding.payload_digest,
            outcome="installed",
            recovered=True,
            active_by_receiver=self._active_map(),
        )

    def activate(self, bundle_digest: str) -> FakeNativeTransactionResult:
        """Activate an installed binding unanimously or restore every prior binding."""

        candidates = [receiver.installed.get(bundle_digest) for receiver in self.receivers]
        if any(candidate is None for candidate in candidates):
            raise FakeNativeBackgroundError(
                "native activation requires the bundle installed on every receiver"
            )
        first = candidates[0]
        if first is None or any(candidate != first for candidate in candidates):
            raise FakeNativeBackgroundError(
                "native activation requires one unanimous bundle/payload binding"
            )
        prior = tuple(receiver.active for receiver in self.receivers)
        try:
            for receiver, candidate in zip(self.receivers, candidates):
                self._fail("activate", receiver)
                receiver.active = candidate
        except Exception as exc:
            recovered = True
            for receiver, previous in zip(self.receivers, prior):
                try:
                    self._fail("compensate_activate", receiver)
                    receiver.active = previous
                except Exception:
                    recovered = False
            raise FakeNativeBackgroundError(
                f"native activation failed; recovered={str(recovered).lower()}: {exc}"
            ) from exc

        return FakeNativeTransactionResult(
            bundle_digest=first.bundle_digest,
            payload_digest=first.payload_digest,
            outcome="active",
            recovered=True,
            active_by_receiver=self._active_map(),
        )


__all__ = [
    "FakeNativeBackgroundError",
    "FakeNativeBinding",
    "FakeNativeReceiver",
    "FakeNativeReceiverWall",
    "FakeNativeTransactionResult",
]
