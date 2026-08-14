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
})


class ReceiverHybridConfigError(ValueError):
    """The durable rollout file is present but unsafe or unsupported."""


@dataclass(frozen=True)
class ReceiverHybridConfig(Mapping[str, object]):
    """Resolved rollout selection with both attribute and mapping access."""

    enabled: bool
    transport_policy: str
    firmware_environment: str

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

    def __getitem__(self, key: str) -> object:
        if key == "enabled":
            return self.enabled
        if key == "transport_policy":
            return self.transport_policy
        if key == "firmware_environment":
            return self.firmware_environment
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(("enabled", "transport_policy", "firmware_environment"))

    def __len__(self) -> int:
        return 3

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
    return ReceiverHybridConfig(
        enabled=enabled,
        transport_policy=transport_policy,
        firmware_environment=firmware_environment,
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
    path = receiver_hybrid_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": RECEIVER_HYBRID_CONFIG_SCHEMA,
        "schema_version": RECEIVER_HYBRID_CONFIG_VERSION,
        "enabled": enabled,
        "transport_policy": transport_policy,
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
        "action", choices=("show", "enable-degraded", "disable")
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.expanduser().resolve()
    if args.action == "enable-degraded":
        config = write_receiver_hybrid_config(root, enabled=True)
    elif args.action == "disable":
        config = write_receiver_hybrid_config(root, enabled=False)
    else:
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
