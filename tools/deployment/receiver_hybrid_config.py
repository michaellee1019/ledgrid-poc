#!/usr/bin/env python3
"""Durable, fail-closed receiver rollout and finalized topology selection.

The target-owned ``run_state`` file is the authority for deployment, startup,
and restart restoration. Firmware selection is derived from allowlisted gates;
arbitrary PlatformIO environments are never persisted. Schema-v1 describes the
retired four-receiver installation and must be migrated explicitly.
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
RECEIVER_HYBRID_CONFIG_VERSION = 2
LEGACY_RECEIVER_HYBRID_CONFIG_VERSION = 1
RECEIVER_HYBRID_CONFIG_RELATIVE_PATH = Path("run_state/receiver_hybrid.json")
RECEIVER_HYBRID_CONFIG_MAX_BYTES = 4096

RECEIVER_HYBRID_TRANSPORT_OFF = "off"
STRICT_RECEIVER_HYBRID_TRANSPORT_POLICY = "strict_all_readable_v1"
DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY = "degraded_spi1_01_readable"
DEGRADED_TRANSPORT_POLICY = DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY
DEGRADED_SPI1_TRANSPORT_POLICY = DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY

PRODUCTION_FIRMWARE_ENVIRONMENT = "esp32-s3-devkitc-1"
DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT = (
    "esp32-s3-devkitc-1-local-canary"
)
DEGRADED_FIRMWARE_ENVIRONMENT = DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT
NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT = (
    "esp32-s3-devkitc-1-native-canary"
)
ALLOWED_FIRMWARE_ENVIRONMENTS = frozenset({
    PRODUCTION_FIRMWARE_ENVIRONMENT,
    DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
    NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
})

FINALIZED_RECEIVER_COUNT = 5
DEFAULT_PHYSICAL_LANE_ORDER = (0, 1, 3, 2, 4)
DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER = (
    False, False, True, True, False,
)
DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER = (
    False, False, True, True, False,
)
DEFAULT_RECEIVER_STRIP_COUNTS = (8, 8, 8, 8, 1)
DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS = (0, 8, 24, 16, 32)
DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS = (0xFF, 0xFF, 0xFF, 0xFF, 0x01)

_CONFIG_KEYS = frozenset({
    "schema", "schema_version", "enabled", "transport_policy",
    "physical_lane_order", "reverse_strips_by_logical_receiver",
    "reverse_native_strips_by_logical_receiver", "receiver_strip_counts",
    "receiver_global_strip_offsets", "physical_output_lane_masks",
    "native_modules_enabled",
})
_LEGACY_CONFIG_KEYS = frozenset({
    "schema", "schema_version", "enabled", "transport_policy",
    "physical_lane_order", "reverse_strips_by_logical_receiver",
    "reverse_native_strips_by_logical_receiver",
})


def _known_legacy_payload() -> dict[str, object]:
    return {
        "schema": RECEIVER_HYBRID_CONFIG_SCHEMA,
        "schema_version": LEGACY_RECEIVER_HYBRID_CONFIG_VERSION,
        "enabled": True,
        "transport_policy": DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY,
        "physical_lane_order": [0, 1, 3, 2],
        "reverse_strips_by_logical_receiver": [False, False, True, True],
        "reverse_native_strips_by_logical_receiver": [False, False, True, True],
    }


class ReceiverHybridConfigError(ValueError):
    """The durable rollout file is present but unsafe or unsupported."""


def _exact_tuple(
    value: Any, *, field: str, length: int, item_type: type
) -> tuple:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ReceiverHybridConfigError(
            f"{field} must contain exactly {length} values"
        )
    if any(type(item) is not item_type for item in value):
        raise ReceiverHybridConfigError(
            f"{field} values must be {item_type.__name__}s"
        )
    return tuple(value)


def _normalize_physical_lane_order(value: Any) -> tuple[int, ...]:
    normalized = _exact_tuple(
        value, field="physical_lane_order",
        length=FINALIZED_RECEIVER_COUNT, item_type=int,
    )
    if set(normalized) != set(range(FINALIZED_RECEIVER_COUNT)):
        raise ReceiverHybridConfigError(
            "physical_lane_order must be a permutation of 0,1,2,3,4"
        )
    if normalized != DEFAULT_PHYSICAL_LANE_ORDER:
        raise ReceiverHybridConfigError(
            "physical_lane_order does not match the finalized installation"
        )
    return normalized


def _normalize_bool_tuple(value: Any, *, field: str) -> tuple[bool, ...]:
    normalized = _exact_tuple(
        value, field=field, length=FINALIZED_RECEIVER_COUNT, item_type=bool
    )
    expected = (
        DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER
        if field == "reverse_native_strips_by_logical_receiver"
        else DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER
    )
    if normalized != expected:
        raise ReceiverHybridConfigError(
            f"{field} does not match the finalized installation"
        )
    return normalized


def _normalize_widths(value: Any) -> tuple[int, ...]:
    normalized = _exact_tuple(
        value, field="receiver_strip_counts",
        length=FINALIZED_RECEIVER_COUNT, item_type=int,
    )
    if any(item <= 0 for item in normalized):
        raise ReceiverHybridConfigError(
            "receiver_strip_counts values must be positive"
        )
    if normalized != DEFAULT_RECEIVER_STRIP_COUNTS:
        raise ReceiverHybridConfigError(
            "receiver_strip_counts does not match the finalized installation"
        )
    return normalized


def _normalize_offsets(value: Any, widths: tuple[int, ...]) -> tuple[int, ...]:
    normalized = _exact_tuple(
        value, field="receiver_global_strip_offsets",
        length=FINALIZED_RECEIVER_COUNT, item_type=int,
    )
    if any(item < 0 for item in normalized):
        raise ReceiverHybridConfigError(
            "receiver_global_strip_offsets values must be non-negative"
        )
    covered: set[int] = set()
    for offset, width in zip(normalized, widths, strict=True):
        span = set(range(offset, offset + width))
        if covered.intersection(span):
            raise ReceiverHybridConfigError(
                "receiver global strip ranges must not overlap"
            )
        covered.update(span)
    if covered != set(range(sum(widths))):
        raise ReceiverHybridConfigError(
            "receiver global strip ranges must cover the wall exactly"
        )
    if normalized != DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS:
        raise ReceiverHybridConfigError(
            "receiver_global_strip_offsets does not match the finalized installation"
        )
    return normalized


def _normalize_lane_masks(value: Any, widths: tuple[int, ...]) -> tuple[int, ...]:
    normalized = _exact_tuple(
        value, field="physical_output_lane_masks",
        length=FINALIZED_RECEIVER_COUNT, item_type=int,
    )
    for mask, width in zip(normalized, widths, strict=True):
        if not 0 < mask <= 0xFF:
            raise ReceiverHybridConfigError(
                "physical_output_lane_masks values must be in 1..255"
            )
        if mask.bit_count() < width:
            raise ReceiverHybridConfigError(
                "physical output lane mask cannot expose fewer lanes than "
                "the receiver's logical width"
            )
    if normalized != DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS:
        raise ReceiverHybridConfigError(
            "physical_output_lane_masks does not match the finalized installation"
        )
    return normalized


def _selection(enabled: bool, native: bool) -> tuple[str, str]:
    if native and not enabled:
        raise ReceiverHybridConfigError(
            "native receiver modules require receiver hybrid mode"
        )
    if not enabled:
        return RECEIVER_HYBRID_TRANSPORT_OFF, PRODUCTION_FIRMWARE_ENVIRONMENT
    return (
        STRICT_RECEIVER_HYBRID_TRANSPORT_POLICY,
        NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT
        if native else DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
    )


@dataclass(frozen=True)
class ReceiverHybridConfig(Mapping[str, object]):
    """Resolved rollout selection with exact installed topology."""

    enabled: bool
    transport_policy: str
    firmware_environment: str
    native_modules_enabled: bool = False
    physical_lane_order: tuple[int, ...] = DEFAULT_PHYSICAL_LANE_ORDER
    reverse_strips_by_logical_receiver: tuple[bool, ...] = (
        DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER
    )
    reverse_native_strips_by_logical_receiver: tuple[bool, ...] = (
        DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER
    )
    receiver_strip_counts: tuple[int, ...] = DEFAULT_RECEIVER_STRIP_COUNTS
    receiver_global_strip_offsets: tuple[int, ...] = (
        DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS
    )
    physical_output_lane_masks: tuple[int, ...] = (
        DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS
    )

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ReceiverHybridConfigError("receiver-hybrid enabled must be boolean")
        if type(self.native_modules_enabled) is not bool:
            raise ReceiverHybridConfigError("native_modules_enabled must be boolean")
        policy, environment = _selection(
            self.enabled, self.native_modules_enabled
        )
        if self.transport_policy != policy:
            raise ReceiverHybridConfigError(
                "receiver-hybrid enabled state and transport policy disagree"
            )
        if self.firmware_environment != environment:
            raise ReceiverHybridConfigError(
                "receiver-hybrid policy and firmware environment disagree"
            )
        widths = _normalize_widths(self.receiver_strip_counts)
        object.__setattr__(self, "receiver_strip_counts", widths)
        object.__setattr__(
            self, "physical_lane_order",
            _normalize_physical_lane_order(self.physical_lane_order),
        )
        for field in (
            "reverse_strips_by_logical_receiver",
            "reverse_native_strips_by_logical_receiver",
        ):
            object.__setattr__(
                self, field,
                _normalize_bool_tuple(getattr(self, field), field=field),
            )
        object.__setattr__(
            self, "receiver_global_strip_offsets",
            _normalize_offsets(self.receiver_global_strip_offsets, widths),
        )
        object.__setattr__(
            self, "physical_output_lane_masks",
            _normalize_lane_masks(self.physical_output_lane_masks, widths),
        )

    @property
    def strip_count(self) -> int:
        return sum(self.receiver_strip_counts)

    def __getitem__(self, key: str) -> object:
        if key in {
            "enabled", "transport_policy", "firmware_environment",
            "native_modules_enabled", "physical_lane_order",
            "reverse_strips_by_logical_receiver",
            "reverse_native_strips_by_logical_receiver",
            "receiver_strip_counts", "receiver_global_strip_offsets",
            "physical_output_lane_masks",
        }:
            return getattr(self, key)
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter((
            "enabled", "transport_policy", "firmware_environment",
            "native_modules_enabled", "physical_lane_order",
            "reverse_strips_by_logical_receiver",
            "reverse_native_strips_by_logical_receiver",
            "receiver_strip_counts", "receiver_global_strip_offsets",
            "physical_output_lane_masks",
        ))

    def __len__(self) -> int:
        return 10

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
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
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


def _read_payload(path: Path) -> dict[str, Any] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
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
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiverHybridConfigError(
            f"cannot read receiver-hybrid config {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ReceiverHybridConfigError(
            f"receiver-hybrid config must be a JSON object: {path}"
        )
    return payload


def _parse_config(payload: dict[str, Any], path: Path) -> ReceiverHybridConfig:
    unknown = sorted(set(payload) - _CONFIG_KEYS)
    missing = sorted(_CONFIG_KEYS - set(payload))
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
            "unsupported receiver-hybrid config schema version; migrate legacy "
            f"topology before startup: {path}"
        )
    enabled = payload["enabled"]
    native = payload["native_modules_enabled"]
    if type(enabled) is not bool:
        raise ReceiverHybridConfigError("receiver-hybrid enabled must be boolean")
    if type(native) is not bool:
        raise ReceiverHybridConfigError("native_modules_enabled must be boolean")
    policy, environment = _selection(enabled, native)
    if payload["transport_policy"] != policy:
        raise ReceiverHybridConfigError(
            "receiver-hybrid config selects an unsupported transport policy"
        )
    return ReceiverHybridConfig(
        enabled=enabled,
        transport_policy=policy,
        firmware_environment=environment,
        native_modules_enabled=native,
        physical_lane_order=payload["physical_lane_order"],
        reverse_strips_by_logical_receiver=(
            payload["reverse_strips_by_logical_receiver"]
        ),
        reverse_native_strips_by_logical_receiver=(
            payload["reverse_native_strips_by_logical_receiver"]
        ),
        receiver_strip_counts=payload["receiver_strip_counts"],
        receiver_global_strip_offsets=payload["receiver_global_strip_offsets"],
        physical_output_lane_masks=payload["physical_output_lane_masks"],
    )


def resolve_receiver_hybrid_config(root: Path) -> ReceiverHybridConfig:
    """Resolve durable state; known legacy state is a safe feature-off bridge."""
    path = receiver_hybrid_config_path(root)
    payload = _read_payload(path)
    if payload is None:
        return OFF_RECEIVER_HYBRID_CONFIG
    if (
        type(payload.get("schema_version")) is int
        and payload.get("schema_version") == LEGACY_RECEIVER_HYBRID_CONFIG_VERSION
    ):
        if set(payload) != _LEGACY_CONFIG_KEYS or payload != _known_legacy_payload():
            raise ReceiverHybridConfigError(
                "legacy receiver-hybrid config is not the known installed v1 "
                "layout; manual inspection is required"
            )
        # This read-only bridge lets the first immutable candidate start and
        # pass health before its post-health migration materializes schema v2.
        return OFF_RECEIVER_HYBRID_CONFIG
    return _parse_config(payload, path)


def _stored_payload(config: ReceiverHybridConfig) -> dict[str, object]:
    return {
        "schema": RECEIVER_HYBRID_CONFIG_SCHEMA,
        "schema_version": RECEIVER_HYBRID_CONFIG_VERSION,
        **{
            key: list(value) if isinstance(value, tuple) else value
            for key, value in config.to_dict().items()
            if key != "firmware_environment"
        },
    }


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
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


def write_receiver_hybrid_config(
    root: Path, *, enabled: bool, native_modules_enabled: bool = False,
    transport_policy: str | None = None, physical_lane_order: Any = None,
    reverse_strips_by_logical_receiver: Any = None,
    reverse_native_strips_by_logical_receiver: Any = None,
    receiver_strip_counts: Any = None,
    receiver_global_strip_offsets: Any = None,
    physical_output_lane_masks: Any = None,
) -> ReceiverHybridConfig:
    """Atomically persist one allowlisted selection and exact topology."""
    if type(enabled) is not bool:
        raise TypeError("receiver-hybrid enabled must be boolean")
    if type(native_modules_enabled) is not bool:
        raise TypeError("native_modules_enabled must be boolean")
    policy, environment = _selection(enabled, native_modules_enabled)
    if transport_policy is not None and transport_policy != policy:
        raise ReceiverHybridConfigError(
            "receiver-hybrid enabled state and transport policy disagree"
        )
    current = resolve_receiver_hybrid_config(root)
    values = {
        "physical_lane_order": physical_lane_order,
        "reverse_strips_by_logical_receiver": reverse_strips_by_logical_receiver,
        "reverse_native_strips_by_logical_receiver": reverse_native_strips_by_logical_receiver,
        "receiver_strip_counts": receiver_strip_counts,
        "receiver_global_strip_offsets": receiver_global_strip_offsets,
        "physical_output_lane_masks": physical_output_lane_masks,
    }
    for key, value in tuple(values.items()):
        if value is None:
            values[key] = getattr(current, key)
    config = ReceiverHybridConfig(
        enabled=enabled, transport_policy=policy,
        firmware_environment=environment,
        native_modules_enabled=native_modules_enabled, **values,
    )
    _atomic_write(receiver_hybrid_config_path(root), _stored_payload(config))
    return resolve_receiver_hybrid_config(root)


def migrate_legacy_receiver_hybrid_config(
    root: Path,
) -> tuple[ReceiverHybridConfig, bool]:
    """Migrate the photographed schema-v1 layout to feature-off schema-v2."""
    path = receiver_hybrid_config_path(root)
    payload = _read_payload(path)
    if payload is None:
        config = write_receiver_hybrid_config(root, enabled=False)
        return config, True
    if payload.get("schema_version") == RECEIVER_HYBRID_CONFIG_VERSION:
        return _parse_config(payload, path), False
    expected = _known_legacy_payload()
    if set(payload) != _LEGACY_CONFIG_KEYS or payload != expected:
        raise ReceiverHybridConfigError(
            "legacy receiver-hybrid config is not the known installed v1 "
            "layout; manual inspection is required"
        )
    config = OFF_RECEIVER_HYBRID_CONFIG
    _atomic_write(path, _stored_payload(config))
    return resolve_receiver_hybrid_config(root), True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "action",
        choices=("show", "enable-local", "enable-native", "disable", "migrate"),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.expanduser().resolve()
    migrated = False
    if args.action == "enable-local":
        config = write_receiver_hybrid_config(root, enabled=True)
    elif args.action == "enable-native":
        config = write_receiver_hybrid_config(
            root, enabled=True, native_modules_enabled=True
        )
    elif args.action == "disable":
        config = write_receiver_hybrid_config(root, enabled=False)
    elif args.action == "migrate":
        config, migrated = migrate_legacy_receiver_hybrid_config(root)
    else:
        config = resolve_receiver_hybrid_config(root)
    print(json.dumps({
        **config.to_dict(), "config_digest": config.selection_digest,
        "migrated": migrated,
        "path": os.fspath(receiver_hybrid_config_path(root)),
    }, sort_keys=True, separators=(",", ":")))
    return 0


__all__ = [
    "ALLOWED_FIRMWARE_ENVIRONMENTS", "DEFAULT_PHYSICAL_LANE_ORDER",
    "DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS",
    "DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS", "DEFAULT_RECEIVER_STRIP_COUNTS",
    "DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER",
    "DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER",
    "DEGRADED_FIRMWARE_ENVIRONMENT",
    "DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT",
    "DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY",
    "DEGRADED_SPI1_TRANSPORT_POLICY", "DEGRADED_TRANSPORT_POLICY",
    "FINALIZED_RECEIVER_COUNT", "LEGACY_RECEIVER_HYBRID_CONFIG_VERSION",
    "NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT",
    "OFF_RECEIVER_HYBRID_CONFIG", "PRODUCTION_FIRMWARE_ENVIRONMENT",
    "RECEIVER_HYBRID_CONFIG_RELATIVE_PATH", "RECEIVER_HYBRID_CONFIG_SCHEMA",
    "RECEIVER_HYBRID_CONFIG_VERSION", "RECEIVER_HYBRID_TRANSPORT_OFF",
    "STRICT_RECEIVER_HYBRID_TRANSPORT_POLICY", "ReceiverHybridConfig",
    "ReceiverHybridConfigError", "migrate_legacy_receiver_hybrid_config",
    "receiver_hybrid_config_path", "resolve_receiver_hybrid_config",
    "write_receiver_hybrid_config",
]


if __name__ == "__main__":
    raise SystemExit(main())
