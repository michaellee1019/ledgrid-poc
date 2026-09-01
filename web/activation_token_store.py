"""Durable, opaque server-owned authorization for scene activation.

Raw bearer tokens are returned once to the requesting browser.  Only their
SHA-256 digests are retained, and the first valid use is transactionally bound
to exactly one activation ID and idempotency key.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
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


TOKEN_BYTES = 32
TOKEN_TTL_SECONDS = 120


class ActivationTokenError(RuntimeError):
    """Base class for token lookup and binding failures."""


class ActivationTokenExpired(ActivationTokenError):
    """The supplied token existed but is no longer valid."""


class ActivationTokenUnknown(ActivationTokenError):
    """The local Check token was never issued by this process."""


class ActivationTokenStale(ActivationTokenError):
    """The local Check token expired before activation."""


class ActivationTokenConflict(ActivationTokenError):
    """The supplied token cannot authorize the requested activation."""


@dataclass(frozen=True)
class IssuedActivationToken:
    token: str
    basis: dict[str, Any]
    basis_digest: str
    issued_at: float
    expires_at: float


@dataclass(frozen=True)
class StoredActivationToken:
    basis: dict[str, Any]
    basis_digest: str
    issued_at: float
    expires_at: float
    activation_id: str | None
    idempotency_key: str | None
    request_digest: str | None
    outbox_command: dict[str, Any] | None
    outbox_status: dict[str, Any] | None
    outbox_delivered: bool


@dataclass(frozen=True)
class BoundActivationToken:
    activation_id: str
    exact_retry: bool
    token: StoredActivationToken


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


def canonical_json(value: Any) -> str:
    """Return the one stable JSON representation used by token bindings."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class ActivationTokenStore:
    """SQLite-backed check-token store with atomic first-use binding."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        ttl_seconds: int = TOKEN_TTL_SECONDS,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.clock = clock
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a positive integer")
        self.ttl_seconds = ttl_seconds
        self._local_records: dict[str, _CheckRecord] = {}
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self.path is None:
            raise ActivationTokenConflict("durable activation token storage is unavailable")
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS activation_tokens (
                    token_digest TEXT PRIMARY KEY,
                    basis_json TEXT NOT NULL,
                    basis_digest TEXT NOT NULL,
                    issued_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    activation_id TEXT,
                    idempotency_key TEXT,
                    request_digest TEXT
                    ,outbox_command_json TEXT
                    ,outbox_status_json TEXT
                    ,outbox_delivered INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(activation_tokens)"
                ).fetchall()
            }
            for name, declaration in (
                ("outbox_command_json", "TEXT"),
                ("outbox_status_json", "TEXT"),
                ("outbox_delivered", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE activation_tokens ADD COLUMN {name} {declaration}"
                    )
        os.chmod(self.path, 0o600)

    def check(
        self, request: Mapping[str, Any], catalog: ComponentCatalog
    ) -> CheckedScene:
        """Validate and retain one process-local Scene v2 Check token."""

        canonical = normalize_composer_scene(request, catalog)
        token = secrets.token_urlsafe(24)
        checked = CheckedScene(
            token=token,
            canonical=canonical,
            expires_at=float(self.clock()) + self.ttl_seconds,
        )
        self._local_records[token] = _CheckRecord(checked=checked)
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
        """Activate exactly the checked local Scene identity, idempotently."""

        record = self._local_record_for(token)
        supplied = normalize_scene_identity(basis)
        if supplied != record.checked.identity:
            raise ActivationTokenConflict(
                "activation basis does not match the Check result"
            )
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ActivationTokenConflict("activation idempotency key is required")
        if not callable(getattr(control_channel, "send_command", None)):
            raise ActivationTokenConflict("runtime control channel is unavailable")
        if not isinstance(local_adapter, LocalSceneAdapter):
            raise ActivationTokenConflict("local scene adapter is unavailable")

        requested_binding = (supplied, idempotency_key)
        if record.binding is not None:
            if record.binding != requested_binding:
                raise ActivationTokenConflict(
                    "Check token already names a different activation"
                )
            assert record.receipt is not None
            return ActivationReceipt(
                identity=record.receipt.identity,
                command=dict(record.receipt.command),
                exact_retry=True,
            )

        command = build_scene_activation_command(record.checked.canonical)
        local_adapter.validate_control(command)
        control_channel.send_command(
            "activate_scene",
            basis=dict(command["basis"]),
            scene=dict(command["scene"]),
        )
        observed = local_adapter.accept_control(command)
        if observed != record.checked.identity:  # pragma: no cover
            raise SceneContractError(
                "local adapter accepted a different scene identity"
            )
        receipt = ActivationReceipt(
            identity=observed, command=command, exact_retry=False
        )
        record.binding = requested_binding
        record.receipt = receipt
        return receipt

    def _local_record_for(self, token: str) -> _CheckRecord:
        if not isinstance(token, str) or not token:
            raise ActivationTokenUnknown("Check token is unknown")
        record = self._local_records.get(token)
        if record is None:
            raise ActivationTokenUnknown("Check token is unknown")
        if float(self.clock()) >= record.checked.expires_at and record.binding is None:
            raise ActivationTokenStale("Check token is stale")
        return record

    @staticmethod
    def _token_digest(token: str) -> str:
        if not isinstance(token, str) or not token:
            raise ActivationTokenConflict("check token is invalid")
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _stored(row: sqlite3.Row) -> StoredActivationToken:
        try:
            basis = json.loads(row["basis_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ActivationTokenConflict("stored check basis is corrupt") from exc
        if not isinstance(basis, dict):
            raise ActivationTokenConflict("stored check basis is corrupt")
        def optional_object(column: str) -> dict[str, Any] | None:
            raw = row[column] if column in row.keys() else None
            if raw is None:
                return None
            try:
                value = json.loads(raw)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ActivationTokenConflict("stored activation outbox is corrupt") from exc
            if not isinstance(value, dict):
                raise ActivationTokenConflict("stored activation outbox is corrupt")
            return value

        return StoredActivationToken(
            basis=basis,
            basis_digest=str(row["basis_digest"]),
            issued_at=float(row["issued_at"]),
            expires_at=float(row["expires_at"]),
            activation_id=row["activation_id"],
            idempotency_key=row["idempotency_key"],
            request_digest=row["request_digest"],
            outbox_command=optional_object("outbox_command_json"),
            outbox_status=optional_object("outbox_status_json"),
            outbox_delivered=bool(
                row["outbox_delivered"] if "outbox_delivered" in row.keys() else 0
            ),
        )

    def issue(self, basis: Mapping[str, Any]) -> IssuedActivationToken:
        if not isinstance(basis, Mapping):
            raise TypeError("activation basis must be a mapping")
        issued_at = float(self.clock())
        expires_at = issued_at + self.ttl_seconds
        complete_basis = dict(basis)
        basis_json = canonical_json(complete_basis)
        basis_digest = hashlib.sha256(basis_json.encode("utf-8")).hexdigest()

        # A collision is cryptographically negligible, but the insert remains
        # exclusive so even a forced collision cannot replace authorization.
        while True:
            raw_token = secrets.token_urlsafe(TOKEN_BYTES)
            token_digest = self._token_digest(raw_token)
            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT INTO activation_tokens (
                            token_digest, basis_json, basis_digest,
                            issued_at, expires_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            token_digest,
                            basis_json,
                            basis_digest,
                            issued_at,
                            expires_at,
                        ),
                    )
            except sqlite3.IntegrityError:
                continue
            return IssuedActivationToken(
                token=raw_token,
                basis=complete_basis,
                basis_digest=basis_digest,
                issued_at=issued_at,
                expires_at=expires_at,
            )

    def inspect(
        self, token: str, *, allow_bound_expired: bool = False
    ) -> StoredActivationToken:
        token_digest = self._token_digest(token)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM activation_tokens WHERE token_digest = ?",
                (token_digest,),
            ).fetchone()
        if row is None:
            raise ActivationTokenConflict("check token is unknown")
        stored = self._stored(row)
        if (
            float(self.clock()) >= stored.expires_at
            and not (allow_bound_expired and stored.activation_id is not None)
        ):
            raise ActivationTokenExpired("check token has expired")
        return stored

    def bind(
        self,
        token: str,
        *,
        basis_digest: str,
        idempotency_key: str,
        request_digest: str,
        activation_id_factory: Callable[[], str],
        outbox_factory: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> BoundActivationToken:
        token_digest = self._token_digest(token)
        if not all(
            isinstance(value, str) and value
            for value in (basis_digest, idempotency_key, request_digest)
        ):
            raise ActivationTokenConflict("activation binding is incomplete")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM activation_tokens WHERE token_digest = ?",
                (token_digest,),
            ).fetchone()
            if row is None:
                raise ActivationTokenConflict("check token is unknown")
            stored = self._stored(row)
            if stored.basis_digest != basis_digest:
                raise ActivationTokenConflict(
                    "activation basis no longer matches the checked basis"
                )
            if stored.activation_id is not None:
                if (
                    stored.idempotency_key == idempotency_key
                    and stored.request_digest == request_digest
                ):
                    connection.commit()
                    return BoundActivationToken(
                        stored.activation_id, True, stored
                    )
                raise ActivationTokenConflict(
                    "check token is already bound to another activation"
                )
            if float(self.clock()) >= stored.expires_at:
                raise ActivationTokenExpired("check token has expired")

            activation_id = activation_id_factory()
            if not isinstance(activation_id, str) or not activation_id:
                raise ActivationTokenConflict("activation ID factory failed")
            outbox = dict(outbox_factory(activation_id)) if outbox_factory else {}
            command = outbox.get("command")
            status = outbox.get("status")
            if outbox_factory and (
                not isinstance(command, dict) or not isinstance(status, dict)
            ):
                raise ActivationTokenConflict("activation outbox is incomplete")
            command_json = canonical_json(command) if command is not None else None
            status_json = canonical_json(status) if status is not None else None
            connection.execute(
                """
                UPDATE activation_tokens
                SET activation_id = ?, idempotency_key = ?, request_digest = ?,
                    outbox_command_json = ?, outbox_status_json = ?,
                    outbox_delivered = 0
                WHERE token_digest = ? AND activation_id IS NULL
                """,
                (
                    activation_id,
                    idempotency_key,
                    request_digest,
                    command_json,
                    status_json,
                    token_digest,
                ),
            )
            connection.commit()
            return BoundActivationToken(
                activation_id,
                False,
                StoredActivationToken(
                    basis=stored.basis,
                    basis_digest=stored.basis_digest,
                    issued_at=stored.issued_at,
                    expires_at=stored.expires_at,
                    activation_id=activation_id,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    outbox_command=command,
                    outbox_status=status,
                    outbox_delivered=False,
                ),
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def pending_outbox(self) -> list[StoredActivationToken]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM activation_tokens
                   WHERE activation_id IS NOT NULL
                     AND outbox_command_json IS NOT NULL
                     AND outbox_status_json IS NOT NULL
                     AND outbox_delivered = 0
                   ORDER BY issued_at, activation_id"""
            ).fetchall()
        return [self._stored(row) for row in rows]

    def mark_outbox_delivered(self, activation_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE activation_tokens SET outbox_delivered = 1 WHERE activation_id = ?",
                (activation_id,),
            )


__all__ = [
    "ActivationReceipt",
    "ActivationTokenConflict",
    "ActivationTokenError",
    "ActivationTokenExpired",
    "ActivationTokenStale",
    "ActivationTokenStore",
    "ActivationTokenUnknown",
    "BoundActivationToken",
    "CheckedScene",
    "IssuedActivationToken",
    "StoredActivationToken",
    "TOKEN_BYTES",
    "TOKEN_TTL_SECONDS",
    "canonical_digest",
    "canonical_json",
]
