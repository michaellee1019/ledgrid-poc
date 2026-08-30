#!/usr/bin/env python3
"""Target-owned, immutable receiver identity authority.

The firmware inventory proves which firmware was installed on a USB-discovered
board, while the receiver-hybrid configuration defines the accepted wall
topology.  This module is the intentionally narrow join between those two
authorities.  It never discovers hardware or chooses an identity from device
enumeration order: an operator must explicitly provide every logical-route to
hardware-serial binding before a new authority record can be published.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Mapping, Sequence

try:
    from drivers.led_layout import WALL_DEVICE_MAP
    from tools.deployment.receiver_hybrid_config import (
        FINALIZED_RECEIVER_COUNT,
        receiver_hybrid_config_path,
        resolve_receiver_hybrid_config,
    )
except ModuleNotFoundError:  # Direct execution from an uploaded snapshot.
    from drivers.led_layout import WALL_DEVICE_MAP  # type: ignore[no-redef]
    from receiver_hybrid_config import (  # type: ignore[no-redef]
        FINALIZED_RECEIVER_COUNT,
        receiver_hybrid_config_path,
        resolve_receiver_hybrid_config,
    )


RECEIVER_IDENTITY_AUTHORITY_SCHEMA = "ledgrid.receiver-identity-authority"
RECEIVER_IDENTITY_AUTHORITY_VERSION = 1
RECEIVER_IDENTITY_AUTHORITY_RELATIVE_PATH = Path(
    "run_state/receiver_identity_authority.json"
)
RECEIVER_IDENTITY_AUTHORITY_MAX_BYTES = 64 * 1024
RECEIVER_IDENTITY_EVIDENCE_SCHEMA = "ledgrid.receiver-identity-evidence"
RECEIVER_IDENTITY_EVIDENCE_VERSION = 1

_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_SERIAL_PATTERN = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")
_AUTHORITY_KEYS = frozenset({
    "schema", "schema_version", "receiver_hybrid_digest",
    "firmware_inventory_digest", "identities", "authority_digest",
})
_EVIDENCE_KEYS = frozenset({"schema", "schema_version", "identities"})
_IDENTITY_KEYS = frozenset({
    "logical_device", "spi_route", "hardware_serial", "firmware_sha256",
})
_INVENTORY_KEYS = frozenset({"schema_version", "devices"})
_INVENTORY_RECORD_KEYS = frozenset({
    "hardware_serial", "installation_digest", "firmware_environment",
    "firmware_sha256",
})


class ReceiverIdentityAuthorityError(RuntimeError):
    """Receiver identity evidence is absent, malformed, or no longer current."""


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _require_exact_keys(payload: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing or unknown:
        raise ReceiverIdentityAuthorityError(
            f"{label} keys are not exact; missing={missing}, unknown={unknown}"
        )


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ReceiverIdentityAuthorityError(f"{label} is malformed")
    return value


def _require_serial(value: Any, label: str = "hardware_serial") -> str:
    if not isinstance(value, str) or _SERIAL_PATTERN.fullmatch(value) is None:
        raise ReceiverIdentityAuthorityError(f"{label} is malformed or noncanonical")
    return value


def _authority_path(root: Path) -> Path:
    if not isinstance(root, Path):
        raise TypeError("receiver identity root must be a pathlib.Path")
    return root / RECEIVER_IDENTITY_AUTHORITY_RELATIVE_PATH


def receiver_identity_authority_path(root: Path) -> Path:
    """Return the preserved target-owned authority location."""

    return _authority_path(root)


@dataclass(frozen=True)
class ReceiverIdentity:
    """One exact logical receiver binding from operator evidence."""

    logical_device: int
    spi_route: tuple[int, int]
    hardware_serial: str
    firmware_sha256: str

    def __post_init__(self) -> None:
        if type(self.logical_device) is not int or not 0 <= self.logical_device < FINALIZED_RECEIVER_COUNT:
            raise ReceiverIdentityAuthorityError("logical_device is outside the finalized roster")
        if (
            not isinstance(self.spi_route, tuple)
            or len(self.spi_route) != 2
            or any(type(value) is not int or value < 0 for value in self.spi_route)
        ):
            raise ReceiverIdentityAuthorityError("spi_route is malformed")
        _require_serial(self.hardware_serial)
        _require_digest(self.firmware_sha256, "firmware_sha256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_device": self.logical_device,
            "spi_route": list(self.spi_route),
            "hardware_serial": self.hardware_serial,
            "firmware_sha256": self.firmware_sha256,
        }


@dataclass(frozen=True)
class ReceiverIdentityAuthority:
    """Frozen authority accepted for the current process lifetime."""

    receiver_hybrid_digest: str
    firmware_inventory_digest: str
    identities: tuple[ReceiverIdentity, ...]
    authority_digest: str

    def __post_init__(self) -> None:
        _require_digest(self.receiver_hybrid_digest, "receiver_hybrid_digest")
        _require_digest(self.firmware_inventory_digest, "firmware_inventory_digest")
        _require_digest(self.authority_digest, "authority_digest")
        _validate_identities(self.identities)
        expected = _canonical_digest(self.canonical_payload())
        if self.authority_digest != expected:
            raise ReceiverIdentityAuthorityError("receiver identity authority digest mismatches payload")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema": RECEIVER_IDENTITY_AUTHORITY_SCHEMA,
            "schema_version": RECEIVER_IDENTITY_AUTHORITY_VERSION,
            "receiver_hybrid_digest": self.receiver_hybrid_digest,
            "firmware_inventory_digest": self.firmware_inventory_digest,
            "identities": [identity.to_dict() for identity in self.identities],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.canonical_payload(), "authority_digest": self.authority_digest}

    @property
    def by_logical_device(self) -> Mapping[int, ReceiverIdentity]:
        return {identity.logical_device: identity for identity in self.identities}


def _parse_identity(payload: Any, *, label: str) -> ReceiverIdentity:
    if not isinstance(payload, Mapping):
        raise ReceiverIdentityAuthorityError(f"{label} is not an object")
    _require_exact_keys(payload, _IDENTITY_KEYS, label)
    route = payload["spi_route"]
    if (
        not isinstance(route, list)
        or len(route) != 2
        or any(type(value) is not int or value < 0 for value in route)
    ):
        raise ReceiverIdentityAuthorityError(f"{label} spi_route is malformed")
    return ReceiverIdentity(
        logical_device=payload["logical_device"],
        spi_route=(route[0], route[1]),
        hardware_serial=payload["hardware_serial"],
        firmware_sha256=payload["firmware_sha256"],
    )


def _validate_identities(identities: Sequence[ReceiverIdentity]) -> None:
    if not isinstance(identities, tuple) or len(identities) != FINALIZED_RECEIVER_COUNT:
        raise ReceiverIdentityAuthorityError(
            f"receiver identity authority requires exactly {FINALIZED_RECEIVER_COUNT} identities"
        )
    expected_routes = tuple(tuple(route) for route in WALL_DEVICE_MAP)
    for expected_logical, identity in enumerate(identities):
        if not isinstance(identity, ReceiverIdentity):
            raise ReceiverIdentityAuthorityError("receiver identity authority contains an invalid identity")
        if identity.logical_device != expected_logical:
            raise ReceiverIdentityAuthorityError(
                "receiver identity records must be in exact logical-device order"
            )
        if identity.spi_route != expected_routes[expected_logical]:
            raise ReceiverIdentityAuthorityError(
                f"receiver {expected_logical} spi route does not match configured topology"
            )
    for label, values in (
        ("spi routes", [identity.spi_route for identity in identities]),
        ("hardware serials", [identity.hardware_serial for identity in identities]),
    ):
        if len(set(values)) != len(values):
            raise ReceiverIdentityAuthorityError(f"receiver identity authority has duplicate {label}")


def _read_regular_json(path: Path, *, maximum_bytes: int, label: str) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ReceiverIdentityAuthorityError(f"{label} is missing") from exc
    except OSError as exc:
        raise ReceiverIdentityAuthorityError(f"cannot inspect {label}: {exc}") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ReceiverIdentityAuthorityError(f"{label} must be a non-symlink regular file")
    if metadata.st_uid != os.geteuid():
        raise ReceiverIdentityAuthorityError(f"{label} is not target-owned")
    if metadata.st_size > maximum_bytes:
        raise ReceiverIdentityAuthorityError(f"{label} is unexpectedly large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiverIdentityAuthorityError(f"{label} is unreadable") from exc
    if not isinstance(payload, dict):
        raise ReceiverIdentityAuthorityError(f"{label} is not a JSON object")
    return payload


def _current_topology(root: Path) -> tuple[str, tuple[tuple[int, int], ...]]:
    # A missing config otherwise resolves to an in-memory default. That is not
    # operator-provisioned target state and must never bootstrap an authority.
    hybrid_path = receiver_hybrid_config_path(root)
    _read_regular_json(hybrid_path, maximum_bytes=4096, label="receiver-hybrid config")
    try:
        config = resolve_receiver_hybrid_config(root)
    except Exception as exc:
        raise ReceiverIdentityAuthorityError("receiver-hybrid topology is invalid") from exc
    routes = tuple(tuple(route) for route in WALL_DEVICE_MAP)
    if (
        len(routes) != FINALIZED_RECEIVER_COUNT
        or len(config.receiver_strip_counts) != FINALIZED_RECEIVER_COUNT
        or len(set(routes)) != FINALIZED_RECEIVER_COUNT
    ):
        raise ReceiverIdentityAuthorityError("receiver-hybrid topology is incomplete")
    return config.selection_digest, routes


def _current_inventory(root: Path) -> tuple[str, Mapping[str, str]]:
    path = root / "run_state" / "receiver_firmware_inventory.json"
    payload = _read_regular_json(
        path, maximum_bytes=RECEIVER_IDENTITY_AUTHORITY_MAX_BYTES,
        label="receiver firmware inventory",
    )
    _require_exact_keys(payload, _INVENTORY_KEYS, "receiver firmware inventory")
    if payload["schema_version"] != 1 or not isinstance(payload["devices"], list):
        raise ReceiverIdentityAuthorityError("receiver firmware inventory schema is invalid")
    records: dict[str, str] = {}
    devices = payload["devices"]
    if len(devices) != FINALIZED_RECEIVER_COUNT:
        raise ReceiverIdentityAuthorityError("receiver firmware inventory must contain the exact receiver roster")
    for index, raw in enumerate(devices):
        if not isinstance(raw, Mapping):
            raise ReceiverIdentityAuthorityError("receiver firmware inventory contains a non-object")
        _require_exact_keys(raw, _INVENTORY_RECORD_KEYS, f"receiver firmware inventory device {index}")
        serial = _require_serial(raw["hardware_serial"])
        _require_digest(raw["installation_digest"], "installation_digest")
        _require_digest(raw["firmware_sha256"], "firmware_sha256")
        if not isinstance(raw["firmware_environment"], str) or not raw["firmware_environment"]:
            raise ReceiverIdentityAuthorityError("firmware_environment is malformed")
        if serial in records:
            raise ReceiverIdentityAuthorityError("receiver firmware inventory contains duplicate hardware serials")
        records[serial] = raw["firmware_sha256"]
    return _canonical_digest(payload), records


def _parse_authority(payload: Mapping[str, Any]) -> ReceiverIdentityAuthority:
    _require_exact_keys(payload, _AUTHORITY_KEYS, "receiver identity authority")
    if payload["schema"] != RECEIVER_IDENTITY_AUTHORITY_SCHEMA:
        raise ReceiverIdentityAuthorityError("unsupported receiver identity authority schema")
    if payload["schema_version"] != RECEIVER_IDENTITY_AUTHORITY_VERSION:
        raise ReceiverIdentityAuthorityError("unsupported receiver identity authority schema version")
    raw_identities = payload["identities"]
    if not isinstance(raw_identities, list):
        raise ReceiverIdentityAuthorityError("receiver identity authority identities are malformed")
    return ReceiverIdentityAuthority(
        receiver_hybrid_digest=payload["receiver_hybrid_digest"],
        firmware_inventory_digest=payload["firmware_inventory_digest"],
        identities=tuple(
            _parse_identity(raw, label=f"receiver identity {index}")
            for index, raw in enumerate(raw_identities)
        ),
        authority_digest=payload["authority_digest"],
    )


def load_receiver_identity_authority(root: Path) -> ReceiverIdentityAuthority:
    """Load one immutable, fully cross-validated target identity snapshot."""

    payload = _read_regular_json(
        _authority_path(root), maximum_bytes=RECEIVER_IDENTITY_AUTHORITY_MAX_BYTES,
        label="receiver identity authority",
    )
    authority = _parse_authority(payload)
    topology_digest, _routes = _current_topology(root)
    inventory_digest, inventory = _current_inventory(root)
    if authority.receiver_hybrid_digest != topology_digest:
        raise ReceiverIdentityAuthorityError("receiver-hybrid topology digest is stale")
    if authority.firmware_inventory_digest != inventory_digest:
        raise ReceiverIdentityAuthorityError("receiver firmware inventory digest is stale")
    expected_serials = {identity.hardware_serial for identity in authority.identities}
    if set(inventory) != expected_serials:
        raise ReceiverIdentityAuthorityError("receiver firmware inventory does not match identity roster")
    for identity in authority.identities:
        if inventory[identity.hardware_serial] != identity.firmware_sha256:
            raise ReceiverIdentityAuthorityError(
                f"receiver {identity.logical_device} firmware identity does not match inventory"
            )
    return authority


def _parse_operator_evidence(evidence: Mapping[str, Any]) -> tuple[ReceiverIdentity, ...]:
    _require_exact_keys(evidence, _EVIDENCE_KEYS, "operator receiver identity evidence")
    if evidence["schema"] != RECEIVER_IDENTITY_EVIDENCE_SCHEMA:
        raise ReceiverIdentityAuthorityError("unsupported operator receiver identity evidence schema")
    if evidence["schema_version"] != RECEIVER_IDENTITY_EVIDENCE_VERSION:
        raise ReceiverIdentityAuthorityError("unsupported operator receiver identity evidence schema version")
    raw_identities = evidence["identities"]
    if not isinstance(raw_identities, list):
        raise ReceiverIdentityAuthorityError("operator receiver identity evidence identities are malformed")
    identities = tuple(
        _parse_identity(raw, label=f"operator receiver identity {index}")
        for index, raw in enumerate(raw_identities)
    )
    _validate_identities(identities)
    return identities


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def provision_receiver_identity_authority(
    root: Path, *, operator_evidence: Mapping[str, Any]
) -> ReceiverIdentityAuthority:
    """Atomically publish a new authority from explicit operator evidence.

    All source records are checked before publication.  This deliberately does
    not accept an inferred serial order, a partially populated inventory, or a
    pre-existing authority as evidence.
    """

    identities = _parse_operator_evidence(operator_evidence)
    topology_digest, routes = _current_topology(root)
    inventory_digest, inventory = _current_inventory(root)
    if tuple(identity.spi_route for identity in identities) != routes:
        raise ReceiverIdentityAuthorityError("operator evidence routes do not match configured topology")
    if set(inventory) != {identity.hardware_serial for identity in identities}:
        raise ReceiverIdentityAuthorityError("operator evidence serials do not match firmware inventory")
    for identity in identities:
        if inventory[identity.hardware_serial] != identity.firmware_sha256:
            raise ReceiverIdentityAuthorityError(
                f"operator evidence firmware identity does not match inventory for receiver {identity.logical_device}"
            )
    canonical = {
        "schema": RECEIVER_IDENTITY_AUTHORITY_SCHEMA,
        "schema_version": RECEIVER_IDENTITY_AUTHORITY_VERSION,
        "receiver_hybrid_digest": topology_digest,
        "firmware_inventory_digest": inventory_digest,
        "identities": [identity.to_dict() for identity in identities],
    }
    authority = ReceiverIdentityAuthority(
        receiver_hybrid_digest=topology_digest,
        firmware_inventory_digest=inventory_digest,
        identities=identities,
        authority_digest=_canonical_digest(canonical),
    )
    existing_path = _authority_path(root)
    if existing_path.exists() or existing_path.is_symlink():
        # An invalid existing record is never silently overwritten.
        existing = load_receiver_identity_authority(root)
        if existing.authority_digest == authority.authority_digest:
            raise ReceiverIdentityAuthorityError("receiver identity authority already has this digest")
    _atomic_write(existing_path, authority.to_dict())
    return load_receiver_identity_authority(root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("action", choices=("show", "provision"))
    parser.add_argument("--evidence", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.expanduser().resolve()
    if args.action == "show":
        authority = load_receiver_identity_authority(root)
    else:
        if args.evidence is None:
            raise SystemExit("provision requires --evidence")
        try:
            evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SystemExit("operator evidence is unreadable") from exc
        if not isinstance(evidence, Mapping):
            raise SystemExit("operator evidence is not an object")
        authority = provision_receiver_identity_authority(
            root, operator_evidence=evidence
        )
    print(json.dumps(authority.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0


__all__ = [
    "RECEIVER_IDENTITY_AUTHORITY_RELATIVE_PATH",
    "RECEIVER_IDENTITY_AUTHORITY_SCHEMA", "RECEIVER_IDENTITY_AUTHORITY_VERSION",
    "RECEIVER_IDENTITY_EVIDENCE_SCHEMA", "RECEIVER_IDENTITY_EVIDENCE_VERSION",
    "ReceiverIdentity", "ReceiverIdentityAuthority", "ReceiverIdentityAuthorityError",
    "load_receiver_identity_authority", "provision_receiver_identity_authority",
    "receiver_identity_authority_path",
]


if __name__ == "__main__":
    raise SystemExit(main())
