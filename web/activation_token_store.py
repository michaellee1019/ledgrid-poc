"""In-memory Check tokens for the bounded local Scene-v1 activation slice."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from animation.core.component_catalog import ComponentCatalog
from ipc.scene_contract import (
    CanonicalScene,
    LocalSceneAdapter,
    SceneContractError,
    SceneIdentity,
    build_scene_activation_command,
    normalize_composer_scene,
    normalize_scene_identity,
)


class ActivationTokenError(RuntimeError):
    """Base error for rejected Check-to-activation transitions."""


class ActivationTokenUnknown(ActivationTokenError):
    """The Check token was never issued by this local store."""


class ActivationTokenStale(ActivationTokenError):
    """The Check token has expired before activation."""


class ActivationTokenConflict(ActivationTokenError):
    """A token was reused with a different exact activation request."""


@dataclass(frozen=True)
class CheckedScene:
    token: str
    canonical: CanonicalScene
    expires_at: float

    @property
    def identity(self) -> SceneIdentity:
        return self.canonical.identity


@dataclass(frozen=True)
class ActivationReceipt:
    identity: SceneIdentity
    command: dict[str, Any]
    exact_retry: bool


@dataclass
class _CheckRecord:
    checked: CheckedScene
    binding: tuple[SceneIdentity, str] | None = None
    receipt: ActivationReceipt | None = None


class ActivationTokenStore:
    """Bind a Check result to one exact-basis, idempotent activation.

    This deliberately stores only process-local state for the local vertical
    slice.  It has no hardware or durable deployment responsibilities.
    """

    def __init__(
        self, *, ttl_seconds: float = 120.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Check token lifetime must be positive")
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._records: dict[str, _CheckRecord] = {}

    def check(self, request: Mapping[str, Any], catalog: ComponentCatalog) -> CheckedScene:
        """Validate before storing anything, then issue a one-scene Check token."""

        canonical = normalize_composer_scene(request, catalog)
        token = secrets.token_urlsafe(24)
        checked = CheckedScene(
            token=token, canonical=canonical, expires_at=self._clock() + self._ttl_seconds
        )
        self._records[token] = _CheckRecord(checked=checked)
        return checked

    def activate(
        self,
        token: str,
        *,
        basis: Mapping[str, Any],
        idempotency_key: str,
        control_channel: Any,
        local_adapter: LocalSceneAdapter,
    ) -> ActivationReceipt:
        """Send and observe exactly the identity created by ``check``.

        All input and command validation occurs before either the control
        channel or adapter mutates.  An exact retry returns its first receipt;
        a differing idempotency key or basis fails without a second send.
        """

        record = self._record_for(token)
        supplied = normalize_scene_identity(basis)
        if supplied != record.checked.identity:
            raise ActivationTokenConflict("activation basis does not match the Check result")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ActivationTokenConflict("activation idempotency key is required")
        if not callable(getattr(control_channel, "send_command", None)):
            raise ActivationTokenConflict("runtime control channel is unavailable")
        if not isinstance(local_adapter, LocalSceneAdapter):
            raise ActivationTokenConflict("local scene adapter is unavailable")

        requested_binding = (supplied, idempotency_key)
        if record.binding is not None:
            if record.binding != requested_binding:
                raise ActivationTokenConflict("Check token already names a different activation")
            assert record.receipt is not None
            return ActivationReceipt(
                identity=record.receipt.identity,
                command=dict(record.receipt.command),
                exact_retry=True,
            )

        command = build_scene_activation_command(record.checked.canonical)
        local_adapter.validate_control(command)
        # Both command representations carry the same closed basis.  The
        # existing generic control channel remains topology-neutral.
        control_channel.send_command(
            "activate_scene", basis=dict(command["basis"]), scene=dict(command["scene"])
        )
        observed = local_adapter.accept_control(command)
        if observed != record.checked.identity:  # pragma: no cover - invariant guard
            raise SceneContractError("local adapter accepted a different scene identity")
        receipt = ActivationReceipt(identity=observed, command=command, exact_retry=False)
        record.binding = requested_binding
        record.receipt = receipt
        return receipt

    def _record_for(self, token: str) -> _CheckRecord:
        if not isinstance(token, str) or not token:
            raise ActivationTokenUnknown("Check token is unknown")
        record = self._records.get(token)
        if record is None:
            raise ActivationTokenUnknown("Check token is unknown")
        if self._clock() >= record.checked.expires_at and record.binding is None:
            raise ActivationTokenStale("Check token is stale")
        return record
