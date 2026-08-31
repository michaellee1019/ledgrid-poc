#!/usr/bin/env python3
"""Receiver hardware discovery and installed-firmware evidence.

The receiver application image is common to every ESP32-S3, but proof that the
image was installed belongs to a physical chip.  PlatformIO exposes the
ESP32-S3 USB Serial/JTAG factory identifier without resetting the device.  This
module parses that identity and persists the last successful installation per
hardware serial so replacing a board cannot inherit another board's success.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Mapping, Sequence


INVENTORY_SCHEMA_VERSION = 1
INVENTORY_RELATIVE_PATH = Path("run_state") / "receiver_firmware_inventory.json"
MAX_INVENTORY_BYTES = 64 * 1024
_PORT_PATTERN = re.compile(r"/dev/tty(?:ACM|USB)[0-9]+")
_MAC_PATTERN = re.compile(r"[0-9a-f]{12}")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_HWID_FIELD_PATTERN = re.compile(r"(?:^|\s)(SER|LOCATION)=([^\s]+)")
_SAFE_LOCATION_PATTERN = re.compile(r"[A-Za-z0-9_.:+-]{1,128}")
_SAFE_ENVIRONMENT_PATTERN = re.compile(r"[A-Za-z0-9_.+-]{1,128}")


@dataclass(frozen=True)
class ReceiverUSBDevice:
    port: str
    hardware_serial: str
    physical_location: str

    @property
    def physical_id(self) -> str:
        return f"usb:{self.physical_location}"

    def to_dict(self) -> dict[str, str]:
        return {
            "port": self.port,
            "hardware_serial": self.hardware_serial,
            "physical_location": self.physical_location,
            "physical_id": self.physical_id,
        }


@dataclass(frozen=True)
class FirmwareInventoryRecord:
    hardware_serial: str
    installation_digest: str
    firmware_environment: str
    firmware_sha256: str

    def __post_init__(self) -> None:
        _validate_hardware_serial(self.hardware_serial)
        _validate_digest(self.installation_digest, "installation_digest")
        _validate_digest(self.firmware_sha256, "firmware_sha256")
        if _SAFE_ENVIRONMENT_PATTERN.fullmatch(self.firmware_environment) is None:
            raise ValueError("firmware_environment is malformed")

    def to_dict(self) -> dict[str, str]:
        return {
            "hardware_serial": self.hardware_serial,
            "installation_digest": self.installation_digest,
            "firmware_environment": self.firmware_environment,
            "firmware_sha256": self.firmware_sha256,
        }


@dataclass(frozen=True)
class ReceiverFlashTarget:
    device: ReceiverUSBDevice
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {**self.device.to_dict(), "reason": self.reason}


def _validate_digest(value: str, field: str) -> None:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} is malformed")


def _normalize_hardware_serial(value: str) -> str:
    compact = value.strip().lower().replace(":", "").replace("-", "")
    if _MAC_PATTERN.fullmatch(compact) is None:
        raise RuntimeError(
            f"receiver USB serial is not a factory MAC identity: {value!r}"
        )
    return ":".join(compact[index:index + 2] for index in range(0, 12, 2))


def _validate_hardware_serial(value: str) -> None:
    try:
        normalized = _normalize_hardware_serial(value)
    except (AttributeError, RuntimeError) as exc:
        raise ValueError("hardware_serial is malformed") from exc
    if normalized != value:
        raise ValueError("hardware_serial is not canonical")


def parse_platformio_receiver_devices(
    payload: str | Sequence[Mapping[str, Any]],
    *,
    receiver_count: int,
    expected_hardware_serials: Sequence[str] | None = None,
) -> tuple[ReceiverUSBDevice, ...]:
    """Parse the configured safe, stable USB receiver identities.

    A complete target-owned roster allows unrelated ESP32 serial devices to
    remain attached.  Without that roster, discovery stays fail-closed and
    requires exactly ``receiver_count`` devices.
    """

    if (
        isinstance(receiver_count, bool)
        or not isinstance(receiver_count, int)
        or receiver_count <= 0
    ):
        raise ValueError("receiver_count must be a positive integer")
    if isinstance(payload, str):
        try:
            raw_devices = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError("PlatformIO device inventory is not valid JSON") from exc
    else:
        raw_devices = payload
    if not isinstance(raw_devices, (list, tuple)):
        raise RuntimeError("PlatformIO device inventory is not a list")

    devices = []
    for item in raw_devices:
        if not isinstance(item, Mapping):
            raise RuntimeError("PlatformIO device inventory contains a non-object")
        port = item.get("port") or item.get("path")
        if not isinstance(port, str) or _PORT_PATTERN.fullmatch(port) is None:
            continue
        hwid = item.get("hwid")
        if not isinstance(hwid, str):
            raise RuntimeError(f"receiver {port} has no USB hardware identity")
        fields = {key: value for key, value in _HWID_FIELD_PATTERN.findall(hwid)}
        serial = fields.get("SER")
        location = fields.get("LOCATION")
        if serial is None:
            raise RuntimeError(f"receiver {port} exposes no unique USB serial")
        if location is None or _SAFE_LOCATION_PATTERN.fullmatch(location) is None:
            raise RuntimeError(f"receiver {port} exposes no safe physical USB location")
        devices.append(
            ReceiverUSBDevice(
                port=port,
                hardware_serial=_normalize_hardware_serial(serial),
                physical_location=location,
            )
        )

    for field, values in (
        ("ports", [item.port for item in devices]),
        ("hardware serials", [item.hardware_serial for item in devices]),
        ("physical USB locations", [item.physical_location for item in devices]),
    ):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise RuntimeError(f"duplicate receiver {field}: {duplicates}")

    expected = None
    if expected_hardware_serials is not None:
        try:
            expected = tuple(_normalize_hardware_serial(value) for value in expected_hardware_serials)
        except (AttributeError, RuntimeError) as exc:
            raise ValueError("expected receiver hardware serials are malformed") from exc
        if len(expected) != receiver_count or len(set(expected)) != receiver_count:
            raise ValueError(
                "expected receiver hardware serials must contain exactly one "
                "canonical identity for every configured receiver"
            )

    if expected is None:
        if len(devices) != receiver_count:
            raise RuntimeError(
                f"expected exactly {receiver_count} ESP32 serial devices; "
                f"found {len(devices)}: {[item.port for item in devices]}"
            )
        selected = devices
    else:
        selected = [
            item for item in devices if item.hardware_serial in set(expected)
        ]
        selected_serials = {item.hardware_serial for item in selected}
        if selected_serials != set(expected):
            missing = sorted(set(expected) - selected_serials)
            raise RuntimeError(
                "configured receiver hardware serials are missing from discovery: "
                f"{missing}"
            )

    selected.sort(key=lambda item: (item.physical_location, item.hardware_serial))
    return tuple(selected)


def inventory_path(root: Path) -> Path:
    return root / INVENTORY_RELATIVE_PATH


def read_firmware_inventory(root: Path) -> dict[str, FirmwareInventoryRecord]:
    path = inventory_path(root)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {}
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("receiver firmware inventory must be a non-symlink regular file")
    if metadata.st_uid != os.geteuid():
        raise RuntimeError("receiver firmware inventory is not target-owned")
    if metadata.st_size > MAX_INVENTORY_BYTES:
        raise RuntimeError("receiver firmware inventory is unexpectedly large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("receiver firmware inventory is unreadable") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("receiver firmware inventory is not an object")
    if payload.get("schema_version") != INVENTORY_SCHEMA_VERSION:
        raise RuntimeError("unsupported receiver firmware inventory schema")
    raw_records = payload.get("devices")
    if not isinstance(raw_records, list):
        raise RuntimeError("receiver firmware inventory devices are malformed")

    records: dict[str, FirmwareInventoryRecord] = {}
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise RuntimeError("receiver firmware inventory contains a non-object")
        try:
            record = FirmwareInventoryRecord(
                hardware_serial=raw["hardware_serial"],
                installation_digest=raw["installation_digest"],
                firmware_environment=raw["firmware_environment"],
                firmware_sha256=raw["firmware_sha256"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("receiver firmware inventory record is malformed") from exc
        if record.hardware_serial in records:
            raise RuntimeError("receiver firmware inventory contains duplicate devices")
        records[record.hardware_serial] = record
    return records


def plan_receiver_flashes(
    devices: Sequence[ReceiverUSBDevice],
    installed: Mapping[str, FirmwareInventoryRecord],
    *,
    installation_digest: str,
    firmware_environment: str,
    firmware_sha256: str,
    force: bool = False,
    aggregate_marker_matches: bool = True,
) -> tuple[ReceiverFlashTarget, ...]:
    _validate_digest(installation_digest, "installation_digest")
    _validate_digest(firmware_sha256, "firmware_sha256")
    if _SAFE_ENVIRONMENT_PATTERN.fullmatch(firmware_environment) is None:
        raise ValueError("firmware_environment is malformed")
    targets = []
    for device in devices:
        record = installed.get(device.hardware_serial)
        reason = None
        if force:
            reason = "forced"
        elif not aggregate_marker_matches:
            reason = "aggregate_marker_mismatch"
        elif record is None:
            reason = "unrecorded_hardware"
        elif record.installation_digest != installation_digest:
            reason = "installation_digest_changed"
        elif record.firmware_environment != firmware_environment:
            reason = "firmware_environment_changed"
        elif record.firmware_sha256 != firmware_sha256:
            reason = "firmware_binary_changed"
        if reason is not None:
            targets.append(ReceiverFlashTarget(device=device, reason=reason))
    return tuple(targets)


def write_firmware_inventory(
    root: Path,
    devices: Sequence[ReceiverUSBDevice],
    *,
    installation_digest: str,
    firmware_environment: str,
    firmware_sha256: str,
) -> Path:
    records = [
        FirmwareInventoryRecord(
            hardware_serial=device.hardware_serial,
            installation_digest=installation_digest,
            firmware_environment=firmware_environment,
            firmware_sha256=firmware_sha256,
        )
        for device in devices
    ]
    if len({record.hardware_serial for record in records}) != len(records):
        raise ValueError("cannot write duplicate receiver hardware identities")
    path = inventory_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "devices": [
            record.to_dict()
            for record in sorted(records, key=lambda item: item.hardware_serial)
        ],
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
    return path
