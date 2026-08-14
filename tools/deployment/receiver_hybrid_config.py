#!/usr/bin/env python3
"""Durable, fail-closed receiver-hybrid rollout selection.

The target-owned ``run_state`` file is the single authority for deployment,
runtime startup, and restart-state restoration.  Firmware selection is derived
from the allowlisted transport policy; callers cannot persist an arbitrary
PlatformIO environment alongside the policy and create a split-brain rollout.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any


RECEIVER_HYBRID_CONFIG_SCHEMA = "ledgrid.receiver-hybrid-rollout"
RECEIVER_HYBRID_CONFIG_VERSION = 1
RECEIVER_HYBRID_CONFIG_RELATIVE_PATH = Path("run_state/receiver_hybrid.json")
RECEIVER_HYBRID_CONFIG_MAX_BYTES = 4096

RECEIVER_HYBRID_TRANSPORT_OFF = "off"
DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY = "degraded_spi1_01_readable"
DEGRADED_TRANSPORT_POLICY = DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY
DEGRADED_SPI1_TRANSPORT_POLICY = DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY

PRODUCTION_FIRMWARE_ENVIRONMENT = "esp32-s3-devkitc-1"
DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT = (
    "esp32-s3-devkitc-1-local-canary"
)
DEGRADED_FIRMWARE_ENVIRONMENT = (
    DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT
)
ALLOWED_FIRMWARE_ENVIRONMENTS = frozenset({
    PRODUCTION_FIRMWARE_ENVIRONMENT,
    DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
})

_CONFIG_KEYS = frozenset({
    "schema", "schema_version", "enabled", "transport_policy",
    "physical_lane_order", "reverse_strips_by_logical_receiver",
    "reverse_native_strips_by_logical_receiver",
})
_CONFIG_REQUIRED_KEYS = _CONFIG_KEYS - {
    "physical_lane_order", "reverse_strips_by_logical_receiver",
    "reverse_native_strips_by_logical_receiver",
}
DEFAULT_PHYSICAL_LANE_ORDER = (0, 1, 2, 3)
DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER = (False, False, False, False)
DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER = (
    False, False, False, False
)


class ReceiverHybridConfigError(ValueError):
    """The durable rollout file is present but unsafe or unsupported."""


def _normalize_physical_lane_order(value: Any) -> tuple[int, int, int, int]:
    """Return logical receiver ids ordered by physical lane, left to right."""

    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ReceiverHybridConfigError(
            "physical_lane_order must contain exactly four logical receiver ids"
        )
    if any(type(item) is not int for item in value):
        raise ReceiverHybridConfigError(
            "physical_lane_order values must be integers"
        )
    normalized = tuple(value)
    if set(normalized) != {0, 1, 2, 3}:
        raise ReceiverHybridConfigError(
            "physical_lane_order must be a permutation of 0,1,2,3"
        )
    return normalized


def _normalize_reverse_strips_by_logical_receiver(
    value: Any, *, field: str = "reverse_strips_by_logical_receiver",
) -> tuple[bool, bool, bool, bool]:
    """Return one exact local-strip direction flag per logical receiver."""

    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ReceiverHybridConfigError(
            f"{field} must contain exactly four booleans"
        )
    if any(type(item) is not bool for item in value):
        raise ReceiverHybridConfigError(
            f"{field} values must be booleans"
        )
    return tuple(value)


@dataclass(frozen=True)
class ReceiverHybridConfig(Mapping[str, object]):
    """Resolved rollout selection with both attribute and mapping access."""

    enabled: bool
    transport_policy: str
    firmware_environment: str
    physical_lane_order: tuple[int, int, int, int] = DEFAULT_PHYSICAL_LANE_ORDER
    reverse_strips_by_logical_receiver: tuple[bool, bool, bool, bool] = (
        DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER
    )
    reverse_native_strips_by_logical_receiver: tuple[bool, bool, bool, bool] = (
        DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER
    )

    def __post_init__(self) -> None:
        expected = (
            DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT
            if self.enabled else PRODUCTION_FIRMWARE_ENVIRONMENT
        )
        expected_policy = (
            DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY
            if self.enabled else RECEIVER_HYBRID_TRANSPORT_OFF
        )
        if self.transport_policy != expected_policy:
            raise ReceiverHybridConfigError(
                "receiver-hybrid enabled state and transport policy disagree"
            )
        if self.firmware_environment != expected:
            raise ReceiverHybridConfigError(
                "receiver-hybrid policy and firmware environment disagree"
            )
        object.__setattr__(
            self,
            "physical_lane_order",
            _normalize_physical_lane_order(self.physical_lane_order),
        )
        object.__setattr__(
            self,
            "reverse_native_strips_by_logical_receiver",
            _normalize_reverse_strips_by_logical_receiver(
                self.reverse_native_strips_by_logical_receiver,
                field="reverse_native_strips_by_logical_receiver",
            ),
        )
        object.__setattr__(
            self,
            "reverse_strips_by_logical_receiver",
            _normalize_reverse_strips_by_logical_receiver(
                self.reverse_strips_by_logical_receiver
            ),
        )

    def __getitem__(self, key: str) -> object:
        if key == "enabled":
            return self.enabled
        if key == "transport_policy":
            return self.transport_policy
        if key == "firmware_environment":
            return self.firmware_environment
        if key == "physical_lane_order":
            return self.physical_lane_order
        if key == "reverse_strips_by_logical_receiver":
            return self.reverse_strips_by_logical_receiver
        if key == "reverse_native_strips_by_logical_receiver":
            return self.reverse_native_strips_by_logical_receiver
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter((
            "enabled", "transport_policy", "firmware_environment",
            "physical_lane_order", "reverse_strips_by_logical_receiver",
            "reverse_native_strips_by_logical_receiver",
        ))

    def __len__(self) -> int:
        return 6

    def to_dict(self) -> dict[str, object]:
        return dict(self)

    @property
    def selection_digest(self) -> str:
        payload = {
            "schema": RECEIVER_HYBRID_CONFIG_SCHEMA,
            "schema_version": RECEIVER_HYBRID_CONFIG_VERSION,
            **self.to_dict(),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()


OFF_RECEIVER_HYBRID_CONFIG = ReceiverHybridConfig(
    enabled=False,
    transport_policy=RECEIVER_HYBRID_TRANSPORT_OFF,
    firmware_environment=PRODUCTION_FIRMWARE_ENVIRONMENT,
)


def receiver_hybrid_config_path(root: Path) -> Path:
    if not isinstance(root, Path):
        raise TypeError("receiver-hybrid root must be a pathlib.Path")
    return root / RECEIVER_HYBRID_CONFIG_RELATIVE_PATH


def _parse_config(payload: Any, path: Path) -> ReceiverHybridConfig:
    if not isinstance(payload, dict):
        raise ReceiverHybridConfigError(
            f"receiver-hybrid config must be a JSON object: {path}"
        )
    unknown = sorted(set(payload) - _CONFIG_KEYS)
    missing = sorted(_CONFIG_REQUIRED_KEYS - set(payload))
    if unknown or missing:
        raise ReceiverHybridConfigError(
            "receiver-hybrid config keys are not exact; "
            f"missing={missing}, unknown={unknown}"
        )
    if payload["schema"] != RECEIVER_HYBRID_CONFIG_SCHEMA:
        raise ReceiverHybridConfigError("unsupported receiver-hybrid config schema")
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != RECEIVER_HYBRID_CONFIG_VERSION
    ):
        raise ReceiverHybridConfigError(
            "unsupported receiver-hybrid config schema version"
        )
    enabled = payload["enabled"]
    if type(enabled) is not bool:
        raise ReceiverHybridConfigError("receiver-hybrid enabled must be boolean")
    transport_policy = payload["transport_policy"]
    expected_policy = (
        DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY
        if enabled else RECEIVER_HYBRID_TRANSPORT_OFF
    )
    if transport_policy != expected_policy:
        raise ReceiverHybridConfigError(
            "receiver-hybrid config selects an unsupported transport policy"
        )
    firmware_environment = (
        DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT
        if enabled else PRODUCTION_FIRMWARE_ENVIRONMENT
    )
    physical_lane_order = _normalize_physical_lane_order(
        payload.get("physical_lane_order", DEFAULT_PHYSICAL_LANE_ORDER)
    )
    reverse_strips = _normalize_reverse_strips_by_logical_receiver(
        payload.get(
            "reverse_strips_by_logical_receiver",
            DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER,
        )
    )
    reverse_native_strips = _normalize_reverse_strips_by_logical_receiver(
        payload.get(
            "reverse_native_strips_by_logical_receiver",
            DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER,
        ),
        field="reverse_native_strips_by_logical_receiver",
    )
    return ReceiverHybridConfig(
        enabled=enabled,
        transport_policy=transport_policy,
        firmware_environment=firmware_environment,
        physical_lane_order=physical_lane_order,
        reverse_strips_by_logical_receiver=reverse_strips,
        reverse_native_strips_by_logical_receiver=reverse_native_strips,
    )


def resolve_receiver_hybrid_config(root: Path) -> ReceiverHybridConfig:
    """Resolve the target-owned rollout file; absence is exactly feature-off."""

    path = receiver_hybrid_config_path(root)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return OFF_RECEIVER_HYBRID_CONFIG
    except OSError as exc:
        raise ReceiverHybridConfigError(
            f"cannot inspect receiver-hybrid config {path}: {exc}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ReceiverHybridConfigError(
            "receiver-hybrid config must be a regular non-symlink file"
        )
    if metadata.st_size > RECEIVER_HYBRID_CONFIG_MAX_BYTES:
        raise ReceiverHybridConfigError("receiver-hybrid config is unexpectedly large")
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiverHybridConfigError(
            f"cannot read receiver-hybrid config {path}: {exc}"
        ) from exc
    return _parse_config(payload, path)


def write_receiver_hybrid_config(
    root: Path,
    *,
    enabled: bool,
    transport_policy: str | None = None,
    physical_lane_order: Any = None,
    reverse_strips_by_logical_receiver: Any = None,
    reverse_native_strips_by_logical_receiver: Any = None,
) -> ReceiverHybridConfig:
    """Atomically persist one allowlisted rollout selection and fsync it."""

    if type(enabled) is not bool:
        raise TypeError("receiver-hybrid enabled must be boolean")
    expected_policy = (
        DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY
        if enabled else RECEIVER_HYBRID_TRANSPORT_OFF
    )
    if transport_policy is None:
        transport_policy = expected_policy
    if transport_policy != expected_policy:
        raise ReceiverHybridConfigError(
            "receiver-hybrid enabled state and transport policy disagree"
        )
    if physical_lane_order is None:
        try:
            physical_lane_order = resolve_receiver_hybrid_config(
                root
            ).physical_lane_order
        except ReceiverHybridConfigError:
            raise
    physical_lane_order = _normalize_physical_lane_order(physical_lane_order)
    if reverse_strips_by_logical_receiver is None:
        reverse_strips_by_logical_receiver = resolve_receiver_hybrid_config(
            root
        ).reverse_strips_by_logical_receiver
    reverse_strips_by_logical_receiver = (
        _normalize_reverse_strips_by_logical_receiver(
            reverse_strips_by_logical_receiver
        )
    )
    if reverse_native_strips_by_logical_receiver is None:
        reverse_native_strips_by_logical_receiver = (
            resolve_receiver_hybrid_config(
                root
            ).reverse_native_strips_by_logical_receiver
        )
    reverse_native_strips_by_logical_receiver = (
        _normalize_reverse_strips_by_logical_receiver(
            reverse_native_strips_by_logical_receiver,
            field="reverse_native_strips_by_logical_receiver",
        )
    )
    path = receiver_hybrid_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": RECEIVER_HYBRID_CONFIG_SCHEMA,
        "schema_version": RECEIVER_HYBRID_CONFIG_VERSION,
        "enabled": enabled,
        "transport_policy": transport_policy,
        "physical_lane_order": list(physical_lane_order),
        "reverse_strips_by_logical_receiver": list(
            reverse_strips_by_logical_receiver
        ),
        "reverse_native_strips_by_logical_receiver": list(
            reverse_native_strips_by_logical_receiver
        ),
    }
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
    return resolve_receiver_hybrid_config(root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--physical-lane-order",
        help="logical receiver ids from physical left to right, e.g. 0,1,3,2",
    )
    parser.add_argument(
        "--reversed-logical-receivers",
        help="comma-separated logical receivers whose host-frame strip order is reversed",
    )
    parser.add_argument(
        "--reversed-native-logical-receivers",
        help="comma-separated logical receivers whose native animation coordinates are reversed",
    )
    parser.add_argument(
        "action", choices=("show", "enable-degraded", "disable")
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.expanduser().resolve()
    lane_order = None
    reverse_strips = None
    reverse_native_strips = None
    if args.physical_lane_order is not None:
        try:
            lane_order = tuple(
                int(item.strip())
                for item in args.physical_lane_order.split(",")
            )
        except ValueError as exc:
            raise ReceiverHybridConfigError(
                "physical lane order must be comma-separated integers"
            ) from exc
    if args.reversed_logical_receivers is not None:
        try:
            reversed_ids = {
                int(item.strip())
                for item in args.reversed_logical_receivers.split(",")
                if item.strip()
            }
        except ValueError as exc:
            raise ReceiverHybridConfigError(
                "reversed logical receivers must be comma-separated integers"
            ) from exc
        if not reversed_ids.issubset({0, 1, 2, 3}):
            raise ReceiverHybridConfigError(
                "reversed logical receivers must contain only 0,1,2,3"
            )
        reverse_strips = tuple(index in reversed_ids for index in range(4))
    if args.reversed_native_logical_receivers is not None:
        try:
            reversed_native_ids = {
                int(item.strip())
                for item in args.reversed_native_logical_receivers.split(",")
                if item.strip()
            }
        except ValueError as exc:
            raise ReceiverHybridConfigError(
                "reversed native logical receivers must be comma-separated integers"
            ) from exc
        if not reversed_native_ids.issubset({0, 1, 2, 3}):
            raise ReceiverHybridConfigError(
                "reversed native logical receivers must contain only 0,1,2,3"
            )
        reverse_native_strips = tuple(
            index in reversed_native_ids for index in range(4)
        )
    if args.action == "enable-degraded":
        config = write_receiver_hybrid_config(
            root, enabled=True, physical_lane_order=lane_order,
            reverse_strips_by_logical_receiver=reverse_strips,
            reverse_native_strips_by_logical_receiver=reverse_native_strips,
        )
    elif args.action == "disable":
        config = write_receiver_hybrid_config(
            root, enabled=False, physical_lane_order=lane_order,
            reverse_strips_by_logical_receiver=reverse_strips,
            reverse_native_strips_by_logical_receiver=reverse_native_strips,
        )
    else:
        if (
            lane_order is not None
            or reverse_strips is not None
            or reverse_native_strips is not None
        ):
            raise ReceiverHybridConfigError(
                "mapping options require enable-degraded or disable"
            )
        config = resolve_receiver_hybrid_config(root)
    print(json.dumps({
        **config.to_dict(),
        "config_digest": config.selection_digest,
        "path": os.fspath(receiver_hybrid_config_path(root)),
    }, sort_keys=True, separators=(",", ":")))
    return 0


__all__ = [
    "ALLOWED_FIRMWARE_ENVIRONMENTS",
    "DEGRADED_FIRMWARE_ENVIRONMENT",
    "DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT",
    "DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY",
    "DEGRADED_SPI1_TRANSPORT_POLICY",
    "DEGRADED_TRANSPORT_POLICY",
    "DEFAULT_PHYSICAL_LANE_ORDER",
    "DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER",
    "DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER",
    "OFF_RECEIVER_HYBRID_CONFIG",
    "PRODUCTION_FIRMWARE_ENVIRONMENT",
    "RECEIVER_HYBRID_CONFIG_RELATIVE_PATH",
    "RECEIVER_HYBRID_CONFIG_SCHEMA",
    "RECEIVER_HYBRID_CONFIG_VERSION",
    "RECEIVER_HYBRID_TRANSPORT_OFF",
    "ReceiverHybridConfig",
    "ReceiverHybridConfigError",
    "receiver_hybrid_config_path",
    "resolve_receiver_hybrid_config",
    "write_receiver_hybrid_config",
]


if __name__ == "__main__":
    raise SystemExit(main())
